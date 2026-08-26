from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.data.postgres.engine import redacted_database_url


class EngineUrlTests(unittest.TestCase):
    def test_password_is_not_rendered(self) -> None:
        url = "postgresql+psycopg://operator:super-secret@localhost:5432/zest"
        redacted = redacted_database_url(url)
        self.assertNotIn("super-secret", redacted)
        self.assertIn("localhost", redacted)

    def test_sqlite_is_rejected_as_test_database(self) -> None:
        from zest.data.errors import PersistenceInputError
        from zest.data.postgres.engine import validate_test_database_url

        with self.assertRaises(PersistenceInputError):
            validate_test_database_url("sqlite:///tmp/research.db")

    def test_system_and_production_database_names_are_rejected(self) -> None:
        from zest.data.errors import PersistenceInputError
        from zest.data.postgres.engine import validate_test_database_url

        with self.assertRaises(PersistenceInputError):
            validate_test_database_url(
                "postgresql+psycopg://u@localhost:5432/postgres"
            )
        with self.assertRaises(PersistenceInputError):
            validate_test_database_url(
                "postgresql+psycopg://u@localhost:5432/zest"
            )

    def test_application_url_cannot_be_reused_as_test_url(self) -> None:
        from zest.data.errors import PersistenceInputError
        from zest.data.postgres.engine import validate_test_database_url

        url = "postgresql+psycopg://u@localhost:5432/zest_test"
        with self.assertRaises(PersistenceInputError):
            validate_test_database_url(url, application_url=url)

    def test_isolated_test_name_is_accepted(self) -> None:
        from zest.data.postgres.engine import validate_test_database_url

        url = "postgresql+psycopg://u@127.0.0.1:55432/zest_test"
        self.assertEqual(validate_test_database_url(url), url)


if __name__ == "__main__":
    unittest.main()
