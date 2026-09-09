from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.discovery.canonical import (
    admit_route_templates,
    canonical_key,
    instance_token_from_segment,
    route_template_from_paths,
)
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.discovery.context_pack import pack_surface_discovery_context
from zest.research.discovery.control_resolve import (
    ControlResolutionOutcome,
    DurableControlSignature,
    LiveControlView,
    resolve_control_ref,
)
from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.frontier import (
    FrontierEvent,
    FrontierEventKind,
    FrontierItem,
    legal_frontier_transition,
    next_selection_generation,
    select_eligible_frontier,
)
from zest.research.discovery.graph import rebuild_attack_surface_graph
from zest.research.discovery.inference import (
    DiscoveryInferenceDraft,
    admit_discovery_inference,
)
from zest.research.discovery.projection import (
    ControlEventView,
    ControlView,
    NetworkEventView,
    ObservationView,
    WorkflowCausalBinding,
    project_control_event,
    project_observation_view,
    seed_inspect_path_frontier,
)
from zest.research.discovery.selection import select_surface_discovery_opportunities
from zest.research.discovery.templates import admit_route_template_inferences
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    ControlEventKind,
    DiscoveryFactKind,
    DiscoveryGoalKind,
    DiscoveryInferenceKind,
    DiscoverySourcePlane,
)
from zest.research.exploration import EXPLORATION_STRATEGY_VERSION, NegativeKnowledge
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError


def _bounds(**overrides) -> DiscoveryBounds:
    values = dict(
        max_discovery_cycles=8,
        max_frontier_items=32,
        max_new_facts_per_cycle=16,
        max_browser_actions=16,
        max_http_transactions=16,
        max_per_route_revisit=1,
        max_identity_variants=3,
        max_transition_depth=4,
        max_graph_depth_from_seed=8,
        max_template_inference_fanout=4,
        max_duplicate_observations=8,
    )
    values.update(overrides)
    return DiscoveryBounds(**values)


def _ids():
    counter = {"n": 0}

    def allocate(label: str) -> str:
        counter["n"] += 1
        return f"id-{counter['n']}"

    return allocate


class CanonicalIdentityTests(unittest.TestCase):
    def test_stable_tuple_sha256(self) -> None:
        first = canonical_key("EXACT_PATH", "http://127.0.0.1:1", "/api/orders/101")
        second = canonical_key("EXACT_PATH", "http://127.0.0.1:1", "/api/orders/101")
        other = canonical_key("EXACT_PATH", "http://127.0.0.1:1", "/api/orders/202")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 64)

    def test_numeric_path_is_instance_candidate_not_object(self) -> None:
        self.assertEqual(instance_token_from_segment("101"), "101")
        self.assertIsNone(instance_token_from_segment("orders"))


