from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.research.differential import DifferentialInterpretation
from research_os.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from research_os.research.discovery.types import AttackSurfaceNodeKind
from research_os.research.identity_anomaly import (
    IDENTITY_ANOMALY_ALTERNATIVE,
    IDENTITY_ANOMALY_CLAIM,
    IdentityAnomalyClass,
    classify_http_authorization_observation,
    classify_identity_differential,
    compile_identity_anomaly_experiment,
    compiler_arguments_from_context,
    exploratory_experiment_id,
    exploratory_hypothesis_origin,
    identity_anomaly_proposal_and_challenge,
    owning_identity_families,
)
from research_os.research.planning import (
    HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
    HTTP_AUTHORIZATION_EXPECTED_OBSERVATION,
)
from research_os.tools.capabilities import HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY
from research_os.research.selection import HunterFamilyView
from research_os.research.target_model import TargetEpistemicStatus
from research_os.core.enums import ScopeClassification


def _family(**overrides) -> HunterFamilyView:
    values = dict(
        family_id="hf-object-authz",
        name="OBJECT_AUTHORIZATION",
        target_node_kinds=("HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template="Object boundary on {origin}{path} may allow cross-owner access.",
        evidence_requirements={"required_observation_kinds": ["HTTP_AUTHORIZATION_DIFFERENTIAL"]},
        validation_tier="V3",
        enabled=True,
        version=1,
    )
    values.update(overrides)
    return HunterFamilyView(**values)


def _node(*, in_scope: bool = True) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id="http://127.0.0.1/vulnerable/accounts",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="GET http://127.0.0.1/vulnerable/accounts",
        epistemic_status=TargetEpistemicStatus.OBSERVED,
        identity_ids=("alice",),
        provenance_refs=("obs-1",),
        scope_classification=(
            ScopeClassification.IN_SCOPE if in_scope else ScopeClassification.UNKNOWN
        ),
    )


