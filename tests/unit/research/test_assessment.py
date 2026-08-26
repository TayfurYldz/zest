from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.assessment import AssessmentOutcome, EvaluatorKind
from zest.research.evaluators.diagnostic_echo import DiagnosticEchoEvaluator
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.planning import plan_diagnostic_echo
from zest.research.types import ResearchInputError


def _plan(message: str = "ping"):
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )


def _echo(observation_id: str, echoed: str) -> ObservedFact:
    return ObservedFact(
        observation_id=observation_id,
        observation_kind="diagnostic.echo",
        payload={"echoed": echoed},
    )


def _feedback(**overrides) -> ExperimentFeedback:
    values = dict(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        expected_observation="echoed value matches input",
        disconfirming_observation="no result or mismatched value",
        evaluation_strategy="diagnostic.echo.v1",
        execution_outcome="OBSERVATION_PRODUCED",
        observations=(_echo("obs-1", "ping"),),
        submitted_value="ping",
        invocation_status="COMPLETED",
        experiment_execution_state="EXECUTION_SUCCEEDED",
        attempt_state="COMPLETED",
    )
    values.update(overrides)
    return ExperimentFeedback(**values)


class DiagnosticEchoEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = DiagnosticEchoEvaluator()
        self.plan = _plan()

    def test_matching_observation_is_consistent_not_proven(self) -> None:
        assessment = self.evaluator.evaluate(self.plan, _feedback())
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        self.assertEqual(assessment.evaluator_kind, EvaluatorKind.DETERMINISTIC)
        self.assertNotEqual(assessment.outcome.value, "SUPPORTED")
        self.assertFalse(hasattr(assessment, "confidence"))
        self.assertNotIn("confidence", assessment.rationale)
        self.assertNotIn("severity", assessment.rationale)

    def test_mismatch_contradicts_prediction_under_context(self) -> None:
        assessment = self.evaluator.evaluate(
            self.plan,
            _feedback(observations=(_echo("obs-1", "nope"),)),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertEqual(assessment.rationale["reason_code"], "ECHO_MISMATCHED")
        self.assertEqual(assessment.rationale["context"]["experiment_id"], "exp-1")

    def test_runtime_timeout_is_execution_unusable(self) -> None:
        assessment = self.evaluator.evaluate(
            self.plan,
            _feedback(
                execution_outcome="INVOCATION_FAILED",
                observations=(),
                submitted_value="ping",
                invocation_status="TIMED_OUT",
                experiment_execution_state="EXECUTION_FAILED",
                attempt_state="TIMED_OUT",
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.EXECUTION_UNUSABLE)
        self.assertFalse(assessment.execution_usable)

    def test_insufficient_observation_is_inconclusive(self) -> None:
        assessment = self.evaluator.evaluate(
            self.plan,
            _feedback(
                observations=(),
                execution_outcome="NO_OBSERVATION",
                invocation_status="COMPLETED",
                experiment_execution_state="EXECUTION_SUCCEEDED",
                attempt_state="COMPLETED",
            ),
        )
        self.assertEqual(assessment.outcome, AssessmentOutcome.INCONCLUSIVE)

    def test_same_inputs_are_deterministic(self) -> None:
        first = self.evaluator.evaluate(self.plan, _feedback())
        second = self.evaluator.evaluate(self.plan, _feedback())
        self.assertEqual(first.outcome, second.outcome)
        self.assertEqual(first.rationale, second.rationale)
        self.assertEqual(first.evaluator_version, second.evaluator_version)

    def test_worker_result_cannot_choose_evaluator(self) -> None:
        with self.assertRaises(ResearchInputError):
            self.evaluator.evaluate(
                self.plan,
                _feedback(evaluation_strategy="worker.chose.this"),
            )

    def test_assessment_cannot_carry_evidence_keys(self) -> None:
        assessment = self.evaluator.evaluate(self.plan, _feedback())
        self.assertFalse(hasattr(assessment, "evidence"))
        self.assertFalse(hasattr(assessment, "finding"))
        self.assertFalse(hasattr(assessment, "candidate"))


if __name__ == "__main__":
    unittest.main()
