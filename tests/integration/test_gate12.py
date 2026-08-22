"""GATE 12 — Autonomous Orchestration Integrity on real PostgreSQL.

Bounded diagnostic cycles only. This is not real security research validation.
GATE 04B remains PENDING unless ≥2 live ModelRuntime configurations execute.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from research_os.application.errors import OrchestrationIntegrityError
from research_os.application.orchestration_config import configuration_from_record
from research_os.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationResolution,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.research.model_port import ContentPolicyBlockedError, ModelRole
from research_os.research.routing import RoutingBudget, RoutingRequest
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.research.orchestration import OrchestrationBounds, OrchestrationState, StopReason
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    truncate_spine,
)
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=2,
        max_experiments=2,
        max_model_calls=20,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _seed(factory: PostgresUnitOfWorkFactory) -> None:
    with factory.open() as uow:
        uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=FixedClock().now(), name="lab"))
        uow.authorization_sources.insert(
            AuthorizationSourceRecord(
                authorization_source_id="as-1",
                program_id="prog-1",
                state="ACTIVE",
                provenance_reference="written-auth-1",
                created_at=FixedClock().now(),
            )
        )
        uow.research_runs.insert(
            ResearchRunRecord(
                research_run_id="run-1",
                program_id="prog-1",
                authorization_source_id="as-1",
                initiated_by_actor_id="operator-1",
                initiated_by_actor_type="HUMAN_OPERATOR",
                started_at=FixedClock().now(),
            )
        )
        uow.issued_budgets.insert(
            IssuedBudgetRecord(
                budget_id="budget-1",
                research_run_id="run-1",
                max_requests=20,
                max_tool_calls=20,
                max_runtime_ms=10_000,
                max_concurrency=1,
                issued_at=FixedClock().now(),
            )
        )
        uow.commit()


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate12AutonomousOrchestrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 12 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def _controller(self, factory, worker=None, model=None):
        return AutonomousResearchController(
            factory,
            worker or RecordingWorkerPort(),
            model or ScriptedModelPort(),
            clock=FixedClock(),
        )

    def _counts(self, factory: PostgresUnitOfWorkFactory) -> dict[str, int]:
        with factory.open() as uow:
            counts = {
                "hypothesis": len(uow.hypotheses.list_for_research_run("run-1")),
                "experiment": len(uow.experiments.list_for_research_run("run-1")),
                "attempt": len(uow.execution_attempts.list_for_research_run("run-1")),
                "observation": len(uow.observations.list_for_research_run("run-1")),
                "evidence": len(uow.evidence.list_for_research_run("run-1")),
                "candidate": len(uow.candidates.list_for_research_run("run-1")),
                "finding": len(uow.findings.list_for_research_run("run-1")),
            }
            uow.rollback()
        return counts

    def test_bounded_cycles_pause_resume_cancel_and_core_gate(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        worker = RecordingWorkerPort()
        controller = self._controller(factory, worker=worker)
        started = controller.start(_command())
        self.assertEqual(started.state, OrchestrationState.READY.value)
        paused = controller.pause("run-1")
        self.assertEqual(paused.state, OrchestrationState.PAUSED.value)
        controller.resume("run-1")
        finished = controller.run_bounded(_command())
        self.assertEqual(finished.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(len(worker.calls), 4)
        counts = self._counts(factory)
        self.assertEqual(counts["hypothesis"], 2)
        self.assertEqual(counts["experiment"], 4)
        self.assertEqual(counts["finding"], 0)
        # Canonical MR-5: CONSISTENT_WITH_PREDICTION admits Evidence and a
        # Candidate, then ARC continues independent verification to
        # FindingProposal. Finding remains human-gated. Reproduction
        # experiments are promotion work, not additional research-cycle
        # selections counted against max_experiments.
        self.assertEqual(counts["evidence"], 4)
        self.assertEqual(counts["candidate"], 2)
        # RT-A: the run already reached its own terminal COMPLETED/
        # MAX_CYCLES_REACHED checkpoint via run_bounded() above. A cancel
        # issued after the fact must not resurrect or reclassify a terminal
        # run -- it is a rejected no-op that preserves the original
        # state/stop_reason, proven here against real PostgreSQL.
        cancelled = controller.cancel("run-1")
        self.assertEqual(cancelled.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(cancelled.stop_reason, StopReason.MAX_CYCLES_REACHED.value)
        with factory.open() as uow:
            reloaded = uow.research_orchestrations.get("run-1")
            uow.rollback()
        assert reloaded is not None
        self.assertEqual(reloaded.stop_reason, StopReason.MAX_CYCLES_REACHED.value)

    def test_cancel_actually_cancels_a_still_active_run(self) -> None:
        """RT-A companion: cancel() must still work -- and persist
        OPERATOR_CANCELLED -- when the run has not yet reached a terminal
        checkpoint on its own."""
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        controller = self._controller(factory)
        controller.start(_command(bounds=_bounds(max_cycles=5)))
        cancelled = controller.cancel("run-1")
        self.assertEqual(cancelled.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(cancelled.stop_reason, StopReason.OPERATOR_CANCELLED.value)
        with factory.open() as uow:
            reloaded = uow.research_orchestrations.get("run-1")
            uow.rollback()
        assert reloaded is not None
        self.assertEqual(reloaded.stop_reason, StopReason.OPERATOR_CANCELLED.value)

    def test_max_cycles_zero_executes_nothing(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        worker = RecordingWorkerPort()
        controller = self._controller(factory, worker=worker)
        started = controller.start(_command(bounds=_bounds(max_cycles=0)))
        self.assertEqual(started.state, OrchestrationState.COMPLETED.value)
        counts = self._counts(factory)
        self.assertEqual(counts["hypothesis"], 0)
        self.assertEqual(counts["experiment"], 0)
        self.assertEqual(len(worker.calls), 0)

    def test_persisted_bounds_and_fingerprint_survive_reload(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        first = self._controller(factory)
        first.start(_command(bounds=_bounds(max_cycles=1)))
        with factory.open() as uow:
            record = uow.research_orchestrations.get("run-1")
            uow.rollback()
        assert record is not None
        config = configuration_from_record(record)
        self.assertEqual(config.fingerprint, record.configuration_fingerprint)
        self.assertEqual(len(config.fingerprint), 64)
        second = self._controller(factory)
        with self.assertRaises(OrchestrationIntegrityError):
            second.step(_command(bounds=_bounds(max_cycles=3)))
        with factory.open() as uow:
            reloaded = uow.research_orchestrations.get("run-1")
            uow.rollback()
        assert reloaded is not None
        self.assertEqual(reloaded.max_cycles, 1)
        self.assertEqual(reloaded.configuration_fingerprint, record.configuration_fingerprint)

    def test_core_deny_prevents_dispatch(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        worker = RecordingWorkerPort()
        deny = ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "src"),),
            ambiguous=False,
        )
        controller = self._controller(factory, worker=worker)
        controller.start(_command(scope=deny))
        result = controller.step(_command(scope=deny))
        self.assertEqual(result.stop_reason, StopReason.CORE_BLOCKED.value)
        self.assertEqual(len(worker.calls), 0)

    def test_runtime_unavailable_stops_cleanly(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        worker = RecordingWorkerPort()
        request = RoutingRequest(
            role=ModelRole.GENERATOR,
            candidates=(),
            budget=RoutingBudget(max_runtime_attempts=1, max_fallback_attempts=0),
        )
        controller = self._controller(factory, worker=worker)
        controller.start(_command(routing_request=request))
        result = controller.step(_command(routing_request=request))
        self.assertEqual(result.stop_reason, StopReason.NO_COMPATIBLE_RUNTIME.value)
        self.assertEqual(len(worker.calls), 0)

    def test_content_policy_blocked_does_not_fallback(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed(factory)
        model = ScriptedModelPort(error=ContentPolicyBlockedError("blocked"))
        worker = RecordingWorkerPort()
        controller = self._controller(factory, worker=worker, model=model)
        controller.start(_command())
        result = controller.step(_command())
        self.assertEqual(result.stop_reason, StopReason.CONTENT_POLICY_BLOCKED.value)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(worker.calls), 0)

    def test_schema_head_is_a17(self) -> None:
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a40_001_mr6a_identity_anomaly")

    def test_postgres_process_crash_matrix_does_not_duplicate(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        child = Path(__file__).with_name("gate12_crash_child.py")
        phases = (
            "OPPORTUNITY_SELECTED",
            "HYPOTHESIS_ADMITTED",
            "EXPERIMENT_PLANNED",
            "AUTHORIZATION_REQUESTED",
            "ATTEMPT_AUTHORIZED",
            "DISPATCHING",
            "WORKER_RESULT_RECORDED",
            "TRANSITION_A_COMPLETE",
            "ASSESSMENT_COMPLETE",
        )
        env = dict(os.environ)
        env[TEST_DATABASE_URL_ENV] = TEST_URL or ""
        env.pop("PYTHONPATH", None)
        env["PYTHONPATH"] = os.pathsep.join([str(_SRC), str(_REPO / "tests"), str(_REPO)])
        for phase in phases:
            with self.subTest(phase=phase):
                truncate_spine(self.engine)
                _seed(factory)
                starter = self._controller(factory)
                starter.start(_command(bounds=_bounds(max_cycles=2)))
                completed = subprocess.run(
                    [sys.executable, str(child), phase],
                    cwd=str(_REPO),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    9,
                    msg=f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
                )
                self.assertIn("CRASH_AFTER", completed.stdout)
                before = self._counts(factory)
                restarted = self._controller(factory)
                result = restarted.step(_command(bounds=_bounds(max_cycles=2)))
                after = self._counts(factory)
                self.assertLessEqual(after["candidate"], 1)
                if before["candidate"]:
                    self.assertEqual(after["candidate"], before["candidate"])
                self.assertEqual(after["finding"], 0)
                self.assertLessEqual(after["hypothesis"], max(before["hypothesis"], 1))
                if before["hypothesis"]:
                    self.assertEqual(after["hypothesis"], before["hypothesis"])
                if before["experiment"]:
                    self.assertGreaterEqual(after["experiment"], before["experiment"])
                if phase == "DISPATCHING":
                    self.assertEqual(result.stop_reason, StopReason.OPERATIONAL_FAILURE.value)
                    recon = ReconcileResearchRun(factory, clock=FixedClock()).execute(
                        ReconcileResearchRunCommand("run-1")
                    )
                    self.assertTrue(
                        any(
                            item.resolution is ReconciliationResolution.UNKNOWN_OUTCOME
                            for item in recon.items
                        )
                    )


if __name__ == "__main__":
    unittest.main()

