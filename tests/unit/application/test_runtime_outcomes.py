from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.runtime_outcomes import (
    runtime_outcome_from_exception,
    stop_reason_for_runtime_outcome,
)
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RuntimeCancelledError,
    RuntimeProcessError,
    RuntimeUnavailableError,
)
from zest.research.model_runtime import RuntimeOutcome
from zest.research.orchestration import StopReason


class RuntimeOutcomeMappingTests(unittest.TestCase):
    def test_content_policy_maps_only_to_policy_stop(self) -> None:
        outcome = runtime_outcome_from_exception(ContentPolicyBlockedError("blocked"))
        self.assertEqual(outcome, RuntimeOutcome.CONTENT_POLICY_BLOCKED)
        self.assertEqual(
            stop_reason_for_runtime_outcome(outcome), StopReason.CONTENT_POLICY_BLOCKED
        )

    def test_auth_is_not_policy_block(self) -> None:
        outcome = runtime_outcome_from_exception(ProviderAuthError("auth"))
        self.assertEqual(outcome, RuntimeOutcome.AUTH_FAILED)
        self.assertNotEqual(stop_reason_for_runtime_outcome(outcome), StopReason.CONTENT_POLICY_BLOCKED)

    def test_rate_limit_maps_exactly_to_rate_limited_stop(self) -> None:
        outcome = runtime_outcome_from_exception(
            ProviderRateLimitError("rate")
        )

        self.assertEqual(
            outcome,
            RuntimeOutcome.RATE_LIMITED,
        )
        self.assertEqual(
            stop_reason_for_runtime_outcome(outcome),
            StopReason.RATE_LIMITED,
        )

    def test_runtime_cancel_maps_exactly_to_cancelled_stop(self) -> None:
        outcome = runtime_outcome_from_exception(
            RuntimeCancelledError("cancelled")
        )

        self.assertEqual(
            outcome,
            RuntimeOutcome.CANCELLED,
        )
        self.assertEqual(
            stop_reason_for_runtime_outcome(outcome),
            StopReason.CANCELLED,
        )

    def test_timeout_is_not_policy_block(self) -> None:
        outcome = runtime_outcome_from_exception(ProviderTimeoutError("timeout"))
        self.assertEqual(outcome, RuntimeOutcome.TIMED_OUT)
        self.assertEqual(stop_reason_for_runtime_outcome(outcome), StopReason.OPERATIONAL_FAILURE)

    def test_process_failed_is_not_policy_block(self) -> None:
        outcome = runtime_outcome_from_exception(RuntimeProcessError("boom"))
        self.assertEqual(outcome, RuntimeOutcome.PROCESS_FAILED)
        self.assertEqual(stop_reason_for_runtime_outcome(outcome), StopReason.OPERATIONAL_FAILURE)

    def test_unavailable_is_not_policy_block(self) -> None:
        outcome = runtime_outcome_from_exception(RuntimeUnavailableError("missing"))
        self.assertEqual(outcome, RuntimeOutcome.UNAVAILABLE)
        self.assertEqual(stop_reason_for_runtime_outcome(outcome), StopReason.NO_COMPATIBLE_RUNTIME)


if __name__ == "__main__":
    unittest.main()
