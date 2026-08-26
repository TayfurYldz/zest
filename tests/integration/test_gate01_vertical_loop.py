"""GATE 01 — real PostgreSQL vertical control-loop validation.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
SQLite is not a substitute. This is not a Research Brain proof.
"""

from __future__ import annotations

import os
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

from zest.application.execute_planned_experiment import (
    AuthorizedDispatch,
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.retry_policy import automatic_retry_allowed
from zest.core.enums import ExecutionDecisionKind, ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceError
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuditEventRecord,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
)
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from zest.research.planning import plan_diagnostic_echo
from support.recording_worker import RecordingWorkerPort, completed_diagnostic_outcome
from integration.harness import (
    FixedClock,
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    seed_authorized_spine,
    truncate_spine,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )

WORKERS_PYTHON = _REPO / "workers" / "python"


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _ambiguous_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=True,
    )


def _command(scope=None) -> ExecutePlannedExperimentCommand:
    return ExecutePlannedExperimentCommand(
        experiment_id="exp-1",
        plan=plan_diagnostic_echo(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            message="ping",
        ),
        scope=scope or _allow_scope(),
    )


class StagingProbeWorker:
    """Records durable attempt state seen from a separate PostgreSQL connection."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self.calls = []
        self.attempt_state = None
        self.idle_in_transaction = None
        self.lock_timeout_error = None

    def invoke(self, request, *, timeout_ms=None):
        request_id = str(request["correlation"]["request_id"])
        with self._engine.connect() as connection:
            with connection.begin():
                idle = connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND state = 'idle in transaction'"
                    )
                ).scalar_one()
                self.idle_in_transaction = int(idle)
                self.attempt_state = connection.execute(
                    text(
                        "SELECT state FROM execution_attempt WHERE request_id = :rid"
                    ),
                    {"rid": request_id},
                ).scalar_one()
                connection.execute(text("SET LOCAL lock_timeout = '1s'"))
                connection.execute(
                    text(
                        "SELECT 1 FROM execution_attempt "
                        "WHERE request_id = :rid FOR NO KEY UPDATE"
                    ),
                    {"rid": request_id},
                )
        self.calls.append(request)
        return completed_diagnostic_outcome(request)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate01VerticalLoopTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(
            "DESTRUCTIVE PostgreSQL integration tests: TRUNCATE CASCADE against "
            f"{redacted_database_url(TEST_URL)}",
            flush=True,
        )
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_deny_does_not_create_execution_attempt(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        outcome = use_case.execute(_command(scope=_deny_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_DENIED)
        self.assertEqual(len(port.calls), 0)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                attempts = uow.execution_attempts.list_for_experiment("exp-1")
                experiment = uow.experiments.get("exp-1")
                audits = [
                    event
                    for event in _list_audit_events(reloaded)
                    if event.event_type == "EXECUTION_DECISION"
                ]
            self.assertEqual(attempts, [])
            assert experiment is not None
            self.assertEqual(experiment.execution_state, "BLOCKED")
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].payload["decision"], "DENY")
            self.assertFalse(audits[0].payload["dispatched"])
        finally:
            reloaded.dispose()

    def test_require_human_review_does_not_create_execution_attempt(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        outcome = use_case.execute(_command(scope=_ambiguous_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.HUMAN_REVIEW_REQUIRED)
        self.assertEqual(len(port.calls), 0)
        with PostgresUnitOfWork(self.engine) as uow:
            self.assertEqual(uow.execution_attempts.list_for_experiment("exp-1"), [])
            experiment = uow.experiments.get("exp-1")
        assert experiment is not None
        self.assertEqual(experiment.execution_state, "AUTHORIZATION_CHECK")

    def test_authorized_intent_persists_before_worker_and_tx_is_not_held(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        probe = StagingProbeWorker(self.engine)
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            probe,
            clock=FixedClock(),
        )
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(probe.attempt_state, "DISPATCHING")
        self.assertEqual(probe.idle_in_transaction, 0)
        self.assertIsNone(probe.lock_timeout_error)
        self.assertEqual(
            probe.calls[0]["authorization_decision_reference"],
            outcome.authorization_decision_reference,
        )

    def test_crash_before_tx1_commit_leaves_no_attempt(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="ae:exec:uncommitted",
                    occurred_at=NOW,
                    actor_id="control-plane",
                    actor_type="CONTROL_PLANE",
                    event_type="EXECUTION_DECISION",
                    subject_type="experiment",
                    subject_id="exp-1",
                    payload={"decision": "ALLOW", "request_id": "uncommitted"},
                    correlation_id="corr-uncommitted",
                )
            )
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="ea:uncommitted",
                    request_id="uncommitted",
                    experiment_id="exp-1",
                    research_run_id="run-1",
                    correlation_id="corr-uncommitted",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    target_reference="target-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference="ae:exec:uncommitted",
                    state=ExecutionAttemptState.AUTHORIZED.value,
                    created_at=NOW,
                    authorized_at=NOW,
                )
            )
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                self.assertIsNone(uow.execution_attempts.get("ea:uncommitted"))
                self.assertIsNone(uow.audit_events.get("ae:exec:uncommitted"))
        finally:
            reloaded.dispose()

    def test_crash_after_authorized_intent_does_not_auto_retry(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        authorized = use_case.authorize(_command())
        self.assertIsInstance(authorized, AuthorizedDispatch)
        assert isinstance(authorized, AuthorizedDispatch)
        self.assertEqual(len(port.calls), 0)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                attempt = uow.execution_attempts.get(authorized.attempt_id)
                experiment = uow.experiments.get("exp-1")
            assert attempt is not None and experiment is not None
            self.assertEqual(attempt.state, "AUTHORIZED")
            self.assertEqual(experiment.execution_state, "READY")
            resumed = ExecutePlannedExperiment(
                PostgresUnitOfWorkFactory(reloaded),
                port,
                clock=FixedClock(),
            )
            outcome = resumed.execute(_command())
            self.assertEqual(outcome.status, ResearchLoopStatus.AUTHORIZED_NOT_DISPATCHED)
            self.assertEqual(len(port.calls), 0)
        finally:
            reloaded.dispose()

    def test_dispatching_unknown_outcome_is_not_failed_and_not_retried(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        authorized = use_case.authorize(_command())
        assert isinstance(authorized, AuthorizedDispatch)
        with PostgresUnitOfWork(self.engine) as uow:
            uow.execution_attempts.set_state(
                authorized.attempt_id,
                ExecutionAttemptState.DISPATCHING.value,
                dispatch_started_at=NOW,
            )
            uow.experiments.set_execution_state("exp-1", "RUNNING")
            uow.commit()
        reloaded = create_sync_engine(TEST_URL)
        try:
            resumed = ExecutePlannedExperiment(
                PostgresUnitOfWorkFactory(reloaded),
                port,
                clock=FixedClock(),
            )
            outcome = resumed.execute(_command())
            self.assertEqual(outcome.status, ResearchLoopStatus.UNKNOWN_OUTCOME)
            self.assertEqual(len(port.calls), 0)
            self.assertFalse(
                automatic_retry_allowed(
                    attempt_state="UNKNOWN_OUTCOME", side_effect_level=2
                )
            )
            with PostgresUnitOfWork(reloaded) as uow:
                attempt = uow.execution_attempts.get(authorized.attempt_id)
                experiment = uow.experiments.get("exp-1")
                hypothesis = uow.hypotheses.get("hyp-1")
            assert attempt is not None and experiment is not None and hypothesis is not None
            self.assertEqual(attempt.state, "UNKNOWN_OUTCOME")
            self.assertEqual(experiment.execution_state, "RUNNING")
            self.assertNotEqual(experiment.execution_state, "EXECUTION_FAILED")
            self.assertEqual(
                hypothesis.claim,
                "diagnostic runtime returns the provided echo value",
            )
        finally:
            reloaded.dispose()

    def test_first_real_vertical_loop_reloads_full_provenance(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        adapter = LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=WORKERS_PYTHON,
                default_timeout_ms=5_000,
            )
        )
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            adapter,
            clock=FixedClock(),
        )
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(outcome.core_decision, ExecutionDecisionKind.ALLOW)
        self.assertTrue(outcome.hypothesis_claim_unchanged)
        self.assertIsNotNone(outcome.request_id)
        self.assertIsNotNone(outcome.authorization_decision_reference)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                observation = uow.observations.get(outcome.observation_ids[0])
                assert observation is not None
                worker_result = uow.worker_results.get(observation.worker_result_id)
                assert worker_result is not None
                attempt = uow.execution_attempts.get_by_request_id(worker_result.request_id)
                assert attempt is not None
                experiment = uow.experiments.get(attempt.experiment_id)
                assert experiment is not None
                hypothesis = uow.hypotheses.get(experiment.hypothesis_id)
                assert hypothesis is not None
                run = uow.research_runs.get(experiment.research_run_id)
                assert run is not None
                source = uow.authorization_sources.get(run.authorization_source_id)
                assert source is not None
                program = uow.programs.get(run.program_id)
                assert program is not None
                decision = uow.audit_events.get(attempt.authorization_decision_reference)
                assert decision is not None
            self.assertEqual(observation.payload, {"echoed": "ping"})
            self.assertEqual(observation.normalization_version, "diagnostic.echo.v1")
            self.assertEqual(worker_result.request_id, outcome.request_id)
            self.assertEqual(worker_result.worker_capability, "diagnostic.echo")
            self.assertEqual(attempt.state, "COMPLETED")
            self.assertEqual(attempt.experiment_id, "exp-1")
            self.assertEqual(experiment.execution_state, "EXECUTION_SUCCEEDED")
            self.assertEqual(
                hypothesis.claim,
                "diagnostic runtime returns the provided echo value",
            )
            self.assertEqual(run.authorization_source_id, "as-1")
            self.assertEqual(source.program_id, "prog-1")
            self.assertEqual(source.state, "ACTIVE")
            self.assertEqual(program.program_id, "prog-1")
            self.assertEqual(decision.event_type, "EXECUTION_DECISION")
            self.assertEqual(decision.payload["decision"], "ALLOW")
            self.assertEqual(
                worker_result.authorization_decision_reference,
                decision.audit_event_id,
            )
            self.assertNotIn("severity", observation.payload)
        finally:
            reloaded.dispose()

    def test_tx2_failure_does_not_fabricate_ingestion(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()

        class Factory:
            def __init__(self, engine) -> None:
                self.engine = engine
                self.set_state_calls = 0

            def open(self):
                return OutcomeFailingUoW(self)

        class OutcomeFailingUoW(PostgresUnitOfWork):
            def __init__(self, factory: Factory) -> None:
                super().__init__(factory.engine)
                self._factory = factory

            def __enter__(self):
                entered = super().__enter__()
                original = entered.execution_attempts.set_state

                def maybe_fail(attempt_id, state, **kwargs):
                    self._factory.set_state_calls += 1
                    if self._factory.set_state_calls >= 2:
                        raise PersistenceError("injected TX2 failure")
                    return original(attempt_id, state, **kwargs)

                entered.execution_attempts.set_state = maybe_fail  # type: ignore[method-assign]
                return entered

        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            Factory(self.engine),
            port,
            clock=FixedClock(),
        )
        outcome = use_case.execute(_command())
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(outcome.status, ResearchLoopStatus.INVOCATION_FAILED)
        with PostgresUnitOfWork(self.engine) as uow:
            attempts = uow.execution_attempts.list_for_experiment("exp-1")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].state, "DISPATCHING")
            self.assertIsNone(uow.worker_results.get_by_request_id(attempts[0].request_id))


def _list_audit_events(engine) -> list[AuditEventRecord]:
    from sqlalchemy import select
    from zest.data.postgres import mapping as map_row
    from zest.data.postgres import tables

    with engine.connect() as connection:
        rows = connection.execute(select(tables.audit_event)).mappings().all()
    return [map_row.audit_event_from_row(row) for row in rows]


if __name__ == "__main__":
    unittest.main()
