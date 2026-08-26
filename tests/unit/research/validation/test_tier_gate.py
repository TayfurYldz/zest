from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.validation.tier_gate import (
    ValidationAdmissionOutcome,
    ValidationTier,
    ValidationTierOutcome,
    validate_required_tiers,
)


class ValidationTierGateTests(unittest.TestCase):
    def test_v2_requires_v1_and_v2_pass(self) -> None:
        decision = validate_required_tiers(
            ValidationTier.V2,
            {
                ValidationTier.V1: ValidationTierOutcome.PASSED,
                ValidationTier.V2: ValidationTierOutcome.PASSED,
            },
        )

        self.assertEqual(decision.outcome, ValidationAdmissionOutcome.ADMITTED)
        self.assertTrue(decision.admitted)
        self.assertEqual(decision.reason_codes, ("VALIDATION_TIERS_PASSED",))

    def test_missing_required_tier_rejects_fail_closed(self) -> None:
        decision = validate_required_tiers(
            "V2",
            {ValidationTier.V1: ValidationTierOutcome.PASSED},
        )

        self.assertEqual(
            decision.outcome,
            ValidationAdmissionOutcome.REJECTED_MISSING_REQUIRED_TIER,
        )
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.reason_codes, ("V2_MISSING",))

    def test_v3_queued_is_not_validation_pass(self) -> None:
        decision = validate_required_tiers(
            ValidationTier.V3,
            {
                "V1": "PASSED",
                "V2": "PASSED",
                "V3": "QUEUED",
            },
        )

        self.assertEqual(
            decision.outcome,
            ValidationAdmissionOutcome.REJECTED_TIER_NOT_PASSED,
        )
        self.assertFalse(decision.admitted)
        self.assertEqual(decision.reason_codes, ("V3_QUEUED",))


if __name__ == "__main__":
    unittest.main()
