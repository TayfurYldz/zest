"""Real PostgreSQL Interactsh v1.3.1 normalization -> OAST admission."""

from __future__ import annotations

import json
import sys
import unittest
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
from zest.application.admit_oast_callback import AdmitOastCallback  # noqa: E402
from zest.application.arm_oast_correlation import ArmOastCorrelation  # noqa: E402
from zest.data.postgres.engine import create_sync_engine  # noqa: E402
from zest.data.postgres.unit_of_work import PostgresUnitOfWork  # noqa: E402
from zest.data.records import (  # noqa: E402
    AuditEventRecord,
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    ExperimentRecord,
    HypothesisRecord,
)
from zest.integrations.oast.interactsh import (  # noqa: E402
    INTERACTSH_PROVIDER_ADAPTER_ID,
    normalize_interactsh_jsonl,
)

TEST_URL = configured_test_url()


def _config(url: str) -> Config:
    config = Config(str(_REPO / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _interactsh_event() -> str:
    timestamp = (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    return json.dumps(
        {
            "protocol": "dns",
            "unique-id": "abc123def456ghi",
            "full-id": "abc123def456ghi.oast.example.test",
            "q-type": "A",
            "raw-request": "Authorization: Bearer SHOULD_NEVER_PERSIST",
            "raw-response": "Set-Cookie: secret=SHOULD_NEVER_PERSIST",
            "remote-address": "203.0.113.10",
            "timestamp": timestamp,
        },
        separators=(",", ":"),
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class OastSliceCInteractshAdmissionIntegrationTests(unittest.TestCase):
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
                    hypothesis_id="hyp-c",
                    research_run_id="run-1",
                    claim="correlated Interactsh callback observed",
                    created_at=NOW,
                    identity_id="identity-c",
                )
            )

            uow.experiments.insert(
                ExperimentRecord(
                    experiment_id="exp-c",
                    research_run_id="run-1",
                    hypothesis_id="hyp-c",
                    budget_id="budget-1",
                    execution_state="PLANNED",
                    created_at=NOW,
                )
            )

            uow.experiment_plans.insert(
                ExperimentPlanRecord(
                    experiment_id="exp-c",
                    research_run_id="run-1",
                    hypothesis_id="hyp-c",
                    required_capability="oast",
                    action="arm-correlation",
                    target_reference="https://example.test",
                    side_effect_level=0,
                    arguments={"identity_id": "identity-c"},
                    requested_budget_id="budget-1",
                    expected_observation="callback",
                    disconfirming_observation="no callback",
                    evaluation_strategy="deterministic",
                    created_at=NOW,
                )
            )

            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="audit-c",
                    occurred_at=NOW,
                    actor_id="operator-1",
                    actor_type="HUMAN_OPERATOR",
                    event_type="AUTHORIZATION_DECISION",
                    subject_type="execution_attempt",
                    subject_id="attempt-c",
                    correlation_id="auth-c",
                    payload={},
                )
            )

            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="attempt-c",
                    request_id="request-c",
                    experiment_id="exp-c",
                    research_run_id="run-1",
                    correlation_id="auth-c",
                    worker_capability="oast",
                    action="arm-correlation",
                    target_reference="https://example.test",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference="audit-c",
                    state="AUTHORIZED",
                    created_at=NOW,
                    authorized_at=NOW,
                )
            )

            uow.commit()

        ArmOastCorrelation(
            PostgresUnitOfWorkFactory(self.engine),
            clock=lambda: NOW,
        ).execute("attempt-c")

    def _correlation_id(self) -> str:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            correlation = uow.oast_correlations.get_by_attempt_id("attempt-c")
            uow.rollback()

        assert correlation is not None
        return correlation.correlation_id

    def test_interactsh_jsonl_is_normalized_and_admitted(self) -> None:
        assert self.engine is not None

        correlation_id = self._correlation_id()

        delivery = normalize_interactsh_jsonl(
            _interactsh_event(),
            correlation_id=correlation_id,
        )

        self.assertEqual(
            delivery.provider_adapter_id,
            INTERACTSH_PROVIDER_ADAPTER_ID,
        )
        self.assertEqual(delivery.normalized_payload["protocol"], "dns")
        self.assertEqual(delivery.normalized_payload["q_type"], "A")
        self.assertNotIn("raw-request", delivery.normalized_payload)
        self.assertNotIn("raw-response", delivery.normalized_payload)

        admitted = AdmitOastCallback(
            PostgresUnitOfWorkFactory(self.engine),
            clock=lambda: NOW,
        ).execute(
            delivery,
            scope_classification="OUT_OF_SCOPE",
            identity_id="attacker-controlled",
        )

        self.assertTrue(admitted.admitted)

        replay = AdmitOastCallback(
            PostgresUnitOfWorkFactory(self.engine),
            clock=lambda: NOW,
        ).execute(delivery)

        self.assertTrue(replay.admitted)
        self.assertEqual(replay.fact_id, admitted.fact_id)

        with self.engine.connect() as connection:
            counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT COUNT(*) FROM oast_callback_delivery), "
                    "(SELECT COUNT(*) FROM oast_admission), "
                    "(SELECT COUNT(*) FROM sensor_observation "
                    " WHERE sensor_id = 'oast.correlation'), "
                    "(SELECT COUNT(*) FROM discovery_fact "
                    " WHERE attributes->>'oast_semantics' = "
                    "'correlated external callback observed')"
                )
            ).one()

            persisted = connection.execute(
                text(
                    "SELECT provider_adapter_id, provider_event_id, "
                    "normalized_payload, normalized_digest "
                    "FROM oast_callback_delivery"
                )
            ).one()

            fact_attributes = connection.execute(
                text(
                    "SELECT attributes "
                    "FROM discovery_fact "
                    "WHERE attributes->>'oast_semantics' = "
                    "'correlated external callback observed'"
                )
            ).scalar_one()

        self.assertEqual(tuple(counts), (1, 1, 1, 1))

        self.assertEqual(
            persisted.provider_adapter_id,
            INTERACTSH_PROVIDER_ADAPTER_ID,
        )
        self.assertEqual(len(persisted.provider_event_id), 64)
        self.assertEqual(len(persisted.normalized_digest), 64)

        persisted_json = json.dumps(
            persisted.normalized_payload,
            sort_keys=True,
        )
        self.assertNotIn("raw-request", persisted_json)
        self.assertNotIn("raw-response", persisted_json)
        self.assertNotIn("SHOULD_NEVER_PERSIST", persisted_json)

        self.assertEqual(
            fact_attributes["scope_classification"],
            "UNKNOWN",
        )
        self.assertEqual(
            fact_attributes["oast_semantics"],
            "correlated external callback observed",
        )

    def test_wrong_research_correlation_binding_fails_closed(self) -> None:
        assert self.engine is not None

        delivery = normalize_interactsh_jsonl(
            _interactsh_event(),
            correlation_id="missing-authoritative-correlation",
        )

        result = AdmitOastCallback(
            PostgresUnitOfWorkFactory(self.engine),
            clock=lambda: NOW,
        ).execute(delivery)

        self.assertFalse(result.admitted)
        self.assertEqual(
            result.reason_code,
            "OAST_CORRELATION_NOT_FOUND",
        )

        with self.engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM oast_callback_delivery")
            ).scalar_one()

        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
