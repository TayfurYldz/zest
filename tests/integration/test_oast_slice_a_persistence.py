"""PostgreSQL integration tests for OAST-1 Slice A persistence.

SQLite is not a substitute. The suite is skipped without an explicit isolated
ZEST_TEST_DATABASE_URL.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (  # noqa: E402
    NOW,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.data.postgres.engine import create_sync_engine  # noqa: E402

TEST_URL = configured_test_url()


def _config(url: str) -> Config:
    config = Config(str(_REPO / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _seed_execution_spine(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_event "
                "(audit_event_id, occurred_at, actor_id, actor_type, event_type, "
                "subject_type, subject_id, correlation_id, payload) "
                "VALUES ('audit-oast-1', :now, 'operator-1', 'HUMAN_OPERATOR', "
                "'AUTHORIZATION_DECISION', 'execution_attempt', 'attempt-1', "
                "'corr-auth-1', '{}'::jsonb)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO experiment_plan "
                "(experiment_id, research_run_id, hypothesis_id, required_capability, "
                "action, target_reference, side_effect_level, arguments, "
                "requested_budget_id, expected_observation, disconfirming_observation, "
                "evaluation_strategy, created_at) "
                "VALUES ('exp-1', 'run-1', 'hyp-1', 'http.request', 'GET', "
                "'https://example.test', 0, '{}'::jsonb, 'budget-1', "
                "'external callback observed', 'no callback', 'deterministic', :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO execution_attempt "
                "(attempt_id, request_id, experiment_id, research_run_id, correlation_id, "
                "worker_capability, action, target_reference, budget_id, side_effect_level, "
                "authorization_decision_reference, state, created_at) "
                "VALUES ('attempt-1', 'request-1', 'exp-1', 'run-1', 'corr-auth-1', "
                "'http.request', 'GET', 'https://example.test', 'budget-1', 0, "
                "'audit-oast-1', 'AUTHORIZED', :now)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO audit_event "
                "(audit_event_id, occurred_at, actor_id, actor_type, event_type, "
                "subject_type, subject_id, correlation_id, payload) "
                "VALUES ('audit-oast-2', :now, 'operator-1', 'HUMAN_OPERATOR', "
                "'AUTHORIZATION_DECISION', 'execution_attempt', 'attempt-2', "
                "'corr-auth-2', '{}'::jsonb)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO execution_attempt "
                "(attempt_id, request_id, experiment_id, research_run_id, correlation_id, "
                "worker_capability, action, target_reference, budget_id, side_effect_level, "
                "authorization_decision_reference, state, created_at) "
                "VALUES ('attempt-2', 'request-2', 'exp-1', 'run-1', 'corr-auth-2', "
                "'http.request', 'GET', 'https://example.test', 'budget-1', 0, "
                "'audit-oast-2', 'AUTHORIZED', :now)"
            ),
            {"now": NOW},
        )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class OastSliceAPersistenceIntegrationTests(unittest.TestCase):
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
        from zest.data.postgres.unit_of_work import PostgresUnitOfWork

        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow, created_at=NOW)
            uow.commit()
        _seed_execution_spine(self.engine)

    def _insert_correlation(self, correlation_id: str, attempt_id: str) -> None:
        assert self.engine is not None
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO oast_correlation "
                    "(correlation_id, attempt_id, experiment_id, research_run_id, "
                    "target_reference, identity_id, armed_at, expires_at, created_at) "
                    "VALUES (:correlation_id, :attempt_id, 'exp-1', 'run-1', "
                    "'https://example.test', 'identity-1', :armed_at, :expires_at, :created_at)"
                ),
                {
                    "correlation_id": correlation_id,
                    "attempt_id": attempt_id,
                    "armed_at": NOW,
                    "expires_at": NOW + timedelta(minutes=5),
                    "created_at": NOW,
                },
            )

    def test_correlation_fk_window_and_one_per_attempt(self) -> None:
        self._insert_correlation("corr-1", "attempt-1")
        assert self.engine is not None
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO oast_correlation "
                        "(correlation_id, attempt_id, experiment_id, research_run_id, "
                        "target_reference, identity_id, armed_at, expires_at, created_at) "
                        "VALUES ('corr-2', 'attempt-1', 'exp-1', 'run-1', "
                        "'https://example.test', 'identity-1', :now, :later, :now)"
                    ),
                    {"now": NOW, "later": NOW + timedelta(minutes=5)},
                )
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO oast_correlation "
                        "(correlation_id, attempt_id, experiment_id, research_run_id, "
                        "target_reference, identity_id, armed_at, expires_at, created_at) "
                        "VALUES ('corr-window-invalid', 'attempt-2', 'exp-1', 'run-1', "
                        "'https://example.test', 'identity-1', :now, :now, :now)"
                    ),
                    {"now": NOW},
                )

    def test_callback_dedup_and_multiple_deliveries(self) -> None:
        self._insert_correlation("corr-1", "attempt-1")
        assert self.engine is not None
        insert = text(
            "INSERT INTO oast_callback_delivery "
            "(delivery_id, correlation_id, provider_adapter_id, provider_event_id, "
            "received_at, normalized_payload, normalized_digest) "
            "VALUES (:delivery_id, 'corr-1', 'adapter', :event_id, :now, "
            "CAST(:payload AS jsonb), :digest)"
        )
        with self.engine.begin() as connection:
            connection.execute(
                insert,
                {
                    "delivery_id": "delivery-1",
                    "event_id": "event-1",
                    "now": NOW,
                    "payload": '{"kind":"dns"}',
                    "digest": "a" * 64,
                },
            )
            connection.execute(
                insert,
                {
                    "delivery_id": "delivery-2",
                    "event_id": "event-2",
                    "now": NOW + timedelta(seconds=1),
                    "payload": '{"kind":"http"}',
                    "digest": "b" * 64,
                },
            )
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(
                    insert,
                    {
                        "delivery_id": "delivery-3",
                        "event_id": "event-1",
                        "now": NOW,
                        "payload": '{"kind":"duplicate"}',
                        "digest": "c" * 64,
                    },
                )
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                connection.execute(
                    insert,
                    {
                        "delivery_id": "delivery-4",
                        "event_id": "event-4",
                        "now": NOW,
                        "payload": '{"kind":"duplicate-digest"}',
                        "digest": "a" * 64,
                    },
                )

    def test_append_only_and_downgrade_are_fail_closed(self) -> None:
        self._insert_correlation("corr-1", "attempt-1")
        assert self.engine is not None
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE oast_correlation SET identity_id = 'identity-2' "
                        "WHERE correlation_id = 'corr-1'"
                    )
                )
        with self.assertRaises(RuntimeError):
            command.downgrade(_config(TEST_URL), "a43_001_budget_type_check_repair")
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.execute(
                    text("SELECT COUNT(*) FROM oast_correlation")
                ).scalar_one(),
                1,
            )


if __name__ == "__main__":
    unittest.main()
