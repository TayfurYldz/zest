"""SD-G14 report package vertical slice on real PostgreSQL.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.package_finding_report import (
    PackageFindingReport,
    PackageFindingReportCommand,
    REPORT_PACKAGE_BUILT,
)
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    ApprovalRecord,
    CandidateRecord,
    EvidenceRecord,
    FindingProposalRecord,
    FindingRecord,
    HumanReviewRecord,
    HypothesisAssessmentRecord,
    ObservationRecord,
    VerificationRecord,
    WorkerResultRecord,
)

TEST_URL = configured_test_url()


def _seed_approved_finding(uow: PostgresUnitOfWork) -> None:
    uow.worker_results.insert(
        WorkerResultRecord(
            worker_result_id="wr-report-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            request_id="req-report-1",
            correlation_id="corr-report-1",
            worker_capability="diagnostic.echo",
            action="echo",
            authorization_decision_reference="authz-report-1",
            budget_id="budget-1",
            side_effect_level=0,
            contract_version="diagnostic.echo.v1",
            worker_id="worker-report-1",
            status="SUCCEEDED",
            received_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        )
    )
    uow.observations.insert(
        ObservationRecord(
            observation_id="obs-report-1",
            worker_result_id="wr-report-1",
            observation_kind="DIAGNOSTIC",
            payload={"result": "ok"},
            normalization_version="diagnostic.v1",
            observed_at=NOW,
            created_at=NOW,
        )
    )
    uow.hypothesis_assessments.insert(
        HypothesisAssessmentRecord(
            assessment_id="ass-report-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            assessment_outcome="CONSISTENT_WITH_PREDICTION",
            observation_ids=("obs-report-1",),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="deterministic.report-package.v1",
            rationale={"detail": "report package spine"},
            evaluation_strategy="diagnostic",
            created_at=NOW,
        )
    )
    uow.evidence.insert(
        EvidenceRecord(
            evidence_id="ev-report-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            admission_record_id="ea-report-1",
            polarity="SUPPORTING",
            claim_scope="report package evidence spine",
            observation_ids=("obs-report-1",),
            assessment_ids=("ass-report-1",),
            created_at=NOW,
        )
    )
    uow.candidates.insert(
        CandidateRecord(
            candidate_id="cand-report-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            claim="diagnostic package candidate",
            classification="DIAGNOSTIC_PLUMBING",
            state="VALIDATED",
            evidence_ids=("ev-report-1",),
            admission_record_id="ca-report-1",
            created_at=NOW,
        )
    )
    uow.verifications.insert(
        VerificationRecord(
            verification_id="ver-report-1",
            candidate_id="cand-report-1",
            research_run_id="run-1",
            strategy="diagnostic.report-package",
            outcome="VALIDATED",
            proposed_candidate_state="VALIDATED",
            original_evidence_ids=("ev-report-1",),
            reproduction_evidence_ids=("ev-report-2",),
            negative_control_evidence_ids=(),
            alternative_explanation_checks={},
            verifier_kind="DETERMINISTIC",
            verifier_identity="deterministic.report-package.v1",
            created_at=NOW,
        )
    )
    uow.finding_proposals.insert(
        FindingProposalRecord(
            proposal_id="fp-report-1",
            candidate_id="cand-report-1",
            research_run_id="run-1",
            title="Diagnostic report package",
            claim="diagnostic report package",
            classification="DIAGNOSTIC_PLUMBING",
            state="APPROVED",
            evidence_ids=("ev-report-1",),
            verification_ids=("ver-report-1",),
            content_fingerprint="report-package-fingerprint",
            created_at=NOW,
        )
    )
    uow.human_reviews.insert(
        HumanReviewRecord(
            review_id="hr-report-1",
            proposal_id="fp-report-1",
            content_fingerprint="report-package-fingerprint",
            decision=ApprovalDecision.APPROVE.value,
            reviewer_id="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            reason_codes=("REPORT_PACKAGE_APPROVED",),
            created_at=NOW,
        )
    )
    uow.approvals.insert(
        ApprovalRecord(
            approval_id="approval-report-1",
            subject_reference="finding-proposal:fp-report-1:report-package-fingerprint",
            decision=ApprovalDecision.APPROVE.value,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            recorded=True,
            created_at=NOW,
            research_run_id="run-1",
            proposal_id="fp-report-1",
            human_review_id="hr-report-1",
        )
    )
    uow.findings.insert(
        FindingRecord(
            finding_id="finding-report-1",
            finding_proposal_id="fp-report-1",
            candidate_id="cand-report-1",
            research_run_id="run-1",
            approval_id="approval-report-1",
            human_review_id="hr-report-1",
            title="Diagnostic report package",
            claim="diagnostic report package",
            classification="DIAGNOSTIC_PLUMBING",
            evidence_ids=("ev-report-1",),
            verification_ids=("ver-report-1",),
            created_at=NOW,
        )
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG14ReportPackageIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            _seed_approved_finding(uow)
            uow.commit()

    def test_report_package_builds_from_approved_finding_and_audits(self) -> None:
        result = PackageFindingReport(PostgresUnitOfWorkFactory(self.engine)).execute(
            PackageFindingReportCommand(
                finding_id="finding-report-1",
                external_duplicate_signals=(
                    {
                        "source": "platform-disclosed-report",
                        "signal_type": "category",
                        "reference": "diagnostic",
                    },
                ),
            )
        )

        payload = result.package.to_dict()
        self.assertEqual(payload["finding_id"], "finding-report-1")
        self.assertTrue(payload["not_auto_submitted"])
        self.assertEqual(payload["sections"]["proof"]["evidence_ids"], ["ev-report-1"])
        self.assertEqual(len(payload["package_hash"]), 64)
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT event_type FROM audit_event WHERE correlation_id = :run_id"),
                {"run_id": "run-1"},
            ).fetchall()
        self.assertIn(REPORT_PACKAGE_BUILT, {row.event_type for row in rows})


if __name__ == "__main__":
    unittest.main()
