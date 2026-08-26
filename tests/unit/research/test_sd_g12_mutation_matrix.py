from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.research.mutation import build_mutation_matrix
from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError


def _family(family_id: str) -> HunterFamilyView:
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


class SDG12MutationMatrixTests(unittest.TestCase):
    def test_sqli_matrix_is_deterministic_and_meets_minimum(self) -> None:
        family = _family("hf-sqli")

        first = build_mutation_matrix(family)
        second = build_mutation_matrix(family)

        self.assertEqual(first.matrix_hash, second.matrix_hash)
        self.assertGreaterEqual(len(first.cells), 30)
        self.assertEqual(first.dimensions, ("input_vector", "encoding", "parser_delta"))
        self.assertIn("secure_fixture", first.controls)
        self.assertIn("deceptive_fixture", first.controls)
        self.assertIn("read_back", first.controls)

    def test_ai_llm_matrix_preserves_metamorphic_and_tool_controls(self) -> None:
        matrix = build_mutation_matrix(_family("hf-ai-llm-target"))

        self.assertGreaterEqual(len(matrix.cells), 30)
        self.assertEqual(
            matrix.dimensions,
            ("instruction_channel", "retrieval_context", "tool_boundary"),
        )
        self.assertIn("metamorphic_variant", matrix.controls)
        self.assertIn("tool_denial_control", matrix.controls)
        cell_values = [cell.dimension_values for cell in matrix.cells]
        self.assertTrue(any(item["retrieval_context"] == "hostile_doc" for item in cell_values))
        self.assertTrue(any(item["tool_boundary"] == "write_tool" for item in cell_values))

    def test_unknown_matrix_dimension_fails_closed(self) -> None:
        family = HunterFamilyView(
            family_id="hf-bad",
            name="BAD",
            target_node_kinds=("HTTP_OPERATION",),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template="bad {canonical_key}",
            evidence_requirements={
                "required_controls": ["negative"],
                "required_matrix_dimensions": ["unknown_dimension"],
            },
            validation_tier="V2",
            enabled=True,
            version=1,
        )

        with self.assertRaises(ResearchInputError):
            build_mutation_matrix(family)

    def test_too_small_matrix_cap_fails_closed(self) -> None:
        with self.assertRaises(ResearchInputError):
            build_mutation_matrix(_family("hf-sqli"), max_cells=29)


if __name__ == "__main__":
    unittest.main()
