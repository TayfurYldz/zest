"""Deterministic HTTP state-transition evaluator. Not an LLM. Not a Finding."""

from __future__ import annotations

from typing import Any, Mapping

from zest.research.assessment import (
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXECUTION_OUTCOMES,
    UNUSABLE_EXPERIMENT_STATES,
    AssessmentOutcome,
    EvaluatorKind,
    HypothesisAssessment,
)
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.invariant import InvariantKind
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import HTTP_STATE_TRANSITION_CAPABILITY

HTTP_STATE_TRANSITION_EVALUATION_STRATEGY = "http.state_transition.v1"
HTTP_STATE_TRANSITION_OBSERVATION_KIND = "HTTP_STATE_TRANSITION_AUTHORIZATION"
DENIED_OR_CONFLICT = frozenset({401, 403, 409})
APPROVED = "APPROVED"
DRAFT = "DRAFT"
UNDER_REVIEW = "UNDER_REVIEW"
REQUESTER = "requester"
UNAUTHORIZED_TRANSITION_FACTS = (
    "fact_actor_identity",
    "fact_resource_identity",
    "fact_pre_state",
    "fact_requested_transition",
    "fact_post_state",
    "fact_transition_occurred",
    "fact_became_approved",
    "fact_actor_is_requester",
    "fact_not_delegated",
    "fact_control_denied",
    "fact_violates_role_or_sequence",
)


class HttpStateTransitionEvaluator:
    strategy = HTTP_STATE_TRANSITION_EVALUATION_STRATEGY
    version = HTTP_STATE_TRANSITION_EVALUATION_STRATEGY

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment:
        if plan.evaluation_strategy != self.strategy:
            raise ResearchInputError("plan evaluation_strategy does not match evaluator")
        if plan.required_capability != HTTP_STATE_TRANSITION_CAPABILITY:
            raise ResearchInputError(
                "http.state_transition evaluator cannot assess other capabilities"
            )
        if feedback.evaluation_strategy != self.strategy:
            raise ResearchInputError("feedback evaluation_strategy does not match evaluator")
        if plan.hypothesis_id != feedback.hypothesis_id:
            raise ResearchInputError("plan and feedback hypothesis_id mismatch")

        rationale: dict[str, object] = {
            "reason_code": "",
            "expected_observation": plan.expected_observation,
            "disconfirming_observation": plan.disconfirming_observation,
            "execution_outcome": feedback.execution_outcome,
            "facts": {},
            "context": {
                "hypothesis_id": feedback.hypothesis_id,
                "experiment_id": feedback.experiment_id,
                "research_run_id": feedback.research_run_id,
            },
        }

        if self._unusable(feedback):
            rationale["reason_code"] = "RUNTIME_UNUSABLE"
            return self._result(
                AssessmentOutcome.EXECUTION_UNUSABLE, plan, feedback, rationale
            )

        observations = [
            item
            for item in feedback.observations
            if item.observation_kind == HTTP_STATE_TRANSITION_OBSERVATION_KIND
        ]
        if not observations:
            rationale["reason_code"] = "NO_TARGET_OBSERVATION"
            return self._result(AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale)

        facts = _facts(observations[0], plan.arguments)
        rationale["facts"] = facts
        missing = [name for name, ok in facts.items() if name.startswith("fact_") and not ok]
        if facts["fact_status_only_or_unchanged"]:
            rationale["reason_code"] = "STATUS_OR_UNCHANGED_STATE_IS_NOT_PROOF"
            return self._result(
                AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale
            )
        if facts["fact_stale_or_conflict"]:
            rationale["reason_code"] = "STALE_OR_CONFLICT"
            return self._result(
                AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale
            )
        if facts["fact_delegated"]:
            rationale["reason_code"] = "LEGITIMATE_DELEGATED_REVIEWER"
            rationale["invariant_kind"] = InvariantKind.ROLE_BOUNDARY.value
            return self._result(
                AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
            )
        if facts["fact_legitimate_reviewer"]:
            rationale["reason_code"] = "LEGITIMATE_REVIEWER_APPROVAL"
            rationale["invariant_kind"] = InvariantKind.ROLE_BOUNDARY.value
            return self._result(
                AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
            )
        if facts["fact_idempotent_repeat"]:
            rationale["reason_code"] = "IDEMPOTENT_NO_NEW_TRANSITION"
            return self._result(
                AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
            )
        if all(facts[name] for name in UNAUTHORIZED_TRANSITION_FACTS):
            rationale["reason_code"] = "UNAUTHORIZED_STATE_TRANSITION_ESTABLISHED"
            rationale["invariant_kind"] = facts["invariant_kind"]
            return self._result(
                AssessmentOutcome.CONSISTENT_WITH_PREDICTION, plan, feedback, rationale
            )
        if facts["fact_control_held_unchanged"]:
            rationale["reason_code"] = "WORKFLOW_CONTROL_HELD"
            rationale["missing_facts"] = missing
            return self._result(
                AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
            )
        rationale["reason_code"] = "STATE_TRANSITION_INCOMPLETE"
        rationale["missing_facts"] = missing
        return self._result(AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale)

    def _unusable(self, feedback: ExperimentFeedback) -> bool:
        if feedback.execution_outcome in UNUSABLE_EXECUTION_OUTCOMES:
            return True
        if feedback.attempt_state in UNUSABLE_ATTEMPT_STATES:
            return True
        if feedback.experiment_execution_state in UNUSABLE_EXPERIMENT_STATES:
            return True
        if feedback.invocation_status in {
            "TIMED_OUT",
            "START_FAILED",
            "PROCESS_FAILED",
            "PROTOCOL_ERROR",
            "CONTRACT_INVALID",
            "CANCELLED",
        }:
            return True
        return False

    def _result(
        self,
        outcome: AssessmentOutcome,
        plan: ExperimentPlan,
        feedback: ExperimentFeedback,
        rationale: dict[str, object],
    ) -> HypothesisAssessment:
        return HypothesisAssessment(
            outcome=outcome,
            evaluator_kind=EvaluatorKind.DETERMINISTIC,
            evaluator_version=self.version,
            rationale=rationale,
            hypothesis_id=feedback.hypothesis_id,
            experiment_id=feedback.experiment_id,
            research_run_id=feedback.research_run_id,
            observation_ids=feedback.observation_ids,
            evaluation_strategy=plan.evaluation_strategy,
        )


