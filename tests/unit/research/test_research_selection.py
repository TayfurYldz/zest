from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.assessment import AssessmentOutcome
from zest.research.exploration import SelectionOutcome
from zest.research.planning import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
)
from zest.research.selection import (
    DiscriminationLevel,
    ExperimentOption,
    ExperimentPurpose,
    HypothesisFamily,
    HypothesisLifecycle,
    ObjectProbeContext,
    ObservedResearchFact,
    ResearchStopReason,
    WorkflowProbeContext,
    build_portfolio,
    experiment_option_identity,
    propose_experiment_options,
    select_next_experiment,
    selector_key,
    stop_reason_for_portfolio,
)


def _option(**overrides) -> ExperimentOption:
    values = dict(
        option_id="opt-1",
        hypothesis_id="hyp-object",
        hypothesis_ids=("hyp-object",),
        purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
        required_capability="http.authorization.differential",
        requested_observation="object authorization differential including owner and controls",
        expected_supporting_observation="foreign owner proven",
        expected_disconfirming_observation="denied or public",
        required_negative_control="secure_only object control",
        unresolved_facts=("cross_object_owner",),
        estimated_request_cost=4,
        side_effect_level=0,
        can_falsify_live=True,
        distinguishes_competing_count=2,
        resolves_missing_fact=True,
        provides_missing_negative_control=False,
        authorized_origin="http://127.0.0.1:9",
        target_reference="http://127.0.0.1:9",
        in_authorized_origin=True,
        context_signature="ctx-a",
        structural_identity="id-a",
        plan_arguments={"actor": "alice", "own_object": "alice", "cross_object": "bob"},
        discrimination=DiscriminationLevel.HIGH_DISCRIMINATION,
    )
    values.update(overrides)
    return ExperimentOption(**values)


