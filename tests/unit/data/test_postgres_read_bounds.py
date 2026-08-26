from __future__ import annotations

import unittest
from unittest import mock

from sqlalchemy.dialects import postgresql

import pathsetup  # noqa: F401

from research_os.data.errors import PersistenceInputError
from research_os.data.postgres.discovery_repositories import (
    PostgresDiscoveryFactRepository,
)
from research_os.data.postgres.repositories import (
    PostgresHypothesisRepository,
    PostgresImpactChainRepository,
)


def _connection_with_empty_rows() -> mock.Mock:
    connection = mock.Mock()
    connection.execute.return_value.mappings.return_value.all.return_value = []
    return connection


def _compiled(statement: object):
    return statement.compile(dialect=postgresql.dialect())  # type: ignore[union-attr]


class PostgresReadBoundsTests(unittest.TestCase):
    def test_research_run_query_uses_sql_limit(self) -> None:
        connection = _connection_with_empty_rows()

        PostgresHypothesisRepository(connection).list_for_research_run(
            "run-1", limit=7
        )

        statement = connection.execute.call_args.args[0]
        compiled = _compiled(statement)
        self.assertIn("LIMIT", str(compiled))
        self.assertEqual(compiled.params["param_1"], 7)

    def test_discovery_query_uses_sql_limit(self) -> None:
        connection = _connection_with_empty_rows()

        PostgresDiscoveryFactRepository(connection).list_for_research_run(
            "run-1", limit=7
        )

        statement = connection.execute.call_args.args[0]
        compiled = _compiled(statement)
        self.assertIn("LIMIT", str(compiled))
        self.assertEqual(compiled.params["param_1"], 7)

    def test_graph_queries_use_sql_limit(self) -> None:
        connection = _connection_with_empty_rows()
        repository = PostgresImpactChainRepository(connection)

        repository.get_nodes("chain-1", limit=3)
        repository.get_edges("chain-1", limit=3)

        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertEqual(len(statements), 2)
        for statement in statements:
            compiled = _compiled(statement)
            self.assertIn("LIMIT", str(compiled))
            self.assertEqual(compiled.params["param_1"], 3)

    def test_omitted_limit_preserves_unbounded_caller_semantics(self) -> None:
        connection = _connection_with_empty_rows()

        PostgresHypothesisRepository(connection).list_for_research_run("run-1")

        statement = connection.execute.call_args.args[0]
        self.assertNotIn(" LIMIT ", str(_compiled(statement)))

    def test_invalid_limit_is_rejected_at_repository_boundary(self) -> None:
        connection = _connection_with_empty_rows()

        with self.assertRaises(PersistenceInputError):
            PostgresHypothesisRepository(connection).list_for_research_run(
                "run-1", limit=0
            )

        connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