def _facts(observation: ObservedFact, arguments: Mapping[str, Any]) -> dict[str, Any]:
    payload = observation.payload
    actor = str(payload.get("actor") or arguments.get("actor") or "")
    resource_id = str(payload.get("resource_id") or arguments.get("resource_id") or "")
    origin = str(payload.get("authorized_origin") or arguments.get("authorized_origin") or "")
    transition = str(
        payload.get("requested_transition") or arguments.get("transition") or ""
    )
    actor_role = str(payload.get("actor_role") or "")
    pre_state = payload.get("pre_state")
    post_state = payload.get("post_state")
    response_status = payload.get("response_status")
    control_status = payload.get("control_status")
    approved_by = payload.get("approved_by")
    delegated = payload.get("delegated_reviewers")
    readers = delegated if isinstance(delegated, list) else []
    from_states = payload.get("approve_from_states")
    approve_from = from_states if isinstance(from_states, list) else []
    requires_role = str(payload.get("approve_requires_role") or "")
    state_changed = bool(payload.get("state_changed"))
    transition_ok = payload.get("transition_ok") is True
    explicitly_delegated = actor in readers
    requester = actor_role == REQUESTER
    became_approved = post_state == APPROVED and approved_by == actor
    role_bypass = (
        transition == "approve"
        and requester
        and not explicitly_delegated
        and pre_state == UNDER_REVIEW
        and post_state == APPROVED
        and state_changed
    )
    sequence_skip = (
        transition == "approve"
        and requester
        and not explicitly_delegated
        and pre_state == DRAFT
        and post_state == APPROVED
        and state_changed
    )
    status_only = response_status == 200 and not isinstance(post_state, str)
    unchanged_success = response_status == 200 and not state_changed
    idempotent = (
        unchanged_success
        and pre_state == APPROVED
        and post_state == APPROVED
    )
    stale = response_status == 409 and not state_changed
    control_denied = control_status in DENIED_OR_CONFLICT
    control_held = control_denied and not state_changed
    legitimate_reviewer = (
        state_changed
        and post_state == APPROVED
        and actor_role in {"reviewer", "admin"}
    )
    invariant_kind = ""
    if sequence_skip:
        invariant_kind = InvariantKind.SEQUENCE_PRECONDITION.value
    elif role_bypass:
        invariant_kind = InvariantKind.ROLE_BOUNDARY.value
    elif state_changed:
        invariant_kind = InvariantKind.STATE_TRANSITION.value
    return {
        "actor": actor,
        "actor_role": actor_role,
        "resource_id": resource_id,
        "authorized_origin": origin,
        "requested_transition": transition,
        "pre_state": pre_state,
        "post_state": post_state,
        "response_status": response_status,
        "control_status": control_status,
        "approved_by": approved_by,
        "delegated_reviewers": readers,
        "approve_from_states": approve_from,
        "approve_requires_role": requires_role,
        "invariant_kind": invariant_kind,
        "fact_actor_identity": bool(actor),
        "fact_resource_identity": bool(resource_id),
        "fact_pre_state": isinstance(pre_state, str) and bool(pre_state),
        "fact_requested_transition": transition == "approve",
        "fact_post_state": isinstance(post_state, str) and bool(post_state),
        "fact_transition_occurred": state_changed,
        "fact_became_approved": became_approved,
        "fact_actor_is_requester": requester,
        "fact_not_delegated": not explicitly_delegated,
        "fact_delegated": explicitly_delegated and became_approved,
        "fact_legitimate_reviewer": legitimate_reviewer,
        "fact_control_denied": control_denied,
        "fact_control_held_unchanged": control_held,
        "fact_violates_role_or_sequence": role_bypass or sequence_skip,
        "fact_role_bypass": role_bypass,
        "fact_sequence_skip": sequence_skip,
        "fact_status_only_or_unchanged": (status_only or (unchanged_success and not idempotent))
        and not stale
        and not control_held,
        "fact_idempotent_repeat": idempotent,
        "fact_stale_or_conflict": stale,
        "fact_shared_origin": bool(origin) and payload.get("authorized_origin") == origin,
        "transition_ok": transition_ok,
    }
