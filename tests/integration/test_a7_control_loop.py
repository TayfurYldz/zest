"""A7-lite PostgreSQL tests. SQLite is not a substitute.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from alembic import command
from alembic.config import Config

from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceConflictError
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuthorizationSourceRecord,
    ExecutionAttemptRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.research.planning import plan_diagnostic_echo
from support.recording_worker import RecordingWorkerPort

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    from zest.data.postgres.engine import validate_test_database_url

    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )
NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)


class PostgresUnitOfWorkFactory:
    def __init__(self, engine) -> None:
        self._engine = engine

    def open(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self._engine)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _alembic_upgrade(url: str) -> None:
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _seed(uow: PostgresUnitOfWork) -> None:
    uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=NOW, name="lab"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="written-auth-1",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id="run-1",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=1,
            max_tool_calls=1,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )
    uow.hypotheses.insert(
        HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            origin_reference="human-seed-1",
            created_at=NOW,
        )
    )
    uow.experiments.insert(
        ExperimentRecord(
            experiment_id="exp-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            execution_state="PLANNED",
            created_at=NOW,
        )
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class A7ControlLoopPostgresTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        cls.engine = create_sync_engine(TEST_URL)
        _alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE program, audit_event CASCADE"))

    def test_head_includes_execution_attempt_request_id_uniqueness(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'execution_attempt' "
                    "AND indexname = 'uq_execution_attempt_request_id'"
                )
            ).all()
        self.assertEqual(len(rows), 1)

    def test_loop_persists_decision_attempt_and_observation(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_diagnostic_echo(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    message="ping",
                ),
                scope=_allow_scope(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)
        assert outcome.request_id is not None
        assert outcome.authorization_decision_reference is not None
        with PostgresUnitOfWork(self.engine) as uow:
            attempt = uow.execution_attempts.get_by_request_id(outcome.request_id)
            experiment = uow.experiments.get("exp-1")
            hypothesis = uow.hypotheses.get("hyp-1")
            audit = uow.audit_events.get(outcome.authorization_decision_reference)
            observations = uow.observations.list_for_worker_result(
                outcome.worker_result_id or ""
            )
        self.assertIsNotNone(attempt)
        assert attempt is not None
        self.assertEqual(attempt.state, "COMPLETED")
        self.assertEqual(attempt.authorization_decision_reference, outcome.authorization_decision_reference)
        self.assertIsNotNone(experiment)
        assert experiment is not None
        self.assertEqual(experiment.execution_state, "EXECUTION_SUCCEEDED")
        self.assertIsNotNone(hypothesis)
        assert hypothesis is not None
        self.assertEqual(
            hypothesis.claim,
            "diagnostic runtime returns the provided echo value",
        )
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.event_type, "EXECUTION_DECISION")
        self.assertEqual(audit.payload["decision"], "ALLOW")
        self.assertEqual(len(observations), 1)

    def test_duplicate_request_id_is_rejected(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        authorized = use_case.authorize(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_diagnostic_echo(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    message="ping",
                ),
                scope=_allow_scope(),
            )
        )
        from zest.application.execute_planned_experiment import AuthorizedDispatch

        self.assertIsInstance(authorized, AuthorizedDispatch)
        assert isinstance(authorized, AuthorizedDispatch)
        with PostgresUnitOfWork(self.engine) as uow:
            existing = uow.execution_attempts.get(authorized.attempt_id)
            assert existing is not None
            with self.assertRaises(PersistenceConflictError):
                uow.execution_attempts.insert(
                    ExecutionAttemptRecord(
                        attempt_id="ea:other",
                        request_id=existing.request_id,
                        experiment_id=existing.experiment_id,
                        research_run_id=existing.research_run_id,
                        correlation_id="corr-dup",
                        worker_capability=existing.worker_capability,
                        action=existing.action,
                        target_reference=existing.target_reference,
                        budget_id=existing.budget_id,
                        side_effect_level=0,
                        authorization_decision_reference=existing.authorization_decision_reference,
                        state="AUTHORIZED",
                        created_at=NOW,
                    )
                )

    def test_stalled_dispatching_reloads_as_unknown_outcome(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        port = RecordingWorkerPort()
        use_case = ExecutePlannedExperiment(
            PostgresUnitOfWorkFactory(self.engine),
            port,
            clock=FixedClock(),
        )
        command = ExecutePlannedExperimentCommand(
            experiment_id="exp-1",
            plan=plan_diagnostic_echo(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                message="ping",
            ),
            scope=_allow_scope(),
        )
        authorized = use_case.authorize(command)
        from zest.application.execute_planned_experiment import AuthorizedDispatch

        assert isinstance(authorized, AuthorizedDispatch)
        with PostgresUnitOfWork(self.engine) as uow:
            uow.execution_attempts.set_state(
                authorized.attempt_id,
                "DISPATCHING",
                dispatch_started_at=NOW,
            )
            uow.experiments.set_execution_state("exp-1", "RUNNING")
            uow.commit()
        port.calls.clear()
        outcome = use_case.execute(command)
        self.assertEqual(outcome.status, ResearchLoopStatus.UNKNOWN_OUTCOME)
        self.assertEqual(len(port.calls), 0)
        with PostgresUnitOfWork(self.engine) as uow:
            attempt = uow.execution_attempts.get(authorized.attempt_id)
            experiment = uow.experiments.get("exp-1")
            hypothesis = uow.hypotheses.get("hyp-1")
        assert attempt is not None
        assert experiment is not None
        assert hypothesis is not None
        self.assertEqual(attempt.state, "UNKNOWN_OUTCOME")
        self.assertEqual(experiment.execution_state, "RUNNING")
        self.assertEqual(
            hypothesis.claim,
            "diagnostic runtime returns the provided echo value",
        )


if __name__ == "__main__":
    unittest.main()
