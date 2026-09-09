"""Coverage debt computation: deterministic, LLM-free, read-only projection."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Mapping

from zest.core.enums import ScopeClassification
from zest.research.coverage.types import (
    CoverageCell,
    CoverageHypothesisView,
    CoverageMatrix,
    CoverageState,
)
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.selection import HunterFamilyView, families_for_node
from zest.research.types import ResearchInputError


# Sentinel identity for nodes that carry no explicit identity.
ANONYMOUS_IDENTITY = "ANONYMOUS"


def _tier_to_state(tier: str) -> CoverageState:
    return {
        "UNTESTED": CoverageState.UNTESTED,
        "V1": CoverageState.V1_PASSED,
        "V2": CoverageState.V2_PASSED,
        "V3_QUEUED": CoverageState.V3_QUEUED,
        "COVERED": CoverageState.COVERED,
        "REJECTED": CoverageState.NOT_APPLICABLE,
    }.get(tier, CoverageState.UNTESTED)


def _missing_evidence_for_state(state: CoverageState, family: HunterFamilyView) -> tuple[str, ...]:
    """Reason strings for why a cell is not yet covered. No raw secrets."""

    if state is CoverageState.COVERED:
        return ()
    if state is CoverageState.NOT_APPLICABLE:
        return ()
    if state is CoverageState.UNTESTED:
        return ("NO_HYPOTHESIS",)
    if state is CoverageState.HYPOTHESIZED:
        return ("HYPOTHESIS_NOT_VALIDATED",)
    if state is CoverageState.V1_PASSED:
        return ("V2_EVIDENCE_MISSING", f"family:{family.family_id}")
    if state is CoverageState.V2_PASSED:
        return ("V3_APPROVAL_MISSING", f"family:{family.family_id}")
    if state is CoverageState.V3_QUEUED:
        return ("ACTIVE_EXPERIMENT_PENDING", f"family:{family.family_id}")
    return ("UNKNOWN_DEFICIT",)


def _identity_ids_for_node(node: AttackSurfaceNode) -> tuple[str, ...]:
    if node.identity_ids:
        return node.identity_ids
    return (ANONYMOUS_IDENTITY,)


def _cell_key(cell: CoverageCell) -> tuple[str, ...]:
    return (
        cell.node_canonical_key,
        cell.identity_id,
        cell.family_id,
    )


def compute_coverage_debt(
    graph: AttackSurfaceGraph,
    registry: tuple[HunterFamilyView, ...],
    hypotheses_view: tuple[CoverageHypothesisView, ...],
) -> CoverageMatrix:
    """Return a deterministic coverage-debt matrix.

    Only IN_SCOPE nodes produce debt cells. UNKNOWN/OUT_OF_SCOPE nodes are
    emitted as single NOT_APPLICABLE cells per (node, family) pair so they
    remain visible without entering the debt count.

    Identity binding (SD-G9):
    - Hypotheses with identity_id == None are legacy/agnostic: they are spread
      across all identity cells of the matching (node, family) pair.
    - Hypotheses with a non-None identity_id (including "ANONYMOUS") affect
      only their own (node, family, identity) cell.
    """

    if not isinstance(graph, AttackSurfaceGraph):
        raise ResearchInputError("graph must be an AttackSurfaceGraph")

    # Index hypotheses by (node, family, identity) for O(1) lookup.
    # Identity-agnostic entries (identity_id is None) are stored under a
    # separate key and applied to every identity of the node×family pair.
    specific: dict[tuple[str, str, str], CoverageHypothesisView] = {}
    agnostic: dict[tuple[str, str], CoverageHypothesisView] = {}
    for view in hypotheses_view:
        key = (view.node_canonical_key, view.family_id)
        if view.identity_id is None:
            # Keep the highest tier if multiple agnostic rows exist.
            existing = agnostic.get(key)
            if existing is None or _tier_rank(view.highest_tier) > _tier_rank(existing.highest_tier):
                agnostic[key] = view
        else:
            specific[(view.node_canonical_key, view.family_id, view.identity_id)] = view

    cells: list[CoverageCell] = []
    for node in graph.nodes:
        if node.scope_classification is not ScopeClassification.IN_SCOPE:
            # Non-IN_SCOPE nodes are tracked as NOT_APPLICABLE by kind match,
            # not debt. Scope preconditions are ignored here because the node is
            # already outside the active hunt boundary.
            for family in _kind_matched_families(node, registry):
                cells.append(
                    CoverageCell(
                        node_canonical_key=node.canonical_key,
                        identity_id=ANONYMOUS_IDENTITY,
                        family_id=family.family_id,
                        state=CoverageState.NOT_APPLICABLE,
                        missing_evidence=(),
                    )
                )
            continue

        applicable = families_for_node(node, graph, registry)
        identities = _identity_ids_for_node(node)
        for family in applicable:
            for identity_id in identities:
                specific_key = (node.canonical_key, family.family_id, identity_id)
                agnostic_key = (node.canonical_key, family.family_id)
                view = specific.get(specific_key) or agnostic.get(agnostic_key)
                if view is None:
                    state = CoverageState.UNTESTED
                else:
                    state = _tier_to_state(view.highest_tier)
                    if state is CoverageState.UNTESTED and view.hypothesis_id is not None:
                        # A hypothesis exists but has not passed V1 yet.
                        state = CoverageState.HYPOTHESIZED
                cells.append(
                    CoverageCell(
                        node_canonical_key=node.canonical_key,
                        identity_id=identity_id,
                        family_id=family.family_id,
                        state=state,
                        missing_evidence=_missing_evidence_for_state(state, family),
                    )
                )

    # Deterministic ordering and hashing.
    sorted_cells = tuple(sorted(cells, key=_cell_key))
    counts = Counter[str]()
    debt = 0
    for cell in sorted_cells:
        counts[cell.state.value] += 1
        if cell.state in {
            CoverageState.UNTESTED,
            CoverageState.HYPOTHESIZED,
            CoverageState.V1_PASSED,
            CoverageState.V2_PASSED,
            CoverageState.V3_QUEUED,
        }:
            debt += 1

    payload = {
        "research_run_id": graph.research_run_id,
        "strategy_version": graph.strategy_version,
        "cells": [
            {
                "node_canonical_key": cell.node_canonical_key,
                "identity_id": cell.identity_id,
                "family_id": cell.family_id,
                "state": cell.state.value,
                "missing_evidence": list(cell.missing_evidence),
            }
            for cell in sorted_cells
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    matrix_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return CoverageMatrix(
        research_run_id=graph.research_run_id,
        strategy_version=graph.strategy_version,
        cells=sorted_cells,
        cell_counts=dict(sorted(counts.items())),
        total_debt=debt,
        matrix_hash=matrix_hash,
    )


def _tier_rank(tier: str) -> int:
    return {
        "UNTESTED": 0,
        "V1": 1,
        "V2": 2,
        "V3_QUEUED": 3,
        "COVERED": 4,
    }.get(tier, 0)


def _kind_matched_families(
    node: AttackSurfaceNode,
    registry: tuple[HunterFamilyView, ...],
) -> tuple[HunterFamilyView, ...]:
    """Enabled families whose target_node_kinds match the node kind.

    Does not evaluate scope or edge preconditions; used only for marking
    non-IN_SCOPE nodes as NOT_APPLICABLE without fabricating applicability.
    """

    node_kind_value = node.kind.value
    return tuple(
        family
        for family in registry
        if family.enabled and node_kind_value in family.target_node_kinds
    )