class ResearchSelectionUnitTests(unittest.TestCase):
    def test_selector_prefers_falsify_then_missing_facts_then_lower_side_effect(self) -> None:
        low = _option(
            option_id="opt-workflow",
            hypothesis_id="hyp-workflow",
            hypothesis_ids=("hyp-workflow",),
            purpose=ExperimentPurpose.WORKFLOW_TRANSITION_PROBE,
            required_capability="http.state_transition",
            side_effect_level=1,
            structural_identity="id-w",
            context_signature="ctx-w",
        )
        high = _option(option_id="opt-object", structural_identity="id-o")
        decisions = select_next_experiment((low, high))
        selected = [item for item in decisions if item.selected]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].option.option_id, "opt-object")
        self.assertIn("LEXICOGRAPHIC_SELECTION", selected[0].reason_codes)
        self.assertNotIn("priority_score", selected[0].reason_codes)

    def test_unauthorized_option_is_blocked_not_selected(self) -> None:
        denied = _option(
            option_id="opt-denied",
            in_authorized_origin=False,
            target_reference="http://127.0.0.1:9",
            structural_identity="id-denied",
        )
        allowed = _option(option_id="opt-ok", structural_identity="id-ok")
        decisions = select_next_experiment((denied, allowed))
        blocked = [item for item in decisions if item.option.option_id == "opt-denied"]
        selected = [item for item in decisions if item.selected]
        self.assertEqual(blocked[0].outcome, SelectionOutcome.BLOCKED_POLICY)
        self.assertEqual(selected[0].option.option_id, "opt-ok")

    def test_redundant_identity_is_skipped(self) -> None:
        option = _option()
        decisions = select_next_experiment(
            (option,), executed_identities=frozenset({option.structural_identity})
        )
        self.assertEqual(decisions[0].outcome, SelectionOutcome.SKIP_DUPLICATE)

    def test_negative_knowledge_is_context_bound(self) -> None:
        same = _option(context_signature="ctx-a", structural_identity="id-a")
        other = _option(
            option_id="opt-b",
            context_signature="ctx-b",
            structural_identity="id-b",
            plan_arguments={"actor": "carol", "own_object": "carol", "cross_object": "dave"},
        )
        decisions = select_next_experiment(
            (same, other),
            negative_context_signatures=frozenset({"ctx-a"}),
        )
        skipped = [item for item in decisions if item.outcome is SelectionOutcome.SKIP_LOW_INFORMATION]
        selected = [item for item in decisions if item.selected]
        self.assertEqual(skipped[0].option.option_id, "opt-1")
        self.assertEqual(selected[0].option.option_id, "opt-b")

    def test_same_state_replay_is_deterministic(self) -> None:
        left = (
            _option(option_id="b", structural_identity="id-b", context_signature="z"),
            _option(option_id="a", structural_identity="id-a", context_signature="a"),
        )
        right = tuple(reversed(left))
        first = select_next_experiment(left)
        second = select_next_experiment(right)
        self.assertEqual(
            [item.option.option_id for item in first if item.selected],
            [item.option.option_id for item in second if item.selected],
        )
        self.assertEqual(selector_key(left[0])[0], 0)

    def test_input_order_perturbation_does_not_change_semantic_choice(self) -> None:
        portfolio = build_portfolio(
            hypotheses=(
                ("hyp-object", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),
                ("hyp-workflow", HTTP_STATE_TRANSITION_CLAIM),
            ),
            assessments_by_hypothesis={"hyp-object": (), "hyp-workflow": ()},
            observation_ids_by_hypothesis={"hyp-object": (), "hyp-workflow": ()},
        )
        object_ctx = (ObjectProbeContext("alice", "alice", "bob"),)
        workflow_ctx = (WorkflowProbeContext("alice", "R1"),)
        first = propose_experiment_options(
            portfolio=portfolio,
            observations=(),
            authorized_origin="http://127.0.0.1:1",
            candidate_origins=("http://127.0.0.1:1",),
            object_contexts=object_ctx,
            workflow_contexts=workflow_ctx,
            id_prefix="one",
        )
        second = propose_experiment_options(
            portfolio=portfolio,
            observations=(),
            authorized_origin="http://127.0.0.1:1",
            candidate_origins=("http://127.0.0.1:1",),
            object_contexts=object_ctx,
            workflow_contexts=workflow_ctx,
            id_prefix="two",
        )
        left = select_next_experiment(first)
        right = select_next_experiment(tuple(reversed(second)))
        self.assertEqual(
            [item.option.purpose for item in left if item.selected],
            [item.option.purpose for item in right if item.selected],
        )

    def test_irrelevant_observation_does_not_redirect_selection(self) -> None:
        portfolio = build_portfolio(
            hypotheses=(("hyp-object", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),),
            assessments_by_hypothesis={"hyp-object": ()},
            observation_ids_by_hypothesis={"hyp-object": ()},
        )
        noise = ObservedResearchFact(
            observation_id="obs-noise",
            observation_kind="HTTP_BANNER",
            payload={"banner": "nginx"},
        )
        options = propose_experiment_options(
            portfolio=portfolio,
            observations=(noise,),
            authorized_origin="http://127.0.0.1:1",
            candidate_origins=("http://127.0.0.1:1",),
            object_contexts=(ObjectProbeContext("alice", "alice", "bob"),),
            workflow_contexts=(),
            id_prefix="noise",
        )
        selected = [item for item in select_next_experiment(options) if item.selected]
        self.assertEqual(selected[0].option.purpose, ExperimentPurpose.OBJECT_CROSS_PROBE)

    def test_public_visibility_changes_lifecycle_not_identity(self) -> None:
        portfolio = build_portfolio(
            hypotheses=(("hyp-object", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),),
            assessments_by_hypothesis={
                "hyp-object": (AssessmentOutcome.CONTRADICTS_PREDICTION.value,)
            },
            observation_ids_by_hypothesis={"hyp-object": ("obs-1",)},
        )
        self.assertEqual(
            portfolio.hypotheses[0].lifecycle, HypothesisLifecycle.FALSIFIED
        )
        self.assertEqual(portfolio.hypotheses[0].family, HypothesisFamily.OBJECT_AUTHORIZATION)

    def test_historical_assessments_are_appended_not_rewritten(self) -> None:
        portfolio = build_portfolio(
            hypotheses=(("hyp-object", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),),
            assessments_by_hypothesis={
                "hyp-object": (
                    AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value,
                    AssessmentOutcome.CONTRADICTS_PREDICTION.value,
                )
            },
            observation_ids_by_hypothesis={"hyp-object": ("obs-1", "obs-2")},
        )
        self.assertEqual(
            portfolio.hypotheses[0].assessment_outcomes[0],
            AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value,
        )
        self.assertEqual(portfolio.hypotheses[0].lifecycle, HypothesisLifecycle.FALSIFIED)

    def test_budget_exhaustion_does_not_falsify(self) -> None:
        portfolio = build_portfolio(
            hypotheses=(("hyp-object", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),),
            assessments_by_hypothesis={"hyp-object": ()},
            observation_ids_by_hypothesis={"hyp-object": ()},
        )
        reason = stop_reason_for_portfolio(
            portfolio,
            selected=None,
            budget_exhausted=True,
            max_cycles_reached=False,
            operational=False,
        )
        self.assertEqual(reason, ResearchStopReason.BUDGET_EXHAUSTED)
        self.assertEqual(portfolio.hypotheses[0].lifecycle, HypothesisLifecycle.ACTIVE)

    def test_experiment_identity_ignores_hypothesis_id(self) -> None:
        left = experiment_option_identity(
            capability="http.authorization.differential",
            purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
            origin="http://127.0.0.1:1",
            actor="alice",
            resource="alice:bob",
            operation="vulnerable",
        )
        right = experiment_option_identity(
            capability="http.authorization.differential",
            purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
            origin="http://127.0.0.1:1",
            actor="alice",
            resource="alice:bob",
            operation="vulnerable",
        )
        other = experiment_option_identity(
            capability="http.authorization.differential",
            purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
            origin="http://127.0.0.1:1",
            actor="carol",
            resource="carol:dave",
            operation="vulnerable",
        )
        self.assertEqual(left, right)
        self.assertNotEqual(left, other)

    def test_negative_knowledge_is_bound_to_origin_context(self) -> None:
        from zest.research.selection import object_origin_reference

        alice = ObjectProbeContext("alice", "alice", "bob")
        carol = ObjectProbeContext("carol", "carol", "dave")
        portfolio = build_portfolio(
            hypotheses=(
                ("hyp-a", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),
                ("hyp-b", HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM),
            ),
            assessments_by_hypothesis={
                "hyp-a": (AssessmentOutcome.CONTRADICTS_PREDICTION.value,),
                "hyp-b": (),
            },
            observation_ids_by_hypothesis={"hyp-a": ("obs-a",), "hyp-b": ()},
            origin_reference_by_hypothesis={
                "hyp-a": object_origin_reference(alice),
                "hyp-b": object_origin_reference(carol),
            },
        )
        self.assertEqual(portfolio.hypotheses[0].lifecycle, HypothesisLifecycle.FALSIFIED)
        self.assertEqual(portfolio.hypotheses[1].lifecycle, HypothesisLifecycle.ACTIVE)
        options = propose_experiment_options(
            portfolio=portfolio,
            observations=(),
            authorized_origin="http://127.0.0.1:1",
            candidate_origins=("http://127.0.0.1:1",),
            object_contexts=(alice, carol),
            workflow_contexts=(),
            id_prefix="bound",
        )
        selected = [item for item in select_next_experiment(options) if item.selected]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].option.hypothesis_id, "hyp-b")
        self.assertEqual(selected[0].option.plan_arguments["actor"], "carol")

    def test_option_rejects_ground_truth_keys(self) -> None:
        with self.assertRaises(Exception):
            _option(plan_arguments={"expected_vulnerable": True})


if __name__ == "__main__":
    unittest.main()
