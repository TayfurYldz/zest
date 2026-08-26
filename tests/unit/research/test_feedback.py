from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.types import ResearchInputError


def _feedback(**overrides) -> ExperimentFeedback:
    values = dict(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        expected_observation="echoed value matches input",
        disconfirming_observation="no result or mismatched value",
        evaluation_strategy="diagnostic.echo.v1",
        execution_outcome="OBSERVATION_PRODUCED",
        observations=(),
    )
    values.update(overrides)
    return ExperimentFeedback(**values)


class ExperimentFeedbackTests(unittest.TestCase):
    def test_feedback_has_no_vulnerability_verdict(self) -> None:
        feedback = _feedback(
            observations=(
                ObservedFact(
                    observation_id="obs-1",
                    observation_kind="diagnostic.echo",
                    payload={"echoed": "ping"},
                ),
            )
        )
        self.assertFalse(hasattr(feedback, "severity"))
        self.assertFalse(hasattr(feedback, "finding"))
        self.assertFalse(hasattr(feedback, "evidence"))
        self.assertFalse(hasattr(feedback, "vulnerability"))
        self.assertEqual(feedback.observation_ids, ("obs-1",))

    def test_empty_hypothesis_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            _feedback(hypothesis_id=" ")


if __name__ == "__main__":
    unittest.main()
