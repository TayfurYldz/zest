"""PostgreSQL integration tests for the a43 budget CHECK repair.

SQLite is not a substitute. The suite is skipped without an explicit isolated
RESEARCH_OS_TEST_DATABASE_URL.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
from research_os.data.postgres.engine import create_sync_engine  # noqa: E402
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork  # noqa: E402

TEST_URL = configured_test_url()
LEGACY_CONSTRAINT = "ck_budget_consumption_resource_type"
V2_CONSTRAINT = "ck_budget_consumption_resource_type_v2"
ALL_VALID_RESOURCE_TYPES = (
    "MODEL_CALL",
    "MODEL_TOKENS_IN",
    "MODEL_TOKENS_OUT",
    "MODEL_ESCALATION_DECISION",
    "WORKER_INVOCATION",
    "REQUEST",
    "EXECUTION_TIME",
    "ARTIFACT_BYTES",
    "COST",
)


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _constraint_names(connection) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'budget_consumption'::regclass AND contype = 'c'"
        )
    )
    return {row[0] for row in rows}


def _insert_consumption(connection, *, resource_type: str, suffix: str) -> None:
    connection.execute(
        text(
            "INSERT INTO budget_consumption "
            "(consumption_id, budget_id, research_run_id, experiment_id, request_id, "
            "resource_type, amount, unit, occurred_at, provenance) "
            "VALUES (:consumption_id, 'budget-1', 'run-1', NULL, :request_id, "
            ":resource_type, 1, 'count', :occurred_at, 'migration-test')"
        ),
        {
            "consumption_id": f"cons-{suffix}",
            "request_id": f"request-{suffix}",
            "resource_type": resource_type,
            "occurred_at": datetime.now(timezone.utc),
        },
    )


@unittest.skipUnless(
    TEST_URL,
    "RESEARCH_OS_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class BudgetResourceTypeCheckRepairIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        command.upgrade(_alembic_config(TEST_URL), "head")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow, created_at=NOW)
            uow.commit()

    def test_upgrade_leaves_only_v2_and_accepts_all_canonical_values(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            names = _constraint_names(connection)
        self.assertNotIn(LEGACY_CONSTRAINT, names)
        self.assertIn(V2_CONSTRAINT, names)

        with self.engine.begin() as connection:
            for index, resource_type in enumerate(ALL_VALID_RESOURCE_TYPES):
                _insert_consumption(
                    connection,
                    resource_type=resource_type,
                    suffix=f"valid-{index}",
                )

    def test_invalid_resource_type_is_rejected_by_postgresql(self) -> None:
        assert self.engine is not None
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as connection:
                _insert_consumption(
                    connection,
                    resource_type="NOT_A_BUDGET_RESOURCE",
                    suffix="invalid",
                )

    def test_downgrade_refuses_token_rows_without_deleting_them(self) -> None:
        assert self.engine is not None
        with self.engine.begin() as connection:
            _insert_consumption(
                connection,
                resource_type="MODEL_TOKENS_IN",
                suffix="downgrade-token",
            )

        cfg = _alembic_config(TEST_URL)
        with self.assertRaises(RuntimeError):
            command.downgrade(cfg, "a42_001_preflight_report")

        with self.engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM budget_consumption "
                    "WHERE consumption_id = 'cons-downgrade-token'"
                )
            ).scalar_one()
            self.assertEqual(count, 1)

        truncate_spine(self.engine)

        try:
            command.downgrade(cfg, "a42_001_preflight_report")
            with self.engine.connect() as connection:
                names = _constraint_names(connection)
            self.assertIn(LEGACY_CONSTRAINT, names)
            self.assertIn(V2_CONSTRAINT, names)
        finally:
            command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
