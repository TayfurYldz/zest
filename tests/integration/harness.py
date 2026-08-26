"""Shared PostgreSQL integration harness. SQLite is not a substitute.

Requires an explicit ZEST_TEST_DATABASE_URL. Tests TRUNCATE this database.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.data.postgres.engine import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.qualification.staging_spine import (
    seed_authorized_spine,
    truncate_spine,
)

NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)
DESTRUCTIVE_NOTICE = (
    "DESTRUCTIVE PostgreSQL integration tests: TRUNCATE CASCADE will run against "
    "the explicit test database only."
)


class PostgresUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def open(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self._engine)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def configured_test_url() -> str | None:
    raw = os.environ.get(TEST_DATABASE_URL_ENV)
    if not raw or not raw.strip():
        return None
    return validate_test_database_url(
        raw,
        application_url=os.environ.get(DATABASE_URL_ENV),
    )


def alembic_upgrade(url: str) -> None:
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def warn_destructive(url: str) -> None:
    print(f"{DESTRUCTIVE_NOTICE} target={redacted_database_url(url)}", flush=True)


# truncate_spine and seed_authorized_spine are imported from
# zest.qualification.staging_spine so VDS fixtures do not need tests/.
