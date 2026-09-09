"""Rebuildable Attack Surface Graph. Not SoR. Grants nothing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from zest.core.enums import ScopeClassification
from zest.research.discovery.facts import DiscoveryFact
from zest.research.discovery.inference import DiscoveryInference
from zest.research.discovery.types import (
    AttackSurfaceEdgeKind,
    AttackSurfaceNodeKind,
    DiscoveryFactKind,
    DiscoveryInferenceKind,
)
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError

FACT_NODE_KIND = {
    DiscoveryFactKind.ORIGIN: AttackSurfaceNodeKind.ORIGIN,
    DiscoveryFactKind.EXACT_PATH: AttackSurfaceNodeKind.EXACT_PATH,
    DiscoveryFactKind.HTTP_OPERATION: AttackSurfaceNodeKind.HTTP_OPERATION,
    DiscoveryFactKind.PAGE_STATE: AttackSurfaceNodeKind.PAGE_STATE,
    DiscoveryFactKind.CONTROL: AttackSurfaceNodeKind.CONTROL,
    DiscoveryFactKind.FORM: AttackSurfaceNodeKind.FORM,
    DiscoveryFactKind.RESPONSE_SHAPE: AttackSurfaceNodeKind.RESPONSE_SHAPE,
    DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE: AttackSurfaceNodeKind.RESOURCE_INSTANCE_CANDIDATE,
    DiscoveryFactKind.WORKFLOW_STATE: AttackSurfaceNodeKind.WORKFLOW_STATE,
    DiscoveryFactKind.WORKFLOW_TRANSITION: AttackSurfaceNodeKind.WORKFLOW_TRANSITION,
    DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE: AttackSurfaceNodeKind.SCOPE_BOUNDARY_CANDIDATE,
    # SD-G3 sensor-derived external census kinds.
    DiscoveryFactKind.DOMAIN: AttackSurfaceNodeKind.DOMAIN,
    DiscoveryFactKind.HOSTNAME: AttackSurfaceNodeKind.HOSTNAME,
    DiscoveryFactKind.CERT: AttackSurfaceNodeKind.CERT,
    DiscoveryFactKind.SERVICE: AttackSurfaceNodeKind.SERVICE,
    DiscoveryFactKind.TECH: AttackSurfaceNodeKind.TECH,
    DiscoveryFactKind.JS_BUNDLE: AttackSurfaceNodeKind.JS_BUNDLE,
    DiscoveryFactKind.API_SPEC: AttackSurfaceNodeKind.API_SPEC,
}

INFERENCE_NODE_KIND = {
    DiscoveryInferenceKind.ROUTE_TEMPLATE: AttackSurfaceNodeKind.ROUTE_TEMPLATE,
    DiscoveryInferenceKind.OBJECT_TYPE: AttackSurfaceNodeKind.OBJECT_TYPE,
    DiscoveryInferenceKind.OBJECT_INSTANCE: AttackSurfaceNodeKind.OBJECT_INSTANCE,
    DiscoveryInferenceKind.SAME_AS: AttackSurfaceNodeKind.OBJECT_INSTANCE,
}


def _scope_classification_from_fact(fact: DiscoveryFact) -> ScopeClassification:
    attrs = fact.attributes or {}
    value = attrs.get("scope_classification")
    if value is None:
        return ScopeClassification.UNKNOWN
    try:
        return ScopeClassification(value)
    except ValueError as exc:
        raise ResearchInputError(f"invalid scope_classification {value!r}") from exc


def _node_epistemic_status(fact: DiscoveryFact) -> TargetEpistemicStatus:
    """Keep sensor-sourced nodes marked UNTRUSTED_EXTERNAL; do not launder them."""
    if any(source.sensor_observation_id is not None for source in fact.sources):
        return TargetEpistemicStatus.UNTRUSTED_EXTERNAL
    return fact.epistemic_status


@dataclass(frozen=True)
class AttackSurfaceNode:
    node_id: str
    kind: AttackSurfaceNodeKind
    canonical_key: str
    epistemic_status: TargetEpistemicStatus
    identity_ids: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    scope_classification: ScopeClassification
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AttackSurfaceNodeKind):
            raise ResearchInputError("kind must be AttackSurfaceNodeKind")
        if not isinstance(self.scope_classification, ScopeClassification):
            raise ResearchInputError("scope_classification must be ScopeClassification")


@dataclass(frozen=True)
class AttackSurfaceEdge:
    edge_id: str
    kind: AttackSurfaceEdgeKind
    from_node_id: str
    to_node_id: str
    identity_id: str | None
    provenance_refs: tuple[str, ...]
    epistemic_status: TargetEpistemicStatus

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AttackSurfaceEdgeKind):
            raise ResearchInputError("kind must be AttackSurfaceEdgeKind")
        if self.kind is AttackSurfaceEdgeKind.SAME_AS and self.epistemic_status not in {
            TargetEpistemicStatus.INFERRED,
            TargetEpistemicStatus.HYPOTHESIZED,
        }:
            raise ResearchInputError("SAME_AS requires admitted inference")


@dataclass(frozen=True)
class AttackSurfaceGraph:
    """Rebuildable Research projection. Cannot grant scope, session, budget, or capability."""

    research_run_id: str
    strategy_version: str
    nodes: tuple[AttackSurfaceNode, ...]
    edges: tuple[AttackSurfaceEdge, ...]

    def node_by_canonical(self, canonical_key: str) -> AttackSurfaceNode | None:
        for node in self.nodes:
            if node.canonical_key == canonical_key:
                return node
        return None

    def grants_scope(self) -> bool:
        return False

    def binds_session(self) -> bool:
        return False

    def mints_budget(self) -> bool:
        return False

    def mints_capability(self) -> bool:
        return False


def graph_hash(graph: AttackSurfaceGraph) -> str:
    """Deterministic SHA-256 hash of ordered node+edge lists."""
    payload = {
        "research_run_id": graph.research_run_id,
        "strategy_version": graph.strategy_version,
        "nodes": [asdict(node) for node in graph.nodes],
        "edges": [asdict(edge) for edge in graph.edges],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rebuild_attack_surface_graph(
    *,
    research_run_id: str,
    strategy_version: str,
    facts: tuple[DiscoveryFact, ...],
    inferences: tuple[DiscoveryInference, ...] = (),
    workflow_edges: tuple[AttackSurfaceEdge, ...] = (),
) -> AttackSurfaceGraph:
    nodes: dict[str, AttackSurfaceNode] = {}
    edges: list[AttackSurfaceEdge] = []

    def _add_node(node: AttackSurfaceNode) -> None:
        existing = nodes.get(node.canonical_key)
        if existing is None:
            nodes[node.canonical_key] = node
            return
        merged_ids = tuple(sorted(set(existing.identity_ids) | set(node.identity_ids)))
        merged_refs = tuple(sorted(set(existing.provenance_refs) | set(node.provenance_refs)))
        # Sensor-sourced nodes preserve UNTRUSTED_EXTERNAL even on merge.
        merged_scope = existing.scope_classification
        if node.scope_classification is ScopeClassification.OUT_OF_SCOPE:
            merged_scope = ScopeClassification.OUT_OF_SCOPE
        elif (
            merged_scope is ScopeClassification.UNKNOWN
            and node.scope_classification is ScopeClassification.IN_SCOPE
        ):
            merged_scope = ScopeClassification.IN_SCOPE
        nodes[node.canonical_key] = AttackSurfaceNode(
            node_id=existing.node_id,
            kind=existing.kind,
            canonical_key=existing.canonical_key,
            epistemic_status=existing.epistemic_status,
            identity_ids=merged_ids,
            provenance_refs=merged_refs,
            scope_classification=merged_scope,
            attributes=existing.attributes,
        )

    origin_nodes: dict[str, str] = {}
    path_nodes: dict[tuple[str, str], str] = {}
    hostname_nodes: dict[str, str] = {}
    cert_nodes: dict[str, str] = {}
    tech_nodes: dict[str, str] = {}
    service_nodes: dict[str, str] = {}
    bundle_nodes: dict[str, str] = {}
    spec_nodes: dict[str, str] = {}

    for fact in sorted(facts, key=lambda item: item.canonical_key):
        kind = FACT_NODE_KIND.get(fact.fact_kind)
        if kind is None:
            raise ResearchInputError(
                f"unmapped DiscoveryFactKind {fact.fact_kind.value} in attack surface graph rebuild"
            )
        provenance = tuple(sorted(_source_ref(source) for source in fact.sources))
        scope_classification = _scope_classification_from_fact(fact)
        node_epistemic = _node_epistemic_status(fact)
        _add_node(
            AttackSurfaceNode(
                node_id=fact.canonical_key,
                kind=kind,
                canonical_key=fact.canonical_key,
                epistemic_status=node_epistemic,
                identity_ids=(fact.identity_id,),
                provenance_refs=provenance,
                scope_classification=scope_classification,
                attributes=_node_attributes_from_fact(fact),
            )
        )
        if fact.identity_id:
            identity_key = f"identity:{fact.identity_id}"
            _add_node(
                AttackSurfaceNode(
                    node_id=identity_key,
                    kind=AttackSurfaceNodeKind.IDENTITY_REF,
                    canonical_key=identity_key,
                    epistemic_status=TargetEpistemicStatus.OBSERVED,
                    identity_ids=(fact.identity_id,),
                    provenance_refs=provenance,
                    scope_classification=ScopeClassification.IN_SCOPE,
                )
            )
            edges.append(
                AttackSurfaceEdge(
                    edge_id=f"{fact.canonical_key}:under:{fact.identity_id}",
                    kind=AttackSurfaceEdgeKind.OBSERVED_UNDER,
                    from_node_id=fact.canonical_key,
                    to_node_id=identity_key,
                    identity_id=fact.identity_id,
                    provenance_refs=provenance,
                    epistemic_status=fact.epistemic_status,
                )
            )
        if fact.session_context_id:
            session_key = f"session:{fact.session_context_id}"
            _add_node(
                AttackSurfaceNode(
                    node_id=session_key,
                    kind=AttackSurfaceNodeKind.SESSION_REF,
                    canonical_key=session_key,
                    epistemic_status=TargetEpistemicStatus.OBSERVED,
                    identity_ids=(fact.identity_id,),
                    provenance_refs=provenance,
                    scope_classification=ScopeClassification.IN_SCOPE,
                )
            )

        # Index sensor and application-derived nodes for edge wiring.
        if fact.fact_kind is DiscoveryFactKind.ORIGIN and fact.normalized_origin:
            origin_nodes[fact.normalized_origin] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.EXACT_PATH and fact.normalized_origin and fact.normalized_path:
            path_nodes[(fact.normalized_origin, fact.normalized_path)] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.HOSTNAME:
            hostname = _hostname_from_attributes(fact.attributes) or fact.canonical_key.split(":")[-1]
            if hostname:
                hostname_nodes[hostname] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.CERT:
            cert_nodes[_cert_key(fact)] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.TECH:
            tech_nodes[_tech_key(fact)] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.SERVICE:
            service_nodes[_service_key(fact)] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.JS_BUNDLE:
            bundle_nodes[_bundle_key(fact)] = fact.canonical_key
        if fact.fact_kind is DiscoveryFactKind.API_SPEC:
            spec_nodes[_spec_key(fact)] = fact.canonical_key

        # Existing application-derived edges.
        if fact.fact_kind is DiscoveryFactKind.EXACT_PATH and fact.normalized_origin and fact.normalized_path:
            origin_id = origin_nodes.get(fact.normalized_origin)
            if origin_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{origin_id}:contains:{fact.canonical_key}",
                        kind=AttackSurfaceEdgeKind.CONTAINS,
                        from_node_id=origin_id,
                        to_node_id=fact.canonical_key,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=fact.epistemic_status,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.HTTP_OPERATION and fact.normalized_origin and fact.normalized_path:
            path_id = path_nodes.get((fact.normalized_origin, fact.normalized_path))
            if path_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{path_id}:req:{fact.canonical_key}",
                        kind=AttackSurfaceEdgeKind.OBSERVED_REQUEST_TO,
                        from_node_id=path_id,
                        to_node_id=fact.canonical_key,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=fact.epistemic_status,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE and fact.normalized_origin:
            origin_id = origin_nodes.get(fact.normalized_origin)
            if origin_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{fact.canonical_key}:boundary:{origin_id}",
                        kind=AttackSurfaceEdgeKind.BOUNDARY_OF,
                        from_node_id=fact.canonical_key,
                        to_node_id=origin_id,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=TargetEpistemicStatus.DERIVED,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE:
            path_id = path_nodes.get((fact.normalized_origin or "", fact.normalized_path or ""))
            if path_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{path_id}:instance:{fact.canonical_key}",
                        kind=AttackSurfaceEdgeKind.INSTANCE_OF,
                        from_node_id=fact.canonical_key,
                        to_node_id=path_id,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=fact.epistemic_status,
                    )
                )

    # SD-G3 sensor-derived edges.
    for fact in sorted(facts, key=lambda item: item.canonical_key):
        provenance = tuple(sorted(_source_ref(source) for source in fact.sources))
        if fact.fact_kind is DiscoveryFactKind.HOSTNAME:
            hostname = _hostname_from_attributes(fact.attributes) or fact.canonical_key.split(":")[-1]
            origin_id = origin_nodes.get(fact.normalized_origin or "")
            if hostname and origin_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{fact.canonical_key}:resolves:{origin_id}",
                        kind=AttackSurfaceEdgeKind.RESOLVES_TO,
                        from_node_id=fact.canonical_key,
                        to_node_id=origin_id,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.CERT:
            for hostname in _cert_hostnames(fact):
                hostname_id = hostname_nodes.get(hostname)
                if hostname_id:
                    edges.append(
                        AttackSurfaceEdge(
                            edge_id=f"{hostname_id}:securedby:{fact.canonical_key}",
                            kind=AttackSurfaceEdgeKind.SECURED_BY,
                            from_node_id=hostname_id,
                            to_node_id=fact.canonical_key,
                            identity_id=fact.identity_id,
                            provenance_refs=provenance,
                            epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                        )
                    )
        if fact.fact_kind is DiscoveryFactKind.TECH:
            for origin in _tech_origins(fact):
                origin_id = origin_nodes.get(origin)
                if origin_id:
                    edges.append(
                        AttackSurfaceEdge(
                            edge_id=f"{origin_id}:runs:{fact.canonical_key}",
                            kind=AttackSurfaceEdgeKind.RUNS,
                            from_node_id=origin_id,
                            to_node_id=fact.canonical_key,
                            identity_id=fact.identity_id,
                            provenance_refs=provenance,
                            epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                        )
                    )
        if fact.fact_kind is DiscoveryFactKind.JS_BUNDLE:
            path_key = _bundle_path(fact)
            path_id = path_nodes.get(path_key) if path_key else None
            if path_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{path_id}:references:{fact.canonical_key}",
                        kind=AttackSurfaceEdgeKind.REFERENCES,
                        from_node_id=path_id,
                        to_node_id=fact.canonical_key,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.API_SPEC:
            path_key = _spec_path(fact)
            path_id = path_nodes.get(path_key) if path_key else None
            if path_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{path_id}:references:{fact.canonical_key}",
                        kind=AttackSurfaceEdgeKind.REFERENCES,
                        from_node_id=path_id,
                        to_node_id=fact.canonical_key,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                    )
                )
        if fact.fact_kind is DiscoveryFactKind.SERVICE:
            hostname = _service_hostname(fact)
            hostname_id = hostname_nodes.get(hostname) if hostname else None
            if hostname_id:
                edges.append(
                    AttackSurfaceEdge(
                        edge_id=f"{fact.canonical_key}:hostedon:{hostname_id}",
                        kind=AttackSurfaceEdgeKind.HOSTED_ON,
                        from_node_id=fact.canonical_key,
                        to_node_id=hostname_id,
                        identity_id=fact.identity_id,
                        provenance_refs=provenance,
                        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
                    )
                )

    fact_id_to_key = {item.fact_id: item.canonical_key for item in facts}
    for fact in sorted(facts, key=lambda item: item.canonical_key):
        if fact.fact_kind is not DiscoveryFactKind.WORKFLOW_TRANSITION:
            continue
        attrs = fact.attributes or {}
        pre_id = attrs.get("pre_state_fact_id")
        pre_key = fact_id_to_key.get(pre_id) if isinstance(pre_id, str) else None
        path_id = path_nodes.get((fact.normalized_origin or "", fact.normalized_path or ""))
        if pre_key and path_id:
            provenance = tuple(sorted(_source_ref(source) for source in fact.sources))
            edges.append(
                AttackSurfaceEdge(
                    edge_id=f"{pre_key}:transitions:{fact.canonical_key}",
                    kind=AttackSurfaceEdgeKind.TRANSITIONS_TO,
                    from_node_id=pre_key,
                    to_node_id=path_id,
                    identity_id=fact.identity_id,
                    provenance_refs=provenance,
                    epistemic_status=fact.epistemic_status,
                )
            )

    for inference in sorted(inferences, key=lambda item: item.canonical_key):
        kind = INFERENCE_NODE_KIND.get(inference.inference_kind)
        if kind is None:
            raise ResearchInputError(
                f"unmapped DiscoveryInferenceKind {inference.inference_kind.value} in attack surface graph rebuild"
            )
        provenance = tuple(sorted(inference.source_fact_ids + inference.source_observation_ids))
        _add_node(
            AttackSurfaceNode(
                node_id=inference.canonical_key,
                kind=kind,
                canonical_key=inference.canonical_key,
                epistemic_status=inference.epistemic_status,
                identity_ids=(inference.identity_id,),
                provenance_refs=provenance,
                scope_classification=ScopeClassification.IN_SCOPE,
                attributes=inference.attributes,
            )
        )
        if inference.inference_kind is DiscoveryInferenceKind.ROUTE_TEMPLATE:
            exact_paths = []
            if inference.attributes and isinstance(inference.attributes.get("exact_paths"), list):
                exact_paths = list(inference.attributes["exact_paths"])
            for path in exact_paths:
                for (origin, exact), path_id in path_nodes.items():
                    if exact == path:
                        edges.append(
                            AttackSurfaceEdge(
                                edge_id=f"{path_id}:variant:{inference.canonical_key}",
                                kind=AttackSurfaceEdgeKind.VARIANT_OF,
                                from_node_id=path_id,
                                to_node_id=inference.canonical_key,
                                identity_id=inference.identity_id,
                                provenance_refs=provenance,
                                epistemic_status=inference.epistemic_status,
                            )
                        )
        if inference.inference_kind is DiscoveryInferenceKind.SAME_AS:
            edges.append(
                AttackSurfaceEdge(
                    edge_id=f"same:{inference.canonical_key}",
                    kind=AttackSurfaceEdgeKind.SAME_AS,
                    from_node_id=inference.canonical_key,
                    to_node_id=inference.canonical_key,
                    identity_id=inference.identity_id,
                    provenance_refs=provenance,
                    epistemic_status=inference.epistemic_status,
                )
            )

    for edge in workflow_edges:
        if edge.kind is not AttackSurfaceEdgeKind.TRANSITIONS_TO:
            raise ResearchInputError("workflow_edges may only supply TRANSITIONS_TO")
        edges.append(edge)

    ordered_nodes = tuple(sorted(nodes.values(), key=lambda item: (item.kind.value, item.canonical_key)))
    ordered_edges = tuple(sorted(edges, key=lambda item: item.edge_id))
    return AttackSurfaceGraph(
        research_run_id=research_run_id,
        strategy_version=strategy_version,
        nodes=ordered_nodes,
        edges=ordered_edges,
    )


def _source_ref(source) -> str:
    if source.observation_id:
        return f"observation:{source.observation_id}"
    if source.sensor_observation_id:
        return f"sensor_observation:{source.sensor_observation_id}"
    if source.control_event_id:
        return f"control_event:{source.control_event_id}"
    if source.source_fact_id:
        return f"fact:{source.source_fact_id}"
    return f"inference:{source.source_inference_id}"


def _hostname_from_attributes(attributes: Mapping[str, Any] | None) -> str | None:
    if not attributes:
        return None
    for key in ("hostname", "name", "host", "subject_hostname"):
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _node_attributes_from_fact(fact: DiscoveryFact) -> dict[str, Any] | None:
    """Project SoR fact fields onto graph node attributes. Does not invent identities."""

    attributes = dict(fact.attributes or {})
    if fact.normalized_origin and not str(attributes.get("origin") or "").strip():
        attributes["origin"] = fact.normalized_origin
        attributes.setdefault("authorized_origin", fact.normalized_origin)
    if fact.normalized_path and not str(attributes.get("path") or "").strip():
        attributes["path"] = fact.normalized_path
    if fact.http_method and not str(attributes.get("method") or "").strip():
        attributes["method"] = fact.http_method
    return attributes or None


def _cert_key(fact: DiscoveryFact) -> str:
    return fact.canonical_key


def _cert_hostnames(fact: DiscoveryFact) -> tuple[str, ...]:
    attrs = fact.attributes or {}
    hostnames: list[str] = []
    for key in ("san", "subject_alt_names", "hostnames"):
        value = attrs.get(key)
        if isinstance(value, list):
            hostnames.extend(str(item) for item in value if isinstance(item, str) and item.strip())
    single = _hostname_from_attributes(attrs)
    if single:
        hostnames.append(single)
    normalized_origin = fact.normalized_origin
    if normalized_origin:
        from urllib.parse import urlsplit
        host = urlsplit(normalized_origin).hostname
        if host and host not in hostnames:
            hostnames.append(host)
    return tuple(sorted(set(hostnames)))


def _tech_key(fact: DiscoveryFact) -> str:
    return fact.canonical_key


def _tech_origins(fact: DiscoveryFact) -> tuple[str, ...]:
    attrs = fact.attributes or {}
    origins: list[str] = []
    for key in ("origins", "origin"):
        value = attrs.get(key)
        if isinstance(value, list):
            origins.extend(str(item) for item in value if isinstance(item, str) and item.strip())
        elif isinstance(value, str) and value.strip():
            origins.append(value.strip())
    if fact.normalized_origin and fact.normalized_origin not in origins:
        origins.append(fact.normalized_origin)
    return tuple(sorted(set(origins)))


def _service_key(fact: DiscoveryFact) -> str:
    return fact.canonical_key


def _service_hostname(fact: DiscoveryFact) -> str | None:
    attrs = fact.attributes or {}
    for key in ("hostname", "host"):
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if fact.normalized_origin:
        from urllib.parse import urlsplit
        return urlsplit(fact.normalized_origin).hostname
    return None


def _bundle_key(fact: DiscoveryFact) -> str:
    return fact.canonical_key


def _bundle_path(fact: DiscoveryFact) -> tuple[str, str] | None:
    attrs = fact.attributes or {}
    origin = attrs.get("origin") or fact.normalized_origin
    path = attrs.get("path") or fact.normalized_path
    if isinstance(origin, str) and isinstance(path, str):
        return (origin, path)
    return None


def _spec_key(fact: DiscoveryFact) -> str:
    return fact.canonical_key


def _spec_path(fact: DiscoveryFact) -> tuple[str, str] | None:
    attrs = fact.attributes or {}
    origin = attrs.get("origin") or fact.normalized_origin
    path = attrs.get("path") or fact.normalized_path
    if isinstance(origin, str) and isinstance(path, str):
        return (origin, path)
    return None
