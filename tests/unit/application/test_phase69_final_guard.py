"""Phase 6.9 cross-engine matrix, sensor ownership, completion guard."""

from __future__ import annotations

import unittest
from datetime import timedelta

import pathsetup  # noqa: F401

from application.test_finding_acceptance import _open_candidate, _validated_candidate
from zest.application.global_research_work_audit import (
    NOT_YET_CONNECTED,
    global_research_work_audit,
)
from zest.application.phase6_engine_matrix import (
    FINAL_ENGINE_MATRIX,
    PHASE6_NOT_CONNECTED,
)
from zest.application.sensor_ownership import SENSOR_OWNERSHIP_MATRIX
from zest.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from zest.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.core.enums import ActorType
from zest.data.records import (
    AuditEventRecord,
    CandidateRecord,
    HypothesisRecord,
    OastCorrelationRecord,
)
from zest.research.candidate import CandidateState
from zest.research.finding_proposal import FindingProposalState
from zest.research.model_context_census import MODEL_CONTEXT_SOURCE
from zest.research.evidence_strategy_map import EVIDENCE_STRATEGY_MAP_COMPLETE
from zest.research.orchestration import StopReason
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


class Phase69FinalGuardTests(unittest.TestCase):
    def test_z1_final_matrix(self) -> None:
        names = {row["ENGINE"] for row in FINAL_ENGINE_MATRIX}
        for required in (
            "Discovery",
            "Browser",
            "HTTP",
            "Frontier",
            "Global Scheduler",
            "Hunter",
            "Coverage",
            "Identity",
            "Authentication",
            "Authorization",
            "IDOR/BOLA",
            "Workflow",
            "Mutation",
            "Protocol",
            "OAST",
            "Differential",
            "Invariant",
            "Chain",
            "Model Context",
            "Evidence",
            "Candidate",
            "Verification",
            "Finding Proposal",
            "Human Review",
            "Sensors",
        ):
            self.assertIn(required, names)
        protocol = next(row for row in FINAL_ENGINE_MATRIX if row["ENGINE"] == "Protocol")
        self.assertEqual(protocol["STATUS"], "CONNECTED_BLOCKED_BY_AUTHORITY")
        self.assertEqual(PHASE6_NOT_CONNECTED, 0)

    def test_z2_sensor_ownership(self) -> None:
        sensors = {row["sensor"] for row in SENSOR_OWNERSHIP_MATRIX}
        self.assertIn("OAST callbacks", sensors)
        self.assertIn("model outputs", sensors)
        for row in SENSOR_OWNERSHIP_MATRIX:
            self.assertTrue(row["ingestion"])
            self.assertTrue(row["observation"])
            self.assertTrue(row["redaction"])

    def test_z3_global_audit_ints(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        payload = audit.as_payload()
        for key, value in payload.items():
            if key == "completion_block_reasons":
                continue
            if key in {
                "completion_allowed",
                "hunter_coverage_exhausted_for_connected_engines",
            }:
                self.assertIsInstance(value, bool)
                continue
            self.assertNotEqual(value, NOT_YET_CONNECTED, key)
            if isinstance(value, int):
                self.assertGreaterEqual(value, 0)

    def test_z5_negative_no_false_finding(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.findings, {})
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertTrue(audit.completion_allowed)

    def test_z6_authority_block_not_covered(self) -> None:
        protocol = next(row for row in FINAL_ENGINE_MATRIX if row["ENGINE"] == "Protocol")
        self.assertIn("not coverage", protocol["LIMITATIONS"])
        self.assertNotEqual(protocol["STATUS"], "CONNECTED_EXECUTABLE")

    def test_z8_missing_precondition_not_fabricated(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.identity_dependency_pending, int)
        self.assertIsInstance(audit.auth_missing_precondition, int)

    def test_z9_model_pending_blocks(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.hypotheses["hyp-model"] = HypothesisRecord(
            hypothesis_id="hyp-model",
            research_run_id="run-1",
            claim="admitted model hypothesis awaiting scheduler",
            created_at=CREATED_AT,
            origin_reference="reasoning-1",
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.model_pending, 0)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("MODEL_PENDING", audit.completion_block_reasons)

    def test_z10_verification_block(self) -> None:
        store = _Store()
        seed_spine(store)
        store.candidates["cand-1"] = CandidateRecord(
            candidate_id="cand-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            claim="object access control missing",
            classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
            state=CandidateState.OPEN.value,
            evidence_ids=("ev-1",),
            admission_record_id="cadm-1",
            created_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertIn("VERIFICATION_PENDING", audit.completion_block_reasons)

    def test_z12_unknown_fail_closed(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(audit.unknown_outcome, 0)
        self.assertNotIn("UNKNOWN_OUTCOME", audit.completion_block_reasons)

    def test_z14_orphan_block(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(audit.orphan_research_work, 0)

    def test_z4_happy_path_finding_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(submitted.state, FindingProposalState.PROPOSED)
        self.assertEqual(store.findings, {})

    def test_z7_oast_wait_reason_exists(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.oast_correlations["corr-wait"] = OastCorrelationRecord(
            correlation_id="corr-wait",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="target-1",
            identity_id="id-alice",
            armed_at=CREATED_AT,
            expires_at=CREATED_AT + timedelta(hours=1),
            created_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            waiting = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(waiting.oast_waiting_callback, 0)
        self.assertFalse(waiting.completion_allowed)
        self.assertIn("OAST_WAITING_CALLBACK", waiting.completion_block_reasons)
        store.audit_events["aud-timeout"] = AuditEventRecord(
            audit_event_id="aud-timeout",
            occurred_at=CREATED_AT,
            actor_id="control-plane:oast",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type="OAST_NO_CALLBACK_TIMEOUT",
            subject_type="research_run",
            subject_id="run-1",
            payload={"correlation_id": "corr-wait"},
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            timed = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(timed.oast_waiting_callback, 0)
        self.assertNotIn("OAST_WAITING_CALLBACK", timed.completion_block_reasons)

    def test_z11_human_review_blocks_complete(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        StartHumanReview(FakeUnitOfWorkFactory(store)).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertIn("FINDING_REVIEW_PENDING", audit.completion_block_reasons)
        self.assertEqual(store.findings, {})

    def test_z13_recovery_no_duplicate_verification(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _open_candidate(store)
        first = StartCandidateVerification(FakeUnitOfWorkFactory(store)).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        second = StartCandidateVerification(FakeUnitOfWorkFactory(store)).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        self.assertEqual(first.state, CandidateState.VERIFYING)
        self.assertEqual(second.state, CandidateState.VERIFYING)
        self.assertEqual(store.candidates[candidate_id].state, CandidateState.VERIFYING.value)
        self.assertEqual(len(store.candidates), 1)

    def test_z15_previous_phase_markers_preserved(self) -> None:
        self.assertEqual(MODEL_CONTEXT_SOURCE, "CONNECTED")
        self.assertTrue(EVIDENCE_STRATEGY_MAP_COMPLETE)
        self.assertEqual(PHASE6_NOT_CONNECTED, 0)
        protocol = next(row for row in FINAL_ENGINE_MATRIX if row["ENGINE"] == "Protocol")
        self.assertEqual(protocol["STATUS"], "CONNECTED_BLOCKED_BY_AUTHORITY")

    def test_stop_reason_authority_complete_is_not_false_coverage(self) -> None:
        self.assertEqual(
            StopReason.COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK.value,
            "COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK",
        )
        self.assertNotEqual(
            StopReason.COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK,
            StopReason.COMPLETED_NO_MORE_OPPORTUNITIES,
        )


if __name__ == "__main__":
    unittest.main()
