from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pathsetup  # noqa: F401


REPO_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = REPO_ROOT / "alembic" / "versions"
MIGRATION = VERSIONS / "a43_001_budget_type_check_repair.py"
A16 = VERSIONS / "a16_001_orchestration_operations.py"
A28 = VERSIONS / "a28_001_token_economy.py"


def _module_constants(source: str) -> dict[str, object]:
    tree = ast.parse(source)
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        try:
            values[node.target.id] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
    return values


class BudgetResourceTypeMigrationTests(unittest.TestCase):
    def test_revision_and_forward_only_scope(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        constants = _module_constants(source)
        self.assertEqual(constants["revision"], "a43_001_budget_type_check_repair")
        self.assertEqual(constants["down_revision"], "a42_001_preflight_report")
        self.assertIn("op.drop_constraint(LEGACY_CONSTRAINT", source)
        self.assertNotIn("create_table(", source)
        self.assertNotIn("alter_column(", source)

    def test_historical_migrations_are_not_rewritten_by_repair(self) -> None:
        a16 = A16.read_text(encoding="utf-8")
        a28 = A28.read_text(encoding="utf-8")
        self.assertNotIn("a43_001_budget_type_check_repair", a16)
        self.assertNotIn("a43_001_budget_type_check_repair", a28)
        self.assertIn("ck_budget_consumption_resource_type", a16)
        self.assertIn("ck_budget_consumption_resource_type_v2", a28)

    def test_vocabulary_and_downgrade_guard_are_explicit(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for resource_type in (
            "MODEL_CALL",
            "MODEL_TOKENS_IN",
            "MODEL_TOKENS_OUT",
            "MODEL_ESCALATION_DECISION",
            "WORKER_INVOCATION",
            "REQUEST",
            "EXECUTION_TIME",
            "ARTIFACT_BYTES",
            "COST",
        ):
            self.assertIn(resource_type, source)
        self.assertIn("cannot downgrade a43", source)
        self.assertIn("no rows were deleted or rewritten", source)


if __name__ == "__main__":
    unittest.main()
