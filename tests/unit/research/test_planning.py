from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.planning import (
    DIAGNOSTIC_LOOP_STATEMENT,
    human_seeded_hypothesis,
    plan_admitted_hypothesis,
    plan_diagnostic_echo,
)
from zest.research.proposals import parse_hypothesis_challenge, parse_hypothesis_proposal
from zest.research.types import ExperimentPlan, ResearchInputError


class ResearchPlanningTests(unittest.TestCase):
    def test_human_seeded_hypothesis_is_not_a_security_finding(self) -> None:
        draft = human_seeded_hypothesis(DIAGNOSTIC_LOOP_STATEMENT)
        self.assertEqual(draft.origin, "human")
        self.assertEqual(draft.statement, DIAGNOSTIC_LOOP_STATEMENT)
        self.assertFalse(hasattr(draft, "confidence"))
        self.assertFalse(hasattr(draft, "severity"))
        self.assertFalse(hasattr(draft, "vulnerability_type"))

    def test_experiment_plan_has_no_judgment_fields(self) -> None:
        plan = plan_diagnostic_echo(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            message="ping",
        )
        self.assertEqual(plan.required_capability, "diagnostic.echo")
        self.assertEqual(plan.expected_observation, "echoed value matches input")
        self.assertEqual(plan.disconfirming_observation, "no result or mismatched value")
        self.assertFalse(hasattr(plan, "exploitability"))
        self.assertFalse(hasattr(plan, "novelty_score"))

    def test_empty_statement_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            human_seeded_hypothesis("  ")

    def test_admitted_hypothesis_plan_is_not_authorization(self) -> None:
        proposal = parse_hypothesis_proposal(
            {
                "proposed_claim": "The diagnostic capability returns the submitted value.",
                "rationale": "round-trip",
                "source_references": ["proc:research-question"],
                "suggested_disconfirming_test": "mismatch",
                "suggested_capability": "diagnostic.echo",
            }
        )
        challenge = parse_hypothesis_challenge(
            {
                "alternative_explanations": ["runtime mismatch"],
                "proposed_disconfirming_observation": "no result or mismatched value",
            }
        )
        plan = plan_admitted_hypothesis(
            "hyp-new",
            proposal,
            challenge,
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertEqual(plan.expected_observation, "echoed value matches input")
        self.assertEqual(plan.disconfirming_observation, "no result or mismatched value")
        self.assertFalse(hasattr(plan, "authorized"))

    def test_invalid_side_effect_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            ExperimentPlan(
                hypothesis_id="hyp-1",
                required_capability="diagnostic.echo",
                action="echo",
                target_reference="target-1",
                side_effect_level=9,
                arguments={},
                requested_budget_id="budget-1",
                expected_observation="echoed value matches input",
                disconfirming_observation="no result or mismatched value",
                evaluation_strategy="diagnostic.echo.v1",
            )


if __name__ == "__main__":
    unittest.main()
