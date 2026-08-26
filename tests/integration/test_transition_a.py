"""Transition A PostgreSQL tests. SQLite is not a substitute.

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

from zest.application.ingest_worker_invocation import (
    IngestCompletedWorkerInvocation,
    IngestionStatus,
)
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuthorizationSourceRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from support.worker_requests import valid_worker_request

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
            claim="diagnostic",
            created_at=NOW,
        )
    )
    uow.experiments.insert(
        ExperimentRecord(
            experiment_id="exp-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            execution_state="RUNNING",
            created_at=NOW,
        )
    )


def _completed(request):
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        worker_result={
            "contract_version": "v1",
            "correlation": request["correlation"],
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-16T20:00:00Z",
            "completed_at": "2026-08-16T20:00:01Z",
            "raw_result": {"echoed": "ping"},
        },
        exit_code=0,
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class TransitionAPostgresTests(unittest.TestCase):
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

    def test_head_includes_request_id_uniqueness(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'worker_result' "
                    "AND indexname = 'uq_worker_result_request_id'"
                )
            ).all()
        self.assertEqual(len(rows), 1)

    def test_ingest_persists_provenance_and_is_idempotent(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        use_case = IngestCompletedWorkerInvocation(
            PostgresUnitOfWorkFactory(self.engine),
            clock=FixedClock(),
        )
        request = valid_worker_request()
        first = use_case.execute(request, _completed(request))
        second = use_case.execute(request, _completed(request))
        self.assertEqual(first.status, IngestionStatus.INGESTED)
        self.assertEqual(second.status, IngestionStatus.ALREADY_INGESTED)
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.worker_results.get_by_request_id("req-1")
            observations = uow.observations.list_for_worker_result(
                first.worker_result_id or ""
            )
            audit = uow.audit_events.get("ae:wr:req-1")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.correlation_id, "corr-1")
        self.assertEqual(stored.research_run_id, "run-1")
        self.assertEqual(stored.request_id, "req-1")
        self.assertEqual(stored.worker_capability, "diagnostic.echo")
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].worker_result_id, stored.worker_result_id)
        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit.event_type, "WORKER_RESULT_INGESTED")
        self.assertNotIn("raw_result", audit.payload)

    def test_unique_request_id_is_enforced(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        use_case = IngestCompletedWorkerInvocation(
            PostgresUnitOfWorkFactory(self.engine),
            clock=FixedClock(),
        )
        request = valid_worker_request()
        first = use_case.execute(request, _completed(request))
        self.assertEqual(first.status, IngestionStatus.INGESTED)
        from zest.data.errors import PersistenceConflictError
        from zest.data.records import WorkerResultRecord

        with PostgresUnitOfWork(self.engine) as uow:
            with self.assertRaises(PersistenceConflictError):
                uow.worker_results.insert(
                    WorkerResultRecord(
                        worker_result_id="wr-dup",
                        experiment_id="exp-1",
                        research_run_id="run-1",
                        request_id="req-1",
                        correlation_id="corr-other",
                        worker_capability="diagnostic.echo",
                        action="echo",
                        authorization_decision_reference="authz-1",
                        budget_id="budget-1",
                        side_effect_level=0,
                        contract_version="v1",
                        worker_id="worker-1",
                        status="SUCCEEDED",
                        received_at=NOW,
                    )
                )

    def test_equal_payloads_with_distinct_request_ids_are_not_collapsed(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        use_case = IngestCompletedWorkerInvocation(
            PostgresUnitOfWorkFactory(self.engine),
            clock=FixedClock(),
        )
        first_req = valid_worker_request()
        second_req = valid_worker_request()
        second_req["correlation"] = dict(first_req["correlation"])
        second_req["correlation"]["request_id"] = "req-2"
        first = use_case.execute(first_req, _completed(first_req))
        second = use_case.execute(second_req, _completed(second_req))
        self.assertEqual(first.status, IngestionStatus.INGESTED)
        self.assertEqual(second.status, IngestionStatus.INGESTED)
        self.assertNotEqual(first.worker_result_id, second.worker_result_id)
        with PostgresUnitOfWork(self.engine) as uow:
            one = uow.observations.list_for_worker_result(first.worker_result_id or "")
            two = uow.observations.list_for_worker_result(second.worker_result_id or "")
        self.assertEqual(len(one), 1)
        self.assertEqual(len(two), 1)
        self.assertEqual(one[0].payload, two[0].payload)
        self.assertNotEqual(one[0].observation_id, two[0].observation_id)

    def test_observed_at_and_normalization_version_are_persisted(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        use_case = IngestCompletedWorkerInvocation(
            PostgresUnitOfWorkFactory(self.engine),
            clock=FixedClock(),
        )
        request = valid_worker_request()
        outcome = use_case.execute(request, _completed(request))
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.worker_results.get_by_request_id("req-1")
            observations = uow.observations.list_for_worker_result(
                outcome.worker_result_id or ""
            )
        assert stored is not None
        self.assertEqual(len(observations), 1)
        observation = observations[0]
        self.assertEqual(observation.normalization_version, "diagnostic.echo.v1")
        self.assertEqual(stored.authorization_decision_reference, "authz-1")
        self.assertEqual(stored.budget_id, "budget-1")
        self.assertEqual(stored.action, "echo")
        self.assertNotEqual(observation.observed_at, observation.created_at)
        self.assertEqual(observation.created_at, NOW)

    def test_midway_observation_failure_rolls_back_worker_result(self) -> None:
        assert self.engine is not None
        from zest.data.errors import PersistenceError

        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()

        class FailingUnitOfWork(PostgresUnitOfWork):
            def __enter__(self):
                entered = super().__enter__()

                def boom(record):
                    raise PersistenceError("injected observation failure")

                entered.observations.insert = boom  # type: ignore[method-assign]
                return entered

        class FailingFactory:
            def __init__(self, engine) -> None:
                self._engine = engine

            def open(self):
                return FailingUnitOfWork(self._engine)

        use_case = IngestCompletedWorkerInvocation(
            FailingFactory(self.engine),
            clock=FixedClock(),
        )
        request = valid_worker_request()
        with self.assertRaises(PersistenceError):
            use_case.execute(request, _completed(request))
        with PostgresUnitOfWork(self.engine) as uow:
            self.assertIsNone(uow.worker_results.get_by_request_id("req-1"))
            self.assertEqual(uow.observations.list_for_worker_result("wr:req-1"), [])

    def test_observation_fk_rejects_orphan(self) -> None:
        assert self.engine is not None
        from sqlalchemy.exc import IntegrityError
        from zest.data.records import ObservationRecord

        with PostgresUnitOfWork(self.engine) as uow:
            _seed(uow)
            uow.commit()
        with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO observation ("
                        "observation_id, worker_result_id, observation_kind, "
                        "payload, normalization_version, observed_at, created_at"
                        ") VALUES ("
                        "'obs-orphan', 'wr-missing', 'diagnostic.echo', "
                        "'{\"echoed\": \"x\"}'::jsonb, 'diagnostic.echo.v1', now(), now()"
                        ")"
                    )
                )


if __name__ == "__main__":
    unittest.main()
