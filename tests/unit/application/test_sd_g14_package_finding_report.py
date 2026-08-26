from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.errors import ApplicationError
from zest.application.package_finding_report import (
    PackageFindingReport,
    PackageFindingReportCommand,
    REPORT_PACKAGE_BUILT,
)
from zest.data.records import FindingProposalRecord, FindingRecord
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


def _seed_approved_finding(store: _Store, *, proposal_state: str = "APPROVED") -> None:
    seed_authorization_run(store)
    store.finding_proposals["proposal-1"] = FindingProposalRecord(
        proposal_id="proposal-1",
        candidate_id="candidate-1",
        research_run_id="run-1",
        title="Missing object authorization",
        claim="Actor can read another actor account",
        classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
        state=proposal_state,
        evidence_ids=("ev-1",),
        verification_ids=("ver-1",),
        content_fingerprint="proposal-fingerprint",
        impact_chain_ids=("chain-1",),
        created_at=CREATED_AT,
    )
    store.findings["finding-1"] = FindingRecord(
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
        created_at=CREATED_AT,
    )


class SDG14PackageFindingReportTests(unittest.TestCase):
    def test_builds_package_from_approved_finding_and_audits(self) -> None:
        store = _Store()
        _seed_approved_finding(store)

        result = PackageFindingReport(FakeUnitOfWorkFactory(store)).execute(
            PackageFindingReportCommand(
                finding_id="finding-1",
                external_duplicate_signals=(
                    {
                        "source": "platform-disclosed-report",
                        "signal_type": "category",
                        "reference": "object-authz",
                    },
                ),
            )
        )

        package = result.package.to_dict()
        self.assertEqual(package["finding_id"], "finding-1")
        self.assertEqual(package["sections"]["proof"]["impact_chain_ids"], ["chain-1"])
        self.assertEqual(package["sections"]["duplicate_check"]["external_signal_count"], 1)
        self.assertTrue(package["not_auto_submitted"])
        audit = next(iter(store.audit_events.values()))
        self.assertEqual(audit.event_type, REPORT_PACKAGE_BUILT)
        self.assertEqual(audit.payload["package_hash"], result.package.package_hash)

    def test_rejects_when_finding_proposal_is_not_approved(self) -> None:
        store = _Store()
        _seed_approved_finding(store, proposal_state="HUMAN_REVIEW")

        with self.assertRaises(ApplicationError):
            PackageFindingReport(FakeUnitOfWorkFactory(store)).execute(
                PackageFindingReportCommand(finding_id="finding-1")
            )

    def test_missing_finding_raises(self) -> None:
        store = _Store()
        seed_authorization_run(store)

        with self.assertRaises(ApplicationError):
            PackageFindingReport(FakeUnitOfWorkFactory(store)).execute(
                PackageFindingReportCommand(finding_id="missing")
            )


if __name__ == "__main__":
    unittest.main()
