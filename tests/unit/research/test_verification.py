from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.candidate import CandidateState
from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    DIAGNOSTIC_ECHO_MISMATCHED_CLAIM,
)
from zest.research.types import ResearchInputError
from zest.research.verification import (
    DIAGNOSTIC_NEGATIVE_CONTROL_TOKEN,
    VerificationContext,
    VerificationEvidenceRef,
    VerificationOutcome,
    apply_verification_to_candidate,
    evaluate_diagnostic_verification,
    plan_diagnostic_verification,
)


def _ref(**overrides) -> VerificationEvidenceRef:
    values = dict(
        evidence_id="ev-1",
        research_run_id="run-1",
        experiment_id="exp-1",
        request_id="req-1",
        observation_ids=("obs-1",),
        polarity="SUPPORTING",
        claim_scope=DIAGNOSTIC_ECHO_MATCHED_CLAIM,
        observed_echo="alpha",
    )
    values.update(overrides)
    return VerificationEvidenceRef(**values)


def _context(**overrides) -> VerificationContext:
    original = overrides.pop("original_evidence", _ref())
    plan = overrides.pop(
        "plan",
        plan_diagnostic_verification("cand-1", (original.evidence_id,)),
    )
    values = dict(
        candidate_id="cand-1",
        candidate_state=CandidateState.VERIFYING,
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        claim=DIAGNOSTIC_ECHO_MATCHED_CLAIM,
        plan=plan,
        original_evidence=original,
    )
    values.update(overrides)
    return VerificationContext(**values)


class DiagnosticVerificationTests(unittest.TestCase):
    def test_independent_reproduction_and_control_validates(self) -> None:
        result = evaluate_diagnostic_verification(
            _context(
                reproduction_evidence=_ref(
                    evidence_id="ev-2",
                    experiment_id="exp-2",
                    request_id="req-2",
                    observation_ids=("obs-2",),
                    observed_echo="beta",
                )
            )
        )
        self.assertEqual(result.outcome, VerificationOutcome.VALIDATED)
        self.assertIn("REPRODUCTION_INDEPENDENT", result.reason_codes)
        self.assertTrue(result.alternative_explanation_checks["negative_control_held"])
        self.assertTrue(result.alternative_explanation_checks["not_a_finding"])

    def test_same_evidence_cannot_self_validate(self) -> None:
        original = _ref()
        result = evaluate_diagnostic_verification(
            _context(original_evidence=original, reproduction_evidence=original)
        )
        self.assertEqual(result.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertIn("REPRODUCTION_NOT_INDEPENDENT", result.reason_codes)

    def test_mismatch_reproduction_is_rejected(self) -> None:
        result = evaluate_diagnostic_verification(
            _context(
                reproduction_evidence=_ref(
                    evidence_id="ev-2",
                    experiment_id="exp-2",
                    request_id="req-2",
                    observation_ids=("obs-2",),
                    polarity="CONTRADICTING",
                    claim_scope=DIAGNOSTIC_ECHO_MISMATCHED_CLAIM,
                    observed_echo="nope",
                )
            )
        )
        self.assertEqual(result.outcome, VerificationOutcome.REJECTED)
        self.assertIn("REPRODUCTION_CONTRADICTS_CLAIM", result.reason_codes)

    def test_timeout_is_inconclusive_not_rejected(self) -> None:
        result = evaluate_diagnostic_verification(
            _context(reproduction_execution_unusable=True)
        )
        self.assertEqual(result.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertIn("REPRODUCTION_UNUSABLE", result.reason_codes)
        self.assertNotEqual(result.outcome, VerificationOutcome.REJECTED)

    def test_missing_reproduction_is_inconclusive(self) -> None:
        result = evaluate_diagnostic_verification(_context())
        self.assertEqual(result.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertIn("CANNOT_SELF_VALIDATE", result.reason_codes)

    def test_control_fail_token_does_not_validate(self) -> None:
        result = evaluate_diagnostic_verification(
            _context(
                reproduction_evidence=_ref(
                    evidence_id="ev-2",
                    experiment_id="exp-2",
                    request_id="req-2",
                    observation_ids=("obs-2",),
                    observed_echo=DIAGNOSTIC_NEGATIVE_CONTROL_TOKEN,
                )
            )
        )
        self.assertEqual(result.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertIn("NEGATIVE_CONTROL_DID_NOT_HOLD", result.reason_codes)

    def test_verifier_cannot_apply_validated_from_open(self) -> None:
        result = evaluate_diagnostic_verification(
            _context(
                candidate_state=CandidateState.OPEN,
                reproduction_evidence=_ref(
                    evidence_id="ev-2",
                    experiment_id="exp-2",
                    request_id="req-2",
                    observation_ids=("obs-2",),
                    observed_echo="beta",
                ),
            )
        )
        self.assertEqual(result.outcome, VerificationOutcome.VALIDATED)
        with self.assertRaises(ResearchInputError):
            apply_verification_to_candidate(CandidateState.OPEN, result)


if __name__ == "__main__":
    unittest.main()
