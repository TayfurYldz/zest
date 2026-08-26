"""SD-G3 attack surface graph v2 unit tests."""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.graph import (
    AttackSurfaceEdgeKind,
    AttackSurfaceGraph,
    AttackSurfaceNodeKind,
    graph_hash,
    rebuild_attack_surface_graph,
)
from zest.research.discovery.types import (
    DiscoveryFactKind,
    DiscoverySourcePlane,
)
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError


NOW = datetime.now(timezone.utc)


def _source(*, observation_id: str | None = None, sensor_observation_id: str | None = None):
    if observation_id is None and sensor_observation_id is None:
        observation_id = "obs-1"
    return DiscoveryFactSourceView(
        source_plane=DiscoverySourcePlane.OBSERVATION,
        observation_id=observation_id,
        sensor_observation_id=sensor_observation_id,
    )


def _fact(
    fact_id: str,
    fact_kind: DiscoveryFactKind,
    canonical_key: str,
    *,
    identity_id: str = "ANONYMOUS",
    target_reference: str = "https://example.com",
    normalized_origin: str | None = "https://example.com",
    normalized_path: str | None = None,
    attributes: dict | None = None,
    sensor_observation_id: str | None = None,
    observation_id: str | None = None,
    epistemic_status: TargetEpistemicStatus = TargetEpistemicStatus.OBSERVED,
):
    return DiscoveryFact(
        fact_id=fact_id,
        research_run_id="run-1",
        fact_kind=fact_kind,
        canonical_key=canonical_key,
        epistemic_status=epistemic_status,
        identity_id=identity_id,
        target_reference=target_reference,
        sources=(_source(observation_id=observation_id, sensor_observation_id=sensor_observation_id),),
        normalized_origin=normalized_origin,
        normalized_path=normalized_path,
        attributes=attributes,
    )


