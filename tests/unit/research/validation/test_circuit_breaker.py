from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.validation.circuit_breaker import (
    CircuitBreakerAction,
    FamilyTelemetry,
    evaluate_family_circuit_breaker,
)


class FamilyCircuitBreakerTests(unittest.TestCase):
    def test_small_samples_are_allowed(self) -> None:
        decision = evaluate_family_circuit_breaker(
            FamilyTelemetry(
                family_id="hf-object-authz",
                supported_count=0,
                rejected_count=3,
                inconclusive_count=2,
                minimum_sample=10,
            )
        )

        self.assertEqual(decision.action, CircuitBreakerAction.ALLOW)
        self.assertFalse(decision.throttle)
        self.assertFalse(decision.disable_family)
        self.assertEqual(decision.reason_codes, ("INSUFFICIENT_SAMPLE_ALLOW",))

    def test_noisy_family_is_throttled_not_disabled(self) -> None:
        decision = evaluate_family_circuit_breaker(
            FamilyTelemetry(
                family_id="hf-xss",
                supported_count=2,
                rejected_count=5,
                inconclusive_count=3,
                minimum_sample=10,
                bad_outcome_threshold=0.60,
            )
        )

        self.assertEqual(decision.action, CircuitBreakerAction.THROTTLE)
        self.assertTrue(decision.throttle)
        self.assertFalse(decision.disable_family)
        self.assertTrue(decision.requires_human_review_to_restore)
        self.assertEqual(decision.bad_outcome_rate, 0.8)
        self.assertIn("FAMILY_NOT_DISABLED", decision.reason_codes)

    def test_family_within_limit_is_allowed(self) -> None:
        decision = evaluate_family_circuit_breaker(
            FamilyTelemetry(
                family_id="hf-api",
                supported_count=7,
                rejected_count=2,
                inconclusive_count=1,
                minimum_sample=10,
                bad_outcome_threshold=0.60,
            )
        )

        self.assertEqual(decision.action, CircuitBreakerAction.ALLOW)
        self.assertFalse(decision.throttle)
        self.assertEqual(decision.reason_codes, ("FAMILY_TELEMETRY_WITHIN_LIMIT",))


if __name__ == "__main__":
    unittest.main()
