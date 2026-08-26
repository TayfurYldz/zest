"""Deterministic diagnostic.echo evaluator. Not a security scanner."""

from __future__ import annotations

from zest.research.assessment import (
    DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXECUTION_OUTCOMES,
    UNUSABLE_EXPERIMENT_STATES,
    AssessmentOutcome,
    EvaluatorKind,
    HypothesisAssessment,
)
from zest.research.feedback import ExperimentFeedback
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY

DIAGNOSTIC_ECHO_OBSERVATION_KIND = "diagnostic.echo"


class DiagnosticEchoEvaluator:
    strategy = DIAGNOSTIC_ECHO_EVALUATION_STRATEGY
    version = DIAGNOSTIC_ECHO_EVALUATION_STRATEGY

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment:
        if plan.evaluation_strategy != self.strategy:
            raise ResearchInputError("plan evaluation_strategy does not match evaluator")
        if plan.required_capability != DIAGNOSTIC_ECHO_CAPABILITY:
            raise ResearchInputError("diagnostic.echo evaluator cannot assess other capabilities")
        if feedback.evaluation_strategy != self.strategy:
            raise ResearchInputError("feedback evaluation_strategy does not match evaluator")
        if plan.hypothesis_id != feedback.hypothesis_id:
            raise ResearchInputError("plan and feedback hypothesis_id mismatch")

        rationale: dict[str, object] = {
            "reason_code": "",
            "expected_observation": plan.expected_observation,
            "disconfirming_observation": plan.disconfirming_observation,
            "submitted_value": feedback.submitted_value,
            "observed_echoed": None,
            "execution_outcome": feedback.execution_outcome,
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

        echoes = [
            item
            for item in feedback.observations
            if item.observation_kind == DIAGNOSTIC_ECHO_OBSERVATION_KIND
        ]
        if not echoes:
            rationale["reason_code"] = "NO_TARGET_OBSERVATION"
            return self._result(
                AssessmentOutcome.INCONCLUSIVE, plan, feedback, rationale
            )

        if feedback.submitted_value is None:
            rationale["reason_code"] = "MISSING_SUBMITTED_VALUE"
            return self._result(
                AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale
            )

        echoed = echoes[0].payload.get("echoed")
        rationale["observed_echoed"] = echoed
        if not isinstance(echoed, str):
            rationale["reason_code"] = "MALFORMED_ECHO_PAYLOAD"
            return self._result(
                AssessmentOutcome.NEEDS_MORE_CONTEXT, plan, feedback, rationale
            )
        if echoed == feedback.submitted_value:
            rationale["reason_code"] = "ECHO_MATCHED"
            return self._result(
                AssessmentOutcome.CONSISTENT_WITH_PREDICTION, plan, feedback, rationale
            )
        rationale["reason_code"] = "ECHO_MISMATCHED"
        return self._result(
            AssessmentOutcome.CONTRADICTS_PREDICTION, plan, feedback, rationale
        )

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
