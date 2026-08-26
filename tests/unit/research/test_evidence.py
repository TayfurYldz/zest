from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.assessment import AssessmentOutcome, DIAGNOSTIC_ECHO_EVALUATION_STRATEGY
from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    EvidenceAdmissionContext,
    EvidenceAdmissionOutcome,
    EvidenceObservationRef,
    EvidencePolarity,
    EvidenceProposal,
    admit_evidence,
    propose_diagnostic_echo_evidence,
)
from zest.research.types import ResearchInputError


def _observation(observation_id: str = "obs-1", run: str = "run-1") -> EvidenceObservationRef:
    return EvidenceObservationRef(
        observation_id=observation_id,
        research_run_id=run,
        worker_result_id="wr-1",
        observation_kind="diagnostic.echo",
    )


def _context(**overrides) -> EvidenceAdmissionContext:
    values = dict(
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        evaluation_strategy=DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
        observations=(_observation(),),
        missing_source_ids=(),
        assessment_id="assess-1",
        assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
        attempt_state="COMPLETED",
        worker_status="SUCCEEDED",
    )
    values.update(overrides)
    return EvidenceAdmissionContext(**values)


def _proposal(**overrides) -> EvidenceProposal:
    values = dict(
        proposal_id="prop-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        observation_ids=("obs-1",),
        assessment_ids=("assess-1",),
        polarity=EvidencePolarity.SUPPORTING,
        claim_scope=DIAGNOSTIC_ECHO_MATCHED_CLAIM,
        rationale={"reason_code": "ECHO_MATCHED", "not_vulnerability_evidence": True},
        provenance={"source": "diagnostic.echo.deterministic"},
    )
    values.update(overrides)
    return EvidenceProposal(**values)


class EvidenceAdmissionTests(unittest.TestCase):
    def test_matching_diagnostic_is_admitted(self) -> None:
        context = _context()
        proposal = propose_diagnostic_echo_evidence(context, proposal_id="prop-1")
        assert proposal is not None
        decision = admit_evidence(proposal, context)
        self.assertTrue(decision.creates_evidence)
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.ADMITTED)
        self.assertEqual(proposal.claim_scope, DIAGNOSTIC_ECHO_MATCHED_CLAIM)

    def test_model_assertion_without_observation_is_rejected(self) -> None:
        context = _context(observations=(), assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        proposal = _proposal(observation_ids=("obs-imagined",))
        decision = admit_evidence(proposal, context)
        self.assertFalse(decision.creates_evidence)
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE)

    def test_hallucinated_source_is_rejected(self) -> None:
        decision = admit_evidence(
            _proposal(observation_ids=("obs-ghost",)),
            _context(missing_source_ids=("obs-ghost",)),
        )
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE)
        self.assertFalse(decision.creates_evidence)

    def test_wrong_research_run_is_rejected(self) -> None:
        decision = admit_evidence(_proposal(research_run_id="run-other"), _context())
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE)

    def test_unusable_execution_is_not_evidence(self) -> None:
        context = _context(
            assessment_outcome=AssessmentOutcome.EXECUTION_UNUSABLE,
            attempt_state="TIMED_OUT",
            worker_status="TIMED_OUT",
        )
        self.assertIsNone(propose_diagnostic_echo_evidence(context, proposal_id="p"))
        decision = admit_evidence(_proposal(), context)
        self.assertEqual(decision.outcome, EvidenceAdmissionOutcome.REJECTED_EXECUTION_UNUSABLE)
        self.assertFalse(decision.creates_evidence)

    def test_numeric_confidence_is_forbidden(self) -> None:
        with self.assertRaises(ResearchInputError):
            _proposal(rationale={"confidence": 0.9})

    def test_mismatch_may_contradict_narrowly(self) -> None:
        context = _context(assessment_outcome=AssessmentOutcome.CONTRADICTS_PREDICTION)
        proposal = propose_diagnostic_echo_evidence(context, proposal_id="prop-2")
        assert proposal is not None
        self.assertEqual(proposal.polarity, EvidencePolarity.CONTRADICTING)
        decision = admit_evidence(proposal, context)
        self.assertTrue(decision.creates_evidence)


if __name__ == "__main__":
    unittest.main()
