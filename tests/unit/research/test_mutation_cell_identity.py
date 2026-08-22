"""Authoritative MutationMatrix cell identity. Caller dimensions cannot mint a cell."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pathsetup  # noqa: F401

from research_os.data.postgres.hunter_family_seed import SEED_FAMILIES
from research_os.research.compiler_registry import (
    CompilerOutcome,
    CompilerRequest,
    ExperimentCompilerRegistry,
)
from research_os.research.mutation.cell_contract import bind_mutation_matrix_cell
from research_os.research.mutation.identity import (
    MutationCellIdentityError,
    lookup_authoritative_mutation_cell,
    rebuild_authoritative_mutation_matrix,
)
from research_os.research.mutation.matrix import MutationMatrixCell, build_mutation_matrix
from research_os.research.selection import HunterFamilyView
from research_os.tools.capabilities import HTTP_TRANSACTION_CAPABILITY


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


def _compile(**arguments):
    return ExperimentCompilerRegistry().compile(
        CompilerRequest(
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            family_name=arguments.pop("family_name", "SQL_INJECTION"),
            family_id=arguments.pop("family_id", "hf-sqli"),
            arguments=arguments,
        )
    )


class MutationCellIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = rebuild_authoritative_mutation_matrix(family_name="SQL_INJECTION")
        self.cell = self.matrix.cells[0]
        self.other = self.matrix.cells[1]
        self.origin = "http://127.0.0.1:8090"

    def test_random_unknown_cell_id_with_complete_dimensions_is_blocked_unknown(self) -> None:
        result = _compile(
            cell_id="fabricated-cell-not-in-matrix",
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")
        self.assertIsNone(result.plan)

    def test_valid_cell_id_ignores_dimensions_copied_from_another_cell(self) -> None:
        authoritative = _compile(
            cell_id=self.cell.cell_id,
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
        )
        swapped = _compile(
            cell_id=self.cell.cell_id,
            dimension_values=dict(self.other.dimension_values),
            control=self.other.control,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertTrue(authoritative.compiled)
        self.assertTrue(swapped.compiled)
        self.assertEqual(authoritative.plan.arguments, swapped.plan.arguments)
        query = swapped.plan.arguments.get("query") or {}
        self.assertEqual(query.get("ros_ctl"), self.cell.control)
        self.assertNotEqual(query.get("ros_ctl"), self.other.control)
        self.assertEqual(query.get("ros_dlt"), self.cell.dimension_values["parser_delta"])
        self.assertNotEqual(query.get("ros_dlt"), self.other.dimension_values["parser_delta"])

    def test_cell_id_from_different_hunter_family_is_blocked(self) -> None:
        ssti = rebuild_authoritative_mutation_matrix(family_name="SERVER_SIDE_TEMPLATE_INJECTION")
        result = _compile(
            cell_id=ssti.cells[0].cell_id,
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")
        self.assertIsNone(result.plan)

    def test_cell_id_from_different_matrix_hash_is_blocked(self) -> None:
        ssti = rebuild_authoritative_mutation_matrix(family_name="SERVER_SIDE_TEMPLATE_INJECTION")
        result = _compile(
            cell_id=self.cell.cell_id,
            matrix_hash=ssti.matrix_hash,
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_CONTEXT_MISMATCH")
        self.assertIsNone(result.plan)

    def test_family_id_transplant_is_blocked(self) -> None:
        result = _compile(
            family_name="SQL_INJECTION",
            family_id="hf-ssti",
            cell_id=self.cell.cell_id,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_FAMILY_MISMATCH")

    def test_known_cell_with_incomplete_authoritative_semantics_is_missing_semantics(self) -> None:
        incomplete = MutationMatrixCell(
            family_id="hf-sqli",
            dimension_values={"input_vector": "query"},
            control="secure_fixture",
            cell_id=self.cell.cell_id,
        )
        with patch(
            "research_os.research.compiler_registry.lookup_authoritative_mutation_cell",
            return_value=incomplete,
        ):
            result = _compile(
                cell_id=self.cell.cell_id,
                authorized_origin=self.origin,
                path="/api/users",
            )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_MISSING_SEMANTICS)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_CELL_DIMENSIONS_INCOMPLETE")
        self.assertIsNone(result.plan)

    def test_unknown_cell_with_arbitrary_query_does_not_compile(self) -> None:
        result = _compile(
            cell_id="unknown-query-cell",
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
            query={"injected": "1=1"},
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertIsNone(result.plan)

    def test_unknown_cell_with_arbitrary_body_does_not_compile(self) -> None:
        result = _compile(
            cell_id="unknown-body-cell",
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
            body='{"drop":true}',
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertIsNone(result.plan)

    def test_unknown_cell_with_arbitrary_headers_does_not_compile(self) -> None:
        result = _compile(
            cell_id="unknown-header-cell",
            dimension_values=dict(self.cell.dimension_values),
            control=self.cell.control,
            authorized_origin=self.origin,
            path="/api/users",
            headers={"X-Attack": "1"},
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertIsNone(result.plan)

    def test_valid_cell_catalog_payload_wins_over_caller_query_body_headers(self) -> None:
        result = _compile(
            cell_id=self.cell.cell_id,
            authorized_origin=self.origin,
            path="/api/users",
            query={"injected": "no"},
            body="attacker-supplied",
            headers={"X-Attack": "1"},
        )
        self.assertTrue(result.compiled)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_TRANSACTION_CAPABILITY)
        self.assertNotEqual(result.plan.arguments.get("body"), "attacker-supplied")
        self.assertNotIn("injected", result.plan.arguments.get("query") or {})
        self.assertNotEqual((result.plan.arguments.get("headers") or {}).get("X-Attack"), "1")

    def test_compile_only_known_cell_does_not_require_worker(self) -> None:
        result = _compile(
            cell_id=self.cell.cell_id,
            authorized_origin=self.origin,
            path="/api/users",
        )
        self.assertTrue(result.compiled)
        self.assertIsNotNone(result.plan)

    def test_known_cell_missing_origin_is_missing_semantics_not_unknown(self) -> None:
        result = _compile(cell_id=self.cell.cell_id, path="/api/users")
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_MISSING_SEMANTICS)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_CELL_TARGET_REQUIRED")

    def test_lookup_rejects_unknown_id_even_when_seed_matrix_exists(self) -> None:
        with self.assertRaises(MutationCellIdentityError) as ctx:
            lookup_authoritative_mutation_cell(
                family_name="SQL_INJECTION",
                cell_id="hf-sqli:cell:999",
            )
        self.assertEqual(ctx.exception.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")

    def test_rebuild_matches_build_mutation_matrix_from_seed(self) -> None:
        expected = build_mutation_matrix(_seed_family("hf-sqli"))
        rebuilt = rebuild_authoritative_mutation_matrix(family_name="SQL_INJECTION")
        self.assertEqual(rebuilt.matrix_hash, expected.matrix_hash)
        self.assertEqual(rebuilt.cells[0].cell_id, expected.cells[0].cell_id)

    def test_research_catalog_matches_seed_mutation_families(self) -> None:
        from research_os.research.mutation.identity import MUTATION_MATRIX_FAMILY_CATALOG

        catalog = {entry.family_name: entry for entry in MUTATION_MATRIX_FAMILY_CATALOG}
        seed_mutation = [
            row for row in SEED_FAMILIES if row["name"] in catalog
        ]
        self.assertEqual({row["name"] for row in seed_mutation}, set(catalog))
        for row in seed_mutation:
            entry = catalog[str(row["name"])]
            req = row["evidence_requirements"]
            self.assertEqual(entry.family_id, row["family_id"])
            self.assertEqual(tuple(req["required_matrix_dimensions"]), entry.dimensions)
            self.assertEqual(tuple(req["required_controls"]), entry.controls)

    def test_bind_incomplete_authoritative_copy_stays_contract_error(self) -> None:
        with self.assertRaises(Exception) as ctx:
            bind_mutation_matrix_cell(
                family_name="SQL_INJECTION",
                cell_id=self.cell.cell_id,
                dimension_values={"input_vector": "query"},
                control=self.cell.control,
                authorized_origin=self.origin,
                path="/api/users",
            )
        self.assertEqual(ctx.exception.reason_code, "MUTATION_MATRIX_CELL_DIMENSIONS_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
