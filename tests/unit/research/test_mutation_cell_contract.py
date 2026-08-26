"""Compiler-owned mutation matrix cell bindings. Not authorization. Not exploits."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.research.compiler_registry import MUTATION_MATRIX_FAMILIES
from zest.research.mutation.cell_contract import (
    FAMILY_REQUIRED_DIMENSIONS,
    bind_mutation_matrix_cell,
)
from zest.research.mutation.matrix import build_mutation_matrix
from zest.research.selection import HunterFamilyView


def _seed_family(family_id: str) -> HunterFamilyView:
    row = next(item for item in SEED_FAMILIES if item["family_id"] == family_id)
    return HunterFamilyView(
        family_id=str(row["family_id"]),
        name=str(row["name"]),
        target_node_kinds=tuple(str(item) for item in row["target_node_kinds"]),
        preconditions=dict(row["preconditions"]),
        claim_template=str(row["claim_template"]),
        evidence_requirements=dict(row["evidence_requirements"]),
        validation_tier=str(row["validation_tier"]),
        enabled=bool(row["enabled"]),
        version=int(row["version"]),
    )


class MutationCellContractTests(unittest.TestCase):
    def test_seed_families_match_required_dimensions(self) -> None:
        self.assertEqual(set(FAMILY_REQUIRED_DIMENSIONS), set(MUTATION_MATRIX_FAMILIES))

    def test_each_family_first_cell_binds_without_model_payload(self) -> None:
        for name in sorted(MUTATION_MATRIX_FAMILIES):
            family_id = next(item["family_id"] for item in SEED_FAMILIES if item["name"] == name)
            matrix = build_mutation_matrix(_seed_family(str(family_id)))
            cell = matrix.cells[0]
            binding = bind_mutation_matrix_cell(
                family_name=name,
                cell_id=cell.cell_id,
                dimension_values=cell.dimension_values,
                control=cell.control,
                authorized_origin="http://127.0.0.1:8090",
                path="/api/users",
            )
            with self.subTest(family=name):
                self.assertEqual(binding.family_name, name)
                self.assertEqual(binding.control, cell.control)
                self.assertIn(binding.action, {"read", "mutate"})
                self.assertTrue(binding.disconfirming_observation)
                args = binding.template.arguments()
                self.assertEqual(args["authorized_origin"], "http://127.0.0.1:8090")
                self.assertIn("ros_ctl", args.get("query") or {})
