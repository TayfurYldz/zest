"""SD-G10 family circuit breaker.

The breaker throttles noisy families; it never disables or deletes a family.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.research.types import ResearchInputError


class CircuitBreakerAction(Enum):
    ALLOW = "ALLOW"
    THROTTLE = "THROTTLE"


@dataclass(frozen=True)
class FamilyTelemetry:
    family_id: str
    supported_count: int
    rejected_count: int
    inconclusive_count: int
    minimum_sample: int = 10
    bad_outcome_threshold: float = 0.60

    def __post_init__(self) -> None:
        if not isinstance(self.family_id, str) or not self.family_id.strip():
            raise ResearchInputError("family_id must be a non-empty string")
        for field_name in (
            "supported_count",
            "rejected_count",
            "inconclusive_count",
            "minimum_sample",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ResearchInputError(f"{field_name} must be a non-negative int")
        if self.minimum_sample < 1:
            raise ResearchInputError("minimum_sample must be >= 1")
        if not isinstance(self.bad_outcome_threshold, float):
            raise ResearchInputError("bad_outcome_threshold must be a float")
        if not 0.0 < self.bad_outcome_threshold <= 1.0:
            raise ResearchInputError("bad_outcome_threshold must be in (0.0, 1.0]")

    @property
    def total_count(self) -> int:
        return self.supported_count + self.rejected_count + self.inconclusive_count

    @property
    def bad_outcome_count(self) -> int:
        return self.rejected_count + self.inconclusive_count

    @property
    def bad_outcome_rate(self) -> float:
        if self.total_count == 0:
            return 0.0
        return self.bad_outcome_count / self.total_count


@dataclass(frozen=True)
class CircuitBreakerDecision:
    action: CircuitBreakerAction
    throttle: bool
    disable_family: bool
    requires_human_review_to_restore: bool
    bad_outcome_rate: float
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action, CircuitBreakerAction):
            raise ResearchInputError("action must be a CircuitBreakerAction")
        for field_name in (
            "throttle",
            "disable_family",
            "requires_human_review_to_restore",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ResearchInputError(f"{field_name} must be a bool")
        if not isinstance(self.bad_outcome_rate, float):
            raise ResearchInputError("bad_outcome_rate must be a float")
        if not isinstance(self.reason_codes, tuple):
            raise ResearchInputError("reason_codes must be a tuple")


def evaluate_family_circuit_breaker(telemetry: FamilyTelemetry) -> CircuitBreakerDecision:
    """Return ALLOW or THROTTLE from append-only family outcome telemetry."""

    if telemetry.total_count < telemetry.minimum_sample:
        return CircuitBreakerDecision(
            action=CircuitBreakerAction.ALLOW,
            throttle=False,
            disable_family=False,
            requires_human_review_to_restore=False,
            bad_outcome_rate=telemetry.bad_outcome_rate,
            reason_codes=("INSUFFICIENT_SAMPLE_ALLOW",),
        )

    if telemetry.bad_outcome_rate >= telemetry.bad_outcome_threshold:
        return CircuitBreakerDecision(
            action=CircuitBreakerAction.THROTTLE,
            throttle=True,
            disable_family=False,
            requires_human_review_to_restore=True,
            bad_outcome_rate=telemetry.bad_outcome_rate,
            reason_codes=("FAMILY_BAD_OUTCOME_RATE_THROTTLE", "FAMILY_NOT_DISABLED"),
        )

    return CircuitBreakerDecision(
        action=CircuitBreakerAction.ALLOW,
        throttle=False,
        disable_family=False,
        requires_human_review_to_restore=False,
        bad_outcome_rate=telemetry.bad_outcome_rate,
        reason_codes=("FAMILY_TELEMETRY_WITHIN_LIMIT",),
    )