class SensorNodeMappingTests(unittest.TestCase):
    def test_all_sensor_kinds_produce_nodes(self) -> None:
        facts = (
            _fact("f-domain", DiscoveryFactKind.DOMAIN, "sensor.dns:DOMAIN:example.com"),
            _fact("f-hostname", DiscoveryFactKind.HOSTNAME, "sensor.dns:HOSTNAME:www.example.com"),
            _fact("f-cert", DiscoveryFactKind.CERT, "sensor.cert:CERT:example.com"),
            _fact("f-service", DiscoveryFactKind.SERVICE, "sensor.cert:SERVICE:example.com:443"),
            _fact("f-tech", DiscoveryFactKind.TECH, "sensor.techfp:TECH:example.com"),
            _fact("f-bundle", DiscoveryFactKind.JS_BUNDLE, "sensor.archive:JS_BUNDLE:example.com/bundle.js"),
            _fact("f-spec", DiscoveryFactKind.API_SPEC, "sensor.archive:API_SPEC:example.com/api/openapi.json"),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=facts,
        )
        kinds = {node.kind for node in graph.nodes}
        for expected in (
            AttackSurfaceNodeKind.DOMAIN,
            AttackSurfaceNodeKind.HOSTNAME,
            AttackSurfaceNodeKind.CERT,
            AttackSurfaceNodeKind.SERVICE,
            AttackSurfaceNodeKind.TECH,
            AttackSurfaceNodeKind.JS_BUNDLE,
            AttackSurfaceNodeKind.API_SPEC,
        ):
            self.assertIn(expected, kinds)

    def test_unmapped_fact_kind_raises(self) -> None:
        # Simulate an unmapped kind by injecting an invalid value through the enum boundary.
        class FakeKind:
            value = "NOT_A_KIND"

        fact = DiscoveryFact(
            fact_id="f-bad",
            research_run_id="run-1",
            fact_kind=DiscoveryFactKind.ORIGIN,
            canonical_key="bad",
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            identity_id="ANONYMOUS",
            target_reference="https://example.com",
            sources=(_source(observation_id="obs-1"),),
        )
        # Directly mutate the mapping to simulate a future unmapped kind.
        from zest.research.discovery import graph as graph_module

        original = dict(graph_module.FACT_NODE_KIND)
        graph_module.FACT_NODE_KIND = {}
        try:
            with self.assertRaises(ResearchInputError):
                rebuild_attack_surface_graph(
                    research_run_id="run-1",
                    strategy_version="surface.discovery.v1",
                    facts=(fact,),
                )
        finally:
            graph_module.FACT_NODE_KIND = original

    def test_sensor_sourced_nodes_keep_untrusted_external(self) -> None:
        fact = _fact(
            "f-hostname",
            DiscoveryFactKind.HOSTNAME,
            "sensor.dns:HOSTNAME:www.example.com",
            sensor_observation_id="so-1",
            attributes={"hostname": "www.example.com", "scope_classification": "IN_SCOPE"},
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=(fact,),
        )
        node = graph.node_by_canonical(fact.canonical_key)
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.epistemic_status, TargetEpistemicStatus.UNTRUSTED_EXTERNAL)

    def test_non_sensor_nodes_keep_admitted_epistemic_status(self) -> None:
        fact = _fact(
            "f-origin",
            DiscoveryFactKind.ORIGIN,
            "origin:https://example.com",
            observation_id="obs-1",
            epistemic_status=TargetEpistemicStatus.OBSERVED,
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=(fact,),
        )
        node = graph.node_by_canonical(fact.canonical_key)
        self.assertIsNotNone(node)
        assert node is not None
        self.assertEqual(node.epistemic_status, TargetEpistemicStatus.OBSERVED)

    def test_scope_classification_carried_to_node(self) -> None:
        fact = _fact(
            "f-hostname",
            DiscoveryFactKind.HOSTNAME,
            "sensor.dns:HOSTNAME:www.example.com",
            sensor_observation_id="so-1",
            attributes={"scope_classification": "OUT_OF_SCOPE"},
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=(fact,),
        )
        node = graph.node_by_canonical(fact.canonical_key)
        self.assertIsNotNone(node)
        assert node is not None
        from zest.core.enums import ScopeClassification
        self.assertEqual(node.scope_classification, ScopeClassification.OUT_OF_SCOPE)

    def test_unknown_scope_classification_default(self) -> None:
        fact = _fact(
            "f-hostname",
            DiscoveryFactKind.HOSTNAME,
            "sensor.dns:HOSTNAME:www.example.com",
            sensor_observation_id="so-1",
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=(fact,),
        )
        node = graph.node_by_canonical(fact.canonical_key)
        self.assertIsNotNone(node)
        assert node is not None
        from zest.core.enums import ScopeClassification
        self.assertEqual(node.scope_classification, ScopeClassification.UNKNOWN)


