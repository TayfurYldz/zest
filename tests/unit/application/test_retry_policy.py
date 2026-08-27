from __future__ import annotations

import unittest
from types import SimpleNamespace

import pathsetup  # noqa: F401

from zest.application.retry_policy import (
    RetryClassification,
    classify_retry_semantics,
)


def _attempt(*, state: str, contact: str = "UNKNOWN", side_effect_level: int = 0, dispatched: bool = False):
    return SimpleNamespace(
        state=state,
        target_contact_status=contact,
        side_effect_level=side_effect_level,
        dispatch_started_at=object() if dispatched else None,
    )


class RetryPolicyTests(unittest.TestCase):
    def test_unknown_contact_is_never_safe_retry(self) -> None:
        decision = classify_retry_semantics(_attempt(state="FAILED"))
        self.assertEqual(decision.classification, RetryClassification.HUMAN_DECISION_REQUIRED)
        self.assertEqual(decision.reason_code, "TARGET_CONTACT_UNKNOWN")

    def test_dispatching_is_unsafe_unknown_outcome(self) -> None:
        decision = classify_retry_semantics(_attempt(state="DISPATCHING", dispatched=True))
        self.assertEqual(decision.classification, RetryClassification.UNSAFE_UNKNOWN_OUTCOME)

    def test_explicitly_not_contacted_level_zero_can_be_retried(self) -> None:
        decision = classify_retry_semantics(
            _attempt(state="FAILED", contact="NOT_CONTACTED")
        )
        self.assertEqual(decision.classification, RetryClassification.SAFE_RETRY)

    def test_side_effectful_attempt_requires_new_authorization(self) -> None:
        decision = classify_retry_semantics(
            _attempt(state="FAILED", contact="NOT_CONTACTED", side_effect_level=1)
        )
        self.assertEqual(
            decision.classification,
            RetryClassification.REAUTHORIZE_THEN_RETRY,
        )


if __name__ == "__main__":
    unittest.main()
