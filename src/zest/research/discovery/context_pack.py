"""Bounded graph -> ResearchContext environmental projection. No G23 hypotheses."""

from __future__ import annotations

from typing import Any, Mapping

from zest.research.context import ContextBudget, ObservationSource, ResearchContext, ResearchContextBuilder
from zest.research.discovery.frontier import FrontierItem, reconstruct_state
from zest.research.discovery.graph import AttackSurfaceGraph
from zest.research.discovery.types import (
    FORBIDDEN_DISCOVERY_KEYS,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    AttackSurfaceNodeKind,
    FrontierState,
)
from zest.research.types import ResearchInputError

CONTEXT_FORBIDDEN = FORBIDDEN_DISCOVERY_KEYS | {
    "hidden_route_map",
    "ground_truth",
    "benchmark_truth",
    "vulnerability_class",
    "exploit_path",
}


def pack_surface_discovery_context(
    graph: AttackSurfaceGraph,
    *,
    research_run_id: str,
    research_question: str,
    unexplored_frontier: tuple[FrontierItem, ...],
    frontier_events_by_id: Mapping[str, tuple] | None = None,
    negative_summaries: tuple[str, ...] = (),
    budget: ContextBudget | None = None,
) -> ResearchContext:
    """Environmental information only. Hidden lab truth must not appear here."""

    if graph.strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
        raise ResearchInputError("context pack requires surface.discovery.v1")
    _reject_graph_payload(graph)
    events_by_id = frontier_events_by_id or {}
    observations = _observation_sources(graph)
    frontier_lines = _frontier_lines(unexplored_frontier, events_by_id)
    negative_items = [
        ObservationSource(
            observation_id=f"neg:{index}",
            observation_kind="CONTEXT_BOUND_NEGATIVE",
            payload={"summary": summary, "not_global": True},
        )
        for index, summary in enumerate(negative_summaries)
        if _safe_text(summary)
    ]
    question = (
        research_question
        + "\nKnown paths: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.EXACT_PATH))
        + "\nOperations: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.HTTP_OPERATION))
        + "\nControls: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.CONTROL))
        + "\nForms: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.FORM))
        + "\nInstance candidates: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.RESOURCE_INSTANCE_CANDIDATE))
        + "\nWorkflow: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.WORKFLOW_STATE))
        + "\nResponse shapes: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.RESPONSE_SHAPE))
        + "\nBoundaries: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.SCOPE_BOUNDARY_CANDIDATE))
        + "\nUnexplored frontier: "
        + ",".join(frontier_lines)
        + "\nIdentities: "
        + ",".join(_nodes(graph, AttackSurfaceNodeKind.IDENTITY_REF))
    )
    if any(key in question.lower() for key in ("vulnerability", "exploit", "hidden route map")):
        raise ResearchInputError("surface context must not include vulnerability or hidden truth")
    builder = ResearchContextBuilder()
    return builder.build(
        research_run_id=research_run_id,
        research_question=question,
        observations=tuple(observations + negative_items),
        budget=budget,
    )


def _nodes(graph: AttackSurfaceGraph, kind: AttackSurfaceNodeKind) -> list[str]:
    values: list[str] = []
    for node in graph.nodes:
        if node.kind is not kind:
            continue
        label = ""
        if node.attributes:
            label = str(node.attributes.get("instance_token") or node.attributes.get("method") or "")
        values.append(label or node.canonical_key[:12])
    return values


def _observation_sources(graph: AttackSurfaceGraph) -> list[ObservationSource]:
    items: list[ObservationSource] = []
    for node in graph.nodes:
        payload: dict[str, Any] = {
            "kind": node.kind.value,
            "epistemic_status": node.epistemic_status.value,
            "identity_ids": list(node.identity_ids),
            "provenance_refs": list(node.provenance_refs),
        }
        if node.attributes:
            payload["attributes"] = dict(node.attributes)
        _reject_mapping(payload)
        items.append(
            ObservationSource(
                observation_id=node.node_id[:64],
                observation_kind=f"SURFACE_{node.kind.value}",
                payload=payload,
            )
        )
    return items


def _frontier_lines(items: tuple[FrontierItem, ...], events_by_id: Mapping[str, tuple]) -> list[str]:
    lines: list[str] = []
    for item in items:
        state = reconstruct_state(events_by_id.get(item.frontier_id, ()))
        if state in {FrontierState.OBSERVED, FrontierState.SUPERSEDED, FrontierState.BLOCKED_SCOPE}:
            continue
        lines.append(f"{item.goal_kind.value}:{item.candidate_path}:{item.identity_id}")
    return lines


def _reject_graph_payload(graph: AttackSurfaceGraph) -> None:
    for node in graph.nodes:
        if node.attributes:
            _reject_mapping(node.attributes)


def _reject_mapping(payload: Mapping[str, Any]) -> None:
    found = CONTEXT_FORBIDDEN.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"context payload must not contain {sorted(found)}")
    for value in payload.values():
        if isinstance(value, Mapping):
            _reject_mapping(value)


def _safe_text(value: str) -> bool:
    lowered = value.lower()
    return "vulnerability" not in lowered and "exploit" not in lowered and "hidden_route" not in lowered
