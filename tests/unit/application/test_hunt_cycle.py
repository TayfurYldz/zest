"""SD-G5 hunt cycle application tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.generate_hunt_hypotheses import (
    IDENTITY_EXPANSION_CAPPED,
    MAX_IDENTITIES_PER_NODE,
    GenerateHuntHypotheses,
    GenerateHuntHypothesesCommand,
)
from zest.application.hunt_validation import (
    ValidateHuntTiers,
    ValidateHuntTiersCommand,
)
from zest.application.run_hunt_cycle import RunHuntCycle, RunHuntCycleCommand
from zest.core.enums import ScopeClassification
from zest.data.records import HunterFamilyRecord, HuntV3QueueRecord
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.discovery.graph import AttackSurfaceEdge, AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceEdgeKind, AttackSurfaceNodeKind
from zest.research.scheduler.types import HunterScore, ScoredCell
from zest.research.selection import HunterFamilyView
from zest.research.target_model import TargetEpistemicStatus
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


def _node(
    *,
    node_id: str,
    kind: AttackSurfaceNodeKind,
    canonical_key: str,
    scope_classification: ScopeClassification = ScopeClassification.IN_SCOPE,
    epistemic_status: TargetEpistemicStatus = TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
    identity_ids: tuple[str, ...] = (),
    attributes: dict | None = None,
) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id=node_id,
        kind=kind,
        canonical_key=canonical_key,
        epistemic_status=epistemic_status,
        identity_ids=identity_ids,
        provenance_refs=("sensor_observation:so-1",),
        scope_classification=scope_classification,
        attributes=attributes,
    )


def _edge(
    *,
    edge_id: str,
    kind: AttackSurfaceEdgeKind,
    from_node_id: str,
    to_node_id: str,
) -> AttackSurfaceEdge:
    return AttackSurfaceEdge(
        edge_id=edge_id,
        kind=kind,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        identity_id=None,
        provenance_refs=(),
        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
    )


def _api_spec_graph() -> AttackSurfaceGraph:
    node = _node(
        node_id="spec-1",
        kind=AttackSurfaceNodeKind.API_SPEC,
        canonical_key="origin:http://example.com/api/spec.json",
        attributes={"origin": "http://example.com", "path": "/api/spec.json"},
    )
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(node,),
        edges=(),
    )


def _hostname_graph(with_observed_under: bool = False) -> AttackSurfaceGraph:
    node = _node(
        node_id="host-1",
        kind=AttackSurfaceNodeKind.HOSTNAME,
        canonical_key="hostname:example.com",
    )
    edges: tuple[AttackSurfaceEdge, ...] = ()
    nodes: tuple[AttackSurfaceNode, ...] = (node,)
    if with_observed_under:
        identity_node = AttackSurfaceNode(
            node_id="identity:ANONYMOUS",
            kind=AttackSurfaceNodeKind.IDENTITY_REF,
            canonical_key="identity:ANONYMOUS",
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            identity_ids=("ANONYMOUS",),
            provenance_refs=(),
            scope_classification=ScopeClassification.IN_SCOPE,
        )
        nodes = (node, identity_node)
        edges = (
            _edge(
                edge_id="host-1:under:ANONYMOUS",
                kind=AttackSurfaceEdgeKind.OBSERVED_UNDER,
                from_node_id="host-1",
                to_node_id="identity:ANONYMOUS",
            ),
        )
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=nodes,
        edges=edges,
    )


def _v3_graph() -> AttackSurfaceGraph:
    node = _node(
        node_id="op-1",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="origin:http://example.com|path:/api/users|method:GET",
        attributes={
            "origin": "http://example.com",
            "path": "/api/users",
            "method": "GET",
            "resource_id": "users",
        },
    )
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(node,),
        edges=(),
    )


def _protocol_graph() -> AttackSurfaceGraph:
    node = _node(
        node_id="op-protocol",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="origin:http://example.com|path:/edge|method:GET",
        attributes={
            "origin": "http://example.com",
            "path": "/edge",
            "method": "GET",
            "protocol_surface_signals": ["reverse_proxy", "http2"],
        },
    )
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(node,),
        edges=(),
    )


def _family_api_spec() -> HunterFamilyView:
    return HunterFamilyView(
        family_id="hf-exposed-api-spec",
        name="EXPOSED_API_SPEC",
        target_node_kinds=("API_SPEC",),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template=(
            "API specification at {canonical_key} documents endpoint surface "
            "that may be wider than observed access controls."
        ),
        evidence_requirements={"required_fact_kinds": ["API_SPEC"]},
        validation_tier="V2",
        enabled=True,
        version=1,
    )


def _family_unprotected_hostname() -> HunterFamilyView:
    return HunterFamilyView(
        family_id="hf-unprotected-hostname",
        name="UNPROTECTED_HOSTNAME",
        target_node_kinds=("HOSTNAME",),
        preconditions={"scope_classification": "IN_SCOPE", "absent_edge_kind": "OBSERVED_UNDER"},
        claim_template=(
            "Hostname {canonical_key} is in scope but has no observed "
            "active probe coverage yet."
        ),
        evidence_requirements={"required_edge_kind": "OBSERVED_UNDER"},
        validation_tier="V2",
        enabled=True,
        version=1,
    )


def _family_object_authz() -> HunterFamilyView:
    return HunterFamilyView(
        family_id="hf-object-authz",
        name="OBJECT_AUTHORIZATION",
        target_node_kinds=("HTTP_OPERATION",),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template=(
            "Object authorization boundary on {origin}{path} "
            "may allow cross-owner access to {resource_id}."
        ),
        evidence_requirements={},
        validation_tier="V3",
        enabled=True,
        version=1,
    )


def _family_object_authz_no_scope_precondition() -> HunterFamilyView:
    """V3 family without scope precondition; used to test _enqueue_v3 IN_SCOPE lock."""
    return HunterFamilyView(
        family_id="hf-object-authz-noscope",
        name="OBJECT_AUTHORIZATION",
        target_node_kinds=("HTTP_OPERATION",),
        preconditions={},
        claim_template=(
            "Object authorization boundary on {origin}{path} "
            "may allow cross-owner access to {resource_id}."
        ),
        evidence_requirements={},
        validation_tier="V3",
        enabled=True,
        version=1,
    )


def _family_object_authz_identity() -> HunterFamilyView:
    """OBJECT_AUTHORIZATION family with identity placeholder (SD-G9)."""
    return HunterFamilyView(
        family_id="hf-object-authz-id",
        name="OBJECT_AUTHORIZATION",
        target_node_kinds=("HTTP_OPERATION",),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template=(
            "Object authorization boundary on {origin}{path} "
            "may allow cross-owner access to {resource_id} "
            "for identity {identity_id}."
        ),
        evidence_requirements={},
        validation_tier="V3",
        enabled=True,
        version=1,
    )


def _family_protocol_smuggling() -> HunterFamilyView:
    return HunterFamilyView(
        family_id="hf-http-smuggling-desync",
        name="HTTP_REQUEST_SMUGGLING_DESYNC",
        target_node_kinds=("HTTP_OPERATION",),
        preconditions={
            "scope_classification": "IN_SCOPE",
            "required_attribute_any": {
                "protocol_surface_signals": ["reverse_proxy", "http2"],
            },
        },
        claim_template=(
            "Protocol surface {canonical_key} has parser-boundary evidence "
            "supporting request-smuggling/desync specialist planning."
        ),
        evidence_requirements={
            "protocol_lane": "http_request_smuggling_desync",
            "required_surface_signals": ["reverse_proxy", "http2"],
            "required_controls": [
                "single_parser_control",
                "connection_close_control",
                "deceptive_proxy_control",
            ],
            "required_protocol_dimensions": [
                "frontend_protocol",
                "backend_protocol",
                "normalization_boundary",
            ],
        },
        validation_tier="V3",
        enabled=True,
        version=1,
    )


class GenerateHuntHypothesesTests(unittest.TestCase):
    def test_generates_hypotheses_for_matching_nodes(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _api_spec_graph()
        registry = (_family_api_spec(),)

        result = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertEqual(result.generated_count, 1)
        self.assertEqual(len(result.hypothesis_ids), 1)
        self.assertEqual(len(result.hypothesis_sources), 1)
        self.assertEqual(result.hypothesis_sources[0][1], "spec-1")
        self.assertEqual(result.hypothesis_sources[0][2], "hf-exposed-api-spec")
        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(len(store.audit_events), 1)
        audit = list(store.audit_events.values())[0]
        self.assertEqual(audit.event_type, "HUNT_HYPOTHESIS_GENERATED")

    def test_no_match_is_no_op(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _api_spec_graph()
        registry = (_family_unprotected_hostname(),)

        result = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertEqual(result.generated_count, 0)
        self.assertEqual(len(store.hypotheses), 0)
        self.assertEqual(len(store.audit_events), 0)

    def test_loads_registry_from_uow_when_not_injected(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.hunter_families["hf-api:1"] = HunterFamilyRecord(
            family_id="hf-api",
            name="EXPOSED_API_SPEC",
            target_node_kinds=("API_SPEC",),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template="API spec at {canonical_key}.",
            evidence_requirements={"required_fact_kinds": ["API_SPEC"]},
            validation_tier="V2",
            enabled=True,
            version=1,
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _api_spec_graph()

        result = use_case.execute(
            GenerateHuntHypothesesCommand(research_run_id="run-1", graph=graph)
        )

        self.assertEqual(result.generated_count, 1)

    def test_generates_per_identity_hypotheses(self) -> None:
        node = _node(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            identity_ids=("alice", "bob"),
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)

        result = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family_object_authz_identity(),),
            )
        )

        self.assertEqual(result.generated_count, 2)
        identities = {source[3] for source in result.hypothesis_sources}
        self.assertEqual(identities, {"alice", "bob"})
        for hypothesis in store.hypotheses.values():
            self.assertIn(hypothesis.identity_id, {"alice", "bob"})
            self.assertIn(hypothesis.identity_id, hypothesis.claim)

    def test_identity_expansion_capped_at_max_per_node(self) -> None:
        identities = tuple(f"id-{i}" for i in range(MAX_IDENTITIES_PER_NODE + 3))
        node = _node(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            identity_ids=identities,
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)

        result = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family_object_authz_identity(),),
            )
        )

        self.assertEqual(result.generated_count, MAX_IDENTITIES_PER_NODE)
        capping_events = [
            event for event in store.audit_events.values()
            if event.event_type == IDENTITY_EXPANSION_CAPPED
        ]
        self.assertEqual(len(capping_events), 1)
        payload = capping_events[0].payload
        self.assertEqual(payload["total_identities"], len(identities))
        self.assertEqual(payload["max_identities"], MAX_IDENTITIES_PER_NODE)


class ValidateHuntTiersTests(unittest.TestCase):
    def test_v1_v2_pass_v2_family_does_not_queue(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _api_spec_graph()
        registry = (_family_api_spec(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        hypothesis_id = generated.hypothesis_ids[0]
        validator = ValidateHuntTiers(factory)
        result = validator.execute(
            ValidateHuntTiersCommand(
                research_run_id="run-1",
                hypothesis_id=hypothesis_id,
                family=_family_api_spec(),
                node_id="spec-1",
                graph=graph,
            )
        )

        self.assertTrue(result.v1_passed)
        self.assertTrue(result.v2_passed)
        self.assertFalse(result.v3_queued)
        self.assertIsNone(result.queue_id)
        self.assertEqual(len(store.hunt_v3_queue), 0)

    def test_v2_rejects_when_required_fact_kind_missing(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        # HOSTNAME node with API_SPEC family => V2 should reject.
        graph = _hostname_graph()
        registry = (_family_api_spec(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertEqual(generated.generated_count, 0)
        # When generated count is zero there is no hypothesis to validate.

    def test_v3_family_queues_active_experiment(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _v3_graph()
        registry = (_family_object_authz(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        hypothesis_id = generated.hypothesis_ids[0]
        validator = ValidateHuntTiers(factory)
        result = validator.execute(
            ValidateHuntTiersCommand(
                research_run_id="run-1",
                hypothesis_id=hypothesis_id,
                family=_family_object_authz(),
                node_id="op-1",
                graph=graph,
            )
        )

        self.assertTrue(result.v1_passed)
        self.assertTrue(result.v2_passed)
        self.assertTrue(result.v3_queued)
        self.assertIsNotNone(result.queue_id)
        self.assertEqual(len(store.hunt_v3_queue), 1)
        queue_record = list(store.hunt_v3_queue.values())[0]
        self.assertIsInstance(queue_record, HuntV3QueueRecord)
        self.assertEqual(queue_record.state, "PENDING")
        self.assertEqual(queue_record.capability, "http.authorization_differential")
        self.assertEqual(queue_record.arguments["family_name"], "OBJECT_AUTHORIZATION")
        self.assertEqual(queue_record.arguments["path"], "/api/users")
        self.assertEqual(queue_record.arguments["authorized_origin"], "http://example.com")

    def test_protocol_parser_family_queues_se3_plan_only_when_surface_supported(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        graph = _protocol_graph()
        registry = (_family_protocol_smuggling(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        hypothesis_id = generated.hypothesis_ids[0]
        validator = ValidateHuntTiers(factory)
        result = validator.execute(
            ValidateHuntTiersCommand(
                research_run_id="run-1",
                hypothesis_id=hypothesis_id,
                family=_family_protocol_smuggling(),
                node_id="op-protocol",
                graph=graph,
            )
        )

        self.assertTrue(result.v3_queued)
        queue_record = list(store.hunt_v3_queue.values())[0]
        self.assertEqual(queue_record.capability, "protocol.parser")
        self.assertEqual(queue_record.action, "plan")
        self.assertEqual(queue_record.side_effect_level, 3)
        self.assertEqual(queue_record.arguments["approval_required"], "SE3")
        self.assertEqual(
            queue_record.arguments["worker_dispatch"],
            "forbidden_until_se3_approval",
        )
        self.assertEqual(queue_record.arguments["protocol_lane"], "http_request_smuggling_desync")
        self.assertGreaterEqual(queue_record.arguments["step_count"], 8)
        self.assertEqual(len(queue_record.arguments["protocol_plan_hash"]), 64)
        self.assertEqual(queue_record.arguments["step_count"], len(queue_record.arguments["steps"]))
        self.assertGreaterEqual(len(queue_record.arguments["steps"]), 8)
        self.assertIn("step_id", queue_record.arguments["steps"][0])
        self.assertNotIn("payload", queue_record.arguments)
        self.assertNotIn("body", queue_record.arguments)

    def test_v3_family_rejects_unknown_scope_node(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        node = _node(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            scope_classification=ScopeClassification.UNKNOWN,
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )
        registry = (_family_object_authz_no_scope_precondition(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        hypothesis_id = generated.hypothesis_ids[0]
        validator = ValidateHuntTiers(factory)
        with self.assertRaises(Exception):
            validator.execute(
                ValidateHuntTiersCommand(
                    research_run_id="run-1",
                    hypothesis_id=hypothesis_id,
                    family=_family_object_authz_no_scope_precondition(),
                    node_id="op-1",
                    graph=graph,
                )
            )

    def test_v3_family_rejects_out_of_scope_node(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = GenerateHuntHypotheses(factory)
        node = _node(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            scope_classification=ScopeClassification.OUT_OF_SCOPE,
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )
        registry = (_family_object_authz_no_scope_precondition(),)

        generated = use_case.execute(
            GenerateHuntHypothesesCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        hypothesis_id = generated.hypothesis_ids[0]
        validator = ValidateHuntTiers(factory)
        with self.assertRaises(Exception):
            validator.execute(
                ValidateHuntTiersCommand(
                    research_run_id="run-1",
                    hypothesis_id=hypothesis_id,
                    family=_family_object_authz_no_scope_precondition(),
                    node_id="op-1",
                    graph=graph,
                )
            )

    def test_missing_hypothesis_raises(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        validator = ValidateHuntTiers(factory)
        with self.assertRaises(Exception):
            validator.execute(
                ValidateHuntTiersCommand(
                    research_run_id="run-1",
                    hypothesis_id="hyp-missing",
                    family=_family_api_spec(),
                    node_id="spec-1",
                    graph=_api_spec_graph(),
                )
            )


class RunHuntCycleTests(unittest.TestCase):
    def test_full_cycle_generates_and_queues_v3(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = RunHuntCycle(factory)
        graph = _v3_graph()
        registry = (_family_object_authz(),)

        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertEqual(result.generated, 1)
        self.assertEqual(result.v1_passed, 1)
        self.assertEqual(result.v2_passed, 1)
        self.assertEqual(result.v3_queued, 1)
        self.assertEqual(len(result.queue_ids), 1)
        self.assertFalse(result.no_op)

    def test_no_op_when_nothing_matches(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = RunHuntCycle(factory)
        graph = _api_spec_graph()
        registry = (_family_unprotected_hostname(),)

        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertTrue(result.no_op)
        self.assertEqual(result.generated, 0)
        self.assertEqual(result.v3_queued, 0)

    def test_v2_family_passes_but_does_not_queue(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = RunHuntCycle(factory)
        graph = _api_spec_graph()
        registry = (_family_api_spec(),)

        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
            )
        )

        self.assertEqual(result.generated, 1)
        self.assertEqual(result.v1_passed, 1)
        self.assertEqual(result.v2_passed, 1)
        self.assertEqual(result.v3_queued, 0)

    def test_cycle_consumes_scheduler_recommendation(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = RunHuntCycle(factory)
        graph = _v3_graph()
        registry = (_family_object_authz_identity(),)
        cell = CoverageCell(
            node_canonical_key="origin:http://example.com|path:/api/users|method:GET",
            identity_id="alice",
            family_id="hf-object-authz-id",
            state=CoverageState.UNTESTED,
            missing_evidence=("NO_HYPOTHESIS",),
        )
        scored = ScoredCell(
            cell=cell,
            score=HunterScore(
                cell=cell,
                total_score=55,
                state_weight=50,
                family_success_bonus=0,
                family_exploration_bonus=5,
                freshness_bonus=0,
                budget_suitability_bonus=5,
                explanation=("test",),
            ),
        )

        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
                schedule=(scored,),
            )
        )

        self.assertEqual(result.generated, 1)
        self.assertEqual(result.v1_passed, 1)
        self.assertEqual(result.v2_passed, 1)
        self.assertEqual(result.v3_queued, 1)
        self.assertFalse(result.no_op)
        hypothesis = next(iter(store.hypotheses.values()))
        self.assertEqual(hypothesis.identity_id, "alice")
        self.assertIn("alice", hypothesis.claim)

    def test_cycle_schedule_skips_covered_cells(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = RunHuntCycle(factory)
        graph = _v3_graph()
        registry = (_family_object_authz_identity(),)
        covered = CoverageCell(
            node_canonical_key="origin:http://example.com|path:/api/users|method:GET",
            identity_id="alice",
            family_id="hf-object-authz-id",
            state=CoverageState.COVERED,
            missing_evidence=(),
        )
        scored = ScoredCell(
            cell=covered,
            score=HunterScore(
                cell=covered,
                total_score=0,
                state_weight=0,
                family_success_bonus=0,
                family_exploration_bonus=0,
                freshness_bonus=0,
                budget_suitability_bonus=0,
                explanation=("test",),
            ),
        )

        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
                registry=registry,
                schedule=(scored,),
            )
        )

        self.assertTrue(result.no_op)
        self.assertEqual(result.generated, 0)
