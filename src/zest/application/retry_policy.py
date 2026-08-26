"""Retry eligibility. A7-lite does not implement an automatic retry engine."""

from __future__ import annotations

from zest.data.records import ExecutionAttemptState


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
