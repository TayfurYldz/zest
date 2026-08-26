"""Independent SD-G10 validator tier gate.

This is not the hunt scheduler's V3 queue. It is a final admission precheck:
required validation tiers must have actually passed before a Candidate/Finding
path can be treated as validator-admissible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from zest.research.types import ResearchInputError


class ValidationTier(Enum):
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"


class ValidationTierOutcome(Enum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    QUEUED = "QUEUED"


class ValidationAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_MISSING_REQUIRED_TIER = "REJECTED_MISSING_REQUIRED_TIER"
    REJECTED_TIER_NOT_PASSED = "REJECTED_TIER_NOT_PASSED"
    REJECTED_UNKNOWN_REQUIRED_TIER = "REJECTED_UNKNOWN_REQUIRED_TIER"


_REQUIRED_CHAIN = {
    ValidationTier.V1: (ValidationTier.V1,),
    ValidationTier.V2: (ValidationTier.V1, ValidationTier.V2),
    ValidationTier.V3: (ValidationTier.V1, ValidationTier.V2, ValidationTier.V3),
}


@dataclass(frozen=True)
class ValidationAdmissionDecision:
    outcome: ValidationAdmissionOutcome
    admitted: bool
    required_tiers: tuple[ValidationTier, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ValidationAdmissionOutcome):
            raise ResearchInputError("outcome must be a ValidationAdmissionOutcome")
        if not isinstance(self.admitted, bool):
            raise ResearchInputError("admitted must be a bool")
        if not isinstance(self.required_tiers, tuple):
            raise ResearchInputError("required_tiers must be a tuple")
        if not all(isinstance(item, ValidationTier) for item in self.required_tiers):
            raise ResearchInputError("required_tiers must contain ValidationTier values")
        if not isinstance(self.reason_codes, tuple):
            raise ResearchInputError("reason_codes must be a tuple")


def validate_required_tiers(
    required_tier: ValidationTier | str,
    observed_outcomes: Mapping[ValidationTier | str, ValidationTierOutcome | str],
) -> ValidationAdmissionDecision:
    """Fail-closed validation admission for the required tier chain.

    V3 QUEUED is not enough for validator admission. The independent validator
    requires a PASSED outcome for every tier in the required chain.
    """

    tier = _coerce_tier(required_tier)
    required = _REQUIRED_CHAIN.get(tier)
    if required is None:
        return ValidationAdmissionDecision(
            outcome=ValidationAdmissionOutcome.REJECTED_UNKNOWN_REQUIRED_TIER,
            admitted=False,
            required_tiers=(),
            reason_codes=("UNKNOWN_REQUIRED_TIER",),
        )

    normalized = {
        _coerce_tier(key): _coerce_outcome(value)
        for key, value in observed_outcomes.items()
    }
    missing = tuple(item for item in required if item not in normalized)
    if missing:
        return ValidationAdmissionDecision(
            outcome=ValidationAdmissionOutcome.REJECTED_MISSING_REQUIRED_TIER,
            admitted=False,
            required_tiers=required,
            reason_codes=tuple(f"{item.value}_MISSING" for item in missing),
        )

    not_passed = tuple(
        item for item in required if normalized[item] is not ValidationTierOutcome.PASSED
    )
    if not_passed:
        return ValidationAdmissionDecision(
            outcome=ValidationAdmissionOutcome.REJECTED_TIER_NOT_PASSED,
            admitted=False,
            required_tiers=required,
            reason_codes=tuple(f"{item.value}_{normalized[item].value}" for item in not_passed),
        )

    return ValidationAdmissionDecision(
        outcome=ValidationAdmissionOutcome.ADMITTED,
        admitted=True,
        required_tiers=required,
        reason_codes=("VALIDATION_TIERS_PASSED",),
    )


def _coerce_tier(value: ValidationTier | str) -> ValidationTier:
    if isinstance(value, ValidationTier):
        return value
    try:
        return ValidationTier(str(value))
    except ValueError as exc:
        raise ResearchInputError(f"unknown validation tier {value!r}") from exc


def _coerce_outcome(value: ValidationTierOutcome | str) -> ValidationTierOutcome:
    if isinstance(value, ValidationTierOutcome):
        return value
    try:
        return ValidationTierOutcome(str(value))
    except ValueError as exc:
        raise ResearchInputError(f"unknown validation tier outcome {value!r}") from exc
