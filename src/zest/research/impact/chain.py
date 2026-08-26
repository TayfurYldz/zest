"""ImpactChain construction and structural validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.impact.types import ImpactGraphError, ImpactKind, ImpactRelation
from zest.research.types import ResearchInputError


@dataclass(frozen=True)
class ImpactScopeRef:
    """Scope boundary for a chain node. Keeps chain nodes inside one program/run."""

    research_run_id: str
    program_id: str
    target_reference: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "program_id", _require_text(self.program_id, "program_id")
        )
        object.__setattr__(
            self,
            "target_reference",
            _require_text(self.target_reference, "target_reference"),
        )


@dataclass(frozen=True)
class ImpactNode:
    """One impact claim node. References one or more proofs."""

    node_id: str
    proof_refs: tuple[str, ...]
    impact_kind: ImpactKind
    claim_text: str
    scope_ref: ImpactScopeRef
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _require_text(self.node_id, "node_id"))
        object.__setattr__(
            self, "proof_refs", _require_ids(self.proof_refs, "proof_refs")
        )
        if not self.proof_refs:
            raise ImpactGraphError("EMPTY_PROOF_REFS")
        if not isinstance(self.impact_kind, ImpactKind):
            raise ResearchInputError("impact_kind must be an ImpactKind")
        object.__setattr__(
            self, "claim_text", _require_text(self.claim_text, "claim_text")
        )
        if not isinstance(self.scope_ref, ImpactScopeRef):
            raise ResearchInputError("scope_ref must be an ImpactScopeRef")
        if not isinstance(self.provenance, Mapping):
            raise ResearchInputError("provenance must be a mapping")
        object.__setattr__(self, "provenance", dict(self.provenance))


@dataclass(frozen=True)
class ImpactEdge:
    """Directed relation between two nodes. The relation itself must be proven."""

    from_node_id: str
    to_node_id: str
    relation: ImpactRelation
    proof_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "from_node_id", _require_text(self.from_node_id, "from_node_id")
        )
        object.__setattr__(
            self, "to_node_id", _require_text(self.to_node_id, "to_node_id")
        )
        if not isinstance(self.relation, ImpactRelation):
            raise ResearchInputError("relation must be an ImpactRelation")
        object.__setattr__(
            self, "proof_refs", _require_ids(self.proof_refs, "proof_refs")
        )
        if not self.proof_refs:
            raise ImpactGraphError("EMPTY_EDGE_PROOF_REFS")


@dataclass(frozen=True)
class ImpactChain:
    """Ordered, acyclic chain of impact nodes connected by edges.

    Structural integrity is enforced at construction time. Proof existence and
    impact-scope rules are validated separately by the validator using a
    ProofResolver.
    """

    chain_id: str
    nodes: tuple[ImpactNode, ...]
    edges: tuple[ImpactEdge, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "chain_id", _require_text(self.chain_id, "chain_id")
        )
        if not isinstance(self.nodes, tuple):
            raise ResearchInputError("nodes must be a tuple")
        if not isinstance(self.edges, tuple):
            raise ResearchInputError("edges must be a tuple")
        if not self.nodes:
            raise ImpactGraphError("CHAIN_HAS_NO_NODES")
        node_ids = {node.node_id for node in self.nodes}
        for edge in self.edges:
            if edge.from_node_id not in node_ids:
                raise ImpactGraphError("DANGLING_EDGE_FROM_NODE")
            if edge.to_node_id not in node_ids:
                raise ImpactGraphError("DANGLING_EDGE_TO_NODE")
            if edge.from_node_id == edge.to_node_id:
                raise ImpactGraphError("SELF_LOOP_NOT_ALLOWED")
        if _has_cycle(node_ids, self.edges):
            raise ImpactGraphError("CHAIN_CONTAINS_CYCLE")


def _has_cycle(node_ids: set[str], edges: tuple[ImpactEdge, ...]) -> bool:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        adjacency[edge.from_node_id].add(edge.to_node_id)
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def visit(node_id: str) -> bool:
        visited.add(node_id)
        rec_stack.add(node_id)
        for neighbor in adjacency[node_id]:
            if neighbor not in visited:
                if visit(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(node_id)
        return False

    for node_id in node_ids:
        if node_id not in visited:
            if visit(node_id):
                return True
    return False


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        cleaned.append(_require_text(item, f"{field_name}[{index}]"))
    return tuple(cleaned)
