"""Local operator dashboard. Read-only by default; never prints secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from dataclasses import asdict
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

from sqlalchemy import text

from zest.data.postgres.engine import (
    DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
)
from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.autonomous_research_controller import (
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from zest.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from zest.application.record_human_review import RecordHumanReview, RecordHumanReviewCommand
from zest.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from zest.application.research_run_control import ResearchRunControl
from zest.core.enums import ActorType, ScopeRuleEffect
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.research.orchestration import OrchestrationBounds
from zest.research.finding_proposal import HumanReviewDecision
from zest.interface.osd_client import OperatorApiRunControl, ZEST_URL_ENV
from zest.interface.operator_api import LOCAL_BIND_HOSTS
from zest.application.operator_errors import OperatorError
from zest.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    RateLimitProfileRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from zest.interface.cli import build_status_snapshot
from zest.safe_data import redact_secret_keys
from zest.hq import read_index, read_static_asset

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
    osd_url = source.get(ZEST_URL_ENV)
    if not osd_url or not osd_url.strip():
        raise RuntimeError(
            f"{ZEST_URL_ENV} is required; dashboard must not own run supervisors"
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
    """Satisfies the dashboard POST type check. zestd reconstructs config from SoR."""

    from zest.core.scope import ScopeEvaluationInput

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


def _operator_run_analysis(research_run_id: str) -> dict[str, Any]:
    runtime = _RUN_CONTROL_RUNTIME
    if runtime is None:
        raise RuntimeError("run control runtime is not configured")
    control = runtime.control
    if not hasattr(control, "get_run_analysis"):
        raise RuntimeError("operator API client does not expose HQ analysis")
    result = control.get_run_analysis(research_run_id)
    if not isinstance(result, dict):
        raise RuntimeError("operator API returned invalid HQ analysis")
    return result


def _operator_semantic_events(research_run_id: str, *, last_event_id: str = ""):
    runtime = _RUN_CONTROL_RUNTIME
    if runtime is None:
        raise RuntimeError("run control runtime is not configured")
    control = runtime.control
    if not hasattr(control, "open_semantic_events"):
        raise RuntimeError("operator API client does not expose semantic events")
    return control.open_semantic_events(research_run_id, last_event_id=last_event_id)


def collect_dashboard_payload(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    snapshot = build_status_snapshot(env=source)
    database = _database_payload(source)
    operator = None
    osd_url = source.get(ZEST_URL_ENV)
    if osd_url and osd_url.strip():
        try:
            client = OperatorApiRunControl(osd_url)
            console = client.console_snapshot()
            operator = console.get("health")
            database["operator_source"] = "zestd"
            if isinstance(console.get("programs"), list) and console["programs"]:
                database["programs"] = console["programs"]
            if isinstance(console.get("runs"), list):
                database["runs"] = console["runs"]
            if isinstance(console.get("run_details"), list):
                database["run_details"] = console["run_details"]
        except (ApplicationError, OperatorError, OSError, ValueError) as exc:
            database["operator_source"] = "zestd-unreachable"
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
                        "required_headers": cleaned["required_headers"],
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
    out_of_scope = _lines(payload.get("out_of_scope"))
    overlap = [entry for entry in in_scope if entry in set(out_of_scope)]
    if overlap:
        raise ValueError(
            "scope entry cannot be both in_scope and out_of_scope: "
            + ", ".join(overlap)
        )
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
        "out_of_scope": out_of_scope,
        "forbidden_actions": _lines(payload.get("forbidden_actions")),
        "required_user_agent": _optional_text(payload, "required_user_agent"),
        "required_headers": _required_headers(payload.get("required_headers")),
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
    if path_prefix and path_prefix.endswith("/*"):
        path_prefix = path_prefix[:-1]
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


_REQUIRED_HEADER_BLOCKLIST = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "origin",
        "referer",
        "user-agent",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)

_REQUIRED_HEADER_SENSITIVE_MARKERS = (
    "api-key",
    "apikey",
    "password",
    "secret",
    "token",
    "credential",
)


def _required_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}

    if isinstance(value, Mapping):
        candidates = list(value.items())
    else:
        candidates = []
        for line in _lines(value):
            if ":" not in line:
                raise ValueError(
                    "required_headers entries must use 'Name: Value'"
                )
            name, header_value = line.split(":", 1)
            candidates.append((name, header_value))

    if len(candidates) > 8:
        raise ValueError("required_headers permits at most 8 headers")

    result: dict[str, str] = {}
    seen: set[str] = set()

    for raw_name, raw_value in candidates:
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("required_headers names and values must be strings")

        name = raw_name.strip()
        header_value = raw_value.strip()
        lower = name.lower()

        if (
            not name
            or len(name) > 64
            or any(not (char.isalnum() or char == "-") for char in name)
        ):
            raise ValueError("required_headers contains an invalid header name")

        if (
            lower in _REQUIRED_HEADER_BLOCKLIST
            or any(marker in lower for marker in _REQUIRED_HEADER_SENSITIVE_MARKERS)
        ):
            raise ValueError(
                f"required_headers header is not permitted: {name}"
            )

        if lower in seen:
            raise ValueError(
                f"required_headers contains a duplicate header: {name}"
            )

        if (
            not header_value
            or len(header_value) > 256
            or any(marker in header_value for marker in ("\r", "\n", "\x00"))
        ):
            raise ValueError(
                f"required_headers contains an invalid value for: {name}"
            )

        seen.add(lower)
        result[name] = header_value

    return result


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
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            raise ValueError("line input entries must be strings")
        text_value = item.strip()
        if (
            text_value
            and not text_value.startswith("#")
            and text_value not in seen
        ):
            seen.add(text_value)
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
    configured = bool(env.get("ZEST_INTERACTSH_SERVER") or env.get("INTERACTSH_SERVER"))
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
    server_version = "ZestDashboard/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, HTML, "text/html; charset=utf-8")
            return
        if path == "/index.html":
            self._send(HTTPStatus.OK, HTML, "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            try:
                asset, content_type = read_static_asset(unquote(path[len("/static/") :]))
            except FileNotFoundError:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain; charset=utf-8")
                return
            self._send(HTTPStatus.OK, asset, content_type)
            return
        if path == "/api/dashboard":
            self._send_json(collect_dashboard_payload())
            return
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0] == "api"
            and parts[1] == "runs"
            and parts[2]
            and parts[3] == "analysis"
        ):
            try:
                result = _operator_run_analysis(unquote(parts[2]))
            except OperatorError as exc:
                self._send_json(exc.to_payload(), status=exc.http_status)
                return
            except RuntimeError as exc:
                self._send_json(
                    {"ok": False, "error": str(exc)},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
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
            and parts[3] == "events"
        ):
            upstream = None
            headers_sent = False
            try:
                upstream = _operator_semantic_events(
                    unquote(parts[2]),
                    last_event_id=self.headers.get("Last-Event-ID", "").strip(),
                )
                self.send_response(HTTPStatus.OK.value)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                headers_sent = True
                while True:
                    chunk = upstream.readline(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OperatorError as exc:
                if not headers_sent:
                    self._send_json(exc.to_payload(), status=exc.http_status)
            except RuntimeError as exc:
                if not headers_sent:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            except Exception as exc:
                if not headers_sent:
                    self._send_json({"ok": False, "error": exc.__class__.__name__}, status=HTTPStatus.BAD_GATEWAY)
            finally:
                if upstream is not None:
                    upstream.close()
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zest-dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.host not in LOCAL_BIND_HOSTS:
        print("dashboard must bind locally", file=sys.stderr)
        return 2
    try:
        source = dict(os.environ)
        if source.get(ZEST_URL_ENV):
            configure_dashboard_run_control(build_dashboard_run_control_runtime())
        else:
            configure_dashboard_run_control(None)
    except (RuntimeError, ValueError):
        configure_dashboard_run_control(None)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Zest dashboard listening on http://{args.host}:{args.port}", flush=True)
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


HTML = read_index()


if __name__ == "__main__":
    raise SystemExit(main())
