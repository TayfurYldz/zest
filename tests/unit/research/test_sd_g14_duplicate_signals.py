from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.report_duplicate import (
    DisclosedReportSignal,
    DuplicateSignalRelation,
    evaluate_disclosed_report_duplicate_signal,
)
from zest.research.report_package import (
    FindingReportInput,
    build_finding_report_package,
)
from zest.research.types import ResearchInputError


def _finding() -> FindingReportInput:
    return FindingReportInput(
        finding_id="finding-1",
        finding_proposal_id="proposal-1",
        candidate_id="candidate-1",
        research_run_id="run-1",
        approval_id="approval-1",
        human_review_id="review-1",
        title="Missing object authorization",
        claim="Actor can read another actor account",
        classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
        evidence_ids=("ev-1",),
        verification_ids=("ver-1",),
    )


class SDG14DuplicateSignalTests(unittest.TestCase):
    def test_classification_match_becomes_advisory_potential_match(self) -> None:
        evaluation = evaluate_disclosed_report_duplicate_signal(
            _finding(),
            DisclosedReportSignal(
                source="platform-api",
                program="example",
                reference="https://platform.test/reports/1",
                title="User object access control issue",
                classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
                tags=("idor", "object authorization"),
            ),
        )

        self.assertEqual(evaluation.relation, DuplicateSignalRelation.POTENTIAL_MATCH)
        self.assertIsNotNone(evaluation.external_signal)
        assert evaluation.external_signal is not None
        self.assertEqual(evaluation.external_signal.relation, "POTENTIAL_MATCH")
        self.assertEqual(evaluation.external_signal.signal_fingerprint, evaluation.signal_fingerprint)
        self.assertIn("EXTERNAL_SIGNAL_IS_ADVISORY", evaluation.reason_codes)

    def test_unrelated_disclosed_report_is_no_match(self) -> None:
        evaluation = evaluate_disclosed_report_duplicate_signal(
            _finding(),
            DisclosedReportSignal(
                source="program-page",
                program="example",
                reference="https://platform.test/reports/2",
                title="Content security policy header missing",
                classification="MISSING_HEADER",
                tags=("csp",),
            ),
        )

        self.assertEqual(evaluation.relation, DuplicateSignalRelation.NO_MATCH)
        self.assertIsNone(evaluation.external_signal)

    def test_disclosed_report_signal_rejects_secret_keys(self) -> None:
        with self.assertRaises(ResearchInputError):
            DisclosedReportSignal(
                source="platform-api",
                program="example",
                reference="https://platform.test/reports/3",
                title="secret leakage",
                classification="INFO_DISCLOSURE",
                metadata={"token": "secret"},
            )

    def test_package_preserves_external_signal_fingerprint(self) -> None:
        evaluation = evaluate_disclosed_report_duplicate_signal(
            _finding(),
            DisclosedReportSignal(
                source="platform-api",
                program="example",
                reference="https://platform.test/reports/1",
                title="User object access control issue",
                classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
            ),
        )
        assert evaluation.external_signal is not None

        package = build_finding_report_package(
            _finding(),
            package_id="package-1",
            external_duplicate_signals=(evaluation.external_signal,),
        )
        payload = package.to_dict()

        self.assertEqual(
            payload["external_duplicate_signals"][0]["signal_fingerprint"],
            evaluation.signal_fingerprint,
        )
        self.assertFalse(payload["sections"]["duplicate_check"]["external_signals_are_truth"])


if __name__ == "__main__":
    unittest.main()
