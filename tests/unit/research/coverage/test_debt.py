from __future__ import annotations

import unittest
from collections import Counter

import pathsetup  # noqa: F401

from zest.core.enums import ScopeClassification
from zest.research.coverage.debt import compute_coverage_debt
from zest.research.coverage.types import (
    CoverageCell,
    CoverageHypothesisView,
    CoverageMatrix,
    CoverageState,
)
from zest.research.discovery.graph import (
    AttackSurfaceGraph,
    AttackSurfaceNode,
)
from zest.research.discovery.types import (
    AttackSurfaceNodeKind,
    AttackSurfaceEdgeKind,
)
from zest.research.selection import HunterFamilyView
from zest.research.target_model import TargetEpistemicStatus


def _node(
    canonical_key: str,
    *,
    kind: AttackSurfaceNodeKind = AttackSurfaceNodeKind.EXACT_PATH,
    scope: ScopeClassification = ScopeClassification.IN_SCOPE,
    identity_ids: tuple[str, ...] = (),
) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id=canonical_key,
        kind=kind,
        canonical_key=canonical_key,
        epistemic_status=TargetEpistemicStatus.OBSERVED,
        identity_ids=identity_ids,
        provenance_refs=(),
        scope_classification=scope,
        attributes={"path": "/api/users/{id}"},
    )


def _family(family_id: str, node_kinds: tuple[str, ...]) -> HunterFamilyView:
    return HunterFamilyView(
        family_id=family_id,
        name=family_id.upper(),
        target_node_kinds=node_kinds,
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template="test claim for {canonical_key}",
        evidence_requirements={},
        validation_tier="V3",
        enabled=True,
        version=1,
    )


