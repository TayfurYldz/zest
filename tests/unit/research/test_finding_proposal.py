from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLAIM,
    DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    CandidateState,
)
from zest.research.finding_proposal import (
    DIAGNOSTIC_FINDING_PROPOSAL_TITLE,
    FindingCreationContext,
    FindingCreationOutcome,
    FindingProposalAdmissionContext,
    FindingProposalAdmissionOutcome,
    FindingProposalDraft,
    FindingProposalState,
    HumanReviewDecision,
    HumanReviewView,
    ImpactClaim,
    admit_finding_proposal,
    admit_human_review,
    approval_subject_for,
    evaluate_finding_creation,
    propose_diagnostic_finding_proposal,
    transition_finding_proposal,
)
from zest.research.types import ResearchInputError


def _context(**overrides) -> FindingProposalAdmissionContext:
    values = dict(
        candidate_id="cand-1",
        candidate_state=CandidateState.VALIDATED,
        research_run_id="run-1",
        evidence_ids=("ev-1", "ev-2"),
        verification_ids=("ver-1",),
        classification=DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    )
    values.update(overrides)
    return FindingProposalAdmissionContext(**values)


def _draft(context: FindingProposalAdmissionContext, **overrides) -> FindingProposalDraft:
    proposed = propose_diagnostic_finding_proposal(context, proposal_id="fp-1")
    assert proposed is not None
    values = dict(
        proposal_id=proposed.proposal_id,
        candidate_id=proposed.candidate_id,
        research_run_id=proposed.research_run_id,
        title=proposed.title,
        claim=proposed.claim,
        evidence_ids=proposed.evidence_ids,
        verification_ids=proposed.verification_ids,
        rationale=dict(proposed.rationale),
        provenance=dict(proposed.provenance),
    )
    values.update(overrides)
    return FindingProposalDraft(**values)


def _review(draft: FindingProposalDraft, **overrides) -> HumanReviewView:
    values = dict(
        review_id="rev-1",
        proposal_id=draft.proposal_id,
        content_fingerprint=draft.content_fingerprint,
        decision=HumanReviewDecision.APPROVE,
        reviewer_id="operator-test-1",
        actor_type="HUMAN_OPERATOR",
        reason_codes=("HUMAN_REVIEW_RECORDED",),
        note="diagnostic plumbing acceptance",
    )
    values.update(overrides)
    return HumanReviewView(**values)


def _creation(draft: FindingProposalDraft, **overrides) -> FindingCreationContext:
    review = overrides.pop("human_review", _review(draft))
    values = dict(
        candidate_id=draft.candidate_id,
        candidate_state=CandidateState.VALIDATED,
        research_run_id=draft.research_run_id,
        proposal_id=draft.proposal_id,
        proposal_state=FindingProposalState.HUMAN_REVIEW,
        title=draft.title,
        claim=draft.claim,
        evidence_ids=draft.evidence_ids,
        verification_ids=draft.verification_ids,
        content_fingerprint=draft.content_fingerprint,
        approval_subject=draft.approval_subject,
        human_review=review,
        approval_valid_record=True,
        approval_authorizes=True,
        approval_subject_matches=True,
        approval_decision=HumanReviewDecision.APPROVE,
        approval_actor_type="HUMAN_OPERATOR",
    )
    values.update(overrides)
    return FindingCreationContext(**values)


class FindingProposalAdmissionTests(unittest.TestCase):
    def test_validated_candidate_creates_proposed_not_finding(self) -> None:
        context = _context()
        draft = propose_diagnostic_finding_proposal(context, proposal_id="fp-1")
        assert draft is not None
        decision = admit_finding_proposal(draft, context)
        self.assertTrue(decision.creates_proposal)
        self.assertEqual(decision.initial_state, FindingProposalState.PROPOSED)
        self.assertEqual(draft.title, DIAGNOSTIC_FINDING_PROPOSAL_TITLE)
        self.assertNotIn("vulnerability", draft.title.lower())
        self.assertEqual(draft.claim, DIAGNOSTIC_CANDIDATE_CLAIM)

    def test_open_candidate_is_rejected(self) -> None:
        context = _context(candidate_state=CandidateState.OPEN)
        self.assertIsNone(propose_diagnostic_finding_proposal(context, proposal_id="fp-1"))
        validated = _context()
        draft = _draft(validated)
        decision = admit_finding_proposal(draft, context)
        self.assertFalse(decision.creates_proposal)
        self.assertEqual(
            decision.outcome,
            FindingProposalAdmissionOutcome.REJECTED_CANDIDATE_NOT_VALIDATED,
        )

    def test_inconclusive_candidate_is_rejected(self) -> None:
        context = _context(candidate_state=CandidateState.INCONCLUSIVE)
        draft = _draft(_context())
        decision = admit_finding_proposal(draft, context)
        self.assertFalse(decision.creates_proposal)
        self.assertEqual(
            decision.outcome,
            FindingProposalAdmissionOutcome.REJECTED_CANDIDATE_NOT_VALIDATED,
        )

    def test_numeric_confidence_is_rejected_on_draft(self) -> None:
        with self.assertRaises(ResearchInputError):
            FindingProposalDraft(
                proposal_id="fp-1",
                candidate_id="cand-1",
                research_run_id="run-1",
                title=DIAGNOSTIC_FINDING_PROPOSAL_TITLE,
                claim=DIAGNOSTIC_CANDIDATE_CLAIM,
                evidence_ids=("ev-1",),
                verification_ids=("ver-1",),
                rationale={"confidence": 0.9},
                provenance={"source": "test"},
            )

    def test_impact_claim_without_chain_id_is_rejected(self) -> None:
        context = _context()
        with self.assertRaises(ResearchInputError):
            _draft(
                context,
                impact_claims=(
                    ImpactClaim(
                        claim_text="attacker can read data",
                        impact_kind="DATA_READ",
                        chain_id="",
                    ),
                ),
            )

    def test_empty_impact_claims_remains_admissible(self) -> None:
        context = _context()
        draft = _draft(context, impact_claims=())
        decision = admit_finding_proposal(draft, context)
        self.assertTrue(decision.creates_proposal)

    def test_impact_claim_with_blank_chain_id_is_rejected(self) -> None:
        context = _context()
        with self.assertRaises(ResearchInputError):
            ImpactClaim(
                claim_text="attacker can read data",
                impact_kind="DATA_READ",
                chain_id="   ",
            )


