from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
J11 = REPO / "src/zest/qualification/j11_fencing.py"
OSD = REPO / "src/zest/interface/zestd.py"


class J11FencingQualificationSourceTests(unittest.TestCase):
    def test_j11_fencing_imports_no_tests_or_integration_harness(self) -> None:
        source = J11.read_text(encoding="utf-8")
        self.assertNotIn("integration.harness", source)
        self.assertNotIn("from integration", source)
        self.assertNotIn("tests.", source)
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        self.assertFalse(any(item == "tests" or item.startswith("tests.") for item in imports))
        self.assertFalse(any(item == "integration" or item.startswith("integration.") for item in imports))

    def test_production_zestd_does_not_import_j11_qualification(self) -> None:
        source = OSD.read_text(encoding="utf-8")
        self.assertNotIn("j11_fencing", source)
        self.assertNotIn("zest.qualification", source)

    def test_j11_uses_existing_fencing_primitives_and_bounded_diagnostic_worker(self) -> None:
        source = J11.read_text(encoding="utf-8")
        self.assertIn("SingleRunFencedUowFactory", source)
        self.assertIn("LeaseFencedWorkerPort", source)
        self.assertIn("acquire_lease", source)
        self.assertIn("release_lease", source)
        self.assertIn("diagnostic.echo", source)
        self.assertIn('"side_effect_level": 0', source)
        self.assertIn('"side_effect_ceiling": 0', source)

    def test_j11_cleanup_is_bounded_to_expired_stopped_qualification_owner(self) -> None:
        source = J11.read_text(encoding="utf-8")
        self.assertIn("cleanup_j11_owner", source)
        self.assertIn("RELEASED_EXPIRED_STOPPED_OWNER", source)
        self.assertIn("ALREADY_UNOWNED", source)
        self.assertIn("j11 cleanup refuses to clear a live owner", source)
        self.assertIn("owner/epoch mismatch", source)
        self.assertIn("checkpoint16-j11-fencing", source)
        self.assertIn('owner.status == "STOPPED"', source)


if __name__ == "__main__":
    unittest.main()