class TemplateAdmissionTests(unittest.TestCase):
    def test_requires_three_compatible_paths(self) -> None:
        two = route_template_from_paths(
            "http://127.0.0.1:1",
            "GET",
            ("/api/orders/101", "/api/orders/202"),
        )
        three = route_template_from_paths(
            "http://127.0.0.1:1",
            "GET",
            ("/api/orders/101", "/api/orders/202", "/api/orders/303"),
        )
        self.assertIsNone(two)
        self.assertIsNotNone(three)
        assert three is not None
        self.assertEqual(three.template_path, "/api/orders/{n}")

    def test_mixed_decimal_and_uuid_rejected(self) -> None:
        admitted = route_template_from_paths(
            "http://127.0.0.1:1",
            "GET",
            (
                "/api/orders/101",
                "/api/orders/202",
                "/api/orders/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            ),
        )
        self.assertIsNone(admitted)

    def test_unrelated_exact_paths_do_not_block_family_admission(self) -> None:
        admitted = admit_route_templates(
            origin="http://127.0.0.1:1",
            http_method="GET",
            exact_paths=(
                "/",
                "/api/browser-only",
                "/api/orders/101",
                "/api/orders/202",
                "/api/orders/303",
            ),
        )
        self.assertEqual(len(admitted), 1)
        self.assertEqual(admitted[0].template_path, "/api/orders/{n}")


class InferenceEpistemicTests(unittest.TestCase):
    def test_observed_inference_rejected(self) -> None:
        draft = DiscoveryInferenceDraft(
            research_run_id="run-1",
            inference_kind=DiscoveryInferenceKind.ROUTE_TEMPLATE,
            canonical_key="k1",
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            identity_id=ANONYMOUS_IDENTITY_ID,
            source_run_ids=("run-1",),
            source_fact_ids=("f1", "f2", "f3"),
            attributes={"exact_paths": ["/a/1", "/a/2", "/a/3"]},
        )
        decision = admit_discovery_inference(draft, inference_id="inf-1")
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.outcome.value, "REJECTED_EPISTEMIC_UPGRADE")

    def test_cross_run_rejected(self) -> None:
        draft = DiscoveryInferenceDraft(
            research_run_id="run-1",
            inference_kind=DiscoveryInferenceKind.OBJECT_TYPE,
            canonical_key="k1",
            epistemic_status=TargetEpistemicStatus.INFERRED,
            identity_id=ANONYMOUS_IDENTITY_ID,
            source_run_ids=("run-2",),
        )
        decision = admit_discovery_inference(draft, inference_id="inf-1")
        self.assertEqual(decision.outcome.value, "REJECTED_CROSS_RUN")


class ConfigFingerprintTests(unittest.TestCase):
    def test_fingerprint_stable_and_detects_widening(self) -> None:
        config = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            bounds=_bounds(),
        )
        same = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            bounds=_bounds(),
        )
        wider = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            bounds=_bounds(max_discovery_cycles=99),
        )
        self.assertEqual(config.fingerprint(), same.fingerprint())
        self.assertNotEqual(config.fingerprint(), wider.fingerprint())


