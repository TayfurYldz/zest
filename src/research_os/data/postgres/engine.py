"""Synchronous SQLAlchemy engine bootstrap. Credentials are not logged."""

from __future__ import annotations

import os

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError, TimeoutError as SATimeoutError

from research_os.data.errors import DatabaseUnavailableError, PersistenceInputError

_CONNECTIVITY_ERRORS = (OperationalError, InterfaceError, SATimeoutError)


def is_connectivity_failure(exc: BaseException) -> bool:
    """True for connection-refused / server-down class failures, not integrity bugs."""

    if isinstance(exc, _CONNECTIVITY_ERRORS):
        return True
    orig = getattr(exc, "orig", None)
    return isinstance(orig, _CONNECTIVITY_ERRORS)


def raise_if_unavailable(exc: BaseException) -> None:
    if is_connectivity_failure(exc):
        raise DatabaseUnavailableError("postgresql unavailable") from exc

DATABASE_URL_ENV = "RESEARCH_OS_DATABASE_URL"
TEST_DATABASE_URL_ENV = "RESEARCH_OS_TEST_DATABASE_URL"

UNSAFE_TEST_DATABASE_NAMES = frozenset(
    {
        "postgres",
        "template0",
        "template1",
        "research_os",
    }
)


def redacted_database_url(url: str) -> str:
    """Render a URL with the password hidden. Never log the raw URL."""
    return make_url(url).render_as_string(hide_password=True)


def validate_test_database_url(
    url: str,
    *,
    application_url: str | None = None,
) -> str:
    """Refuse SQLite, system catalogs, and the application database URL.

    Isolation is by explicit RESEARCH_OS_TEST_DATABASE_URL only. Unrelated
    PG* environment variables are not consulted.
    """
    if not isinstance(url, str) or not url.strip():
        raise PersistenceInputError(f"{TEST_DATABASE_URL_ENV} is required")
    cleaned = url.strip()
    parsed = make_url(cleaned)
    if parsed.get_backend_name() != "postgresql":
        raise PersistenceInputError(
            "test database must be PostgreSQL; SQLite is not a substitute"
        )
    database = (parsed.database or "").strip()
    if not database:
        raise PersistenceInputError("test database URL must include a database name")
    if database.lower() in UNSAFE_TEST_DATABASE_NAMES:
        raise PersistenceInputError(
            "test database name looks like a system or production database"
        )
    if "test" not in database.lower():
        raise PersistenceInputError(
            "test database name must contain 'test' so destructive tests stay isolated"
        )
    if application_url and application_url.strip() == cleaned:
        raise PersistenceInputError(
            f"{TEST_DATABASE_URL_ENV} must not equal {DATABASE_URL_ENV}"
        )
    return cleaned


def redacted_database_url(url: str) -> str:
    """Render a URL with the password hidden. Never log the raw URL."""
    return make_url(url).render_as_string(hide_password=True)


def database_url_from_env(*, testing: bool = False) -> str:
    name = TEST_DATABASE_URL_ENV if testing else DATABASE_URL_ENV
    url = os.environ.get(name)
    if not url or not url.strip():
        raise PersistenceInputError(f"{name} is required")
    return url.strip()


def create_sync_engine(url: str) -> Engine:
    if not isinstance(url, str) or not url.strip():
        raise PersistenceInputError("database url is required")
    return create_engine(
        url.strip(),
        future=True,
        hide_parameters=True,
        pool_pre_ping=True,
    )


def ping_database(engine: Engine) -> bool:
    """Connection health only. Does not log credentials."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise_if_unavailable(exc)
        raise
    return True


def check_schema_head(engine: Engine, *, alembic_ini_path: str) -> tuple[bool, str]:
    """Compare the database's current Alembic revision to this repository's
    local migration head. Not a substitute for running migrations: a
    mismatch means the running application code and the connected database
    schema have drifted apart, which a live worker/model health check
    cannot otherwise detect."""

    config = Config(alembic_ini_path)
    expected = ScriptDirectory.from_config(config).get_current_head()
    try:
        with engine.connect() as connection:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    except SQLAlchemyError as exc:
        raise_if_unavailable(exc)
        raise
    return current == expected, f"database={current!r} expected={expected!r}"
