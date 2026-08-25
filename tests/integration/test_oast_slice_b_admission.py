"""Real PostgreSQL Slice B admission, idempotency, and concurrency tests."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (  # noqa: E402
    NOW,
    PostgresUnitOfWorkFactory,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from research_os.application.admit_oast_callback import AdmitOastCallback  # noqa: E402
from research_os.application.arm_oast_correlation import ArmOastCorrelation  # noqa: E402
from research_os.data.postgres.engine import create_sync_engine  # noqa: E402
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork  # noqa: E402
from research_os.data.records import (  # noqa: E402
    AuditEventRecord,
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    ExperimentRecord,
    HypothesisRecord,
)
from research_os.research.oast.types import OastCallbackDelivery  # noqa: E402

TEST_URL = configured_test_url()


def _config(url: str) -> Config:
    config = Config(str(_REPO / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _delivery(correlation_id: str) -> OastCallbackDelivery:
    payload = {"kind": "dns"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OastCallbackDelivery(
        delivery_id="delivery-concurrent",
        correlation_id=correlation_id,
        provider_adapter_id="provider-neutral-test",
        provider_event_id="event-concurrent",
        received_at=NOW + timedelta(minutes=1),
        normalized_payload=payload,
        normalized_digest=digest,
    )


@unittest.skipUnless(
    TEST_URL,
    "RESEARCH_OS_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class OastSliceBAdmissionIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        command.upgrade(_config(TEST_URL), "head")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow, created_at=NOW)
            uow.hypotheses.insert(
                HypothesisRecord(
                    hypothesis_id="hyp-b",
                    research_run_id="run-1",
                    claim="correlated callback observed",
                    created_at=NOW,
                    identity_id="identity-b",
                )
            )
            uow.experiments.insert(
                ExperimentRecord(
                    experiment_id="exp-b",
                    research_run_id="run-1",
                    hypothesis_id="hyp-b",
                    budget_id="budget-1",
                    execution_state="PLANNED",
                    created_at=NOW,
                )
            )
            uow.experiment_plans.insert(
                ExperimentPlanRecord(
                    experiment_id="exp-b",
                    research_run_id="run-1",
                    hypothesis_id="hyp-b",
                    required_capability="oast",
                    action="arm-correlation",
                    target_reference="https://example.test",
                    side_effect_level=0,
                    arguments={"identity_id": "identity-b"},
                    requested_budget_id="budget-1",
                    expected_observation="callback",
                    disconfirming_observation="no callback",
                    evaluation_strategy="deterministic",
                    created_at=NOW,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="audit-b",
                    occurred_at=NOW,
                    actor_id="operator-1",
                    actor_type="HUMAN_OPERATOR",
                    event_type="AUTHORIZATION_DECISION",
                    subject_type="execution_attempt",
                    subject_id="attempt-b",
                    correlation_id="auth-b",
                    payload={},
                )
            )
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="attempt-b",
                    request_id="request-b",
                    experiment_id="exp-b",
                    research_run_id="run-1",
                    correlation_id="auth-b",
                    worker_capability="oast",
                    action="arm-correlation",
                    target_reference="https://example.test",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference="audit-b",
                    state="AUTHORIZED",
                    created_at=NOW,
                    authorized_at=NOW,
                )
            )
            uow.commit()
        ArmOastCorrelation(
            PostgresUnitOfWorkFactory(self.engine), clock=lambda: NOW
        ).execute("attempt-b")

    def test_same_callback_concurrently_creates_one_authoritative_chain(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            correlation = uow.oast_correlations.get_by_attempt_id("attempt-b")
            uow.rollback()
        assert correlation is not None
        delivery = _delivery(correlation.correlation_id)
        barrier = threading.Barrier(2)

        def ingest() -> object:
            barrier.wait(timeout=10)
            return AdmitOastCallback(
                PostgresUnitOfWorkFactory(self.engine), clock=lambda: NOW
            ).execute(delivery, scope_classification="OUT_OF_SCOPE", identity_id="attacker")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=30) for future in (pool.submit(ingest), pool.submit(ingest))]

        self.assertTrue(all(result.admitted for result in results))
        self.assertEqual(len({result.fact_id for result in results}), 1)
        with self.engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT COUNT(*) FROM oast_callback_delivery), "
                    "(SELECT COUNT(*) FROM oast_admission), "
                    "(SELECT COUNT(*) FROM sensor_observation WHERE sensor_id = 'oast.correlation'), "
                    "(SELECT COUNT(*) FROM discovery_fact WHERE attributes->>'oast_semantics' = 'correlated external callback observed')"
                )
            ).one()
        self.assertEqual(tuple(counts), (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
