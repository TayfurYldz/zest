"""Full research lifecycle acceptance.

Uses production LocalRunSupervisor command lifecycle. Surface discovery
continues from persisted frontier state until exhaustion, then general
research begins. Does not replay GATE 22's in-memory persistent command loop.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.lab.full_lifecycle_lab import FullLifecycleLab
from integration.harness import (
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    truncate_spine,
)
from zest.application.autonomous_research_controller import AutonomousResearchController
from zest.application.identity import new_opaque_id
from zest.application.local_run_supervisor import LocalRunSupervisor
from zest.application.orchestration_obligations import unresolved_control_obligations
from zest.application.reconstruct_run_command import (
    allocate_daily_budget_if_required,
    reconstruct_start_command,
)
from zest.core.enums import ScopeRuleEffect
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.discovery.frontier import FrontierEventKind
from zest.research.discovery.types import DiscoveryGoalKind
from zest.research.orchestration import OrchestrationState, StopReason
from zest.worker_runtime.python.browser_engine import BrowserEngineUnavailable
from zest.worker_runtime.python.browser_page import execute_browser_page
from zest.worker_runtime.python.http_transaction import execute_http_transaction
from zest.worker_runtime.python.implementation import _execute_echo
from support.fake_model import ScriptedModelPort

TEST_URL = configured_test_url()
CHROMIUM_REASON = "Chromium/Playwright is not installed for full-lifecycle field regressions"
PG_REASON = f"{TEST_DATABASE_URL_ENV} not set; full-lifecycle PostgreSQL E2E skipped"
MAX_TICKS = 12
SESSION_PATH = "/api/session.js"
DAILY_LLM_BUDGET_MICRODOLLARS = 1_000_000
TERMINAL = frozenset(
    {
        OrchestrationState.COMPLETED.value,
        OrchestrationState.BUDGET_EXHAUSTED.value,
        OrchestrationState.FAILED_OPERATIONAL.value,
    }
)
ALLOWED_FRONTIER_DISPOSITIONS = frozenset(
    {
        "EXECUTED",
        "BLOCKED_SCOPE",
        "BLOCKED_POLICY",
        "BLOCKED_BUDGET",
        "DEFERRED_WITH_REASON",
        "DEDUPLICATED_WITH_TARGET",
        "SUPERSEDED_WITH_TARGET",
        "WAITING_HUMAN",
        "UNSUPPORTED_CAPABILITY_WITH_REASON",
        "FAILED_WITH_REASON",
    }
)
EVENT_DISPOSITION = {
    FrontierEventKind.OBSERVED.value: "EXECUTED",
    FrontierEventKind.AWAITING_REAUTHORIZATION.value: "WAITING_HUMAN",
    FrontierEventKind.BLOCKED_SCOPE.value: "BLOCKED_SCOPE",
    FrontierEventKind.BLOCKED_AUTH.value: "BLOCKED_POLICY",
    FrontierEventKind.BLOCKED_BUDGET.value: "BLOCKED_BUDGET",
    FrontierEventKind.SUPERSEDED.value: "SUPERSEDED_WITH_TARGET",
    FrontierEventKind.DEFERRED_TO_RESEARCH.value: "DEFERRED_WITH_REASON",
    FrontierEventKind.UNSUPPORTED.value: "UNSUPPORTED_CAPABILITY_WITH_REASON",
    FrontierEventKind.FAILED_TERMINAL.value: "FAILED_WITH_REASON",
    FrontierEventKind.NO_NEW_INFORMATION.value: "DEFERRED_WITH_REASON",
}


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _chromium_engine():
    try:
        from zest.worker_runtime.python.playwright_chromium_engine import (
            PlaywrightChromiumEngine,
        )

        engine = PlaywrightChromiumEngine()
        engine.start()
        return engine
    except (BrowserEngineUnavailable, ImportError, OSError):
        return None


def _head_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _blocked_boundaries_from_result(item) -> list | None:
    diagnostics = item.diagnostics if isinstance(item.diagnostics, dict) else {}
    raw = item.raw_result if isinstance(item.raw_result, dict) else {}
    boundaries = diagnostics.get("blocked_boundaries")
    if not boundaries:
        boundaries = raw.get("blocked_boundaries")
    if not isinstance(boundaries, list):
        return None
    return boundaries


def _blocked_boundary_trace(item) -> list[dict]:
    boundaries = _blocked_boundaries_from_result(item) or []
    keys = (
        "browser_action",
        "requested_url",
        "resource_type",
        "is_navigation_request",
        "frame_kind",
        "envelope_allowed",
        "route_decision",
        "egress_occurred",
        "reauth_required",
        "main_observation_preserved",
        "blocked_before_egress",
        "followed",
        "self_authorized",
        "authority_granted",
        "reason",
        "source_page",
        "action",
        "boundary_kind",
    )
    return [{key: entry.get(key) for key in keys} for entry in boundaries if isinstance(entry, dict)]


class LabWorkerPort:
    """Real Playwright/HTTP/echo worker. Not a fake frontier or fake result."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.calls: list[dict] = []

    def invoke(self, request, *, timeout_ms=None):
        del timeout_ms
        payload = dict(request)
        self.calls.append(payload)
        cap = request.get("worker_capability")
        started = datetime.now(timezone.utc)
        if cap == "browser.page":
            status, raw, diagnostics = execute_browser_page(request, engine=self.engine)
        elif cap == "http.transaction":
            status, raw, diagnostics = execute_http_transaction(request)
        elif cap == "diagnostic.echo":
            status, raw, diagnostics = _execute_echo(request)
        else:
            status, raw, diagnostics = "EXECUTION_FAILED", {}, {"error": "unsupported capability"}
        completed = datetime.now(timezone.utc)
        result = {
            "contract_version": "v1",
            "correlation": request.get("correlation"),
            "worker_id": "full-lifecycle-lab-worker",
            "status": status,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "raw_result": raw,
        }
        if diagnostics is not None:
            result["diagnostics"] = diagnostics
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=started,
            completed_at=completed,
            worker_result=result,
            exit_code=0,
        )


