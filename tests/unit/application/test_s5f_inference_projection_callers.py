from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.discovery.snapshot_views import (
    _inference_from_record,
)


class S5fInferenceProjectionCallerTests(
    unittest.TestCase
):
    def test_inference_projection_contract_is_uow_bound(
        self,
    ) -> None:
        parameters = tuple(
            inspect.signature(
                _inference_from_record
            ).parameters
        )

        self.assertEqual(
            parameters,
            ("uow", "record"),
        )

    def test_all_production_callers_pass_uow_and_record(
        self,
    ) -> None:
        repo = Path(__file__).resolve().parents[3]
        root = repo / "src" / "zest"

        stale: list[str] = []
        seen = 0

        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(
                path.read_text(encoding="utf-8")
            )

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                func = node.func

                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(
                    func,
                    ast.Attribute,
                ):
                    name = func.attr
                else:
                    continue

                if name != "_inference_from_record":
                    continue

                seen += 1

                if len(node.args) < 2:
                    stale.append(
                        f"{path.relative_to(repo)}:"
                        f"{node.lineno}"
                    )

        self.assertGreaterEqual(seen, 3)
        self.assertEqual(
            stale,
            [],
            "all production inference projections "
            "must provide the UnitOfWork so "
            "normalized provenance can be read",
        )


if __name__ == "__main__":
    unittest.main()
