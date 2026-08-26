from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.security_benchmark.recall import (
    RecallReportError,
    consolidate_recall_report,
)
from zest.security_benchmark.scorecard import (
    ResearchSelectionScorecard,
    SecurityScorecard,
    WorkflowScorecard,
)


def _object_scorecard(*, missed: int = 0, false_finding: int = 0) -> SecurityScorecard:
    return SecurityScorecard(
        benchmark_version="security-ground-truth.v1",
        scenarios_executed=("S01_TRUE_BOLA@1",),
        true_vulnerability_validated=1 - missed,
        true_vulnerability_missed=missed,
        false_evidence_admission=0,
        false_candidate_validation=0,
        false_finding=false_finding,
        correct_secure_rejection=1,
        correct_insufficient_evidence=1,
        correct_inconclusive=1,
        scope_enforcement_failure=0,
        redirect_boundary_failure=0,
        human_approval_bypass=0,
        verification_independence_failure=0,
        hard_failures=(),
        scenario_scores=(),
    )


def _workflow_scorecard() -> WorkflowScorecard:
    return WorkflowScorecard(
        benchmark_version="workflow-state-transition.v1",
        scenarios_executed=("W01_TRUE_ROLE_BYPASS@1", "W02_TRUE_SEQUENCE_SKIP@1"),
        workflow_true_positive=2,
        workflow_missed=0,
        workflow_false_evidence=0,
        workflow_false_candidate_validation=0,
        workflow_false_finding=0,
        correct_sequence_rejection=1,
        correct_role_rejection=1,
        correct_delegation_handling=1,
        correct_idempotent_handling=1,
        correct_stale_state_handling=1,
        correct_inconclusive=1,
        cross_class_misclassification=0,
        human_approval_bypass=0,
        verification_independence_failure=0,
        scope_enforcement_failure=0,
        redirect_boundary_failure=0,
        ground_truth_leakage=0,
        hard_failures=(),
        scenario_scores=(),
    )


def _selection_scorecard() -> ResearchSelectionScorecard:
    return ResearchSelectionScorecard(
        benchmark_version="research-selection.v1",
        scenarios_executed=("R01_BOLA_TRUE_WORKFLOW_DECOY@1",),
        multi_hypothesis_scenarios_completed=1,
        correct_hypothesis_survival=1,
        incorrect_hypothesis_survival=0,
        required_hypothesis_falsified=1,
        false_hypothesis_promoted=0,
        false_evidence_admission=0,
        false_candidate_validation=0,
        false_finding=0,
        cross_class_misclassification=0,
        discriminating_experiment_selected=1,
        redundant_experiment_executed=0,
        counterfactual_branch_failure=0,
        fixed_order_behavior_detected=0,
        context_negative_knowledge_leak=0,
        budget_overrun=0,
        core_authority_bypass=0,
        verification_independence_failure=0,
        ground_truth_leakage=0,
        restart_resume_failure=0,
        human_approval_bypass=0,
        hard_failures=(),
        scenario_scores=(),
    )


class SDG15RecallConsolidationTests(unittest.TestCase):
    def test_consolidates_family_recall_without_weighted_average(self) -> None:
        report = consolidate_recall_report(
            object_authorization=_object_scorecard(),
            workflow_authorization=_workflow_scorecard(),
            research_selection=_selection_scorecard(),
        )

        self.assertTrue(report.pass_gate)
        self.assertFalse(report.weighted_average_allowed)
        self.assertTrue(report.not_a_finding)
        self.assertEqual(report.total_expected_positive, 4)
        self.assertEqual(report.total_validated_positive, 4)
        self.assertEqual(report.total_missed_positive, 0)
        by_family = {row.family_id: row for row in report.families}
        self.assertEqual(by_family["object_authorization"].recall_fraction, (1, 1))
        self.assertEqual(by_family["workflow_authorization"].recall_fraction, (2, 2))
        self.assertEqual(by_family["research_selection"].recall_fraction, (1, 1))

    def test_any_miss_fails_the_gate(self) -> None:
        report = consolidate_recall_report(object_authorization=_object_scorecard(missed=1))

        self.assertFalse(report.pass_gate)
        self.assertEqual(report.total_missed_positive, 1)

    def test_any_false_finding_fails_the_gate(self) -> None:
        report = consolidate_recall_report(
            object_authorization=_object_scorecard(false_finding=1)
        )

        self.assertFalse(report.pass_gate)
        self.assertEqual(report.total_false_finding, 1)

    def test_rejects_empty_consolidation(self) -> None:
        with self.assertRaises(RecallReportError):
            consolidate_recall_report()


if __name__ == "__main__":
    unittest.main()