class SensorEdgeMappingTests(unittest.TestCase):
    def test_hostname_resolves_to_origin(self) -> None:
        facts = (
            _fact(
                "f-origin",
                DiscoveryFactKind.ORIGIN,
                "origin:https://example.com",
                observation_id="obs-1",
                normalized_origin="https://example.com",
            ),
            _fact(
                "f-hostname",
                DiscoveryFactKind.HOSTNAME,
                "sensor.dns:HOSTNAME:www.example.com",
                sensor_observation_id="so-1",
                normalized_origin="https://example.com",
                attributes={"hostname": "www.example.com"},
            ),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=facts,
        )
        edge_kinds = {edge.kind for edge in graph.edges}
        self.assertIn(AttackSurfaceEdgeKind.RESOLVES_TO, edge_kinds)

    def test_cert_secures_hostname(self) -> None:
        facts = (
            _fact(
                "f-hostname",
                DiscoveryFactKind.HOSTNAME,
                "sensor.dns:HOSTNAME:www.example.com",
                sensor_observation_id="so-1",
                attributes={"hostname": "www.example.com"},
            ),
            _fact(
                "f-cert",
                DiscoveryFactKind.CERT,
                "sensor.cert:CERT:example.com",
                sensor_observation_id="so-2",
                attributes={"subject_hostname": "www.example.com"},
            ),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=facts,
        )
        edge_kinds = {edge.kind for edge in graph.edges}
        self.assertIn(AttackSurfaceEdgeKind.SECURED_BY, edge_kinds)

    def test_tech_runs_on_origin(self) -> None:
        facts = (
            _fact(
                "f-origin",
                DiscoveryFactKind.ORIGIN,
                "origin:https://example.com",
                observation_id="obs-1",
                normalized_origin="https://example.com",
            ),
            _fact(
                "f-tech",
                DiscoveryFactKind.TECH,
                "sensor.techfp:TECH:example.com",
                sensor_observation_id="so-1",
                normalized_origin="https://example.com",
                attributes={"technology": "nginx"},
            ),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=facts,
        )
        edge_kinds = {edge.kind for edge in graph.edges}
        self.assertIn(AttackSurfaceEdgeKind.RUNS, edge_kinds)

    def test_bundle_references_exact_path(self) -> None:
        facts = (
            _fact(
                "f-origin",
                DiscoveryFactKind.ORIGIN,
                "origin:https://example.com",
                observation_id="obs-1",
                normalized_origin="https://example.com",
            ),
            _fact(
                "f-path",
                DiscoveryFactKind.EXACT_PATH,
                "path:https://example.com:/",
                observation_id="obs-1",
                normalized_origin="https://example.com",
                normalized_path="/",
            ),
            _fact(
                "f-bundle",
                DiscoveryFactKind.JS_BUNDLE,
                "sensor.archive:JS_BUNDLE:example.com/bundle.js",
                sensor_observation_id="so-1",
                attributes={"origin": "https://example.com", "path": "/"},
            ),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=facts,
        )
        edge_kinds = {edge.kind for edge in graph.edges}
        self.assertIn(AttackSurfaceEdgeKind.REFERENCES, edge_kinds)


class GraphDeterminismTests(unittest.TestCase):
    def test_same_facts_same_hash_regardless_of_order(self) -> None:
        base_facts = [
            _fact(
                f"f-host-{index}",
                DiscoveryFactKind.HOSTNAME,
                f"sensor.dns:HOSTNAME:host{index}.example.com",
                sensor_observation_id=f"so-{index}",
                attributes={"hostname": f"host{index}.example.com"},
            )
            for index in range(5)
        ]
        hashes = set()
        for _ in range(10):
            shuffled = list(base_facts)
            random.shuffle(shuffled)
            graph = rebuild_attack_surface_graph(
                research_run_id="run-1",
                strategy_version="surface.discovery.v1",
                facts=tuple(shuffled),
            )
            hashes.add(graph_hash(graph))
        self.assertEqual(len(hashes), 1)

    def test_permutation_invariant_nodes_and_edges(self) -> None:
        base_facts = tuple(
            _fact(
                f"f-host-{index}",
                DiscoveryFactKind.HOSTNAME,
                f"sensor.dns:HOSTNAME:host{index}.example.com",
                sensor_observation_id=f"so-{index}",
                attributes={"hostname": f"host{index}.example.com"},
            )
            for index in range(5)
        )
        graphs: list[AttackSurfaceGraph] = []
        for _ in range(10):
            shuffled = list(base_facts)
            random.shuffle(shuffled)
            graphs.append(
                rebuild_attack_surface_graph(
                    research_run_id="run-1",
                    strategy_version="surface.discovery.v1",
                    facts=tuple(shuffled),
                )
            )
        first = graphs[0]
        for graph in graphs[1:]:
            self.assertEqual(graph.nodes, first.nodes)
            self.assertEqual(graph.edges, first.edges)


class GraphAuthorityTests(unittest.TestCase):
    def test_graph_grants_nothing(self) -> None:
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            facts=(),
        )
        self.assertFalse(graph.grants_scope())
        self.assertFalse(graph.binds_session())
        self.assertFalse(graph.mints_budget())
        self.assertFalse(graph.mints_capability())


if __name__ == "__main__":
    unittest.main()
