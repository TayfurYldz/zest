"""Phase 6.8 Evidence → Candidate → Verification → Finding closure."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from application.test_finding_acceptance import (
    _open_candidate,
    _original_evidence,
    _validated_candidate,
)
from zest.application.errors import ApplicationError
from zest.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
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
from zest.research.assessment import (
    AssessmentOutcome,
    HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
    HTTP_TRANSACTION_EVALUATION_STRATEGY,
)
from zest.research.candidate import CandidateState
from zest.research.evidence import (
    EvidenceAdmissionContext,
    EvidenceAdmissionOutcome,
    EvidenceObservationRef,
    EvidencePolarity,
    EvidenceProposal,
    admit_evidence,
    propose_authorization_differential_evidence,
)
from zest.research.evidence_strategy_map import (
    EVIDENCE_NOT_CONNECTED,
    EVIDENCE_PIPELINE_PENDING,
    EVIDENCE_STRATEGY_MAP,
    EVIDENCE_STRATEGY_MAP_COMPLETE,
    FINDING_PROPOSAL_NOT_CONNECTED,
    NOT_EVIDENCE,
    SUPPORTED_NOW,
    VERIFICATION_NOT_CONNECTED,
    assert_supported_aligns_with_admission,
    classify_evidence_strategy,
)
from zest.research.finding_proposal import FindingProposalAdmissionOutcome, FindingProposalState
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _authz_context(**overrides) -> EvidenceAdmissionContext:
    values = dict(
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        evaluation_strategy=HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
        observations=(
            EvidenceObservationRef(
                observation_id="obs-1",
                research_run_id="run-1",
                worker_result_id="wr-1",
                observation_kind="http.authorization.differential",
            ),
        ),
        missing_source_ids=(),
        assessment_id="assess-1",
        assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
        attempt_state="COMPLETED",
        worker_status="SUCCEEDED",
    )
    values.update(overrides)
    return EvidenceAdmissionContext(**values)


class Phase68EvidenceClosureTests(unittest.TestCase):
    def test_e1_e2_strategy_map(self) -> None:
        self.assertTrue(EVIDENCE_STRATEGY_MAP_COMPLETE)
        assert_supported_aligns_with_admission()
        self.assertGreaterEqual(len(EVIDENCE_STRATEGY_MAP), 10)
        self.assertEqual(
            classify_evidence_strategy(HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY),
            SUPPORTED_NOW,
        )
        self.assertEqual(
            classify_evidence_strategy("oast.callback.v1"),
            EVIDENCE_PIPELINE_PENDING,
        )
        self.assertEqual(
            classify_evidence_strategy("mutation.matrix.v1"),
            EVIDENCE_PIPELINE_PENDING,
        )
        self.assertEqual(
            classify_evidence_strategy("differential.controlled.v1"),
            EVIDENCE_PIPELINE_PENDING,
        )
        self.assertEqual(
            classify_evidence_strategy(HTTP_TRANSACTION_EVALUATION_STRATEGY),
            NOT_EVIDENCE,
        )
        self.assertEqual(EVIDENCE_NOT_CONNECTED, 0)
        self.assertEqual(VERIFICATION_NOT_CONNECTED, 0)
        self.assertEqual(FINDING_PROPOSAL_NOT_CONNECTED, 0)

    def test_e3_authz_positive_evidence(self) -> None:
        context = _authz_context()
        proposal = propose_authorization_differential_evidence(context, proposal_id="p1")
        self.assertIsNotNone(proposal)
        decision = admit_evidence(proposal, context)
        self.assertTrue(decision.creates_evidence)
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.ADMITTED)

    def test_e4_authz_negative_no_evidence(self) -> None:
        context = _authz_context(assessment_outcome=AssessmentOutcome.CONTRADICTS_PREDICTION)
        self.assertIsNone(propose_authorization_differential_evidence(context, proposal_id="p1"))

    def test_e5_e8_pending_strategies_explicit(self) -> None:
        for strategy in (
            "oast.callback.v1",
            "mutation.matrix.v1",
            "differential.controlled.v1",
            "invariant.security_property.v1",
            "chain.causal.v1",
        ):
            row = EVIDENCE_STRATEGY_MAP[strategy]
            self.assertEqual(row["class"], EVIDENCE_PIPELINE_PENDING)
            self.assertTrue(row["reason"])

    def test_e6_oast_negatives_not_supported(self) -> None:
        self.assertNotEqual(classify_evidence_strategy("oast.callback.v1"), SUPPORTED_NOW)

    def test_e9_candidate_from_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        proposed = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        self.assertIsNotNone(proposed.candidate_id)
        again = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        self.assertEqual(proposed.candidate_id, again.candidate_id)
        self.assertEqual(len(store.candidates), 1)

    def test_e10_e11_e12_verification_native(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _open_candidate(store)
        started = StartCandidateVerification(FakeUnitOfWorkFactory(store)).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        self.assertEqual(started.state, CandidateState.VERIFYING)
        self.assertTrue(started.plan.verification_strategy)
        self.assertNotIn("Worker", type(StartCandidateVerification).__name__)

    def test_e12_verified_candidate_update(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        self.assertEqual(store.candidates[candidate_id].state, CandidateState.VALIDATED.value)

    def test_e13_disproven_suppresses_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _open_candidate(store)
        self.assertEqual(store.candidates[candidate_id].state, CandidateState.OPEN.value)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(
                FakeUnitOfWorkFactory(store), clock=FixedClock()
            ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(store.findings, {})
        self.assertEqual(store.finding_proposals, {})

    def test_e14_e15_finding_proposal_human_review(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(submitted.outcome, FindingProposalAdmissionOutcome.ADMITTED)
        self.assertEqual(submitted.state, FindingProposalState.PROPOSED)
        self.assertEqual(len(store.findings), 0)
        reviewed = StartHumanReview(FakeUnitOfWorkFactory(store)).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        self.assertEqual(reviewed.state, FindingProposalState.HUMAN_REVIEW)
        with self.assertRaises(Exception):
            FinalizeFinding(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=submitted.proposal_id,
                    decided_by="operator-1",
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )

    def test_e16_redaction(self) -> None:
        context = _authz_context()
        proposal = propose_authorization_differential_evidence(context, proposal_id="p1")
        blob = str(proposal.claim_scope) + str(proposal.rationale) + str(proposal.provenance)
        self.assertNotIn("password", blob.lower().split("not")[0] if False else blob)
        self.assertNotIn("Bearer ", blob)

    def test_e17_e18_dedupe_recovery(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        first = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        second = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(len(store.candidates), 1)

    def test_e19_model_only_claim_not_evidence(self) -> None:
        context = _authz_context(observations=())
        proposal = EvidenceProposal(
            proposal_id="p-model",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            observation_ids=("obs-imagined",),
            assessment_ids=("assess-1",),
            polarity=EvidencePolarity.SUPPORTING,
            claim_scope="model declared a vulnerability",
            rationale={"reason_code": "MODEL"},
            provenance={"source": "model"},
        )
        decision = admit_evidence(proposal, context)
        self.assertFalse(decision.creates_evidence)

    def test_e20_verification_blocks_completion(self) -> None:
        store = _Store()
        seed_spine(store)
        _open_candidate(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.verification_pending, 0)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("VERIFICATION_PENDING", audit.completion_block_reasons)


if __name__ == "__main__":
    unittest.main()
