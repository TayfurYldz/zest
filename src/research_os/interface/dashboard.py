"""Local operator dashboard. Read-only by default; never prints secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from dataclasses import asdict
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

from sqlalchemy import text

from research_os.data.postgres.engine import (
    DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
)
from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.autonomous_research_controller import (
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from research_os.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from research_os.application.record_human_review import RecordHumanReview, RecordHumanReviewCommand
from research_os.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from research_os.application.research_run_control import ResearchRunControl
from research_os.core.enums import ActorType, ScopeRuleEffect
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.research.orchestration import OrchestrationBounds
from research_os.research.finding_proposal import HumanReviewDecision
from research_os.interface.osd_client import OperatorApiRunControl, RESEARCH_OSD_URL_ENV
from research_os.application.operator_errors import OperatorError
from research_os.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    RateLimitProfileRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from research_os.interface.cli import build_status_snapshot
from research_os.safe_data import redact_secret_keys

ALLOWED_PLATFORMS = frozenset(
    {"manual", "yeswehack", "hackerone", "bugcrowd", "intigriti", "other"}
)


@dataclass(frozen=True)
class DashboardRunControlRuntime:
    """Injected application boundary; dashboard never constructs Worker/ModelPort."""

    control: ResearchRunControl
    command_factory: Callable[[str, Mapping[str, Any]], StartAutonomousResearchCommand]
    close: Callable[[], None] | None = None
    approval: "DashboardApprovalRuntime | None" = None


@dataclass(frozen=True)
class DashboardApprovalRuntime:
    start_review: StartHumanReview
    record_review: RecordHumanReview
    finalize: FinalizeFinding


def _operator_finding_action(
    action: str, proposal_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    runtime = _RUN_CONTROL_RUNTIME
    if runtime is None or runtime.approval is None:
        raise RuntimeError("approval runtime is not configured")
    operator_id = _optional_text(payload, "operator_id")
    if not operator_id:
        raise ValueError("operator_id is required")
    if action == "review":
        decision_value = _optional_text(payload, "decision")
        if decision_value not in {item.value for item in HumanReviewDecision}:
            raise ValueError("decision must be APPROVE or REJECT")
        runtime.approval.start_review.execute(
            StartHumanReviewCommand(proposal_id=proposal_id)
        )
        result = runtime.approval.record_review.execute(
            RecordHumanReviewCommand(
                proposal_id=proposal_id,
                reviewer_id=operator_id,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision(decision_value),
            )
        )
        return {
            "proposal_id": result.proposal_id,
            "review_id": result.review_id,
            "decision": result.decision.value,
        }
    if action == "finalize":
        result = runtime.approval.finalize.execute(
            FinalizeFindingCommand(
                proposal_id=proposal_id,
                decided_by=operator_id,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        return {
            "proposal_id": result.proposal_id,
            "outcome": result.outcome.value,
            "proposal_state": result.proposal_state.value,
            "finding_id": result.finding_id,
            "approval_id": result.approval_id,
            "reason_codes": list(result.reason_codes),
        }
    raise ValueError("unsupported finding action")


_RUN_CONTROL_RUNTIME: DashboardRunControlRuntime | None = None


def configure_dashboard_run_control(runtime: DashboardRunControlRuntime | None) -> None:
    global _RUN_CONTROL_RUNTIME
    _RUN_CONTROL_RUNTIME = runtime


def build_dashboard_run_control_runtime(
    *, env: Mapping[str, str] | None = None
) -> DashboardRunControlRuntime:
    """Attach dashboard as an Operator API client. Dashboard never owns supervisors."""

    source = dict(os.environ if env is None else env)
    osd_url = source.get(RESEARCH_OSD_URL_ENV)
    if not osd_url or not osd_url.strip():
        raise RuntimeError(
            f"{RESEARCH_OSD_URL_ENV} is required; dashboard must not own run supervisors"
        )
    approval, close_runtime = _optional_approval_runtime(source)
    return DashboardRunControlRuntime(
        control=OperatorApiRunControl(osd_url),
        command_factory=_osd_ignored_command_factory,
        close=close_runtime,
        approval=approval,
    )


def _optional_approval_runtime(
    source: Mapping[str, str],
) -> tuple[DashboardApprovalRuntime | None, Callable[[], None] | None]:
    url = source.get(DATABASE_URL_ENV)
    if not url:
        return None, None
    engine = create_sync_engine(url)
    factory = PostgresUnitOfWork(engine)

    def close_runtime() -> None:
        engine.dispose()

    return (
        DashboardApprovalRuntime(
            start_review=StartHumanReview(factory),
            record_review=RecordHumanReview(factory),
            finalize=FinalizeFinding(factory),
        ),
        close_runtime,
    )


def _osd_ignored_command_factory(
    research_run_id: str, _payload: Mapping[str, Any]
) -> StartAutonomousResearchCommand:
    """Satisfies the dashboard POST type check. research-osd reconstructs config from SoR."""

    from research_os.core.scope import ScopeEvaluationInput

    return StartAutonomousResearchCommand(
        research_run_id=research_run_id,
        budget_id="osd-reconstructs",
        target_reference="osd-reconstructs",
        scope=ScopeEvaluationInput(matches=(), ambiguous=True),
        bounds=OrchestrationBounds(
            max_cycles=1,
            max_experiments=1,
            max_model_calls=1,
            max_worker_invocations=1,
            max_elapsed_ms=1,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
        ),
        research_question="osd-reconstructs",
    )


def _operator_run_action(
    action: str,
    research_run_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    runtime = _RUN_CONTROL_RUNTIME
    if runtime is None:
        raise RuntimeError("run control runtime is not configured")
    if action in {"start", "resume"}:
        command = runtime.command_factory(research_run_id, payload)
        if not isinstance(command, StartAutonomousResearchCommand):
            raise TypeError("command_factory must return StartAutonomousResearchCommand")
        result = getattr(runtime.control, action)(command)
    elif action in {"pause", "cancel"}:
        result = getattr(runtime.control, action)(research_run_id)
    else:
        raise ValueError("unsupported run action")
    if not isinstance(result, OrchestrationTickResult):
        raise TypeError("run control must return OrchestrationTickResult")
    return {
        "research_run_id": result.research_run_id,
        "state": result.state,
        "cycle_number": result.cycle_number,
        "outcome": result.outcome,
        "stop_reason": result.stop_reason,
        "last_phase": result.last_phase,
        "hypothesis_id": result.hypothesis_id,
        "experiment_id": result.experiment_id,
    }


def _operator_preflight(research_run_id: str) -> dict[str, Any]:
    runtime = _RUN_CONTROL_RUNTIME
    if runtime is None:
        raise RuntimeError("run control runtime is not configured")
    control = runtime.control
    if not hasattr(control, "execute_preflight"):
        raise RuntimeError("operator API client does not expose preflight")
    return control.execute_preflight(research_run_id)


def collect_dashboard_payload(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    snapshot = build_status_snapshot(env=source)
    database = _database_payload(source)
    operator = None
    osd_url = source.get(RESEARCH_OSD_URL_ENV)
    if osd_url and osd_url.strip():
        try:
            client = OperatorApiRunControl(osd_url)
            console = client.console_snapshot()
            operator = console.get("health")
            database["operator_source"] = "research-osd"
            if isinstance(console.get("programs"), list) and console["programs"]:
                database["programs"] = console["programs"]
            if isinstance(console.get("runs"), list):
                database["runs"] = console["runs"]
            if isinstance(console.get("run_details"), list):
                database["run_details"] = console["run_details"]
        except (ApplicationError, OperatorError, OSError, ValueError) as exc:
            database["operator_source"] = "research-osd-unreachable"
            database["operator_error"] = exc.__class__.__name__
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": asdict(snapshot),
        "database": database,
        "git": _git_payload(),
        "oast": _oast_payload(source),
        "operator": operator,
        "client_only": True,
    }


def bootstrap_program(
    payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    url = source.get(DATABASE_URL_ENV)
    if not url:
        raise ValueError(f"{DATABASE_URL_ENV} is required")
    cleaned = _bootstrap_payload(payload)
    now = datetime.now(timezone.utc)
    program_id = new_opaque_id()
    auth_id = new_opaque_id()
    run_id = new_opaque_id()
    budget_id = new_opaque_id()
    rate_limit_id = new_opaque_id()
    audit_id = new_opaque_id()
    fingerprint = hashlib.sha256(
        json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    engine = create_sync_engine(url)
    try:
        with PostgresUnitOfWork(engine) as uow:
            uow.programs.insert(
                ProgramRecord(
                    program_id=program_id,
                    name=cleaned["program_name"],
                    handle=cleaned["program_handle"],
                    platform=cleaned["platform"],
                    created_at=now,
                )
            )
            for rule in _scope_records(cleaned, program_id=program_id, now=now):
                uow.scope_rules_v2.insert(rule)
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id=program_id,
                    loopback_fixture=False,
                    max_response_bytes=cleaned["max_response_bytes"],
                    timeout_ms=cleaned["timeout_ms"],
                    action_policy={
                        "forbidden_actions": cleaned["forbidden_actions"],
                        "dashboard_bootstrap": True,
                        "required_user_agent": cleaned["required_user_agent"],
                        "run": {
                            "target_reference": cleaned["target_reference"],
                            "research_question": cleaned["research_question"],
                        },
                        "orchestration": {
                            "max_cycles": cleaned["max_cycles"],
                            "max_experiments": cleaned["max_experiments"],
                            "max_model_calls": cleaned["max_model_calls"],
                            "max_worker_invocations": cleaned[
                                "max_worker_invocations"
                            ],
                            "max_elapsed_ms": cleaned["max_elapsed_ms"],
                            "max_selected_opportunities": cleaned[
                                "max_selected_opportunities"
                            ],
                            "max_runtime_fallback": cleaned["max_runtime_fallback"],
                            "side_effect_ceiling": cleaned["side_effect_ceiling"],
                            "allow_repeated_control_experiments": False,
                            "policy_version": "dashboard.bootstrap.v1",
                        },
                    },
                    daily_llm_budget_microdollars=cleaned[
                        "daily_llm_budget_microdollars"
                    ],
                    created_at=now,
                    updated_at=now,
                )
            )
            uow.rate_limit_profiles.insert(
                RateLimitProfileRecord(
                    profile_id=rate_limit_id,
                    program_id=program_id,
                    max_requests_per_window=cleaned["max_requests_per_window"],
                    window_seconds=cleaned["window_seconds"],
                    created_at=now,
                )
            )
            uow.authorization_sources.insert(
                AuthorizationSourceRecord(
                    authorization_source_id=auth_id,
                    program_id=program_id,
                    state="ACTIVE",
                    provenance_reference=cleaned["authorization_reference"],
                    created_at=now,
                    effective_from=now,
                )
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id=run_id,
                    program_id=program_id,
                    authorization_source_id=auth_id,
                    initiated_by_actor_id=cleaned["operator_id"],
                    initiated_by_actor_type=ActorType.HUMAN_OPERATOR.value,
                    started_at=now,
                )
            )
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id=budget_id,
                    research_run_id=run_id,
                    max_requests=cleaned["max_requests"],
                    max_tool_calls=cleaned["max_tool_calls"],
                    max_runtime_ms=cleaned["max_runtime_ms"],
                    max_concurrency=cleaned["max_concurrency"],
                    issued_at=now,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=audit_id,
                    occurred_at=now,
                    actor_id=cleaned["operator_id"],
                    actor_type=ActorType.HUMAN_OPERATOR.value,
                    event_type="DASHBOARD_PROGRAM_BOOTSTRAPPED",
                    subject_type="research_run",
                    subject_id=run_id,
                    payload={
                        "program_id": program_id,
                        "authorization_source_id": auth_id,
                        "budget_id": budget_id,
                        "rate_limit_profile_id": rate_limit_id,
                        "scope_rule_count": len(cleaned["in_scope"])
                        + len(cleaned["out_of_scope"]),
                        "configuration_fingerprint": fingerprint,
                        "active_testing_started": False,
                        "not_a_scan": True,
                    },
                    correlation_id=run_id,
                )
            )
            uow.commit()
    finally:
        engine.dispose()
    return {
        "program_id": program_id,
        "authorization_source_id": auth_id,
        "research_run_id": run_id,
        "budget_id": budget_id,
        "rate_limit_profile_id": rate_limit_id,
        "audit_event_id": audit_id,
        "configuration_fingerprint": fingerprint,
        "state": "STARTABLE",
    }


def _bootstrap_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    program_name = _text(payload, "program_name")
    in_scope = _lines(payload.get("in_scope"))
    if not in_scope:
        raise ValueError("in_scope requires at least one scope entry")
    platform = _optional_text(payload, "platform") or "manual"
    if platform not in ALLOWED_PLATFORMS:
        raise ValueError("platform is not supported")
    side_effect_ceiling = _non_negative_int(payload, "side_effect_ceiling", 0)
    if side_effect_ceiling not in {0, 1, 2, 3}:
        raise ValueError("side_effect_ceiling must be 0, 1, 2, or 3")
    return {
        "program_name": program_name,
        "program_handle": _optional_text(payload, "program_handle"),
        "platform": platform,
        "target_reference": _text(payload, "target_reference"),
        "authorization_reference": _text(payload, "authorization_reference"),
        "operator_id": _optional_text(payload, "operator_id") or new_opaque_id(),
        "research_question": (
            _optional_text(payload, "research_question")
            or "Which authorized surfaces require deeper manual review?"
        ),
        "in_scope": in_scope,
        "out_of_scope": _lines(payload.get("out_of_scope")),
        "forbidden_actions": _lines(payload.get("forbidden_actions")),
        "required_user_agent": _optional_text(payload, "required_user_agent"),
        "max_response_bytes": _positive_int(payload, "max_response_bytes", 1_048_576),
        "timeout_ms": _positive_int(payload, "timeout_ms", 10_000),
        "max_requests_per_window": _positive_int(
            payload, "max_requests_per_window", 30
        ),
        "window_seconds": _positive_int(payload, "window_seconds", 60),
        "max_requests": _positive_int(payload, "max_requests", 500),
        "max_tool_calls": _positive_int(payload, "max_tool_calls", 200),
        "max_runtime_ms": _positive_int(payload, "max_runtime_ms", 3_600_000),
        "max_concurrency": _positive_int(payload, "max_concurrency", 1),
        "max_cycles": _positive_int(payload, "max_cycles", 20),
        "max_experiments": _positive_int(payload, "max_experiments", 50),
        "max_model_calls": _positive_int(payload, "max_model_calls", 50),
        "max_worker_invocations": _positive_int(
            payload, "max_worker_invocations", 100
        ),
        "max_elapsed_ms": _positive_int(payload, "max_elapsed_ms", 3_600_000),
        "max_selected_opportunities": _positive_int(
            payload, "max_selected_opportunities", 4
        ),
        "max_runtime_fallback": _positive_int(payload, "max_runtime_fallback", 1),
        "daily_llm_budget_microdollars": _non_negative_int(
            payload, "daily_llm_budget_microdollars", 0
        ),
        "side_effect_ceiling": side_effect_ceiling,
    }


def _scope_records(
    cleaned: Mapping[str, Any], *, program_id: str, now: datetime
) -> list[ScopeRuleV2Record]:
    records: list[ScopeRuleV2Record] = []
    for value in cleaned["in_scope"]:
        records.append(
            _scope_record(
                program_id=program_id,
                effect=ScopeRuleEffect.ALLOW.value,
                value=value,
                now=now,
            )
        )
    for value in cleaned["out_of_scope"]:
        records.append(
            _scope_record(
                program_id=program_id,
                effect=ScopeRuleEffect.OUT_OF_SCOPE.value,
                value=value,
                now=now,
            )
        )
    return records


def _scope_record(
    *, program_id: str, effect: str, value: str, now: datetime
) -> ScopeRuleV2Record:
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"scope entry has unsupported scheme: {value}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"scope entry requires a host: {value}")
    host = host.lower()
    host_pattern = None
    exact_host = host
    if host.startswith("*."):
        host_pattern = host
        exact_host = None
    path_prefix = parsed.path if parsed.path and parsed.path != "/" else None
    return ScopeRuleV2Record(
        rule_id=new_opaque_id(),
        program_id=program_id,
        effect=effect,
        scheme=parsed.scheme,
        source_reference=new_opaque_id(),
        created_at=now,
        host=exact_host,
        host_pattern=host_pattern,
        port=parsed.port,
        path_prefix=path_prefix,
    )


def _lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.splitlines()
    elif isinstance(value, list):
        candidates = value
    else:
        raise ValueError("line input must be a string or list")
    result: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            raise ValueError("line input entries must be strings")
        text_value = item.strip()
        if text_value and not text_value.startswith("#"):
            result.append(text_value)
    return result


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_text(payload, key)
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    return value or None


def _positive_int(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = _int_value(payload, key, default)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _non_negative_int(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = _int_value(payload, key, default)
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
    return value


def _int_value(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if value == "":
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    return parsed


def _database_payload(env: Mapping[str, str]) -> dict[str, Any]:
    url = env.get(DATABASE_URL_ENV)
    if not url:
        return {
            "state": "UNAVAILABLE",
            "dsn": "unset",
            "summary": {},
            "programs": [],
            "runs": [],
            "run_details": [],
            "audit_events": [],
            "coverage": [],
            "queue": {},
            "error": "application database is not configured",
        }
    try:
        dsn = redacted_database_url(url)
    except Exception:
        dsn = "unparseable"
    engine = create_sync_engine(url)
    try:
        with engine.connect() as connection:
            summary = {
                "programs": _scalar(connection, "SELECT COUNT(*) FROM program"),
                "research_runs": _scalar(connection, "SELECT COUNT(*) FROM research_run"),
                "active_authorizations": _scalar(
                    connection,
                    "SELECT COUNT(*) FROM authorization_source WHERE state = 'ACTIVE'",
                ),
                "enabled_families": _scalar(
                    connection,
                    "SELECT COUNT(*) FROM hunter_family WHERE enabled = true",
                ),
                "pending_v3": _scalar(
                    connection,
                    "SELECT COUNT(*) FROM hunt_v3_queue WHERE state = 'PENDING'",
                ),
                "audit_events": _scalar(connection, "SELECT COUNT(*) FROM audit_event"),
            }
            programs = [
                _json_row(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT p.program_id, p.name, p.handle, p.platform,
                               p.created_at,
                               COUNT(DISTINCT s.rule_id) AS scope_rules,
                               COUNT(DISTINCT a.authorization_source_id)
                                 FILTER (WHERE a.state = 'ACTIVE')
                                 AS active_authorizations
                        FROM program p
                        LEFT JOIN scope_rule_v2 s ON s.program_id = p.program_id
                        LEFT JOIN authorization_source a
                          ON a.program_id = p.program_id
                        GROUP BY p.program_id, p.name, p.handle, p.platform,
                                 p.created_at
                        ORDER BY p.created_at DESC
                        LIMIT 8
                        """
                    )
                ).mappings()
            ]
            runs = [
                _json_row(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT r.research_run_id, r.program_id, r.started_at,
                               o.state, o.current_phase, o.cycle_number,
                               o.target_reference, o.updated_at
                        FROM research_run r
                        LEFT JOIN research_orchestration o
                          ON o.research_run_id = r.research_run_id
                        ORDER BY COALESCE(o.updated_at, r.started_at) DESC
                        LIMIT 8
                        """
                    )
                ).mappings()
            ]
            run_details = [
                _json_row(row, redact_payload=True)
                for row in connection.execute(
                    text(
                        """
                        SELECT r.research_run_id, r.program_id, p.name AS program_name,
                               p.platform, r.started_at,
                               a.authorization_source_id, a.state AS authorization_state,
                               o.state, o.current_phase, o.cycle_number, o.max_cycles,
                               o.stop_reason, o.pause_reason, o.last_phase,
                               o.last_hypothesis_id, o.last_experiment_id,
                               o.last_attempt_id, o.last_observation_id,
                               o.last_assessment_id, o.last_worker_result_id,
                               pp.daily_llm_budget_microdollars, pp.action_policy,
                               ib.max_requests, ib.max_tool_calls,
                               ib.max_runtime_ms, ib.max_concurrency,
                               COALESCE(bu.request_count, 0) AS request_count,
                               COALESCE(bu.worker_count, 0) AS worker_count,
                               COALESCE(bu.model_count, 0) AS model_count,
                               COALESCE(h.hypothesis_count, 0) AS hypothesis_count,
                               COALESCE(e.experiment_count, 0) AS experiment_count,
                               COALESCE(obs.observation_count, 0) AS observation_count,
                               COALESCE(ev.evidence_count, 0) AS evidence_count,
                               COALESCE(c.candidate_count, 0) AS candidate_count,
                               COALESCE(v.verification_count, 0) AS verification_count,
                               COALESCE(fp.finding_proposal_count, 0) AS finding_proposal_count,
                               COALESCE(f.finding_count, 0) AS finding_count,
                               COALESCE(ap.pending_approval_count, 0) AS pending_approval_count
                        FROM research_run r
                        JOIN program p ON p.program_id = r.program_id
                        LEFT JOIN authorization_source a
                          ON a.authorization_source_id = r.authorization_source_id
                        LEFT JOIN program_policy pp ON pp.program_id = r.program_id
                        LEFT JOIN research_orchestration o
                          ON o.research_run_id = r.research_run_id
                        LEFT JOIN issued_budget ib ON ib.budget_id = o.budget_id
                        LEFT JOIN (
                          SELECT research_run_id,
                                 SUM(amount) FILTER (WHERE resource_type = 'REQUEST') AS request_count,
                                 SUM(amount) FILTER (WHERE resource_type = 'WORKER_INVOCATION') AS worker_count,
                                 SUM(amount) FILTER (WHERE resource_type = 'MODEL_CALL') AS model_count
                          FROM budget_consumption GROUP BY research_run_id
                        ) bu ON bu.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS hypothesis_count FROM hypothesis GROUP BY research_run_id) h
                          ON h.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS experiment_count FROM experiment GROUP BY research_run_id) e
                          ON e.research_run_id = r.research_run_id
                        LEFT JOIN (
                          SELECT wr.research_run_id, COUNT(*) AS observation_count
                          FROM observation ob
                          JOIN worker_result wr ON wr.worker_result_id = ob.worker_result_id
                          GROUP BY wr.research_run_id
                        ) obs
                          ON obs.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS evidence_count FROM evidence GROUP BY research_run_id) ev
                          ON ev.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS candidate_count FROM candidate GROUP BY research_run_id) c
                          ON c.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS verification_count FROM verification GROUP BY research_run_id) v
                          ON v.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS finding_proposal_count FROM finding_proposal GROUP BY research_run_id) fp
                          ON fp.research_run_id = r.research_run_id
                        LEFT JOIN (SELECT research_run_id, COUNT(*) AS finding_count FROM finding GROUP BY research_run_id) f
                          ON f.research_run_id = r.research_run_id
                        LEFT JOIN (
                          SELECT research_run_id, COUNT(*) AS pending_approval_count
                          FROM approval WHERE decision = 'PENDING' GROUP BY research_run_id
                        ) ap ON ap.research_run_id = r.research_run_id
                        ORDER BY COALESCE(o.updated_at, r.started_at) DESC
                        LIMIT 8
                        """
                    )
                ).mappings()
            ]
            audit_events = [
                _json_row(row, redact_payload=True)
                for row in connection.execute(
                    text(
                        """
                        SELECT occurred_at, event_type, subject_type, subject_id,
                               correlation_id, payload
                        FROM audit_event
                        ORDER BY occurred_at DESC
                        LIMIT 40
                        """
                    )
                ).mappings()
            ]
            coverage = [
                _json_row(row, redact_payload=True)
                for row in connection.execute(
                    text(
                        """
                        SELECT research_run_id, total_debt, matrix_hash,
                               cell_counts, created_at
                        FROM coverage_debt_snapshot
                        ORDER BY created_at DESC
                        LIMIT 6
                        """
                    )
                ).mappings()
            ]
            queue = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    text(
                        """
                        SELECT state, COUNT(*) AS count
                        FROM hunt_v3_queue
                        GROUP BY state
                        ORDER BY state
                        """
                    )
                ).mappings()
            }
        return {
            "state": "HEALTHY",
            "dsn": dsn,
            "summary": summary,
            "programs": programs,
            "runs": runs,
            "run_details": run_details,
            "audit_events": audit_events,
            "coverage": coverage,
            "queue": queue,
            "error": None,
        }
    except Exception as exc:
        return {
            "state": "UNAVAILABLE",
            "dsn": dsn,
            "summary": {},
            "programs": [],
            "runs": [],
            "run_details": [],
            "audit_events": [],
            "coverage": [],
            "queue": {},
            "error": exc.__class__.__name__,
        }
    finally:
        engine.dispose()


def _oast_payload(env: Mapping[str, str]) -> dict[str, Any]:
    configured = bool(env.get("RESEARCH_OS_INTERACTSH_SERVER") or env.get("INTERACTSH_SERVER"))
    return {
        "mode": "INTERACTSH_CONFIGURED" if configured else "LOOPBACK_CORE_ONLY",
        "adapter": "not implemented" if configured else "loopback",
        "live_ready": False,
    }


def _git_payload() -> dict[str, str | None]:
    root = Path(__file__).resolve().parents[3]

    def run(args: list[str]) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=root,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            ).strip()
        except Exception:
            return None

    return {
        "branch": run(["rev-parse", "--abbrev-ref", "HEAD"]),
        "head": run(["rev-parse", "--short", "HEAD"]),
        "status": run(["status", "-sb"]),
    }


def _scalar(connection: Any, statement: str) -> int:
    value = connection.execute(text(statement)).scalar()
    return int(value or 0)


def _json_row(row: Mapping[str, Any], *, redact_payload: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key in {"payload", "action_policy"} and redact_payload:
            value = redact_secret_keys(value, key)
        result[str(key)] = _json_value(value)
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ResearchOSDashboard/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, HTML, "text/html; charset=utf-8")
            return
        if path == "/api/dashboard":
            self._send_json(collect_dashboard_payload())
            return
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "finding-proposals"
            and parts[2]
            and parts[3] in {"review", "finalize"}
        ):
            try:
                result = _operator_finding_action(
                    parts[3], parts[2], self._read_json_body()
                )
            except ValueError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST
                )
                return
            except RuntimeError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE
                )
                return
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": exc.__class__.__name__},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json({"ok": True, "result": result})
            return
        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "runs"
            and parts[2]
            and parts[3] in {"start", "pause", "resume", "cancel", "preflight"}
        ):
            try:
                if parts[3] == "preflight":
                    result = _operator_preflight(unquote(parts[2]))
                else:
                    result = _operator_run_action(
                        parts[3],
                        unquote(parts[2]),
                        self._read_json_body() if parts[3] in {"start", "resume"} else {},
                    )
            except OperatorError as exc:
                self._send_json(exc.to_payload(), status=exc.http_status)
                return
            except ValueError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST
                )
                return
            except RuntimeError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE
                )
                return
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": exc.__class__.__name__},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json({"ok": True, "result": result})
            return
        if path == "/api/programs/bootstrap":
            try:
                result = bootstrap_program(self._read_json_body())
            except ValueError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST
                )
                return
            except Exception as exc:
                self._send_json(
                    {"ok": False, "error": exc.__class__.__name__},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json({"ok": True, "result": result})
            return
        self._send_json(
            {"ok": False, "error": "not found"}, status=HTTPStatus.NOT_FOUND
        )

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Mapping[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length must be an integer") from exc
        if length <= 0:
            raise ValueError("request body is required")
        if length > 65_536:
            raise ValueError("request body is too large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("request body must be a JSON object")
        return value

    def _send_json(
        self, value: Mapping[str, Any], *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _send(self, status: HTTPStatus, body: str | bytes, content_type: str) -> None:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-os-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        source = dict(os.environ)
        if source.get(RESEARCH_OSD_URL_ENV):
            configure_dashboard_run_control(build_dashboard_run_control_runtime())
        else:
            configure_dashboard_run_control(None)
    except (RuntimeError, ValueError):
        configure_dashboard_run_control(None)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Research OS dashboard listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        runtime = _RUN_CONTROL_RUNTIME
        if runtime is not None and runtime.close is not None:
            runtime.close()
        configure_dashboard_run_control(None)
    return 0


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Research OS Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f6f4;
      --panel: #ffffff;
      --line: #d9ddd7;
      --text: #222825;
      --muted: #69726d;
      --soft: #eef1ed;
      --ok: #12805c;
      --warn: #a66b00;
      --bad: #b42318;
      --info: #2f6f7e;
      --ink: #151918;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.4;
      letter-spacing: 0;
    }
    button, input, select, textarea {
      font: inherit;
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
    }
    .rail {
      border-right: 1px solid var(--line);
      background: #fbfcfa;
      padding: 18px 14px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 22px;
      font-weight: 750;
      color: var(--ink);
    }
    .mark {
      width: 28px;
      height: 28px;
      border: 1px solid #27312d;
      display: grid;
      place-items: center;
      font-size: 13px;
      font-weight: 800;
      background: #202724;
      color: white;
      border-radius: 6px;
    }
    .nav {
      display: grid;
      gap: 6px;
    }
    .nav button {
      width: 100%;
      height: 34px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      text-align: left;
      padding: 0 10px;
      font-weight: 650;
      cursor: pointer;
    }
    .nav button.active,
    .nav button:hover {
      background: var(--soft);
      color: var(--ink);
    }
    .railBlock {
      border-top: 1px solid var(--line);
      margin-top: 18px;
      padding-top: 14px;
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    main {
      min-width: 0;
      padding: 18px 22px 26px;
    }
    .topbar {
      height: 42px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 14px;
    }
    .title {
      font-size: 18px;
      font-weight: 800;
      color: var(--ink);
    }
    .actions {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-weight: 650;
      font-size: 12px;
    }
    .iconButton {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      width: 34px;
      height: 34px;
      cursor: pointer;
      color: var(--ink);
      font-weight: 800;
    }
    .grid {
      display: grid;
      gap: 14px;
    }
    .metrics {
      grid-template-columns: repeat(6, minmax(130px, 1fr));
    }
    .columns {
      grid-template-columns: minmax(0, 1.1fr) minmax(360px, .9fr);
      align-items: start;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    .panelHead {
      height: 40px;
      padding: 0 12px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-weight: 780;
      color: var(--ink);
    }
    .panelBody { padding: 12px; }
    .metric {
      padding: 12px;
      min-height: 82px;
      display: grid;
      gap: 8px;
    }
    .metric .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .metric .value {
      color: var(--ink);
      font-size: 22px;
      font-weight: 820;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .metric .sub {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .statusRow {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      min-height: 34px;
      border-bottom: 1px solid var(--soft);
      gap: 12px;
    }
    .statusRow:last-child { border-bottom: 0; }
    .name {
      min-width: 0;
      font-weight: 700;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 22px;
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 0 8px;
      font-size: 11px;
      font-weight: 780;
      color: var(--muted);
      background: #fff;
      white-space: nowrap;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--muted);
    }
    .ok .dot { background: var(--ok); }
    .ok { color: var(--ok); border-color: #b9d8c9; background: #f3faf6; }
    .warn .dot { background: var(--warn); }
    .warn { color: var(--warn); border-color: #e7cf9e; background: #fff9ea; }
    .bad .dot { background: var(--bad); }
    .bad { color: var(--bad); border-color: #efbbb6; background: #fff5f3; }
    .info .dot { background: var(--info); }
    .info { color: var(--info); border-color: #b8d5dc; background: #f0f8fa; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 9px 8px;
      border-bottom: 1px solid var(--soft);
      text-align: left;
      vertical-align: top;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    th {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }
    td {
      color: var(--text);
      font-weight: 620;
      font-size: 13px;
    }
    tr:last-child td { border-bottom: 0; }
    .log {
      display: grid;
      gap: 8px;
      max-height: 520px;
      overflow: auto;
      padding-right: 4px;
    }
    .event {
      border-left: 3px solid var(--line);
      padding: 2px 0 8px 10px;
      display: grid;
      gap: 3px;
    }
    .event strong {
      font-size: 13px;
      color: var(--ink);
    }
    .event span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .empty {
      color: var(--muted);
      font-weight: 650;
      padding: 14px 0;
    }
    .view.hidden { display: none; }
    .setupGrid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
    }
    .field {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .field.wide { grid-column: 1 / -1; }
    .field label {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 8px 9px;
      font-weight: 640;
      outline: none;
    }
    textarea {
      min-height: 86px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    input:focus, select:focus, textarea:focus {
      border-color: #66887e;
      box-shadow: 0 0 0 3px #dbe8e4;
    }
    .formActions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 12px;
    }
    .primaryButton {
      border: 1px solid #1d3b33;
      border-radius: 6px;
      background: #1f3d35;
      color: #fff;
      height: 36px;
      padding: 0 14px;
      font-weight: 800;
      cursor: pointer;
    }
    .primaryButton:disabled {
      opacity: .6;
      cursor: wait;
    }
    .formStatus {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      min-width: 160px;
      text-align: right;
    }
    .runControls {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      min-width: 220px;
    }
    .controlButton {
      border: 1px solid var(--line);
      border-radius: 5px;
      background: #fff;
      color: var(--ink);
      padding: 5px 8px;
      font-size: 11px;
      font-weight: 800;
      cursor: pointer;
    }
    .controlButton:hover { border-color: #66887e; background: #f3faf6; }
    .controlButton:disabled { opacity: .55; cursor: wait; }
    .controlStatus {
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
    }
    @media (max-width: 1120px) {
      .shell { grid-template-columns: 1fr; }
      .rail { display: none; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .columns { grid-template-columns: 1fr; }
      .setupGrid { grid-template-columns: 1fr; }
      main { padding: 14px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="rail">
      <div class="brand"><div class="mark">RO</div><div>Research OS</div></div>
      <div class="nav">
        <button class="active" data-view="operations" data-title="Security Operations">Operations</button>
        <button data-view="setup" data-title="Program Setup">Setup</button>
        <button data-view="operations" data-title="Security Operations">Runs</button>
        <button data-view="operations" data-title="Security Operations">Coverage</button>
        <button data-view="operations" data-title="Security Operations">Approvals</button>
        <button data-view="operations" data-title="Security Operations">OAST</button>
        <button data-view="operations" data-title="Security Operations">Audit</button>
      </div>
      <div class="railBlock">
        <div id="gitBranch">branch: -</div>
        <div id="gitHead">head: -</div>
      </div>
    </aside>
    <main>
      <div class="topbar">
        <div class="title" id="pageTitle">Security Operations</div>
        <div class="actions">
          <span id="updated">waiting</span>
          <button class="iconButton" id="refresh" title="Refresh">R</button>
        </div>
      </div>

      <div id="view-operations" class="view">
      <section class="grid metrics">
        <div class="panel metric"><div class="label">PostgreSQL</div><div class="value" id="metricDb">-</div><div class="sub" id="metricDsn">-</div></div>
        <div class="panel metric"><div class="label">Runs</div><div class="value" id="metricRuns">0</div><div class="sub" id="metricOrch">-</div></div>
        <div class="panel metric"><div class="label">Pending V3</div><div class="value" id="metricV3">0</div><div class="sub">approval queue</div></div>
        <div class="panel metric"><div class="label">Coverage Debt</div><div class="value" id="metricDebt">-</div><div class="sub" id="metricCoverage">latest snapshot</div></div>
        <div class="panel metric"><div class="label">Worker</div><div class="value" id="metricWorker">-</div><div class="sub">local python</div></div>
        <div class="panel metric"><div class="label">OAST</div><div class="value" id="metricOast">-</div><div class="sub" id="metricOastSub">-</div></div>
      </section>

      <section class="grid columns" style="margin-top:14px">
        <div class="grid">
          <div class="panel">
            <div class="panelHead"><span>System</span><span class="pill info"><span class="dot"></span><span id="maturity">maturity</span></span></div>
            <div class="panelBody" id="systemRows"></div>
          </div>
          <div class="panel">
            <div class="panelHead"><span>Programs</span><span class="pill" id="programCount">0</span></div>
            <div class="panelBody">
              <table>
                <thead><tr><th>Name</th><th>Platform</th><th>Scope</th><th>Auth</th><th>Created</th></tr></thead>
                <tbody id="programs"></tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <div class="panelHead"><span>Research Runs</span><span class="pill" id="runCount">0</span></div>
            <div class="panelBody">
              <table>
                <thead><tr><th>Run</th><th>Program</th><th>State</th><th>Phase</th><th>Updated</th><th>Controls</th></tr></thead>
                <tbody id="runs"></tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <div class="panelHead"><span>Authoritative Run Detail</span><span class="pill" id="runDetailCount">0</span></div>
            <div class="panelBody">
              <table>
                <thead><tr><th>Run</th><th>Authorization</th><th>Cycle</th><th>Budget</th><th>Research Progress</th><th>Approval</th><th>Preflight</th><th>Held / recon</th></tr></thead>
                <tbody id="runDetails"></tbody>
              </table>
            </div>
          </div>
          <div class="panel">
            <div class="panelHead"><span>Coverage</span><span class="pill" id="coverageCount">0</span></div>
            <div class="panelBody">
              <table>
                <thead><tr><th>Run</th><th>Total Debt</th><th>Matrix</th><th>Created</th></tr></thead>
                <tbody id="coverage"></tbody>
              </table>
            </div>
          </div>
        </div>
        <div class="grid">
          <div class="panel">
            <div class="panelHead"><span>Gates</span><span class="pill ok"><span class="dot"></span>SD sealed</span></div>
            <div class="panelBody" id="gates"></div>
          </div>
          <div class="panel">
            <div class="panelHead"><span>Audit Tail</span><span class="pill" id="auditCount">0</span></div>
            <div class="panelBody"><div class="log" id="audit"></div></div>
          </div>
        </div>
      </section>
      </div>

      <div id="view-setup" class="view hidden">
        <section class="grid columns">
          <div class="panel">
            <div class="panelHead"><span>Program Bootstrap</span><span class="pill warn"><span class="dot"></span>ready only</span></div>
            <div class="panelBody">
              <form id="setupForm">
                <div class="setupGrid">
                  <div class="field"><label for="programName">Program</label><input id="programName" name="program_name" required autocomplete="off"></div>
                  <div class="field"><label for="platform">Platform</label><select id="platform" name="platform"><option value="manual">Manual</option><option value="yeswehack">YesWeHack</option><option value="hackerone">HackerOne</option><option value="bugcrowd">Bugcrowd</option><option value="intigriti">Intigriti</option><option value="other">Other</option></select></div>
                  <div class="field"><label for="programHandle">Handle</label><input id="programHandle" name="program_handle" autocomplete="off"></div>
                  <div class="field"><label for="operatorId">Operator ID</label><input id="operatorId" name="operator_id" autocomplete="off"></div>
                  <div class="field wide"><label for="targetReference">Target Reference</label><input id="targetReference" name="target_reference" required autocomplete="off"></div>
                  <div class="field wide"><label for="authorizationReference">Authorization Reference</label><input id="authorizationReference" name="authorization_reference" required autocomplete="off"></div>
                  <div class="field wide"><label for="researchQuestion">Research Question</label><input id="researchQuestion" name="research_question" autocomplete="off"></div>
                  <div class="field wide"><label for="inScope">In Scope</label><textarea id="inScope" name="in_scope" required spellcheck="false"></textarea></div>
                  <div class="field wide"><label for="outScope">Out of Scope</label><textarea id="outScope" name="out_of_scope" spellcheck="false"></textarea></div>
                  <div class="field wide"><label for="forbiddenActions">Forbidden Actions</label><textarea id="forbiddenActions" name="forbidden_actions" spellcheck="false">destructive tests
denial of service
social engineering
out-of-scope probing</textarea></div>
                  <div class="field"><label for="rateLimit">Requests / Window</label><input id="rateLimit" name="max_requests_per_window" type="number" min="1" value="30"></div>
                  <div class="field"><label for="windowSeconds">Window Seconds</label><input id="windowSeconds" name="window_seconds" type="number" min="1" value="60"></div>
                  <div class="field"><label for="maxRequests">Max Requests</label><input id="maxRequests" name="max_requests" type="number" min="1" value="500"></div>
                  <div class="field"><label for="maxRuntime">Max Runtime MS</label><input id="maxRuntime" name="max_runtime_ms" type="number" min="1" value="3600000"></div>
                  <div class="field"><label for="maxToolCalls">Tool Calls</label><input id="maxToolCalls" name="max_tool_calls" type="number" min="1" value="200"></div>
                  <div class="field"><label for="maxConcurrency">Concurrency</label><input id="maxConcurrency" name="max_concurrency" type="number" min="1" value="1"></div>
                  <div class="field"><label for="maxCycles">Cycles</label><input id="maxCycles" name="max_cycles" type="number" min="1" value="20"></div>
                  <div class="field"><label for="maxExperiments">Experiments</label><input id="maxExperiments" name="max_experiments" type="number" min="1" value="50"></div>
                  <div class="field"><label for="maxModels">Model Calls</label><input id="maxModels" name="max_model_calls" type="number" min="1" value="50"></div>
                  <div class="field"><label for="maxWorkers">Worker Invocations</label><input id="maxWorkers" name="max_worker_invocations" type="number" min="1" value="100"></div>
                  <div class="field"><label for="maxSelected">Selected Opportunities</label><input id="maxSelected" name="max_selected_opportunities" type="number" min="1" value="4"></div>
                  <div class="field"><label for="runtimeFallback">Runtime Fallback</label><input id="runtimeFallback" name="max_runtime_fallback" type="number" min="1" value="1"></div>
                  <div class="field"><label for="maxResponse">Max Response Bytes</label><input id="maxResponse" name="max_response_bytes" type="number" min="1" value="1048576"></div>
                  <div class="field"><label for="timeoutMs">Timeout MS</label><input id="timeoutMs" name="timeout_ms" type="number" min="1" value="10000"></div>
                  <div class="field"><label for="llmBudget">LLM Budget Micro USD</label><input id="llmBudget" name="daily_llm_budget_microdollars" type="number" min="0" value="0"></div>
                  <div class="field"><label for="sideEffect">Side Effect Ceiling</label><select id="sideEffect" name="side_effect_ceiling"><option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></div>
                </div>
                <div class="formActions"><span id="formStatus" class="formStatus"></span><button class="primaryButton" id="createRun" type="submit">Create Ready Run</button></div>
              </form>
            </div>
          </div>
          <div class="grid">
            <div class="panel">
              <div class="panelHead"><span>Recent Programs</span><span class="pill" id="setupProgramCount">0</span></div>
              <div class="panelBody">
                <table>
                  <thead><tr><th>Name</th><th>Platform</th><th>Scope</th><th>Auth</th></tr></thead>
                  <tbody id="setupPrograms"></tbody>
                </table>
              </div>
            </div>
            <div class="panel">
              <div class="panelHead"><span>Latest Runs</span><span class="pill" id="setupRunCount">0</span></div>
              <div class="panelBody">
                <table>
                  <thead><tr><th>Run</th><th>State</th><th>Phase</th><th>Target</th></tr></thead>
                  <tbody id="setupRuns"></tbody>
                </table>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    const cls = (value) => {
      const text = String(value || '').toLowerCase();
      if (text.includes('healthy') || text === 'pass' || text === 'true' || text.includes('ready')) return 'ok';
      if (text.includes('pending') || text.includes('not_implemented') || text.includes('loopback')) return 'warn';
      if (text.includes('unavailable') || text.includes('false') || text.includes('failed')) return 'bad';
      return 'info';
    };
    const pill = (value) => `<span class="pill ${cls(value)}"><span class="dot"></span>${escapeHtml(value ?? '-')}</span>`;
    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const short = (value, n = 12) => {
      const text = String(value || '-');
      return text.length > n ? text.slice(0, n) : text;
    };
    const time = (value) => value ? new Date(value).toLocaleString() : '-';
    const runState = (row) => String(row.state || 'CREATED').toUpperCase();
    const runControls = (row) => {
      const state = runState(row);
      const runId = encodeURIComponent(String(row.research_run_id || ''));
      const button = (action, label) => `<button type="button" class="controlButton" data-run-action="${action}" data-run-id="${runId}">${label}</button>`;
      if (state === 'CREATED' || state === 'STARTABLE' || state === '') {
        return `<div class="runControls">${button('preflight', 'PREFLIGHT')}${button('start', 'START')}<span class="controlStatus" data-run-status></span></div>`;
      }
      if (state === 'READY' || state === 'RUNNING') {
        return `<div class="runControls">${button('pause', 'PAUSE')}${button('cancel', 'CANCEL')}<span class="controlStatus" data-run-status></span></div>`;
      }
      if (state === 'PAUSED') {
        return `<div class="runControls">${button('preflight', 'PREFLIGHT')}${button('resume', 'RESUME')}${button('cancel', 'CANCEL')}<span class="controlStatus" data-run-status></span></div>`;
      }
      if (state === 'WAITING_HUMAN' || state === 'RECONCILIATION_REQUIRED') {
        const reason = row.pause_reason || (row.reconciliation && row.reconciliation.classification) || 'human review required';
        return `<div class="runControls"><span class="controlStatus">Held: ${escapeHtml(reason)}</span></div>`;
      }
      return `<div class="runControls"><span class="controlStatus">Terminal: ${escapeHtml(state)}</span></div>`;
    };
    const numericFields = new Set([
      'max_response_bytes', 'timeout_ms', 'max_requests_per_window', 'window_seconds',
      'max_requests', 'max_tool_calls', 'max_runtime_ms', 'max_concurrency',
      'max_cycles', 'max_experiments', 'max_model_calls', 'max_worker_invocations',
      'max_selected_opportunities', 'max_runtime_fallback',
      'daily_llm_budget_microdollars', 'side_effect_ceiling'
    ]);

    function switchView(view, title, activeButton) {
      document.querySelectorAll('.view').forEach(node => node.classList.add('hidden'));
      $(`view-${view}`).classList.remove('hidden');
      $('pageTitle').textContent = title || 'Security Operations';
      document.querySelectorAll('.nav button').forEach(button => {
        button.classList.toggle('active', button === activeButton);
      });
    }

    async function load() {
      const response = await fetch('/api/dashboard', { cache: 'no-store' });
      const data = await response.json();
      render(data);
    }

    function render(data) {
      const status = data.status || {};
      const db = data.database || {};
      const summary = db.summary || {};
      const latestCoverage = (db.coverage || [])[0] || {};
      $('updated').textContent = time(data.generated_at);
      $('gitBranch').textContent = `branch: ${(data.git || {}).branch || '-'}`;
      $('gitHead').textContent = `head: ${(data.git || {}).head || '-'}`;

      $('metricDb').textContent = status.postgresql || '-';
      $('metricDsn').textContent = status.application_dsn || db.dsn || '-';
      $('metricRuns').textContent = summary.research_runs ?? 0;
      $('metricOrch').textContent = status.orchestrator || '-';
      $('metricV3').textContent = summary.pending_v3 ?? 0;
      $('metricDebt').textContent = latestCoverage.total_debt ?? '-';
      $('metricCoverage').textContent = latestCoverage.research_run_id || 'latest snapshot';
      $('metricWorker').textContent = Object.values(status.worker || {})[0] || '-';
      $('metricOast').textContent = (data.oast || {}).mode || '-';
      $('metricOastSub').textContent = (data.oast || {}).adapter || '-';
      $('maturity').textContent = status.gate_16 === 'PASS' ? 'SD-G16 PASS' : 'check';

      const operator = data.operator || {};
      const system = [
        ['Application DB', status.postgresql],
        ['Test DB', status.test_postgresql],
        ['research-osd', operator.ok === false ? 'unavailable' : (operator.status || (db.operator_source === 'research-osd' ? 'connected' : 'not attached'))],
        ['Worker now', ((operator.worker || {}).available_now === false) ? 'unavailable' : ((operator.worker || {}).health || Object.values(status.worker || {})[0])],
        ['Model now', ((operator.model || {}).available_now === false) ? ((operator.model || {}).health || 'unavailable') : ((operator.model || {}).health || (status.model_runtimes || {}).API)],
        ['Model auth', (operator.model || {}).authenticated === false ? 'AUTH_REQUIRED' : ((operator.model || {}).authenticated === true ? 'authenticated' : status.auth)],
        ['GATE 04B (separate)', status.gate_04b],
        ['Budget', status.budget_ledger],
        ['OAST Adapter', (data.oast || {}).adapter],
      ];
      $('systemRows').innerHTML = system.map(([name, value]) => `<div class="statusRow"><div class="name">${escapeHtml(name)}</div>${pill(value)}</div>`).join('');

      const gates = [
        ['GATE 01', status.gate_01], ['GATE 04B', status.gate_04b], ['GATE 10', status.gate_10],
        ['GATE 14', status.gate_14], ['GATE 15', status.gate_15], ['GATE 16', status.gate_16],
        ['GATE 17', status.gate_17], ['GATE 18', status.gate_18], ['GATE 19', status.gate_19],
        ['GATE 20', status.gate_20], ['GATE 21', status.gate_21], ['GATE 22', status.gate_22],
      ];
      $('gates').innerHTML = gates.map(([name, value]) => `<div class="statusRow"><div class="name">${escapeHtml(name)}</div>${pill(value)}</div>`).join('');

      const programs = db.programs || [];
      $('programCount').textContent = programs.length;
      $('setupProgramCount').textContent = programs.length;
      const programRows = programs.length ? programs.map(row => `
        <tr>
          <td title="${escapeHtml(row.program_id)}">${escapeHtml(row.name || row.handle || short(row.program_id, 18))}</td>
          <td>${escapeHtml(row.platform || '-')}</td>
          <td>${escapeHtml(row.scope_rules ?? 0)}</td>
          <td>${pill((row.active_authorizations || 0) > 0 ? 'ACTIVE' : 'NONE')}</td>
          <td>${escapeHtml(time(row.created_at))}</td>
        </tr>`).join('') : `<tr><td colspan="5" class="empty">No programs</td></tr>`;
      $('programs').innerHTML = programRows;
      $('setupPrograms').innerHTML = programs.length ? programs.map(row => `
        <tr>
          <td title="${escapeHtml(row.program_id)}">${escapeHtml(row.name || row.handle || short(row.program_id, 18))}</td>
          <td>${escapeHtml(row.platform || '-')}</td>
          <td>${escapeHtml(row.scope_rules ?? 0)}</td>
          <td>${pill((row.active_authorizations || 0) > 0 ? 'ACTIVE' : 'NONE')}</td>
        </tr>`).join('') : `<tr><td colspan="4" class="empty">No programs</td></tr>`;

      const runs = db.runs || [];
      $('runCount').textContent = runs.length;
      $('setupRunCount').textContent = runs.length;
      $('runs').innerHTML = runs.length ? runs.map(row => `
        <tr>
          <td class="mono" title="${escapeHtml(row.research_run_id)}">${escapeHtml(short(row.research_run_id, 18))}</td>
          <td>${escapeHtml(row.program_id)}</td>
          <td>${pill(row.state || 'not started')}</td>
          <td>${escapeHtml(row.current_phase || '-')}</td>
          <td>${escapeHtml(time(row.updated_at || row.started_at))}</td>
          <td>${runControls(row)}</td>
        </tr>`).join('') : `<tr><td colspan="6" class="empty">No research runs</td></tr>`;
      $('setupRuns').innerHTML = runs.length ? runs.map(row => `
        <tr>
          <td class="mono" title="${escapeHtml(row.research_run_id)}">${escapeHtml(short(row.research_run_id, 14))}</td>
          <td>${pill(row.state || 'not started')}</td>
          <td>${escapeHtml(row.current_phase || '-')}</td>
          <td title="${escapeHtml(row.target_reference)}">${escapeHtml(short(row.target_reference, 28))}</td>
        </tr>`).join('') : `<tr><td colspan="4" class="empty">No research runs</td></tr>`;

      const runDetails = db.run_details || [];
      $('runDetailCount').textContent = runDetails.length;
      $('runDetails').innerHTML = runDetails.length ? runDetails.map(row => `
        <tr>
          <td class="mono" title="${escapeHtml(row.research_run_id)}">${escapeHtml(short(row.research_run_id, 14))}</td>
          <td>${escapeHtml(row.authorization_state || 'unknown')}</td>
          <td>${escapeHtml(row.cycle_number ?? 'unknown')} / ${escapeHtml(row.max_cycles ?? 'unknown')}</td>
          <td>${escapeHtml(row.model_count ?? 0)} model · ${escapeHtml(row.worker_count ?? 0)} worker</td>
          <td>${escapeHtml(row.hypothesis_count ?? 0)} H · ${escapeHtml(row.experiment_count ?? 0)} E · ${escapeHtml(row.observation_count ?? 0)} O · ${escapeHtml(row.evidence_count ?? 0)} Ev · ${escapeHtml(row.candidate_count ?? 0)} C · ${escapeHtml(row.finding_proposal_count ?? 0)} FP · ${escapeHtml(row.finding_count ?? 0)} F</td>
          <td>${escapeHtml(row.pending_approval_count ?? 0)}</td>
          <td>${escapeHtml((row.latest_preflight && row.latest_preflight.status) || row.latest_preflight_status || '-')}</td>
          <td>${escapeHtml((row.reconciliation && row.reconciliation.classification) || row.pause_reason || '-')}</td>
        </tr>`).join('') : `<tr><td colspan="8" class="empty">No authoritative run detail</td></tr>`;

      const coverage = db.coverage || [];
      $('coverageCount').textContent = coverage.length;
      $('coverage').innerHTML = coverage.length ? coverage.map(row => `
        <tr>
          <td class="mono">${escapeHtml(short(row.research_run_id, 18))}</td>
          <td>${escapeHtml(row.total_debt)}</td>
          <td class="mono" title="${escapeHtml(row.matrix_hash)}">${escapeHtml(short(row.matrix_hash, 16))}</td>
          <td>${escapeHtml(time(row.created_at))}</td>
        </tr>`).join('') : `<tr><td colspan="4" class="empty">No coverage snapshots</td></tr>`;

      const audit = db.audit_events || [];
      $('auditCount').textContent = audit.length;
      $('audit').innerHTML = audit.length ? audit.map(row => `
        <div class="event">
          <strong>${escapeHtml(row.event_type)}</strong>
          <span>${escapeHtml(row.subject_type)} · ${escapeHtml(short(row.subject_id, 24))}</span>
          <span>${escapeHtml(time(row.occurred_at))}</span>
        </div>`).join('') : `<div class="empty">No audit events</div>`;
    }

    document.querySelectorAll('.nav button').forEach(button => {
      button.addEventListener('click', () => switchView(button.dataset.view, button.dataset.title, button));
    });

    $('runs').addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-run-action]');
      if (!button || button.disabled) return;
      const action = button.dataset.runAction;
      const runId = button.dataset.runId;
      const controls = button.closest('.runControls');
      const status = controls ? controls.querySelector('[data-run-status]') : null;
      const labels = { start: 'starting', pause: 'pausing', resume: 'resuming', cancel: 'cancelling', preflight: 'preflight' };
      const actionLabel = labels[action] || 'updating';
      const rowButtons = controls ? controls.querySelectorAll('button[data-run-action]') : [button];
      rowButtons.forEach(item => { item.disabled = true; });
      if (status) status.textContent = actionLabel;
      try {
        const response = await fetch(`/api/runs/${runId}/${action}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.detail || result.error || `${action} failed`);
        if (action === 'preflight') {
          if (status) status.textContent = `preflight ${result.result && result.result.status ? result.result.status : 'done'}`;
        } else if (status) status.textContent = `${action} accepted`;
        await load();
      } catch (err) {
        if (status) status.textContent = `error: ${err.message}`;
        rowButtons.forEach(item => { item.disabled = false; });
      }
    });

    $('setupForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const submit = $('createRun');
      const status = $('formStatus');
      if (submit.disabled) return;
      const payload = {};
      for (const [key, value] of new FormData(form).entries()) {
        const textValue = String(value);
        if (numericFields.has(key)) {
          payload[key] = Number(textValue);
        } else if (textValue.trim() !== '') {
          payload[key] = textValue;
        }
      }
      submit.disabled = true;
      status.textContent = 'creating';
      try {
        const response = await fetch('/api/programs/bootstrap', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || 'request failed');
        status.textContent = `ready: ${short(result.result.research_run_id, 14)}`;
        form.reset();
        await load();
      } catch (err) {
        status.textContent = `error: ${err.message}`;
      } finally {
        submit.disabled = false;
      }
    });

    $('refresh').addEventListener('click', load);
    load().catch(err => { $('updated').textContent = `error: ${err.message}`; });
    setInterval(() => load().catch(() => {}), 3000);
  </script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
