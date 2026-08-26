"""Mutation engine entry point. Scope-confined, deterministic, LLM-free by default."""

from __future__ import annotations

from typing import Any, Mapping

from zest.core.enums import ScopeClassification
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.mutation.families import (
    AuthHeaderVariationFamily,
    BoundaryValueFamily,
    ContentTypeConfusionFamily,
    IdOrTraversalCandidateFamily,
    MethodOverrideFamily,
    ParamPollutionFamily,
    TypeJugglingFamily,
)
from zest.research.mutation.types import MutationFamily, MutationVariant

DEFAULT_MUTATION_FAMILIES: tuple[MutationFamily, ...] = (
    ParamPollutionFamily(),
    TypeJugglingFamily(),
    BoundaryValueFamily(),
    AuthHeaderVariationFamily(),
    MethodOverrideFamily(),
    ContentTypeConfusionFamily(),
    IdOrTraversalCandidateFamily(),
)

SUPPORTED_MUTATION_NODE_KINDS = frozenset(
    {
        AttackSurfaceNodeKind.HTTP_OPERATION,
        AttackSurfaceNodeKind.EXACT_PATH,
    }
)


class MutationEngine:
    """Deterministic variant generator for observed attack-surface nodes."""

    def __init__(
        self,
        families: tuple[MutationFamily, ...] | None = None,
    ) -> None:
        self._families = families if families is not None else DEFAULT_MUTATION_FAMILIES

    def mutate(
        self,
        node: AttackSurfaceNode,
        graph: AttackSurfaceGraph,
        *,
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]:
        return mutate_for_node(
            node,
            graph,
            families=self._families,
            variant_id_prefix=variant_id_prefix,
        )


def mutate_for_node(
    node: AttackSurfaceNode,
    graph: AttackSurfaceGraph,
    *,
    variant_id_prefix: str,
    families: tuple[MutationFamily, ...] | None = None,
) -> tuple[MutationVariant, ...]:
    """Generate deterministic variants for one node. Empty for non-IN_SCOPE nodes (K1)."""
    if node.scope_classification is not ScopeClassification.IN_SCOPE:
        return ()
    if node.kind not in SUPPORTED_MUTATION_NODE_KINDS:
        return ()
    prefix = variant_id_prefix
    provenance: Mapping[str, Any] = {
        "graph_research_run_id": graph.research_run_id,
        "graph_strategy_version": graph.strategy_version,
    }
    active_families = families if families is not None else DEFAULT_MUTATION_FAMILIES
    variants: list[MutationVariant] = []
    for family in active_families:
        variants.extend(family.generate(node, provenance, prefix))
    return tuple(variants)
