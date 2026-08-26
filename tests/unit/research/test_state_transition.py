from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.assessment import AssessmentOutcome, default_evaluator_registry
from zest.research.candidate import (
    CandidateAdmissionContext,
    CandidateEvidenceRef,
    CandidateProposal,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
    admit_candidate,
    propose_authorization_differential_candidate,
    propose_state_transition_candidate,
)
from zest.research.evidence import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
    EvidenceAdmissionContext,
    EvidenceObservationRef,
    EvidencePolarity,
    propose_authorization_differential_evidence,
    propose_state_transition_evidence,
)
from zest.research.evaluators.state_transition import HttpStateTransitionEvaluator
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.invariant import InvariantKind
from zest.research.planning import plan_state_transition


def _plan(**overrides):
    values = dict(
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        target_reference="http://127.0.0.1:9",
        authorized_origin="http://127.0.0.1:9",
        actor="alice",
        resource_id="R1",
        transition="approve",
        area="workflow",
    )
    values.update(overrides)
    return plan_state_transition(**values)


def _payload(**overrides):
    values = dict(
        authorized_origin="http://127.0.0.1:9",
        area="workflow",
        actor="alice",
        actor_role="requester",
        resource_id="R1",
        requested_transition="approve",
        pre_state="UNDER_REVIEW",
        response_status=200,
        post_state="APPROVED",
        state_changed=True,
        approved_by="alice",
        owner="alice",
        delegated_reviewers=[],
        approve_requires_role="reviewer",
        approve_from_states=["UNDER_REVIEW"],
        control_status=403,
        pre_status=200,
        post_status=200,
        transition_ok=False,
    )
    values.update(overrides)
    return values


def _feedback(payload=None, **overrides):
    values = dict(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        expected_observation="authenticated requester changes workflow state to APPROVED without reviewer authority or required prior states, while the control path denies the transition",
        disconfirming_observation="the requested transition is denied or authoritative state does not change",
        evaluation_strategy="http.state_transition.v1",
        execution_outcome="OBSERVATION_PRODUCED",
        observations=(
            ObservedFact(
                observation_id="obs-1",
                observation_kind="HTTP_STATE_TRANSITION_AUTHORIZATION",
                payload=_payload() if payload is None else payload,
            ),
        ),
        invocation_status="SUCCEEDED",
        experiment_execution_state="EXECUTION_SUCCEEDED",
        attempt_state="COMPLETED",
    )
    values.update(overrides)
    return ExperimentFeedback(**values)


class StateTransitionEvaluatorTests(unittest.TestCase):
    def test_plan_is_level_one_post_capability(self) -> None:
        plan = _plan()
        self.assertEqual(plan.required_capability, "http.state_transition")
        self.assertEqual(plan.side_effect_level, 1)
        self.assertEqual(plan.action, "probe")

    def test_role_bypass_is_consistent_role_boundary(self) -> None:
        assessment = HttpStateTransitionEvaluator().evaluate(_plan(), _feedback())
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        self.assertEqual(
            assessment.rationale["invariant_kind"], InvariantKind.ROLE_BOUNDARY.value
        )

    def test_sequence_skip_is_sequence_precondition(self) -> None:
        assessment = HttpStateTransitionEvaluator().evaluate(
            _plan(),
            _feedback(_payload(pre_state="DRAFT")),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        self.assertEqual(
            assessment.rationale["invariant_kind"], InvariantKind.SEQUENCE_PRECONDITION.value
        )

    def test_http_200_unchanged_state_is_not_proof(self) -> None:
        assessment = HttpStateTransitionEvaluator().evaluate(
            _plan(),
            _feedback(
                _payload(
                    post_state="UNDER_REVIEW",
                    state_changed=False,
                    approved_by=None,
                    control_status=200,
                    transition_ok=True,
                )
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.NEEDS_MORE_CONTEXT)
        self.assertEqual(
            assessment.rationale["reason_code"], "STATUS_OR_UNCHANGED_STATE_IS_NOT_PROOF"
        )

    def test_delegated_reviewer_contradicts(self) -> None:
        assessment = HttpStateTransitionEvaluator().evaluate(
            _plan(),
            _feedback(_payload(delegated_reviewers=["alice"])),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertEqual(assessment.rationale["reason_code"], "LEGITIMATE_DELEGATED_REVIEWER")

    def test_timeout_is_unusable(self) -> None:
        assessment = HttpStateTransitionEvaluator().evaluate(
            _plan(),
            _feedback(invocation_status="TIMED_OUT", observations=()),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.EXECUTION_UNUSABLE)

    def test_default_registry_includes_state_transition(self) -> None:
        registry = default_evaluator_registry()
        self.assertEqual(
            registry.get("http.state_transition.v1").strategy,
            "http.state_transition.v1",
        )


class CrossClassAdmissionTests(unittest.TestCase):
    def test_bola_evidence_cannot_seed_workflow_candidate(self) -> None:
        context = CandidateAdmissionContext(
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence=(
                CandidateEvidenceRef(
                    evidence_id="ev-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    experiment_id="exp-1",
                    polarity=EvidencePolarity.SUPPORTING.value,
                    claim_scope=HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
                ),
            ),
        )
        self.assertIsNone(propose_state_transition_candidate(context, proposal_id="p1"))
        proposal = CandidateProposal(
            proposal_id="p1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence_ids=("ev-1",),
            claim=HTTP_STATE_TRANSITION_CLAIM,
            classification=HTTP_STATE_TRANSITION_CLASSIFICATION,
            rationale={"reason_code": "CROSS_CLASS_TEST", "not_a_finding": True},
            provenance={"source": "test"},
        )
        decision = admit_candidate(proposal, context)
        self.assertFalse(decision.admitted)
        self.assertIn("CROSS_CLASS_EVIDENCE_REJECTED", decision.reason_codes)

    def test_workflow_evidence_cannot_seed_bola_candidate(self) -> None:
        context = CandidateAdmissionContext(
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence=(
                CandidateEvidenceRef(
                    evidence_id="ev-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    experiment_id="exp-1",
                    polarity=EvidencePolarity.SUPPORTING.value,
                    claim_scope=HTTP_STATE_TRANSITION_CLAIM,
                ),
            ),
        )
        self.assertIsNone(propose_authorization_differential_candidate(context, proposal_id="p1"))
        proposal = propose_state_transition_candidate(context, proposal_id="p1")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.classification, HTTP_STATE_TRANSITION_CLASSIFICATION)
        self.assertNotEqual(proposal.classification, HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION)

    def test_needs_more_context_does_not_propose_workflow_evidence(self) -> None:
        context = EvidenceAdmissionContext(
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            evaluation_strategy="http.state_transition.v1",
            observations=(
                EvidenceObservationRef(
                    observation_id="obs-1",
                    research_run_id="run-1",
                    worker_result_id="wr-1",
                    observation_kind="HTTP_STATE_TRANSITION_AUTHORIZATION",
                ),
            ),
            assessment_id="as-1",
            assessment_outcome=AssessmentOutcome.NEEDS_MORE_CONTEXT,
        )
        self.assertIsNone(propose_state_transition_evidence(context, proposal_id="p1"))
        self.assertIsNone(propose_authorization_differential_evidence(context, proposal_id="p1"))


if __name__ == "__main__":
    unittest.main()