class CoverageDebtCoreTests(unittest.TestCase):
    def test_empty_graph_yields_empty_matrix(self) -> None:
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="v1",
            nodes=(),
            edges=(),
        )
        matrix = compute_coverage_debt(graph, (), ())
        self.assertEqual(matrix.cells, ())
        self.assertEqual(matrix.total_debt, 0)
        self.assertEqual(matrix.cell_counts, {})
        self.assertEqual(len(matrix.matrix_hash), 64)

    def test_unknown_scope_is_not_applicable_and_not_debt(self) -> None:
        node = _node("n1", scope=ScopeClassification.UNKNOWN)
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        matrix = compute_coverage_debt(graph, (family,), ())
        self.assertEqual(len(matrix.cells), 1)
        self.assertEqual(matrix.cells[0].state, CoverageState.NOT_APPLICABLE)
        self.assertEqual(matrix.total_debt, 0)
        self.assertEqual(matrix.cell_counts[CoverageState.NOT_APPLICABLE.value], 1)

    def test_out_of_scope_is_not_applicable_and_not_debt(self) -> None:
        node = _node("n1", scope=ScopeClassification.OUT_OF_SCOPE)
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        matrix = compute_coverage_debt(graph, (family,), ())
        self.assertEqual(matrix.cells[0].state, CoverageState.NOT_APPLICABLE)
        self.assertEqual(matrix.total_debt, 0)

    def test_anonymous_identity_used_when_node_has_none(self) -> None:
        node = _node("n1", identity_ids=())
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        matrix = compute_coverage_debt(graph, (family,), ())
        self.assertEqual(len(matrix.cells), 1)
        self.assertEqual(matrix.cells[0].identity_id, "ANONYMOUS")
        self.assertEqual(matrix.cells[0].state, CoverageState.UNTESTED)
        self.assertEqual(matrix.total_debt, 1)

    def test_identity_agnostic_hypothesis_spreads_to_all_identities(self) -> None:
        node = _node("n1", identity_ids=("alice", "bob"))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        view = CoverageHypothesisView(
            hypothesis_id="hyp-1",
            family_id="f1",
            node_canonical_key="n1",
            identity_id=None,
            highest_tier="V2",
        )
        matrix = compute_coverage_debt(graph, (family,), (view,))
        self.assertEqual(len(matrix.cells), 2)
        states = {cell.identity_id: cell.state for cell in matrix.cells}
        self.assertEqual(states["alice"], CoverageState.V2_PASSED)
        self.assertEqual(states["bob"], CoverageState.V2_PASSED)
        self.assertEqual(matrix.total_debt, 2)

    def test_specific_hypothesis_beats_agnostic(self) -> None:
        node = _node("n1", identity_ids=("alice", "bob"))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        agnostic = CoverageHypothesisView(
            hypothesis_id="hyp-agnostic",
            family_id="f1",
            node_canonical_key="n1",
            identity_id=None,
            highest_tier="V1",
        )
        specific = CoverageHypothesisView(
            hypothesis_id="hyp-specific",
            family_id="f1",
            node_canonical_key="n1",
            identity_id="alice",
            highest_tier="COVERED",
        )
        matrix = compute_coverage_debt(graph, (family,), (agnostic, specific))
        states = {cell.identity_id: cell.state for cell in matrix.cells}
        self.assertEqual(states["alice"], CoverageState.COVERED)
        self.assertEqual(states["bob"], CoverageState.V1_PASSED)

    def test_untested_cell_reports_missing_evidence(self) -> None:
        node = _node("n1", identity_ids=("alice",))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        matrix = compute_coverage_debt(graph, (family,), ())
        cell = matrix.cells[0]
        self.assertEqual(cell.state, CoverageState.UNTESTED)
        self.assertIn("NO_HYPOTHESIS", cell.missing_evidence)

    def test_hypothesized_but_not_validated(self) -> None:
        node = _node("n1", identity_ids=("alice",))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        view = CoverageHypothesisView(
            hypothesis_id="hyp-1",
            family_id="f1",
            node_canonical_key="n1",
            identity_id=None,
            highest_tier="UNTESTED",
        )
        matrix = compute_coverage_debt(graph, (family,), (view,))
        self.assertEqual(matrix.cells[0].state, CoverageState.HYPOTHESIZED)
        self.assertIn("HYPOTHESIS_NOT_VALIDATED", matrix.cells[0].missing_evidence)

    def test_deterministic_across_permutations(self) -> None:
        node = _node("n1", identity_ids=("alice", "bob", "carol"))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        views = (
            CoverageHypothesisView("h1", "f1", "n1", "bob", "V1"),
            CoverageHypothesisView("h2", "f1", "n1", "alice", "V2"),
            CoverageHypothesisView("h3", "f1", "n1", "carol", "UNTESTED"),
        )
        hashes: set[str] = set()
        from itertools import permutations

        for perm in permutations(views):
            matrix = compute_coverage_debt(graph, (family,), perm)
            hashes.add(matrix.matrix_hash)
        self.assertEqual(len(hashes), 1)

    def test_total_debt_counts_non_covered_cells(self) -> None:
        node = _node("n1", identity_ids=("alice",))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        view = CoverageHypothesisView(
            hypothesis_id="h1",
            family_id="f1",
            node_canonical_key="n1",
            identity_id="alice",
            highest_tier="COVERED",
        )
        matrix = compute_coverage_debt(graph, (family,), (view,))
        self.assertEqual(matrix.cells[0].state, CoverageState.COVERED)
        self.assertEqual(matrix.total_debt, 0)
        self.assertEqual(matrix.cell_counts[CoverageState.COVERED.value], 1)

    def test_identity_aware_hypothesis_affects_only_its_cell(self) -> None:
        """SD-G9: non-None identity_id is bound to a single cell."""
        node = _node("n1", identity_ids=("alice", "bob"))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        view = CoverageHypothesisView(
            hypothesis_id="h1",
            family_id="f1",
            node_canonical_key="n1",
            identity_id="alice",
            highest_tier="V2",
        )
        matrix = compute_coverage_debt(graph, (family,), (view,))
        states = {cell.identity_id: cell.state for cell in matrix.cells}
        self.assertEqual(states["alice"], CoverageState.V2_PASSED)
        self.assertEqual(states["bob"], CoverageState.UNTESTED)
        self.assertEqual(matrix.total_debt, 2)

    def test_null_identity_hypothesis_spreads_to_all_identities(self) -> None:
        """Legacy/agnostic identity_id=None still spreads (G8 compatibility)."""
        node = _node("n1", identity_ids=("alice", "bob"))
        family = _family("f1", ("EXACT_PATH",))
        graph = AttackSurfaceGraph("run-1", "v1", (node,), ())
        view = CoverageHypothesisView(
            hypothesis_id="h1",
            family_id="f1",
            node_canonical_key="n1",
            identity_id=None,
            highest_tier="V1",
        )
        matrix = compute_coverage_debt(graph, (family,), (view,))
        states = {cell.identity_id: cell.state for cell in matrix.cells}
        self.assertEqual(states["alice"], CoverageState.V1_PASSED)
        self.assertEqual(states["bob"], CoverageState.V1_PASSED)
        self.assertEqual(matrix.total_debt, 2)


if __name__ == "__main__":
    unittest.main()
