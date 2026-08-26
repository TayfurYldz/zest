"""Read-only attack surface summary views. Not SoR authority."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from zest.data.unit_of_work import UnitOfWork
from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.graph import (
    AttackSurfaceGraph,
    graph_hash,
    rebuild_attack_surface_graph,
)
from zest.research.discovery.inference import DiscoveryInference
from zest.research.discovery.types import (
    DiscoveryFactKind,
    DiscoveryInferenceKind,
    DiscoverySourcePlane,
)
from zest.research.target_model import TargetEpistemicStatus


@dataclass(frozen=True)
class AttackSurfaceSummary:
    """Deterministic summary of an attack surface graph projection."""

    research_run_id: str
    strategy_version: str
    node_count: int
    edge_count: int
    graph_hash: str
    kind_counts: dict[str, int]
    identity_node_counts: dict[str, int]
    scope_classification_counts: dict[str, int]


def summarize_attack_surface(
    uow: UnitOfWork,
    research_run_id: str,
    *,
    strategy_version: str = "surface.discovery.v1",
) -> AttackSurfaceSummary:
    """Rebuild the graph from the discovery ledger and produce a deterministic summary."""

    facts = uow.discovery_facts.list_for_research_run(research_run_id)
    inferences = uow.discovery_inferences.list_for_research_run(research_run_id)
    domain_facts = tuple(_fact_from_record(uow, row) for row in facts)
    domain_inferences = tuple(_inference_from_record(row) for row in inferences)
    graph = rebuild_attack_surface_graph(
        research_run_id=research_run_id,
        strategy_version=strategy_version,
        facts=domain_facts,
        inferences=domain_inferences,
    )
    return summarize_graph(graph)


def summarize_graph(graph: AttackSurfaceGraph) -> AttackSurfaceSummary:
    kind_counts: Counter[str] = Counter()
    identity_node_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    for node in graph.nodes:
        kind_counts[node.kind.value] += 1
        scope_counts[node.scope_classification.value] += 1
        for identity_id in node.identity_ids:
            identity_node_counts[identity_id] += 1
    return AttackSurfaceSummary(
        research_run_id=graph.research_run_id,
        strategy_version=graph.strategy_version,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        graph_hash=graph_hash(graph),
        kind_counts=dict(sorted(kind_counts.items())),
        identity_node_counts=dict(sorted(identity_node_counts.items())),
        scope_classification_counts=dict(sorted(scope_counts.items())),
    )


def _derive_source_plane(source) -> DiscoverySourcePlane | None:
    if source.observation_id is not None or source.sensor_observation_id is not None:
        return DiscoverySourcePlane.OBSERVATION
    if source.control_event_id is not None:
        return DiscoverySourcePlane.CONTROL_EVENT
    return None


def _fact_from_record(uow: UnitOfWork, record) -> DiscoveryFact:
    sources = uow.discovery_fact_sources.list_for_fact(record.fact_id)
    return DiscoveryFact(
        fact_id=record.fact_id,
        research_run_id=record.research_run_id,
        fact_kind=DiscoveryFactKind(record.fact_kind),
        canonical_key=record.canonical_key,
        epistemic_status=TargetEpistemicStatus(record.epistemic_status),
        identity_id=record.identity_id,
        target_reference=record.target_reference,
        sources=tuple(
            DiscoveryFactSourceView(
                source_plane=_derive_source_plane(source),
                observation_id=source.observation_id,
                sensor_observation_id=source.sensor_observation_id,
                control_event_id=source.control_event_id,
                source_fact_id=source.source_fact_id,
                source_inference_id=source.source_inference_id,
            )
            for source in sources
        ),
        session_context_id=record.session_context_id,
        normalized_origin=record.normalized_origin,
        normalized_path=record.normalized_path,
        http_method=record.http_method,
        attributes=record.attributes,
    )


def _inference_from_record(record) -> DiscoveryInference:
    return DiscoveryInference(
        inference_id=record.inference_id,
        research_run_id=record.research_run_id,
        inference_kind=DiscoveryInferenceKind(record.inference_kind),
        canonical_key=record.canonical_key,
        epistemic_status=TargetEpistemicStatus(record.epistemic_status),
        identity_id=record.identity_id,
        source_fact_ids=record.source_fact_ids,
        source_inference_ids=record.source_inference_ids,
        source_observation_ids=record.source_observation_ids,
        attributes=record.attributes,
    )
