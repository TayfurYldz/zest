from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLAIM,
    DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    CandidateAdmissionContext,
    CandidateAdmissionOutcome,
    CandidateEvidenceRef,
    CandidateProposal,
    CandidateState,
    admit_candidate,
    propose_diagnostic_candidate,
    transition_candidate,
)
from zest.research.evidence import DIAGNOSTIC_ECHO_MATCHED_CLAIM
from zest.research.types import ResearchInputError


def _evidence(**overrides) -> CandidateEvidenceRef:
    values = dict(
        evidence_id="ev-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        polarity="SUPPORTING",
        claim_scope=DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    )
    values.update(overrides)
    return CandidateEvidenceRef(**values)


def _context(**overrides) -> CandidateAdmissionContext:
    values = dict(
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        evidence=(_evidence(),),
        missing_evidence_ids=(),
    )
    values.update(overrides)
    return CandidateAdmissionContext(**values)


class CandidateAdmissionTests(unittest.TestCase):
    def test_supporting_diagnostic_evidence_creates_open_candidate(self) -> None:
        context = _context()
        proposal = propose_diagnostic_candidate(context, proposal_id="prop-1")
        assert proposal is not None
        decision = admit_candidate(proposal, context)
        self.assertTrue(decision.creates_candidate)
        self.assertEqual(decision.initial_state, CandidateState.OPEN)
        self.assertEqual(proposal.claim, DIAGNOSTIC_CANDIDATE_CLAIM)
        self.assertEqual(proposal.classification, DIAGNOSTIC_CANDIDATE_CLASSIFICATION)

    def test_hallucinated_evidence_is_rejected(self) -> None:
        context = _context()
        proposal = CandidateProposal(
            proposal_id="prop-ghost",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence_ids=("ev-does-not-exist",),
            claim=DIAGNOSTIC_CANDIDATE_CLAIM,
            classification=DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
            rationale={"reason_code": "test", "not_a_vulnerability": True},
            provenance={"source": "test"},
        )
        decision = admit_candidate(proposal, context)
        self.assertFalse(decision.creates_candidate)
        self.assertEqual(
            decision.outcome, CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE
        )

    def test_wrong_run_is_rejected(self) -> None:
        proposal = propose_diagnostic_candidate(_context(), proposal_id="prop-1")
        assert proposal is not None
        decision = admit_candidate(proposal, _context(research_run_id="run-other"))
        self.assertEqual(
            decision.outcome, CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE
        )

    def test_security_prose_claim_is_not_testable_in_this_slice(self) -> None:
        proposal = CandidateProposal(
            proposal_id="prop-vuln",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence_ids=("ev-1",),
            claim="SQL injection in /login",
            classification=DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
            rationale={"reason_code": "test", "not_a_vulnerability": True},
            provenance={"source": "test"},
        )
        decision = admit_candidate(proposal, _context())
        self.assertEqual(decision.outcome, CandidateAdmissionOutcome.REJECTED_NOT_TESTABLE)

    def test_numeric_confidence_is_rejected_on_proposal(self) -> None:
        with self.assertRaises(ResearchInputError):
            CandidateProposal(
                proposal_id="prop-1",
                research_run_id="run-1",
                hypothesis_id="hyp-1",
                evidence_ids=("ev-1",),
                claim=DIAGNOSTIC_CANDIDATE_CLAIM,
                classification=DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
                rationale={"confidence": 0.9},
                provenance={"source": "test"},
            )


class CandidateTransitionTests(unittest.TestCase):
    def test_open_to_verifying_is_legal(self) -> None:
        self.assertEqual(
            transition_candidate(CandidateState.OPEN, CandidateState.VERIFYING),
            CandidateState.VERIFYING,
        )

    def test_open_to_validated_is_illegal(self) -> None:
        with self.assertRaises(ResearchInputError):
            transition_candidate(CandidateState.OPEN, CandidateState.VALIDATED)

    def test_rejected_to_validated_is_illegal(self) -> None:
        with self.assertRaises(ResearchInputError):
            transition_candidate(CandidateState.REJECTED, CandidateState.VALIDATED)


if __name__ == "__main__":
    unittest.main()
