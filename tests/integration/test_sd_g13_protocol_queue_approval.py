"""SD-G13 Protocol/parser queue approval integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
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
from zest.application.hunt_v3_queue_approval import (
    ApproveHuntV3Queue,
    ApproveHuntV3QueueCommand,
    approval_subject_for_queue,
)
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    ApprovalRecord,
    CandidateRecord,
    EvidenceRecord,
    FindingProposalRecord,
    HumanReviewRecord,
    HypothesisAssessmentRecord,
    HuntV3QueueRecord,
    ObservationRecord,
    VerificationRecord,
    WorkerResultRecord,
)

TEST_URL = configured_test_url()


def _queue(queue_id: str = "queue-1") -> HuntV3QueueRecord:
    return HuntV3QueueRecord(
        queue_id=queue_id,
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        family_id="hf-http-smuggling-desync",
        node_canonical_key="op:https://example.test/edge",
        identity_id=None,
        capability="protocol.parser",
        action="plan",
        arguments={
            "claim": "protocol parser plan",
            "node_id": "op-1",
            "family_name": "HTTP_REQUEST_SMUGGLING_DESYNC",
            "protocol_plan_hash": "a" * 64,
            "plan_version": "protocol.parser.v1",
            "protocol_lane": "http_request_smuggling_desync",
            "step_count": 8,
            "approval_required": "SE3",
            "worker_dispatch": "forbidden_until_se3_approval",
        },
        side_effect_level=3,
        state="PENDING",
        created_at=NOW,
    )


def _seed_reviewable_approval_spine(uow: PostgresUnitOfWork) -> None:
    uow.worker_results.insert(
        WorkerResultRecord(
            worker_result_id="wr-approval-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            request_id="req-approval-1",
            correlation_id="corr-approval-1",
            worker_capability="diagnostic.echo",
            action="echo",
            authorization_decision_reference="authz-approval-1",
            budget_id="budget-1",
            side_effect_level=0,
            contract_version="diagnostic.echo.v1",
            worker_id="worker-approval-1",
            status="SUCCEEDED",
            received_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        )
    )
    uow.observations.insert(
        ObservationRecord(
            observation_id="obs-approval-1",
            worker_result_id="wr-approval-1",
            observation_kind="DIAGNOSTIC",
            payload={"result": "ok"},
            normalization_version="diagnostic.v1",
            observed_at=NOW,
            created_at=NOW,
        )
    )
    uow.hypothesis_assessments.insert(
        HypothesisAssessmentRecord(
            assessment_id="ass-approval-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            assessment_outcome="CONSISTENT_WITH_PREDICTION",
            observation_ids=("obs-approval-1",),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="deterministic.approval-spine.v1",
            rationale={"detail": "approval spine"},
            evaluation_strategy="diagnostic",
            created_at=NOW,
        )
    )
    uow.evidence.insert(
        EvidenceRecord(
            evidence_id="ev-approval-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            admission_record_id="ea-approval-1",
            polarity="SUPPORTING",
            claim_scope="approval spine for protocol queue approval",
            observation_ids=("obs-approval-1",),
            assessment_ids=("ass-approval-1",),
            created_at=NOW,
        )
    )
    uow.candidates.insert(
        CandidateRecord(
            candidate_id="cand-approval-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            claim="approval spine candidate",
            classification="DIAGNOSTIC_PLUMBING",
            state="VALIDATED",
            evidence_ids=("ev-approval-1",),
            admission_record_id="ca-approval-1",
            created_at=NOW,
        )
    )
    uow.verifications.insert(
        VerificationRecord(
            verification_id="ver-approval-1",
            candidate_id="cand-approval-1",
            research_run_id="run-1",
            strategy="diagnostic.approval-spine",
            outcome="VALIDATED",
            proposed_candidate_state="VALIDATED",
            original_evidence_ids=("ev-approval-1",),
            reproduction_evidence_ids=("ev-approval-1",),
            negative_control_evidence_ids=(),
            alternative_explanation_checks={},
            verifier_kind="DETERMINISTIC",
            verifier_identity="deterministic.approval-spine.v1",
            created_at=NOW,
        )
    )
    uow.finding_proposals.insert(
        FindingProposalRecord(
            proposal_id="fp-approval-1",
            candidate_id="cand-approval-1",
            research_run_id="run-1",
            title="Diagnostic approval spine",
            claim="diagnostic approval spine",
            classification="DIAGNOSTIC_PLUMBING",
            state="HUMAN_REVIEW",
            evidence_ids=("ev-approval-1",),
            verification_ids=("ver-approval-1",),
            content_fingerprint="approval-spine-fingerprint",
            created_at=NOW,
        )
    )
    uow.human_reviews.insert(
        HumanReviewRecord(
            review_id="hr-approval-1",
            proposal_id="fp-approval-1",
            content_fingerprint="approval-spine-fingerprint",
            decision=ApprovalDecision.APPROVE.value,
            reviewer_id="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            reason_codes=("QUEUE_SE3_APPROVED",),
            created_at=NOW,
        )
    )
    uow.approvals.insert(
        ApprovalRecord(
            approval_id="approval-queue-1",
            subject_reference=approval_subject_for_queue("queue-1"),
            decision=ApprovalDecision.APPROVE.value,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            recorded=True,
            created_at=NOW,
            research_run_id="run-1",
            proposal_id="fp-approval-1",
            human_review_id="hr-approval-1",
        )
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG13ProtocolQueueApprovalIntegrationTests(unittest.TestCase):
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
            uow.hunt_v3_queue.insert(_queue())
            uow.commit()

    def test_se3_queue_remains_pending_without_recorded_approval(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        result = ApproveHuntV3Queue(factory).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertFalse(result.approved)
        self.assertEqual(result.reason_code, "APPROVAL_REQUIRED")
        with PostgresUnitOfWork(self.engine) as uow:
            item = uow.hunt_v3_queue.get("queue-1")
            uow.rollback()
        assert item is not None
        self.assertEqual(item.state, "PENDING")

    def test_bound_recorded_human_approval_moves_se3_queue_to_approved(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            _seed_reviewable_approval_spine(uow)
            uow.commit()

        factory = PostgresUnitOfWorkFactory(self.engine)
        result = ApproveHuntV3Queue(factory).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertTrue(result.approved)
        self.assertEqual(result.reason_code, "ALLOWED")
        with PostgresUnitOfWork(self.engine) as uow:
            item = uow.hunt_v3_queue.get("queue-1")
            uow.rollback()
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT event_type FROM audit_event WHERE correlation_id = :run_id"),
                {"run_id": "run-1"},
            ).fetchall()
        assert item is not None
        self.assertEqual(item.state, "APPROVED")
        self.assertIn("HUNT_V3_QUEUE_APPROVED", {row.event_type for row in rows})


if __name__ == "__main__":
    unittest.main()
