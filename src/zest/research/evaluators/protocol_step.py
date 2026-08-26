"""Deterministic protocol-step evaluator. Records raw-exchange facts, not findings."""

from __future__ import annotations

from zest.research.assessment import (
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXECUTION_OUTCOMES,
    UNUSABLE_EXPERIMENT_STATES,
    AssessmentOutcome,
    EvaluatorKind,
    HypothesisAssessment,
)
from zest.research.feedback import ExperimentFeedback
from zest.research.protocol.step_compile import (
    HTTP_RAW_EXCHANGE_CAPABILITY,
    PROTOCOL_STEP_EVALUATION_STRATEGY,
)
from zest.research.types import ExperimentPlan, ResearchInputError

HTTP_RAW_EXCHANGE_OBSERVATION_KIND = "HTTP_RAW_EXCHANGE"


class ProtocolStepEvaluator:
    strategy = PROTOCOL_STEP_EVALUATION_STRATEGY
    version = PROTOCOL_STEP_EVALUATION_STRATEGY

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment:
        if plan.evaluation_strategy != self.strategy:
            raise ResearchInputError("plan evaluation_strategy does not match evaluator")
        if plan.required_capability != HTTP_RAW_EXCHANGE_CAPABILITY:
            raise ResearchInputError("protocol step evaluator requires http.raw_exchange")
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
            return self._result(AssessmentOutcome.EXECUTION_UNUSABLE, plan, feedback, rationale)
        observations = [
            item
            for item in feedback.observations
            if item.observation_kind == HTTP_RAW_EXCHANGE_OBSERVATION_KIND
        ]
        if not observations:
            rationale["reason_code"] = "NO_TARGET_OBSERVATION"
            return self._result(AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale)
        payload = observations[0].payload
        rationale["facts"] = {
            "status_code": payload.get("status_code"),
            "framing_profile": payload.get("framing_profile"),
            "write_count": payload.get("write_count"),
            "redirect": payload.get("redirect"),
        }
        rationale["reason_code"] = "RAW_EXCHANGE_OBSERVED"
        return self._result(
            AssessmentOutcome.CONSISTENT_WITH_PREDICTION, plan, feedback, rationale
        )

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
            hypothesis_id=feedback.hypothesis_id,
            experiment_id=feedback.experiment_id,
            research_run_id=feedback.research_run_id,
            observation_ids=feedback.observation_ids,
            evaluation_strategy=plan.evaluation_strategy,
        )