class IdentityAnomalyClassificationTests(unittest.TestCase):
    def test_controlled_actor_difference_without_family_is_registry_external(self) -> None:
        context = classify_identity_differential(
            research_run_id="run-1",
            differential_id="diff-1",
            differential_run_id="run-1",
            interpretation=DifferentialInterpretation.CONTROLLED_DIFFERENCE.value,
            changed_dimensions=("ACTOR",),
            observation_ids=("obs-1", "obs-2"),
            unresolved_observation_ids=(),
            cross_run_observation_ids=(),
            owning_family_ids=(),
            authorized_origin="http://127.0.0.1:9",
            actor="alice",
            own_object="alice",
            cross_object="bob",
            mode="vulnerable",
        )
        self.assertEqual(context.classification, IdentityAnomalyClass.REGISTRY_EXTERNAL)
        self.assertTrue(context.registry_external)
        self.assertIn("identity-dependent", context.claim)
        self.assertEqual(context.alternative_explanation, IDENTITY_ANOMALY_ALTERNATIVE)
        args = compiler_arguments_from_context(context)
        self.assertEqual(args["actor"], "alice")
        self.assertEqual(args["cross_object"], "bob")

    def test_unresolved_or_cross_run_source_is_rejected(self) -> None:
        missing = classify_identity_differential(
            research_run_id="run-1",
            differential_id="diff-1",
            differential_run_id="run-1",
            interpretation=DifferentialInterpretation.CONTROLLED_DIFFERENCE.value,
            changed_dimensions=("ACTOR",),
            observation_ids=("obs-1",),
            unresolved_observation_ids=("obs-1",),
            cross_run_observation_ids=(),
            owning_family_ids=(),
            authorized_origin="http://127.0.0.1:9",
            actor="alice",
            own_object="alice",
            cross_object="bob",
            mode="vulnerable",
        )
        self.assertEqual(missing.classification, IdentityAnomalyClass.REJECT_UNRESOLVED_SOURCE)
        cross = classify_identity_differential(
            research_run_id="run-1",
            differential_id="diff-1",
            differential_run_id="run-2",
            interpretation=DifferentialInterpretation.CONTROLLED_DIFFERENCE.value,
            changed_dimensions=("ACTOR",),
            observation_ids=("obs-1",),
            unresolved_observation_ids=(),
            cross_run_observation_ids=(),
            owning_family_ids=(),
            authorized_origin="http://127.0.0.1:9",
            actor="alice",
            own_object="alice",
            cross_object="bob",
            mode="vulnerable",
        )
        self.assertEqual(cross.classification, IdentityAnomalyClass.REJECT_CROSS_RUN)

    def test_known_family_match_is_not_exploratory(self) -> None:
        context = classify_http_authorization_observation(
            research_run_id="run-1",
            observation_id="obs-1",
            observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
            observation_run_id="run-1",
            payload={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
                "cross_object_request_status": 200,
            },
            owning_family_ids=("hf-object-authz",),
        )
        self.assertEqual(context.classification, IdentityAnomalyClass.KNOWN_FAMILY)
        self.assertFalse(context.registry_external)

    def test_secure_cross_deny_is_not_an_anomaly(self) -> None:
        context = classify_http_authorization_observation(
            research_run_id="run-1",
            observation_id="obs-1",
            observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
            observation_run_id="run-1",
            payload={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "secure_only",
                "cross_object_request_status": 403,
            },
            owning_family_ids=(),
        )
        self.assertEqual(context.classification, IdentityAnomalyClass.REJECT_NO_ANOMALY)

    def test_in_scope_http_operation_is_owned_by_object_authorization(self) -> None:
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(_node(),),
            edges=(),
        )
        owned = owning_identity_families(graph, (_family(),))
        self.assertEqual(owned, ("hf-object-authz",))

    def test_empty_graph_is_not_owned(self) -> None:
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(),
            edges=(),
        )
        self.assertEqual(owning_identity_families(graph, (_family(),)), ())

    def test_proposal_keeps_alternative_and_does_not_claim_vulnerability(self) -> None:
        context = classify_http_authorization_observation(
            research_run_id="run-1",
            observation_id="obs-1",
            observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
            observation_run_id="run-1",
            payload={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
                "cross_object_request_status": 200,
            },
            owning_family_ids=(),
        )
        proposal, challenge = identity_anomaly_proposal_and_challenge(context)
        self.assertEqual(proposal.proposed_claim, IDENTITY_ANOMALY_CLAIM)
        self.assertEqual(proposal.source_references, ("obs-1",))
        self.assertEqual(challenge.alternative_explanations, (IDENTITY_ANOMALY_ALTERNATIVE,))
        self.assertNotIn("confirmed vulnerability", proposal.proposed_claim.lower())
        origin = exploratory_hypothesis_origin(context.structural_identity())
        self.assertTrue(origin.startswith("exh:"))
        self.assertEqual(
            exploratory_experiment_id("run-1", "hyp-1"),
            exploratory_experiment_id("run-1", "hyp-1"),
        )
        self.assertNotEqual(
            exploratory_experiment_id("run-1", "hyp-1"),
            exploratory_experiment_id("run-2", "hyp-1"),
        )

    def test_compiler_builds_discriminating_plan_from_durable_context(self) -> None:
        context = classify_http_authorization_observation(
            research_run_id="run-1",
            observation_id="obs-1",
            observation_kind="HTTP_AUTHORIZATION_DIFFERENTIAL",
            observation_run_id="run-1",
            payload={
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
                "cross_object_request_status": 200,
            },
            owning_family_ids=(),
        )
        plan = compile_identity_anomaly_experiment(
            context,
            hypothesis_id="hyp-exp",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertEqual(plan.required_capability, HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY)
        self.assertEqual(plan.expected_observation, HTTP_AUTHORIZATION_EXPECTED_OBSERVATION)
        self.assertEqual(
            plan.disconfirming_observation, HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION
        )
        self.assertEqual(plan.arguments["actor"], "alice")
        self.assertEqual(plan.arguments["cross_object"], "bob")
        self.assertEqual(plan.side_effect_level, 0)
        self.assertNotIn("body", plan.arguments)
        self.assertNotIn("headers", plan.arguments)


if __name__ == "__main__":
    unittest.main()
