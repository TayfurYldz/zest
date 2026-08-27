"""Retry eligibility. A7-lite does not implement an automatic retry engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.data.records import ExecutionAttemptState


class RetryClassification(Enum):
    SAFE_RETRY = "SAFE_RETRY"
    REAUTHORIZE_THEN_RETRY = "REAUTHORIZE_THEN_RETRY"
    UNSAFE_UNKNOWN_OUTCOME = "UNSAFE_UNKNOWN_OUTCOME"
    NON_RETRYABLE = "NON_RETRYABLE"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"


@dataclass(frozen=True)
class RetryDecision:
    classification: RetryClassification
    reason_code: str
    reason: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "classification": self.classification.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


def classify_retry_semantics(attempt) -> RetryDecision:
    """Classify one persisted attempt without authorizing or executing a retry.

    The conservative default is HUMAN_DECISION_REQUIRED. In particular,
    UNKNOWN contact or a possible external side effect can never become
    SAFE_RETRY merely because a process failed.
    """

    state = getattr(attempt, "state", None)
    contact = getattr(attempt, "target_contact_status", "UNKNOWN")
    side_effect_level = int(getattr(attempt, "side_effect_level", 0))
    dispatched = getattr(attempt, "dispatch_started_at", None) is not None

    if state == ExecutionAttemptState.COMPLETED.value:
        return RetryDecision(
            RetryClassification.NON_RETRYABLE,
            "ATTEMPT_COMPLETED",
            "completed attempts are not retried",
        )
    if state in {
        ExecutionAttemptState.DISPATCHING.value,
        ExecutionAttemptState.UNKNOWN_OUTCOME.value,
    } or dispatched:
        return RetryDecision(
            RetryClassification.UNSAFE_UNKNOWN_OUTCOME,
            "EXTERNAL_OUTCOME_UNKNOWN",
            "dispatch may have reached an external target; reconcile before any retry",
        )
    if contact == "UNKNOWN":
        return RetryDecision(
            RetryClassification.HUMAN_DECISION_REQUIRED,
            "TARGET_CONTACT_UNKNOWN",
            "target contact is unknown; the system cannot prove a safe retry",
        )
    if side_effect_level > 0:
        return RetryDecision(
            RetryClassification.REAUTHORIZE_THEN_RETRY,
            "SIDE_EFFECT_REQUIRES_REAUTHORIZATION",
            "a new authorization decision is required before repeating a side-effectful attempt",
        )
    if state == ExecutionAttemptState.AUTHORIZED.value and contact == "NOT_CONTACTED":
        return RetryDecision(
            RetryClassification.SAFE_RETRY,
            "AUTHORIZED_NOT_CONTACTED",
            "level-zero attempt was authorized but target contact is explicitly not recorded",
        )
    if state in {
        ExecutionAttemptState.FAILED.value,
        ExecutionAttemptState.TIMED_OUT.value,
        ExecutionAttemptState.CANCELLED.value,
    } and contact == "NOT_CONTACTED":
        return RetryDecision(
            RetryClassification.SAFE_RETRY,
            "TERMINAL_NOT_CONTACTED",
            "terminal attempt has no recorded target contact and no side effect",
        )
    return RetryDecision(
        RetryClassification.HUMAN_DECISION_REQUIRED,
        "RETRY_CONTEXT_INCOMPLETE",
        "retry context is incomplete; require explicit human decision",
    )


def automatic_retry_allowed(*, attempt_state: str, side_effect_level: int) -> bool:
    """Fail closed. Unknown external outcome is not a license to repeat the action.

    Future policy may consider side_effect_level, capability semantics, attempt
    state, known/unknown outcome, budget, and a fresh Core evaluation. A7-lite
    never auto-retries, including Level 0 diagnostic.echo.
    """
    del side_effect_level
    if attempt_state in {
        ExecutionAttemptState.DISPATCHING.value,
        ExecutionAttemptState.UNKNOWN_OUTCOME.value,
    }:
        return False
    return False
