from __future__ import annotations

import unittest
from typing import cast

import pathsetup  # noqa: F401
from sqlalchemy.engine import Engine

from zest.data.postgres.unit_of_work import PostgresUnitOfWork


class PostgresUnitOfWorkFactoryTests(unittest.TestCase):
    def test_open_returns_fresh_unit_of_work_for_shared_engine(self) -> None:
        engine = cast(Engine, object())
        factory = PostgresUnitOfWork(engine)

        opened = factory.open()

        self.assertIsInstance(opened, PostgresUnitOfWork)
        self.assertIsNot(opened, factory)
        self.assertIs(opened._engine, engine)


if __name__ == "__main__":
    unittest.main()