class FindingProposalTransitionTests(unittest.TestCase):
    def test_proposed_to_human_review_is_legal(self) -> None:
        self.assertEqual(
            transition_finding_proposal(
                FindingProposalState.PROPOSED, FindingProposalState.HUMAN_REVIEW
            ),
            FindingProposalState.HUMAN_REVIEW,
        )

    def test_proposed_to_approved_is_illegal(self) -> None:
        with self.assertRaises(ResearchInputError):
            transition_finding_proposal(
                FindingProposalState.PROPOSED, FindingProposalState.APPROVED
            )


class HumanReviewAdmissionTests(unittest.TestCase):
    def test_non_human_review_is_rejected(self) -> None:
        draft = _draft(_context())
        review = _review(draft, actor_type="CONTROL_PLANE", reviewer_id="model-1")
        with self.assertRaises(ResearchInputError):
            admit_human_review(
                review,
                proposal_id=draft.proposal_id,
                content_fingerprint=draft.content_fingerprint,
            )


class FindingCreationGateTests(unittest.TestCase):
    def test_human_approve_with_matching_core_approval_creates_finding(self) -> None:
        draft = _draft(_context())
        decision = evaluate_finding_creation(_creation(draft))
        self.assertTrue(decision.creates_finding)
        self.assertEqual(decision.outcome, FindingCreationOutcome.CREATED)
        self.assertEqual(decision.proposal_state, FindingProposalState.APPROVED)
        self.assertIn("NOT_A_VULNERABILITY", decision.reason_codes)

    def test_human_reject_does_not_create_finding_or_demote_candidate(self) -> None:
        draft = _draft(_context())
        review = _review(draft, decision=HumanReviewDecision.REJECT)
        decision = evaluate_finding_creation(
            _creation(
                draft,
                human_review=review,
                approval_authorizes=False,
                approval_decision=HumanReviewDecision.REJECT,
            )
        )
        self.assertFalse(decision.creates_finding)
        self.assertEqual(decision.outcome, FindingCreationOutcome.REJECTED_PROPOSAL)
        self.assertEqual(decision.proposal_state, FindingProposalState.REJECTED)
        self.assertIn("CANDIDATE_REMAINS_VALIDATED", decision.reason_codes)

    def test_missing_review_creates_no_finding(self) -> None:
        draft = _draft(_context())
        decision = evaluate_finding_creation(
            _creation(
                draft,
                human_review=None,
                approval_valid_record=False,
                approval_authorizes=False,
                approval_subject_matches=False,
                approval_decision=None,
            )
        )
        self.assertFalse(decision.creates_finding)
        self.assertEqual(decision.outcome, FindingCreationOutcome.REJECTED_MISSING_REVIEW)

    def test_missing_approval_creates_no_finding(self) -> None:
        draft = _draft(_context())
        decision = evaluate_finding_creation(
            _creation(
                draft,
                approval_valid_record=False,
                approval_authorizes=False,
            )
        )
        self.assertFalse(decision.creates_finding)
        self.assertEqual(
            decision.outcome, FindingCreationOutcome.REJECTED_MISSING_APPROVAL
        )

    def test_wrong_approval_subject_creates_no_finding(self) -> None:
        draft = _draft(_context())
        decision = evaluate_finding_creation(
            _creation(draft, approval_subject_matches=False)
        )
        self.assertFalse(decision.creates_finding)
        self.assertEqual(
            decision.outcome, FindingCreationOutcome.REJECTED_SUBJECT_MISMATCH
        )

    def test_modified_content_cannot_reuse_review(self) -> None:
        draft = _draft(_context())
        review = _review(draft, content_fingerprint="0" * 64)
        decision = evaluate_finding_creation(_creation(draft, human_review=review))
        self.assertFalse(decision.creates_finding)
        self.assertEqual(
            decision.outcome, FindingCreationOutcome.REJECTED_SUBJECT_MISMATCH
        )

    def test_non_human_actor_creates_no_finding(self) -> None:
        draft = _draft(_context())
        decision = evaluate_finding_creation(
            _creation(draft, approval_actor_type="CONTROL_PLANE")
        )
        self.assertFalse(decision.creates_finding)
        self.assertEqual(decision.outcome, FindingCreationOutcome.REJECTED_ACTOR)

    def test_approval_subject_includes_proposal_and_fingerprint(self) -> None:
        draft = _draft(_context())
        other = _draft(_context(), proposal_id="fp-2")
        self.assertNotEqual(
            approval_subject_for(draft.proposal_id, draft.content_fingerprint),
            approval_subject_for(other.proposal_id, other.content_fingerprint),
        )


if __name__ == "__main__":
    unittest.main()
