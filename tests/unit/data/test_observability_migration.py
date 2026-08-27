from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pathsetup  # noqa: F401


class ObservabilityMigrationTests(unittest.TestCase):
    def test_forward_migration_is_single_current_head_and_preserves_append_only_faults(self) -> None:
        path = (
            Path(__file__).resolve().parents[3]
            / "alembic/versions/a45_001_observability_foundation.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = {
            node.target.id: node.value.value
            for node in tree.body
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and node.target.id in {"revision", "down_revision"}
            )
        }
        self.assertEqual(assignments["revision"], "a45_001_observability_foundation")
        self.assertEqual(assignments["down_revision"], "a44_001_oast_correlation")
        source = path.read_text(encoding="utf-8")
        self.assertIn("run_fault", source)
        self.assertIn("trg_run_fault_append_only", source)
        self.assertIn("target_contact_status", source)
        self.assertIn("cannot downgrade a45", source)


if __name__ == "__main__":
    unittest.main()
