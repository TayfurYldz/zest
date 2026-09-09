"""OAST callback evaluator. Correlated interaction is not automatically a Finding."""

from __future__ import annotations

from zest.application.oast_source import OAST_CALLBACK_EVALUATION_STRATEGY
from zest.research.assessment import (
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXECUTION_OUTCOMES,
    UNUSABLE_EXPERIMENT_STATES,
    AssessmentOutcome,
    EvaluatorKind,
    HypothesisAssessment,
)
from zest.research.feedback import ExperimentFeedback
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import HTTP_TRANSACTION_CAPABILITY

OAST_CALLBACK_OBSERVATION_KIND = "OAST_CALLBACK"
OAST_NO_CALLBACK_OBSERVATION_KIND = "OAST_NO_CALLBACK"


class OastCallbackEvaluator:
    strategy = OAST_CALLBACK_EVALUATION_STRATEGY
    version = OAST_CALLBACK_EVALUATION_STRATEGY

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment:
        if plan.evaluation_strategy != self.strategy:
            raise ResearchInputError("plan evaluation_strategy does not match evaluator")
        if plan.required_capability != HTTP_TRANSACTION_CAPABILITY:
            raise ResearchInputError("oast callback evaluator requires http.transaction")
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
            "not_a_finding": True,
        }
        if self._unusable(feedback):
            rationale["reason_code"] = "RUNTIME_UNUSABLE"
            return self._result(AssessmentOutcome.EXECUTION_UNUSABLE, plan, feedback, rationale)
        correlated = [
            item
            for item in feedback.observations
            if item.observation_kind == OAST_CALLBACK_OBSERVATION_KIND
        ]
        if correlated:
            payload = dict(correlated[0].payload)
            rationale["facts"] = {
                "correlation_status": "CORRELATED",
                "normalized_digest": payload.get("normalized_digest"),
                "callback_channel": payload.get("callback_channel"),
            }
            rationale["reason_code"] = "OAST_CALLBACK_CORRELATED"
            return self._result(
                AssessmentOutcome.CONSISTENT_WITH_PREDICTION, plan, feedback, rationale
            )
        timeout = [
            item
            for item in feedback.observations
            if item.observation_kind == OAST_NO_CALLBACK_OBSERVATION_KIND
        ]
        if timeout:
            rationale["facts"] = {"correlation_status": "NO_CALLBACK_TIMEOUT"}
            rationale["reason_code"] = "NO_CALLBACK_OBSERVED"
            return self._result(AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale)
        rationale["reason_code"] = "OAST_WAITING_CALLBACK"
        return self._result(AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale)

    def _unusable(self, feedback: ExperimentFeedback) -> bool:
        if feedback.execution_outcome in UNUSABLE_EXECUTION_OUTCOMES:
            return True
        if feedback.attempt_state in UNUSABLE_ATTEMPT_STATES:
            return True
        if feedback.experiment_execution_state in UNUSABLE_EXPERIMENT_STATES:
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
            hypothesis_id=plan.hypothesis_id,
            experiment_id=feedback.experiment_id,
            research_run_id=feedback.research_run_id,
            observation_ids=feedback.observation_ids,
            evaluation_strategy=self.strategy,
        )