class FrontierTransitionTests(unittest.TestCase):
    def test_created_to_selected_is_illegal(self) -> None:
        self.assertTrue(
            legal_frontier_transition(None, FrontierEventKind.CREATED)
        )
        self.assertFalse(
            legal_frontier_transition(FrontierEventKind.CREATED, FrontierEventKind.SELECTED)
        )
        self.assertTrue(
            legal_frontier_transition(FrontierEventKind.ELIGIBLE, FrontierEventKind.SELECTED)
        )
        self.assertTrue(
            legal_frontier_transition(
                FrontierEventKind.ELIGIBLE, FrontierEventKind.DEFERRED_TO_RESEARCH
            )
        )

    def test_selection_generation_increments(self) -> None:
        events = (
            FrontierEvent(
                event_id="e1",
                frontier_id="f1",
                research_run_id="run-1",
                event_kind=FrontierEventKind.SELECTED,
                sequence=3,
                selection_generation=1,
            ),
        )
        self.assertEqual(next_selection_generation(events), 2)

    def test_se0_is_selected_before_se1_interact(self) -> None:
        se0 = FrontierItem(
            frontier_id="front-http",
            research_run_id="run-1",
            goal_kind=DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
            candidate_origin="http://127.0.0.1:1",
            candidate_path="/api/orders/101",
            identity_id=ANONYMOUS_IDENTITY_ID,
            proposed_capability="http.transaction",
            proposed_action="read",
            expected_side_effect=0,
            budget_class=0,
            structural_signature="sig-http",
            dedupe_identity="dedupe-http",
        )
        se1 = FrontierItem(
            frontier_id="front-click",
            research_run_id="run-1",
            goal_kind=DiscoveryGoalKind.INSPECT_CONTROL,
            candidate_origin="http://127.0.0.1:1",
            candidate_path="/",
            identity_id=ANONYMOUS_IDENTITY_ID,
            proposed_capability="browser.page",
            proposed_action="interact",
            expected_side_effect=1,
            budget_class=1,
            structural_signature="sig-click",
            dedupe_identity="dedupe-click",
        )

        def _eligible(frontier_id: str) -> tuple[FrontierEvent, ...]:
            return (
                FrontierEvent(
                    event_id=f"{frontier_id}-c",
                    frontier_id=frontier_id,
                    research_run_id="run-1",
                    event_kind=FrontierEventKind.CREATED,
                    sequence=1,
                ),
                FrontierEvent(
                    event_id=f"{frontier_id}-e",
                    frontier_id=frontier_id,
                    research_run_id="run-1",
                    event_kind=FrontierEventKind.ELIGIBLE,
                    sequence=2,
                ),
            )

        chosen = select_eligible_frontier(
            (se1, se0),
            {"front-http": _eligible("front-http"), "front-click": _eligible("front-click")},
            max_side_effect=1,
        )
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.goal_kind, DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION)

    def test_identity_observe_is_selected_before_http_characterize(self) -> None:
        identity = FrontierItem(
            frontier_id="front-alice",
            research_run_id="run-1",
            goal_kind=DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY,
            candidate_origin="http://127.0.0.1:1",
            candidate_path="/",
            identity_id="id-alice",
            proposed_capability="browser.page",
            proposed_action="observe",
            expected_side_effect=0,
            budget_class=0,
            structural_signature="sig-alice",
            dedupe_identity="dedupe-alice",
        )
        characterize = FrontierItem(
            frontier_id="front-http",
            research_run_id="run-1",
            goal_kind=DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
            candidate_origin="http://127.0.0.1:1",
            candidate_path="/api/orders/101",
            identity_id=ANONYMOUS_IDENTITY_ID,
            proposed_capability="http.transaction",
            proposed_action="read",
            expected_side_effect=0,
            budget_class=0,
            structural_signature="sig-http",
            dedupe_identity="dedupe-http",
        )

        def _eligible(frontier_id: str) -> tuple[FrontierEvent, ...]:
            return (
                FrontierEvent(
                    event_id=f"{frontier_id}-c",
                    frontier_id=frontier_id,
                    research_run_id="run-1",
                    event_kind=FrontierEventKind.CREATED,
                    sequence=1,
                ),
                FrontierEvent(
                    event_id=f"{frontier_id}-e",
                    frontier_id=frontier_id,
                    research_run_id="run-1",
                    event_kind=FrontierEventKind.ELIGIBLE,
                    sequence=2,
                ),
            )

        chosen = select_eligible_frontier(
            (characterize, identity),
            {
                "front-http": _eligible("front-http"),
                "front-alice": _eligible("front-alice"),
            },
            max_side_effect=1,
        )
        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.goal_kind, DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY)


