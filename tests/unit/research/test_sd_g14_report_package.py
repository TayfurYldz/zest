from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.report_package import (
    ExternalDuplicateSignal,
    FindingReportInput,
    build_finding_report_package,
    internal_duplicate_fingerprint,
)
from zest.research.types import ResearchInputError


def _input(*, claim: str = "Actor can read another actor account") -> FindingReportInput:
    return FindingReportInput(
        finding_id="finding-1",
        finding_proposal_id="proposal-1",
        candidate_id="candidate-1",
        research_run_id="run-1",
        approval_id="approval-1",
        human_review_id="review-1",
        title="Missing object authorization",
        claim=claim,
        classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
        evidence_ids=("ev-1", "ev-2"),
        verification_ids=("ver-1",),
    )


class SDG14ReportPackageTests(unittest.TestCase):
    def test_duplicate_fingerprint_is_stable_for_normalized_claim_text(self) -> None:
        first = _input(claim="Actor CAN read   another actor account")
        second = _input(claim="actor can read another actor account")

        self.assertEqual(
            internal_duplicate_fingerprint(first),
            internal_duplicate_fingerprint(second),
        )

    def test_package_contains_replay_anchors_without_raw_payloads(self) -> None:
        signal = ExternalDuplicateSignal(
            source="platform-disclosed-report",
            signal_type="category",
            reference="known-object-authz",
        )
        package = build_finding_report_package(
            _input(),
            package_id="package-1",
            external_duplicate_signals=(signal,),
        )
        payload = package.to_dict()

        self.assertEqual(payload["package_version"], "report.package.v1")
        self.assertTrue(payload["not_auto_submitted"])
        self.assertEqual(payload["sections"]["proof"]["evidence_ids"], ["ev-1", "ev-2"])
        self.assertEqual(payload["sections"]["reproduction"]["verification_ids"], ["ver-1"])
        self.assertFalse(payload["sections"]["safety"]["raw_payloads_included"])
        self.assertEqual(len(payload["internal_duplicate_fingerprint"]), 64)
        self.assertEqual(len(payload["package_hash"]), 64)
        self.assertNotIn("payload", payload["sections"])
        self.assertNotIn("body", payload["sections"])

    def test_external_duplicate_signal_secret_keys_fail_closed(self) -> None:
        with self.assertRaises(ResearchInputError):
            build_finding_report_package(
                _input(),
                package_id="package-1",
                external_duplicate_signals=(
                    {
                        "source": "platform",
                        "signal_type": "reference",
                        "reference": "abc",
                        "token": "secret",
                    },
                ),
            )

    def test_evidence_and_verification_anchors_are_required(self) -> None:
        with self.assertRaises(ResearchInputError):
            FindingReportInput(
                finding_id="finding-1",
                finding_proposal_id="proposal-1",
                candidate_id="candidate-1",
                research_run_id="run-1",
                approval_id="approval-1",
                human_review_id="review-1",
                title="Missing object authorization",
                claim="Actor can read another actor account",
                classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
                evidence_ids=(),
                verification_ids=("ver-1",),
            )


if __name__ == "__main__":
    unittest.main()