def _latest_event_kind(events) -> str | None:
    if not events:
        return None
    latest = sorted(events, key=lambda item: (item.sequence, item.event_id))[-1]
    return latest.event_kind


def _record_map(record) -> dict:
    payload = {}
    for key, value in vars(record).items():
        if key.startswith("_"):
            continue
        payload[key] = value
    return payload


class _RunSnapshot:
    def __init__(self, factory: PostgresUnitOfWorkFactory, research_run_id: str) -> None:
        self.research_run_id = research_run_id
        with factory.open() as uow:
            self.orchestration = uow.research_orchestrations.get(research_run_id)
            self.run = uow.research_runs.get(research_run_id)
            self.frontiers = uow.frontier_items.list_for_research_run(research_run_id)
            self.frontier_events = uow.frontier_events.list_for_research_run(research_run_id)
            self.facts = uow.discovery_facts.list_for_research_run(research_run_id)
            self.hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
            self.opportunities = uow.research_opportunities.list_for_research_run(
                research_run_id
            )
            self.selections = uow.research_selections.list_for_research_run(research_run_id)
            self.experiments = uow.experiments.list_for_research_run(research_run_id)
            self.attempts = uow.execution_attempts.list_for_research_run(research_run_id)
            self.worker_results = uow.worker_results.list_for_research_run(research_run_id)
            self.observations = uow.observations.list_for_research_run(research_run_id)
            self.plans = [
                uow.experiment_plans.get(item.experiment_id) for item in self.experiments
            ]
            budgets = uow.issued_budgets.list_for_research_run(research_run_id)
            self.budget = budgets[0] if budgets else None
            self.consumptions = (
                uow.budget_consumptions.list_for_budget(self.budget.budget_id)
                if self.budget is not None
                else []
            )
            self.audit = uow.audit_events.list_for_subject("research_run", research_run_id)
            self.auth_sources = [
                uow.authorization_sources.get(self.run.authorization_source_id)
            ]
            self.scope_rules = uow.scope_rules_v2.list_for_program(self.run.program_id)
            uow.rollback()
        self.events_by = {}
        for event in self.frontier_events:
            self.events_by.setdefault(event.frontier_id, []).append(event)
        self.obligations = unresolved_control_obligations(
            attempts=self.attempts,
            experiments=self.experiments,
            worker_results=self.worker_results,
        )

    @property
    def eligible(self):
        items = []
        for item in self.frontiers:
            if item.budget_class > 1 or item.expected_side_effect > 1:
                continue
            kind = _latest_event_kind(self.events_by.get(item.frontier_id, ()))
            if kind == FrontierEventKind.ELIGIBLE.value:
                items.append(item)
        return items

    def frontier_disposition(self, frontier_id: str) -> str | None:
        kind = _latest_event_kind(self.events_by.get(frontier_id, ()))
        if kind is None:
            return None
        if kind == FrontierEventKind.ELIGIBLE.value:
            return None
        if kind == FrontierEventKind.SELECTED.value:
            return None
        if kind == FrontierEventKind.CREATED.value:
            return None
        mapped = EVENT_DISPOSITION.get(kind)
        if mapped is not None:
            return mapped
        if kind == FrontierEventKind.NO_NEW_INFORMATION.value:
            return "DEFERRED_WITH_REASON"
        return None