class ProjectionAndGraphTests(unittest.TestCase):
    def test_seed_does_not_create_observed_fact(self) -> None:
        config = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            bounds=_bounds(),
        )
        item = seed_inspect_path_frontier(config, frontier_id="front-1")
        self.assertEqual(item.goal_kind.value, "INSPECT_PATH")
        self.assertNotIn("el-", item.structural_signature)

    def test_browser_network_creates_http_characterization_frontier_only(self) -> None:
        view = ObservationView(
            observation_id="obs-1",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-1",
            snapshot_fingerprint="fp-1",
            network_events=(
                NetworkEventView(
                    event_id="ne-1",
                    method="GET",
                    path="/api/hidden",
                    normalized_target="http://127.0.0.1:1/api/hidden",
                    redirect=False,
                    representability="NOT_REPRESENTABLE",
                ),
            ),
        )
        delta = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        goals = {item.goal_kind.value for item in delta.frontier_items}
        self.assertIn("CHARACTERIZE_HTTP_OPERATION", goals)
        attrs = [item.attributes for item in delta.frontier_items if item.attributes]
        self.assertTrue(any(item.get("auto_replay") is False for item in attrs))

    def test_off_origin_network_event_is_boundary_not_characterization(self) -> None:
        view = ObservationView(
            observation_id="obs-1",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-1",
            snapshot_fingerprint="fp-1",
            containment_degraded=True,
            blocked_external_dependency_count=1,
            network_events=(
                NetworkEventView(
                    event_id="ne-cdn",
                    method="GET",
                    path="/gtm.js",
                    normalized_target="https://www.googletagmanager.com/gtm.js",
                    redirect=False,
                    representability="NOT_REPRESENTABLE",
                ),
            ),
        )
        delta = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        goals = {item.goal_kind.value for item in delta.frontier_items}
        self.assertNotIn("CHARACTERIZE_HTTP_OPERATION", goals)
        kinds = {item.fact.fact_kind for item in delta.facts}
        self.assertIn(DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE, kinds)
        page = next(
            item.fact
            for item in delta.facts
            if item.fact.fact_kind is DiscoveryFactKind.PAGE_STATE
        )
        self.assertTrue(page.attributes["containment_degraded"])
        self.assertEqual(page.attributes["blocked_external_dependency_count"], 1)
        self.assertTrue(page.attributes["main_observation_present"])
        self.assertFalse(page.attributes["page_complete"])

    def test_same_path_network_event_does_not_duplicate_exact_path(self) -> None:
        view = ObservationView(
            observation_id="obs-1",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-1",
            snapshot_fingerprint="fp-1",
            network_events=(
                NetworkEventView(
                    event_id="ne-1",
                    method="GET",
                    path="/",
                    normalized_target="http://127.0.0.1:1/",
                    redirect=False,
                    representability="NOT_REPRESENTABLE",
                ),
            ),
        )
        delta = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        path_keys = [
            item.fact.canonical_key
            for item in delta.facts
            if item.fact.fact_kind is DiscoveryFactKind.EXACT_PATH
        ]
        self.assertEqual(len(path_keys), len(set(path_keys)))
        self.assertEqual(len(path_keys), 1)

    def test_duplicate_source_attaches_without_new_semantic_fact(self) -> None:
        view = ObservationView(
            observation_id="obs-2",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="t",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-2",
            snapshot_fingerprint="fp-2",
        )
        first = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        keys = frozenset(item.fact.canonical_key for item in first.facts)
        ids = {item.fact.canonical_key: item.fact.fact_id for item in first.facts}
        second = project_observation_view(
            ObservationView(
                observation_id="obs-3",
                research_run_id="run-1",
                observation_kind="BROWSER_PAGE_STATE",
                identity_id=ANONYMOUS_IDENTITY_ID,
                target_reference="t",
                normalized_origin="http://127.0.0.1:1",
                normalized_path="/",
                worker_result_id="wr-3",
                snapshot_fingerprint="fp-2",
            ),
            existing_canonical_keys=keys,
            fact_id_for_key=ids,
            allocate_id=_ids(),
        )
        self.assertTrue(any(item.is_new_semantic for item in first.facts))
        self.assertTrue(all(not item.is_new_semantic for item in second.facts))
        self.assertEqual(
            {item.fact.canonical_key for item in first.facts},
            {item.fact.canonical_key for item in second.facts},
        )

    def test_control_event_boundary_is_derived_not_observed(self) -> None:
        delta = project_control_event(
            ControlEventView(
                control_event_id="ce-1",
                research_run_id="run-1",
                event_kind=ControlEventKind.NEW_ORIGIN_BOUNDARY,
                identity_id=ANONYMOUS_IDENTITY_ID,
                target_reference="t",
                worker_result_id="wr-1",
                normalized_origin="http://127.0.0.1:1",
                location_origin="http://evil.example",
                location_path="/",
            ),
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        self.assertEqual(len(delta.facts), 1)
        fact = delta.facts[0].fact
        self.assertEqual(fact.fact_kind, DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE)
        self.assertEqual(fact.epistemic_status, TargetEpistemicStatus.DERIVED)

    def test_identities_remain_separated_on_shared_nodes(self) -> None:
        def _page(obs_id: str, identity: str) -> ObservationView:
            return ObservationView(
                observation_id=obs_id,
                research_run_id="run-1",
                observation_kind="BROWSER_PAGE_STATE",
                identity_id=identity,
                target_reference="t",
                normalized_origin="http://127.0.0.1:1",
                normalized_path="/me",
                worker_result_id=obs_id,
                snapshot_fingerprint=f"fp-{identity}",
            )

        alice = project_observation_view(
            _page("obs-a", "alice"),
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        bob = project_observation_view(
            _page("obs-b", "bob"),
            existing_canonical_keys=frozenset(item.fact.canonical_key for item in alice.facts),
            fact_id_for_key={item.fact.canonical_key: item.fact.fact_id for item in alice.facts},
            allocate_id=_ids(),
        )
        facts = tuple(item.fact for item in alice.facts + bob.facts)
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=facts,
        )
        path_nodes = [node for node in graph.nodes if node.kind.value == "EXACT_PATH"]
        self.assertEqual(len(path_nodes), 1)
        self.assertEqual(set(path_nodes[0].identity_ids), {"alice", "bob"})

    def test_graph_rebuild_is_deterministic(self) -> None:
        view = ObservationView(
            observation_id="obs-1",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="t",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-1",
            snapshot_fingerprint="fp",
            controls=(ControlView("button", "go", "button", ""),),
        )
        delta = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        facts = tuple(item.fact for item in delta.facts)
        first = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=facts,
        )
        second = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=tuple(reversed(facts)),
        )
        self.assertEqual([n.canonical_key for n in first.nodes], [n.canonical_key for n in second.nodes])
        self.assertEqual([e.edge_id for e in first.edges], [e.edge_id for e in second.edges])

    def test_three_order_paths_admit_template_not_object_instance(self) -> None:
        allocate = _ids()
        facts = []
        for path in ("/api/orders/101", "/api/orders/202", "/api/orders/303"):
            view = ObservationView(
                observation_id=path,
                research_run_id="run-1",
                observation_kind="HTTP_TRANSACTION",
                identity_id=ANONYMOUS_IDENTITY_ID,
                target_reference="t",
                normalized_origin="http://127.0.0.1:1",
                normalized_path=path,
                worker_result_id=path,
                http_method="GET",
                status_code=200,
            )
            delta = project_observation_view(
                view,
                existing_canonical_keys=frozenset(item.canonical_key for item in facts),
                fact_id_for_key={item.canonical_key: item.fact_id for item in facts},
                allocate_id=allocate,
            )
            facts.extend(item.fact for item in delta.facts)
        kinds = {fact.fact_kind for fact in facts}
        self.assertIn(DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE, kinds)
        self.assertNotIn(DiscoveryFactKind.WORKFLOW_TRANSITION, kinds)
        inferences = admit_route_template_inferences(
            tuple(facts),
            research_run_id="run-1",
            identity_id=ANONYMOUS_IDENTITY_ID,
            allocate_id=allocate,
        )
        self.assertEqual(len(inferences), 1)
        self.assertEqual(inferences[0].epistemic_status, TargetEpistemicStatus.INFERRED)
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=tuple(facts),
            inferences=inferences,
        )
        self.assertTrue(any(node.kind.value == "ROUTE_TEMPLATE" for node in graph.nodes))
        self.assertFalse(any(node.kind.value == "OBJECT_INSTANCE" for node in graph.nodes))

    def test_workflow_requires_causal_binding(self) -> None:
        view = ObservationView(
            observation_id="obs-post",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id="alice",
            target_reference="t",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/ticket",
            worker_result_id="wr-post",
            snapshot_fingerprint="fp-post",
        )
        unbound = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        self.assertFalse(unbound.workflow_transition_ready)
        bound = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
            workflow=WorkflowCausalBinding(
                pre_state_fact_id="pre-1",
                experiment_plan_id="plan-1",
                execution_attempt_id="att-1",
                actor_identity_id="alice",
                post_observation_id="obs-post",
                object_handle="ticket",
            ),
        )
        self.assertTrue(bound.workflow_transition_ready)
        self.assertTrue(
            any(item.fact.fact_kind is DiscoveryFactKind.WORKFLOW_TRANSITION for item in bound.facts)
        )

    def test_secret_attributes_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            DiscoveryFact(
                fact_id="f1",
                research_run_id="run-1",
                fact_kind=DiscoveryFactKind.CONTROL,
                canonical_key="k",
                epistemic_status=TargetEpistemicStatus.OBSERVED,
                identity_id=ANONYMOUS_IDENTITY_ID,
                target_reference="t",
                sources=(
                    DiscoveryFactSourceView(
                        source_plane=DiscoverySourcePlane.OBSERVATION,
                        observation_id="obs-1",
                    ),
                ),
                attributes={"password": "secret"},
            )


