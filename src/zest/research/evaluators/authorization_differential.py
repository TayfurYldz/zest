"""Deterministic HTTP authorization-differential evaluator. Not an LLM. Not a Finding."""

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
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY

HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY = "http.authorization.differential.v1"
HTTP_AUTHORIZATION_DIFFERENTIAL_OBSERVATION_KIND = "HTTP_AUTHORIZATION_DIFFERENTIAL"
DENIED_STATUSES = frozenset({401, 403})
PUBLIC_VISIBILITY = "PUBLIC"
SHARED_RESOURCE_KIND = "SHARED"
UNAUTHORIZED_DIFFERENTIAL_FACTS = (
    "fact_owner_can_read_own",
    "fact_actor_can_read_cross_object",
    "fact_returned_object_is_cross_owner",
    "fact_unauthenticated_denied",
    "fact_secure_control_denied",
    "fact_shared_origin",
    "fact_not_explicitly_public",
    "fact_not_explicitly_delegated",
    "fact_not_shared_resource",
)


class HttpAuthorizationDifferentialEvaluator:
    strategy = HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY
    version = HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment:
        if plan.evaluation_strategy != self.strategy:
            raise ResearchInputError("plan evaluation_strategy does not match evaluator")
        if plan.required_capability != HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY:
            raise ResearchInputError(
                "http.authorization.differential evaluator cannot assess other capabilities"
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
            if item.observation_kind == HTTP_AUTHORIZATION_DIFFERENTIAL_OBSERVATION_KIND
        ]
        if not observations:
            rationale["reason_code"] = "NO_TARGET_OBSERVATION"
            return self._result(AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale)

        facts = _facts(observations[0], plan.arguments)
        rationale["facts"] = facts
        missing = [name for name, ok in facts.items() if name.startswith("fact_") and not ok]
        if facts["fact_cross_status_only"]:
            rationale["reason_code"] = "STATUS_ALONE_IS_NOT_OBJECT_ACCESS_PROOF"
            return self._result(
                AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale
            )
        if facts["fact_actor_can_read_cross_object"] and facts["fact_returned_object_is_cross_owner"]:
            if not facts["fact_not_explicitly_public"]:
                rationale["reason_code"] = "LEGITIMATE_PUBLIC_ACCESS"
                return self._result(
                    AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
                )
            if not facts["fact_not_explicitly_delegated"]:
                rationale["reason_code"] = "LEGITIMATE_DELEGATED_ACCESS"
                return self._result(
                    AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
                )
            if not facts["fact_not_shared_resource"]:
                rationale["reason_code"] = "LEGITIMATE_SHARED_ACCESS"
                return self._result(
                    AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
                )
        if all(facts[name] for name in UNAUTHORIZED_DIFFERENTIAL_FACTS):
            rationale["reason_code"] = "AUTHORIZATION_DIFFERENTIAL_ESTABLISHED"
            return self._result(
                AssessmentOutcome.CONSISTENT_WITH_PREDICTION, plan, feedback, rationale
            )
        if facts["fact_secure_control_denied"] and not facts["fact_actor_can_read_cross_object"]:
            rationale["reason_code"] = "OBJECT_ACCESS_CONTROL_HELD"
            rationale["missing_facts"] = missing
            return self._result(
                AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
            )
        rationale["reason_code"] = "DIFFERENTIAL_INCOMPLETE"
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
    own_object = str(payload.get("own_object") or arguments.get("own_object") or "")
    cross_object = str(payload.get("cross_object") or arguments.get("cross_object") or "")
    origin = str(payload.get("authorized_origin") or arguments.get("authorized_origin") or "")
    owner_status = payload.get("owner_request_status")
    owner_owner = payload.get("owner_request_object_owner")
    cross_status = payload.get("cross_object_request_status")
    cross_owner = payload.get("cross_object_request_object_owner")
    secure_status = payload.get("secure_control_status")
    unauth_status = payload.get("unauthenticated_control_status")
    cross_visibility = payload.get("cross_object_request_visibility")
    cross_readers = payload.get("cross_object_request_authorized_readers")
    cross_kind = payload.get("cross_object_request_resource_kind")
    owner_ok = owner_status == 200 and owner_owner == own_object == actor
    owner_proven = (
        isinstance(cross_owner, str)
        and bool(cross_owner.strip())
        and cross_owner == cross_object
    )
    cross_ok = cross_status == 200 and owner_proven and cross_object != actor
    status_only = cross_status == 200 and not owner_proven
    readers = cross_readers if isinstance(cross_readers, list) else []
    explicitly_public = (
        isinstance(cross_visibility, str)
        and cross_visibility.strip().upper() == PUBLIC_VISIBILITY
    )
    explicitly_delegated = actor in readers
    explicitly_shared = (
        isinstance(cross_kind, str) and cross_kind.strip().upper() == SHARED_RESOURCE_KIND
    )
    return {
        "actor": actor,
        "own_object": own_object,
        "cross_object": cross_object,
        "authorized_origin": origin,
        "owner_request_status": owner_status,
        "owner_request_object_owner": owner_owner,
        "cross_object_request_status": cross_status,
        "cross_object_request_object_owner": cross_owner,
        "cross_object_request_visibility": cross_visibility,
        "cross_object_request_authorized_readers": readers,
        "cross_object_request_resource_kind": cross_kind,
        "secure_control_status": secure_status,
        "unauthenticated_control_status": unauth_status,
        "fact_owner_can_read_own": owner_ok,
        "fact_actor_can_read_cross_object": cross_status == 200,
        "fact_returned_object_is_cross_owner": cross_ok,
        "fact_unauthenticated_denied": unauth_status in DENIED_STATUSES,
        "fact_secure_control_denied": secure_status == 403,
        "fact_shared_origin": bool(origin) and payload.get("authorized_origin") == origin,
        "fact_cross_status_only": status_only,
        "fact_not_explicitly_public": not explicitly_public,
        "fact_not_explicitly_delegated": not explicitly_delegated,
        "fact_not_shared_resource": not explicitly_shared,
    }