@unittest.skipUnless(TEST_URL, PG_REASON)
@unittest.skipUnless(_playwright_installed(), CHROMIUM_REASON)
class FullLifecycleFieldRegressionTests(unittest.TestCase):
    engine = None
    browser = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        cls.browser = _chromium_engine()
        if cls.browser is None:
            raise unittest.SkipTest(CHROMIUM_REASON)
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser is not None:
            cls.browser.stop()
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        if self.browser is not None:
            self.browser.close_all()
        self.factory = PostgresUnitOfWorkFactory(self.engine)
        self.head_sha = _head_sha()
        self.ids: dict[str, str] = {}
        self.lab: FullLifecycleLab | None = None
        self.worker: LabWorkerPort | None = None
        self.supervisor: LocalRunSupervisor | None = None
        self.controller: AutonomousResearchController | None = None
        self.ticks: list = []
        self.had_surface_discovery_on_start = False
        self.surface_discovery_after_first_running_tick = "UNSET"

    def tearDown(self) -> None:
        if self.lab is not None:
            self.lab.stop()
            self.lab = None

    def _seed(self, origin: str) -> str:
        now = datetime.now(timezone.utc)
        parsed = urlsplit(origin)
        program_id = new_opaque_id()
        run_id = new_opaque_id()
        auth_id = new_opaque_id()
        budget_id = new_opaque_id()
        self.ids = {
            "program_id": program_id,
            "research_run_id": run_id,
            "authorization_source_id": auth_id,
            "budget_id": budget_id,
        }
        with self.factory.open() as uow:
            uow.programs.insert(
                ProgramRecord(program_id=program_id, created_at=now, name="full-lifecycle-lab")
            )
            uow.authorization_sources.insert(
                AuthorizationSourceRecord(
                    authorization_source_id=auth_id,
                    program_id=program_id,
                    state="ACTIVE",
                    provenance_reference="written-auth-full-lifecycle",
                    created_at=now,
                )
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id=run_id,
                    program_id=program_id,
                    authorization_source_id=auth_id,
                    initiated_by_actor_id="operator-1",
                    initiated_by_actor_type="HUMAN_OPERATOR",
                    started_at=now,
                )
            )
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id=budget_id,
                    research_run_id=run_id,
                    max_requests=64,
                    max_tool_calls=32,
                    max_runtime_ms=30_000,
                    max_concurrency=1,
                    issued_at=now,
                )
            )
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id=program_id,
                    loopback_fixture=True,
                    max_response_bytes=65536,
                    timeout_ms=8000,
                    created_at=now,
                    updated_at=now,
                    daily_llm_budget_microdollars=DAILY_LLM_BUDGET_MICRODOLLARS,
                    action_policy={
                        "run": {
                            "target_reference": origin + "/",
                            "research_question": "Characterize in-scope surface without leaving scope.",
                        },
                        "orchestration": {
                            "max_cycles": 12,
                            "max_experiments": 12,
                            "max_model_calls": 8,
                            "max_worker_invocations": 16,
                            "max_elapsed_ms": 120_000,
                            "max_selected_opportunities": 1,
                            "max_runtime_fallback": 0,
                            "side_effect_ceiling": 1,
                            "allow_repeated_control_experiments": False,
                        },
                    },
                )
            )
            uow.scope_rules_v2.insert(
                ScopeRuleV2Record(
                    rule_id=new_opaque_id(),
                    program_id=program_id,
                    effect=ScopeRuleEffect.ALLOW.value,
                    scheme="http",
                    host=parsed.hostname or "127.0.0.1",
                    port=parsed.port,
                    path_prefix=None,
                    source_reference="scope-src",
                    created_at=now,
                )
            )
            uow.commit()
        return run_id

    def _start_supervisor(self, *, include_passive_third_party: bool, model=None) -> str:
        self.lab = FullLifecycleLab(include_passive_third_party=include_passive_third_party)
        origin = self.lab.start()
        run_id = self._seed(origin)
        allocate_daily_budget_if_required(self.factory, run_id)
        command = reconstruct_start_command(self.factory, run_id, recovery=False)
        self.had_surface_discovery_on_start = command.surface_discovery is not None
        self.worker = LabWorkerPort(self.browser)
        self.controller = AutonomousResearchController(
            self.factory, self.worker, model if model is not None else ScriptedModelPort()
        )
        started = self.controller.start(command)
        self.assertEqual(started.state, OrchestrationState.READY.value)
        self.supervisor = LocalRunSupervisor(
            run_id, self.controller, command, self.factory, cadence_seconds=30
        )
        return run_id

    def _tick(self):
        assert self.supervisor is not None
        before = self.supervisor.command.surface_discovery is not None
        result = self.supervisor.tick()
        after = self.supervisor.command.surface_discovery is not None
        self.ticks.append(result)
        if (
            self.surface_discovery_after_first_running_tick == "UNSET"
            and result is not None
            and result.state
            in {OrchestrationState.READY.value, OrchestrationState.RUNNING.value}
        ):
            self.surface_discovery_after_first_running_tick = after
        return before, result, after

    def _drive(self, run_id: str, *, max_ticks: int = MAX_TICKS):
        last = None
        for _ in range(max_ticks):
            _, last, _ = self._tick()
            if last is None:
                break
            if last.state in TERMINAL or last.state == OrchestrationState.WAITING_HUMAN.value:
                break
        return last, _RunSnapshot(self.factory, run_id)

    def _assert_production_supervisor(self) -> None:
        self.assertTrue(
            self.had_surface_discovery_on_start,
            "reconstructed START command must include surface_discovery",
        )

    def _forensic(self, snap: _RunSnapshot, *, expected: str, extra: dict | None = None) -> dict:
        eligible = snap.eligible
        third_hits = [] if self.lab is None else list(self.lab.third.hits)
        main_hits = [] if self.lab is None else list(self.lab.main.hits)
        worker_statuses = [item.status for item in snap.worker_results]
        attempted = []
        for item in snap.worker_results:
            raw = item.raw_result if isinstance(item.raw_result, dict) else {}
            attempted.append(raw.get("attempted_network_requests"))
        why = {
            "expected": expected,
            "actual_state": None if snap.orchestration is None else snap.orchestration.state,
            "stop_reason": None if snap.orchestration is None else snap.orchestration.stop_reason,
            "last_phase": None if snap.orchestration is None else snap.orchestration.last_phase,
            "cycle_number": None if snap.orchestration is None else snap.orchestration.cycle_number,
            "eligible_frontier": len(eligible),
            "eligible_frontier_ids": [item.frontier_id for item in eligible],
            "hypothesis_count": len(snap.hypotheses),
            "selected_count": sum(1 for item in snap.selections if item.outcome == "SELECT"),
            "worker_statuses": worker_statuses,
            "third_party_hits": third_hits,
            "surface_discovery_on_start": self.had_surface_discovery_on_start,
            "surface_discovery_after_first_running_tick": self.surface_discovery_after_first_running_tick,
            "ticks": len(self.ticks),
            "blocked_boundaries": [
                _blocked_boundaries_from_result(item) for item in snap.worker_results
            ],
        }
        if extra:
            why.update(extra)
        if (
            why["stop_reason"] == StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
            and why["eligible_frontier"] > 0
        ):
            why["verdict"] = "INVALID_COMPLETION"
        bundle = {
            "WHY_DID_THIS_TEST_FAIL": why,
            "RUN_ID": snap.research_run_id,
            "PROGRAM_ID": None if snap.run is None else snap.run.program_id,
            "HEAD_SHA": self.head_sha,
            "frontier": [
                {
                    "frontier_id": item.frontier_id,
                    "goal_kind": item.goal_kind,
                    "candidate_origin": item.candidate_origin,
                    "candidate_path": item.candidate_path,
                    "proposed_capability": item.proposed_capability,
                    "proposed_action": item.proposed_action,
                    "current_state": item.current_state,
                    "latest_event": _latest_event_kind(snap.events_by.get(item.frontier_id, ())),
                    "disposition": snap.frontier_disposition(item.frontier_id),
                }
                for item in snap.frontiers
            ],
            "hypotheses": [_record_map(item) for item in snap.hypotheses],
            "opportunities": [_record_map(item) for item in snap.opportunities],
            "selections": [_record_map(item) for item in snap.selections],
            "experiments": [_record_map(item) for item in snap.experiments],
            "plans": [_record_map(item) for item in snap.plans if item is not None],
            "attempts": [_record_map(item) for item in snap.attempts],
            "worker_results": [
                {
                    "worker_result_id": item.worker_result_id,
                    "status": item.status,
                    "experiment_id": item.experiment_id,
                    "attempted_network_requests": (
                        item.raw_result.get("attempted_network_requests")
                        if isinstance(item.raw_result, dict)
                        else None
                    ),
                    "diagnostics": item.diagnostics,
                    "blocked_boundaries": _blocked_boundaries_from_result(item),
                    "blocked_boundary_trace": _blocked_boundary_trace(item),
                }
                for item in snap.worker_results
            ],
            "observations": [
                {
                    "observation_id": item.observation_id,
                    "observation_kind": item.observation_kind,
                    "worker_result_id": item.worker_result_id,
                }
                for item in snap.observations
            ],
            "control_obligations": [
                {"code": item.code, "subject_id": item.subject_id, "detail": item.detail}
                for item in snap.obligations
            ],
            "budget_ledger": [_record_map(item) for item in snap.consumptions],
            "actual_network_request_count": attempted,
            "blocked_before_egress_count": 0 if self.lab is None else len(self.lab.third.hits),
            "main_hits": main_hits,
            "audit_events": [
                {"event_type": item.event_type, "payload": item.payload} for item in snap.audit
            ],
            "discovery_cycle_trace": [
                item.payload
                for item in snap.audit
                if item.event_type == "SURFACE_DISCOVERY_CYCLE"
            ],
        }
        return bundle

    def _fail(self, snap: _RunSnapshot, *, expected: str, extra: dict | None = None) -> None:
        bundle = self._forensic(snap, expected=expected, extra=extra)
        print(json.dumps(bundle, indent=2, default=str), flush=True)
        why = bundle["WHY_DID_THIS_TEST_FAIL"]
        self.fail(json.dumps(why, default=str))

    def test_s_production_discovery_continues_until_frontier_exhausted(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=False)
        self.assertTrue(self.had_surface_discovery_on_start)
        before, result, after = self._tick()
        self.assertTrue(before)
        self.assertIsNotNone(result)
        snap = _RunSnapshot(self.factory, run_id)
        if result.state in {OrchestrationState.READY.value, OrchestrationState.RUNNING.value} and snap.eligible:
            self.assertTrue(
                after,
                "surface_discovery must remain while eligible discovery frontier exists",
            )
            self.assertIsNotNone(self.supervisor.command.surface_discovery)
        last, snap = self._drive(run_id)
        self._assert_production_supervisor()
        traces = [
            item.payload
            for item in snap.audit
            if item.event_type == "SURFACE_DISCOVERY_CYCLE"
        ]
        self.assertGreaterEqual(len(traces), 1)
        if snap.eligible:
            self.assertIsNotNone(
                self.supervisor.command.surface_discovery,
                "eligible discovery work remaining must keep surface_discovery attached",
            )
        else:
            self.assertIsNone(
                self.supervisor.command.surface_discovery,
                "surface_discovery drops only after discovery exhaustion",
            )
        self.assertGreaterEqual(len(snap.frontiers), 1)
        del last

    def test_a_passive_oos_subresource_does_not_reauth_main_document(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=True)
        last, snap = self._drive(run_id)
        self._assert_production_supervisor()
        assert self.lab is not None
        statuses = [item.status for item in snap.worker_results]
        observations = [
            item for item in snap.observations if item.observation_kind == "BROWSER_PAGE_STATE"
        ]
        reauth_obligations = [item for item in snap.obligations if item.code == "REAUTHORIZATION_REQUIRED"]
        third_hits = list(self.lab.third.hits)
        auth_unchanged = (
            snap.auth_sources
            and snap.auth_sources[0] is not None
            and snap.auth_sources[0].state == "ACTIVE"
            and len(snap.auth_sources) == 1
        )
        if not observations:
            self._fail(
                snap,
                expected="MAIN_BROWSER_PAGE_STATE_OBSERVATION",
                extra={"verdict": "MAIN_OBSERVATION_MISSING", "third_party_hits": third_hits},
            )
        succeeded_pages = [
            item
            for item in snap.worker_results
            if item.status == "SUCCEEDED"
            and isinstance(item.raw_result, dict)
            and item.raw_result.get("snapshot_fingerprint")
        ]
        reauth_results = [
            item for item in snap.worker_results if item.status == "REAUTHORIZATION_REQUIRED"
        ]
        if not succeeded_pages:
            self._fail(
                snap,
                expected="PASSIVE_OOS_DOES_NOT_REAUTH_MAIN_DOCUMENT",
                extra={
                    "verdict": "PASSIVE_SUBRESOURCE_DESTROYED_MAIN_OBSERVATION",
                    "worker_statuses": statuses,
                    "observation_count": len(observations),
                    "third_party_hits": third_hits,
                    "reauth_obligations": len(reauth_obligations),
                },
            )
        passive_boundaries = []
        for item in succeeded_pages:
            for boundary in _blocked_boundaries_from_result(item) or []:
                if boundary.get("reason") == "PASSIVE_EXTERNAL_RESOURCE_BLOCKED":
                    passive_boundaries.append(boundary)
        if not passive_boundaries:
            self._fail(
                snap,
                expected="PASSIVE_BOUNDARY_ON_MAIN_OBSERVATION",
                extra={"verdict": "PASSIVE_BLOCK_NOT_RECORDED", "third_party_hits": third_hits},
            )
        if any(boundary.get("reauth_required") for boundary in passive_boundaries):
            self._fail(
                snap,
                expected="PASSIVE_OOS_DOES_NOT_REAUTH_MAIN_DOCUMENT",
                extra={"verdict": "PASSIVE_BOUNDARY_MARKED_REAUTH"},
            )
        if any(
            boundary.get("egress_occurred") or boundary.get("authority_granted") or boundary.get("followed")
            for boundary in passive_boundaries
        ):
            self._fail(
                snap,
                expected="THIRD_PARTY_EGRESS_ZERO",
                extra={"verdict": "UNAUTHORIZED_EGRESS_OR_FOLLOW"},
            )
        for item in reauth_results:
            diagnostics = item.diagnostics if isinstance(item.diagnostics, dict) else {}
            if diagnostics.get("reason") == "PASSIVE_EXTERNAL_RESOURCE_BLOCKED":
                self._fail(
                    snap,
                    expected="NO_REAUTH_OBLIGATION_FOR_PASSIVE_SUBRESOURCE",
                    extra={"verdict": "SPURIOUS_REAUTH_OBLIGATION"},
                )
            if diagnostics.get("followed") or diagnostics.get("self_authorized"):
                self._fail(snap, expected="NO_NEW_AUTHORITY", extra={"verdict": "AUTHORITY_MUTATED"})
            if diagnostics.get("channel") not in {"REDIRECT", "SPA", "POPUP", "IFRAME"}:
                self._fail(
                    snap,
                    expected="AUTHORITY_TRANSITION_CHANNEL",
                    extra={"verdict": "UNEXPECTED_REAUTH_CHANNEL", "channel": diagnostics.get("channel")},
                )
        if reauth_obligations and not reauth_results:
            self._fail(
                snap,
                expected="NO_REAUTH_OBLIGATION_FOR_PASSIVE_SUBRESOURCE",
                extra={"verdict": "SPURIOUS_REAUTH_OBLIGATION"},
            )
        if third_hits:
            self._fail(
                snap,
                expected="THIRD_PARTY_EGRESS_ZERO",
                extra={"verdict": "UNAUTHORIZED_EGRESS", "third_party_hits": third_hits},
            )
        if not auth_unchanged:
            self._fail(snap, expected="NO_NEW_AUTHORITY", extra={"verdict": "AUTHORITY_MUTATED"})
        if len(snap.scope_rules) != 1:
            self._fail(
                snap,
                expected="SCOPE_UNCHANGED",
                extra={"verdict": "SCOPE_MUTATED", "scope_rule_count": len(snap.scope_rules)},
            )
        if last is None:
            self._fail(snap, expected="RUN_PROGRESSED", extra={"verdict": "NO_TICK_RESULT"})
        third_origin = self.lab.third.origin
        home_html = (self.lab.main.pages.get("/") or ("", b""))[1].decode(
            "utf-8", errors="ignore"
        )
        self.assertIn(
            third_origin,
            home_html,
            "fixture must embed the third-party origin in the main page",
        )
        attempted = [
            (item.raw_result or {}).get("attempted_network_requests")
            for item in snap.worker_results
            if isinstance(item.raw_result, dict)
        ]
        self.assertTrue(
            any(isinstance(value, int) and value >= 2 for value in attempted),
            "passive third-party fixture must cause additional attempted network requests",
        )

    def test_b_human_deny_closes_only_the_blocked_branch(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=False)
        last, snap = self._drive(run_id)
        self._assert_production_supervisor()
        independent = [
            item
            for item in snap.eligible
            if item.goal_kind == DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION.value
            and item.candidate_path == SESSION_PATH
        ]
        reauth = [item for item in snap.worker_results if item.status == "REAUTHORIZATION_REQUIRED"]
        deny_applied = False
        if last is not None and last.state == OrchestrationState.WAITING_HUMAN.value and reauth:
            denied = self.controller.deny_reauthorization(
                run_id,
                worker_result_id=reauth[0].worker_result_id,
                operator_id="operator-1",
            )
            deny_applied = True
            last, snap = self._drive(run_id)
            blocked = [
                item
                for item in snap.experiments
                if item.experiment_id == reauth[0].experiment_id
            ]
            if not blocked or blocked[0].execution_state != "BLOCKED":
                self._fail(
                    snap,
                    expected="DENY_BRANCH_ONLY",
                    extra={"verdict": "DENIED_EXPERIMENT_NOT_BLOCKED", "deny_applied": True},
                )
            remaining_reauth = [
                item for item in snap.obligations if item.code == "REAUTHORIZATION_REQUIRED"
            ]
            if remaining_reauth:
                self._fail(
                    snap,
                    expected="DENY_BRANCH_ONLY",
                    extra={"verdict": "REAUTH_OBLIGATION_NOT_CLEARED", "deny_applied": True},
                )
            if snap.orchestration is not None and snap.orchestration.stop_reason == (
                StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
            ):
                self._fail(
                    snap,
                    expected="RESEARCH_CONTINUES_AFTER_DENY",
                    extra={
                        "verdict": "DENY_ENDED_ENTIRE_RUN",
                        "deny_applied": True,
                        "independent_frontier": len(independent) or len(snap.eligible),
                    },
                )
            session_still_runnable = [
                item
                for item in snap.eligible
                if item.candidate_path == SESSION_PATH
            ] or any(
                (plan is not None and (plan.arguments or {}).get("path") == SESSION_PATH)
                for plan in snap.plans
            )
            if not session_still_runnable:
                self._fail(
                    snap,
                    expected="INDEPENDENT_FRONTIER_SURVIVES",
                    extra={"verdict": "BRANCH_B_LOST_AFTER_DENY", "deny_applied": True},
                )
            return
        extra = {
            "deny_applied": deny_applied,
            "reauth_count": len(reauth),
            "independent_session_frontier": len(independent),
            "eligible_frontier": len(snap.eligible),
            "verdict": "BRANCH_A_NEVER_REACHED_REAUTH_WHILE_B_REMAINED"
            if snap.eligible
            else "NO_INDEPENDENT_BRANCH_AND_NO_DENY",
        }
        if (
            snap.orchestration is not None
            and snap.orchestration.stop_reason
            == StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
            and snap.eligible
        ):
            extra["verdict"] = "INVALID_COMPLETION"
        self._fail(snap, expected="RESEARCH_CONTINUES_AFTER_DENY", extra=extra)

    def test_c_eligible_frontier_forbids_completed_no_more_opportunities(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=False)
        last, snap = self._drive(run_id)
        self._assert_production_supervisor()
        eligible = snap.eligible
        if last is None:
            self._fail(
                snap,
                expected="ELIGIBLE_FRONTIER_PREVENTS_COMPLETE",
                extra={"verdict": "NO_TICK"},
            )
        model_calls = [item for item in snap.consumptions if item.resource_type == "MODEL_CALL"]
        if (
            snap.orchestration is not None
            and snap.orchestration.stop_reason == StopReason.BUDGET_EXHAUSTED.value
            and snap.orchestration.last_phase == "model_budget"
            and not model_calls
            and eligible
        ):
            self._fail(
                snap,
                expected="ELIGIBLE_FRONTIER_PREVENTS_COMPLETE",
                extra={
                    "verdict": "MODEL_BUDGET_GATE_MASKED_NO_MORE_PATH",
                    "model_call_consumptions": len(model_calls),
                },
            )
        if (
            snap.orchestration is not None
            and snap.orchestration.stop_reason
            == StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
            and eligible
        ):
            self._fail(
                snap,
                expected="ELIGIBLE_FRONTIER_PREVENTS_COMPLETE",
                extra={
                    "verdict": "INVALID_COMPLETION",
                    "eligible_frontier": len(eligible),
                    "eligible_frontier_ids": [item.frontier_id for item in eligible],
                    "goal_kind": [item.goal_kind for item in eligible],
                    "candidate_origin": [item.candidate_origin for item in eligible],
                    "candidate_path": [item.candidate_path for item in eligible],
                    "proposed_capability": [item.proposed_capability for item in eligible],
                    "proposed_action": [item.proposed_action for item in eligible],
                    "current_orchestration_phase": snap.orchestration.current_phase,
                    "selected_count": sum(
                        1 for item in snap.selections if item.outcome == "SELECT"
                    ),
                    "hypothesis_count": len(snap.hypotheses),
                    "stop_reason": snap.orchestration.stop_reason,
                },
            )
        http_frontier = [
            item
            for item in snap.frontiers
            if item.goal_kind == DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION.value
        ]
        if len(http_frontier) < 3:
            self._fail(
                snap,
                expected="AT_LEAST_THREE_ELIGIBLE_FRONTIER_ITEMS",
                extra={
                    "verdict": "FIXTURE_OR_HANDOFF_LOST_ELIGIBLE_ITEMS",
                    "frontier_count": len(snap.frontiers),
                    "http_frontier": len(http_frontier),
                },
            )
        if (
            last is not None
            and last.state in TERMINAL
            and eligible
            and snap.orchestration is not None
            and snap.orchestration.stop_reason
            != StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
        ):
            self._fail(
                snap,
                expected="ELIGIBLE_FRONTIER_PREVENTS_COMPLETE",
                extra={
                    "verdict": "NO_MORE_PATH_NOT_REACHED",
                    "stop_reason": snap.orchestration.stop_reason,
                    "eligible_frontier": len(eligible),
                },
            )

    def test_d_eligible_http_frontier_must_reach_worker_or_explicit_disposition(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=False)
        last, snap = self._drive(run_id)
        self._assert_production_supervisor()
        session_items = [
            item
            for item in snap.frontiers
            if item.goal_kind == DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION.value
            and item.candidate_path == SESSION_PATH
            and item.proposed_capability == "http.transaction"
            and item.proposed_action == "read"
            and item.expected_side_effect == 0
        ]
        if not session_items:
            self._fail(
                snap,
                expected="SESSION_JS_ELIGIBLE_FRONTIER",
                extra={
                    "verdict": "FRONTIER_ITEM_NOT_CREATED",
                    "paths": [item.candidate_path for item in snap.frontiers],
                },
            )
        silent = []
        for item in snap.frontiers:
            kind = _latest_event_kind(snap.events_by.get(item.frontier_id, ()))
            if kind != FrontierEventKind.ELIGIBLE.value:
                continue
            disposition = snap.frontier_disposition(item.frontier_id)
            if last is not None and last.state in TERMINAL and disposition not in ALLOWED_FRONTIER_DISPOSITIONS:
                silent.append(item)
        http_calls = [
            call
            for call in (self.worker.calls if self.worker is not None else [])
            if call.get("worker_capability") == "http.transaction"
            and (call.get("arguments") or {}).get("path") == SESSION_PATH
        ]
        session = session_items[0]
        session_disposition = snap.frontier_disposition(session.frontier_id)
        session_kind = _latest_event_kind(snap.events_by.get(session.frontier_id, ()))
        executed = bool(http_calls) or session_disposition == "EXECUTED"
        if last is not None and last.state in TERMINAL and not executed:
            self._fail(
                snap,
                expected="SESSION_JS_HTTP_TRANSACTION_HANDOFF",
                extra={
                    "verdict": "ELIGIBLE_FRONTIER_SILENTLY_LOST",
                    "session_frontier_id": session.frontier_id,
                    "session_latest_event": session_kind,
                    "session_disposition": session_disposition,
                    "http_transaction_calls": len(http_calls),
                    "silent_eligible_count": len(silent),
                    "stop_reason": None if snap.orchestration is None else snap.orchestration.stop_reason,
                },
            )
        if last is not None and last.state in TERMINAL and silent:
            self._fail(
                snap,
                expected="EVERY_ELIGIBLE_FRONTIER_HAS_DISPOSITION",
                extra={
                    "verdict": "SILENT_FRONTIER_LOSS",
                    "silent_ids": [item.frontier_id for item in silent],
                },
            )
        if not executed:
            self._fail(
                snap,
                expected="SESSION_JS_HTTP_TRANSACTION_HANDOFF",
                extra={
                    "verdict": "HANDOFF_NOT_EXECUTED",
                    "session_latest_event": session_kind,
                    "session_disposition": session_disposition,
                    "state": None if last is None else last.state,
                },
            )

    def test_r_recovery_resumes_remaining_discovery_frontier(self) -> None:
        run_id = self._start_supervisor(include_passive_third_party=False)
        first_before, first, first_after = self._tick()
        self.assertTrue(first_before)
        self.assertIsNotNone(first)
        snap = _RunSnapshot(self.factory, run_id)
        if len(snap.eligible) < 3:
            self._fail(
                snap,
                expected="RECOVERY_HAS_REMAINING_ELIGIBLE_FRONTIER",
                extra={"verdict": "FIXTURE_DID_NOT_LEAVE_ELIGIBLE_WORK", "after": first_after},
            )
        calls_before = len(self.worker.calls if self.worker is not None else [])
        recovered = reconstruct_start_command(self.factory, run_id, recovery=True)
        self.assertIsNotNone(
            recovered.surface_discovery,
            "recovery must reattach surface discovery while eligible frontier remains",
        )
        self.supervisor = LocalRunSupervisor(
            run_id, self.controller, recovered, self.factory, cadence_seconds=30
        )
        _, resumed, _ = self._tick()
        self.assertIsNotNone(resumed)
        snap = _RunSnapshot(self.factory, run_id)
        calls_after = len(self.worker.calls if self.worker is not None else [])
        selected_or_observed = [
            item
            for item in snap.frontiers
            if _latest_event_kind(snap.events_by.get(item.frontier_id, ()))
            in {
                FrontierEventKind.SELECTED.value,
                FrontierEventKind.OBSERVED.value,
                FrontierEventKind.AWAITING_REAUTHORIZATION.value,
            }
        ]
        if calls_after <= calls_before and len(selected_or_observed) < 2:
            self._fail(
                snap,
                expected="RECOVERY_CONSUMES_REMAINING_DISCOVERY_WORK",
                extra={
                    "verdict": "ELIGIBLE_WORK_STRANDED_AFTER_RECOVERY",
                    "calls_before": calls_before,
                    "calls_after": calls_after,
                    "selected_or_observed": len(selected_or_observed),
                    "eligible_frontier": len(snap.eligible),
                },
            )