class ControlResolveTests(unittest.TestCase):
    def test_stale_and_ambiguous_never_click(self) -> None:
        signature = DurableControlSignature(
            origin="http://127.0.0.1:1",
            path="/",
            tag="button",
            name="go",
            role="button",
            input_type="",
        )
        live = (
            LiveControlView("el-1", "fp-new", "button", "go", "button", ""),
            LiveControlView("el-2", "fp-new", "button", "go", "button", ""),
        )
        stale = resolve_control_ref(
            signature,
            live[:1],
            current_page_fingerprint="fp-new",
            expected_page_fingerprint="fp-old",
            lease_present=True,
            process_generation_changed=False,
        )
        ambiguous = resolve_control_ref(
            signature,
            live,
            current_page_fingerprint="fp-new",
            expected_page_fingerprint="fp-new",
            lease_present=True,
            process_generation_changed=False,
        )
        match = resolve_control_ref(
            signature,
            live[:1],
            current_page_fingerprint="fp-new",
            expected_page_fingerprint="fp-new",
            lease_present=True,
            process_generation_changed=False,
        )
        self.assertEqual(stale.outcome, ControlResolutionOutcome.STALE_FINGERPRINT)
        self.assertFalse(stale.may_interact)
        self.assertEqual(ambiguous.outcome, ControlResolutionOutcome.AMBIGUOUS)
        self.assertFalse(ambiguous.may_interact)
        self.assertTrue(match.may_interact)
        self.assertEqual(match.live_element_reference, "el-1")


