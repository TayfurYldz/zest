from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.data.errors import DatabaseUnavailableError, PersistenceError
from zest.data.postgres.engine import (
    check_schema_head,
    create_sync_engine,
    ping_database,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork

DEAD_URL = "postgresql+psycopg://zest_test@127.0.0.1:1/zest_test"


class EngineConnectivityTests(unittest.TestCase):
    def test_ping_refused_connection_is_database_unavailable(self) -> None:
        engine = create_sync_engine(DEAD_URL)
        try:
            with self.assertRaises(DatabaseUnavailableError) as raised:
                ping_database(engine)
            self.assertNotIn("password", str(raised.exception).lower())
            self.assertNotIn("postgresql+psycopg://", str(raised.exception))
        finally:
            engine.dispose()

    def test_schema_probe_refused_connection_is_database_unavailable(self) -> None:
        engine = create_sync_engine(DEAD_URL)
        try:
            with self.assertRaises(DatabaseUnavailableError):
                check_schema_head(engine, alembic_ini_path="alembic.ini")
        finally:
            engine.dispose()

    def test_unit_of_work_connect_refused_is_database_unavailable(self) -> None:
        engine = create_sync_engine(DEAD_URL)
        try:
            with self.assertRaises(DatabaseUnavailableError):
                with PostgresUnitOfWork(engine) as uow:
                    uow.rollback()
        finally:
            engine.dispose()

    def test_integrity_errors_are_not_mapped_as_unavailable(self) -> None:
        from sqlalchemy.exc import IntegrityError

        from zest.data.postgres.engine import is_connectivity_failure

        self.assertFalse(is_connectivity_failure(IntegrityError("stmt", {}, Exception("orig"))))
        self.assertFalse(is_connectivity_failure(PersistenceError("persistence write failed")))
