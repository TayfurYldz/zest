from __future__ import annotations

import os
import unittest

import pathsetup  # noqa: F401

from research_os.qualification.staging_spine import (
    TRUNCATE_GUARD,
    StagingTruncateDenied,
    require_explicit_spine_truncate,
)


class StagingTruncateGateTests(unittest.TestCase):
    def test_seed_without_truncate_flag_is_denied(self) -> None:
        with self.assertRaises(StagingTruncateDenied) as raised:
            require_explicit_spine_truncate(
                truncate=False,
                env={TRUNCATE_GUARD: "YES"},
            )
        self.assertIn("--truncate", str(raised.exception))

    def test_truncate_without_explicit_env_is_denied(self) -> None:
        with self.assertRaises(StagingTruncateDenied) as raised:
            require_explicit_spine_truncate(truncate=True, env={})
        self.assertIn(TRUNCATE_GUARD, str(raised.exception))

    def test_yes_is_the_only_accepted_guard_value(self) -> None:
        for value in ("yes", "true", "1", "Y", ""):
            with self.subTest(value=value):
                with self.assertRaises(StagingTruncateDenied):
                    require_explicit_spine_truncate(
                        truncate=True,
                        env={TRUNCATE_GUARD: value},
                    )

    def test_explicit_yes_and_truncate_is_allowed(self) -> None:
        require_explicit_spine_truncate(
            truncate=True,
            env={TRUNCATE_GUARD: "YES"},
        )

    def test_process_env_is_not_consulted_implicitly(self) -> None:
        previous = os.environ.get(TRUNCATE_GUARD)
        os.environ[TRUNCATE_GUARD] = "YES"
        try:
            with self.assertRaises(StagingTruncateDenied):
                require_explicit_spine_truncate(truncate=True, env={})
        finally:
            if previous is None:
                os.environ.pop(TRUNCATE_GUARD, None)
            else:
                os.environ[TRUNCATE_GUARD] = previous


class StagingSpineTablePreservationTests(unittest.TestCase):
    def test_metadata_tables_filter_excludes_runtime_instance_by_default(self) -> None:
        from research_os.data.postgres.tables import metadata

        tables = [
            table
            for table in metadata.sorted_tables
            if not (True and table.name == "runtime_instance")
        ]
        table_names = [table.name for table in tables]
        self.assertNotIn("runtime_instance", table_names)
        self.assertIn("research_run", table_names)
        self.assertIn("research_orchestration", table_names)
        self.assertIn("preflight_report", table_names)

    def test_metadata_tables_filter_includes_runtime_instance_when_preserve_false(self) -> None:
        from research_os.data.postgres.tables import metadata

        tables = [
            table
            for table in metadata.sorted_tables
            if not (False and table.name == "runtime_instance")
        ]
        table_names = [table.name for table in tables]
        self.assertIn("runtime_instance", table_names)
        self.assertIn("research_run", table_names)


if __name__ == "__main__":
    unittest.main()