class SelectionIsolationTests(unittest.TestCase):
    def test_rejects_diagnostic_strategy_version(self) -> None:
        with self.assertRaises(ResearchInputError):
            select_surface_discovery_opportunities(
                (),
                research_run_id="run-1",
                strategy_version=EXPLORATION_STRATEGY_VERSION,
            )

    def test_negative_knowledge_is_context_bound(self) -> None:
        from zest.research.discovery.selection import opportunity_from_frontier

        config = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference="http://127.0.0.1:1/",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/missing",
            bounds=_bounds(),
        )
        item = seed_inspect_path_frontier(config, frontier_id="front-1")
        opportunity = opportunity_from_frontier(item)
        negative = NegativeKnowledge(
            structural_identity=opportunity.structural_identity,
            context_signature=opportunity.context_signature,
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        )
        decisions = select_surface_discovery_opportunities(
            (opportunity,),
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            negative_knowledge=(negative,),
        )
        self.assertEqual(decisions[0].reason_codes, ("CONTEXT_BOUND_NEGATIVE",))


class ContextPackTests(unittest.TestCase):
    def test_context_omits_hidden_truth_and_vulnerability_labels(self) -> None:
        view = ObservationView(
            observation_id="obs-1",
            research_run_id="run-1",
            observation_kind="BROWSER_PAGE_STATE",
            identity_id=ANONYMOUS_IDENTITY_ID,
            target_reference="t",
            normalized_origin="http://127.0.0.1:1",
            normalized_path="/",
            worker_result_id="wr-1",
            snapshot_fingerprint="fp",
        )
        delta = project_observation_view(
            view,
            existing_canonical_keys=frozenset(),
            fact_id_for_key={},
            allocate_id=_ids(),
        )
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=tuple(item.fact for item in delta.facts),
        )
        context = pack_surface_discovery_context(
            graph,
            research_run_id="run-1",
            research_question="Observe authorized surface",
            unexplored_frontier=delta.frontier_items,
        )
        blob = str(context).lower()
        self.assertNotIn("vulnerability", blob)
        self.assertNotIn("hidden_route_map", blob)
        self.assertNotIn("ground_truth", blob)


if __name__ == "__main__":
    unittest.main()
