"""Security-property checks over observed target behavior. UNKNOWN is not VIOLATED."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

INVARIANT_EVALUATION_STRATEGY = "invariant.security_property.v1"

PROPERTY_UNAUTHENTICATED_DENY = "UNAUTHENTICATED_MUST_NOT_ACCESS"
PROPERTY_CROSS_OWNER_DENY = "CROSS_OWNER_READ_MUST_FAIL"
PROPERTY_PROTOCOL_DENY_NOT_COVERAGE = "PROTOCOL_CORE_DENIAL_NOT_COVERAGE"
DENIED_STATUSES = frozenset({401, 403, 404})


class InvariantCheckOutcome(Enum):
    HOLDS = "HOLDS"
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    MISSING_PRECONDITION = "MISSING_PRECONDITION"
    BLOCKED_BY_AUTHORITY = "BLOCKED_BY_AUTHORITY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    SUPERSEDED = "SUPERSEDED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class InvariantCheckResult:
    outcome: InvariantCheckOutcome
    property_id: str
    reason_codes: tuple[str, ...]
    details: Mapping[str, Any]
    may_support_evidence: bool


def evaluate_access_deny(
    *,
    property_id: str,
    allowed_status: object | None,
    denied_status: object | None,
    has_allowed_observation: bool,
    has_denied_observation: bool,
) -> InvariantCheckResult:
    if not has_allowed_observation or not has_denied_observation:
        return InvariantCheckResult(
            outcome=InvariantCheckOutcome.INSUFFICIENT_DATA,
            property_id=property_id,
            reason_codes=("INSUFFICIENT_DATA", "UNKNOWN_NOT_VIOLATION"),
            details={"has_allowed": has_allowed_observation, "has_denied": has_denied_observation},
            may_support_evidence=False,
        )
    if denied_status in DENIED_STATUSES:
        return InvariantCheckResult(
            outcome=InvariantCheckOutcome.HOLDS,
            property_id=property_id,
            reason_codes=("EXPECTED_DENY_OBSERVED",),
            details={"denied_status": denied_status, "allowed_status": allowed_status},
            may_support_evidence=False,
        )
    if isinstance(denied_status, int) and 200 <= denied_status < 300:
        return InvariantCheckResult(
            outcome=InvariantCheckOutcome.VIOLATED,
            property_id=property_id,
            reason_codes=("EXPECTED_DENY_NOT_OBSERVED",),
            details={"denied_status": denied_status, "allowed_status": allowed_status},
            may_support_evidence=True,
        )
    return InvariantCheckResult(
        outcome=InvariantCheckOutcome.UNKNOWN,
        property_id=property_id,
        reason_codes=("UNKNOWN", "STATUS_NOT_CLASSIFIED"),
        details={"denied_status": denied_status},
        may_support_evidence=False,
    )


def evaluate_authority_block(property_id: str) -> InvariantCheckResult:
    return InvariantCheckResult(
        outcome=InvariantCheckOutcome.BLOCKED_BY_AUTHORITY,
        property_id=property_id,
        reason_codes=("CONNECTED_BLOCKED_BY_AUTHORITY", "NOT_COVERAGE"),
        details={"native_capability": "http.raw_exchange"},
        may_support_evidence=False,
    )
