"""Alembic environment. URL is taken from the environment; never log passwords."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from zest.data.postgres.engine import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
)
from zest.data.postgres.tables import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured and configured.strip():
        return configured.strip()
    url = os.environ.get(DATABASE_URL_ENV) or os.environ.get(TEST_DATABASE_URL_ENV)
    if not url or not url.strip():
        raise RuntimeError(
            f"{DATABASE_URL_ENV} or {TEST_DATABASE_URL_ENV} is required for Alembic"
        )
    return url.strip()


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    connectable = create_sync_engine(url)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
