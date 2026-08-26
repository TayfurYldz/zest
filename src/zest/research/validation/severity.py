"""SD-G10 severity engine.

Severity is downstream of validation. It must never appear in Hypothesis,
Observation, Evidence, Candidate, or early FindingProposal rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.research.impact.types import ImpactKind
from zest.research.types import ResearchInputError


class InternalSeverity(Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ValidationState(Enum):
    PASSED = "PASSED"
    NOT_PASSED = "NOT_PASSED"


class ScopeState(Enum):
    IN_SCOPE = "IN_SCOPE"
    NOT_IN_SCOPE = "NOT_IN_SCOPE"


@dataclass(frozen=True)
class PlatformSeverityMapping:
    internal: InternalSeverity
    bugcrowd_priority: str
    hackerone_severity: str
    vrt_category: str

    def __post_init__(self) -> None:
        if not isinstance(self.internal, InternalSeverity):
            raise ResearchInputError("internal must be an InternalSeverity")
        for field_name in ("bugcrowd_priority", "hackerone_severity", "vrt_category"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ResearchInputError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class SeverityInput:
    validation_state: ValidationState | str
    scope_state: ScopeState | str
    impact_kinds: tuple[ImpactKind | str, ...]
    data_sensitivity: str = "NONE"
    affected_scope: str = "SINGLE_USER"

    def __post_init__(self) -> None:
        object.__setattr__(self, "validation_state", _coerce_validation(self.validation_state))
        object.__setattr__(self, "scope_state", _coerce_scope(self.scope_state))
        if not isinstance(self.impact_kinds, tuple):
            raise ResearchInputError("impact_kinds must be a tuple")
        object.__setattr__(
            self,
            "impact_kinds",
            tuple(_coerce_impact_kind(item) for item in self.impact_kinds),
        )
        object.__setattr__(
            self,
            "data_sensitivity",
            _require_choice(
                self.data_sensitivity,
                "data_sensitivity",
                {"NONE", "LOW", "SENSITIVE", "BULK_SENSITIVE"},
            ),
        )
        object.__setattr__(
            self,
            "affected_scope",
            _require_choice(
                self.affected_scope,
                "affected_scope",
                {"SINGLE_USER", "MULTI_USER", "ADMIN", "INFRASTRUCTURE"},
            ),
        )


@dataclass(frozen=True)
class SeverityResult:
    severity: InternalSeverity | None
    platform_mapping: PlatformSeverityMapping | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.severity is not None and not isinstance(self.severity, InternalSeverity):
            raise ResearchInputError("severity must be an InternalSeverity or None")
        if self.platform_mapping is not None and not isinstance(
            self.platform_mapping, PlatformSeverityMapping
        ):
            raise ResearchInputError("platform_mapping must be a PlatformSeverityMapping or None")
        if not isinstance(self.reason_codes, tuple):
            raise ResearchInputError("reason_codes must be a tuple")

    @property
    def scored(self) -> bool:
        return self.severity is not None


_PLATFORM_MAPPING = {
    InternalSeverity.P0: PlatformSeverityMapping(
        internal=InternalSeverity.P0,
        bugcrowd_priority="P1",
        hackerone_severity="Critical",
        vrt_category="Server Security Misconfiguration / Privilege Escalation",
    ),
    InternalSeverity.P1: PlatformSeverityMapping(
        internal=InternalSeverity.P1,
        bugcrowd_priority="P2",
        hackerone_severity="High",
        vrt_category="Broken Access Control",
    ),
    InternalSeverity.P2: PlatformSeverityMapping(
        internal=InternalSeverity.P2,
        bugcrowd_priority="P3",
        hackerone_severity="Medium",
        vrt_category="Sensitive Data Exposure",
    ),
    InternalSeverity.P3: PlatformSeverityMapping(
        internal=InternalSeverity.P3,
        bugcrowd_priority="P4",
        hackerone_severity="Low",
        vrt_category="Low Impact Security Weakness",
    ),
}


def classify_severity(signal: SeverityInput) -> SeverityResult:
    """Classify validated in-scope impact into internal P0-P3 severity."""

    if signal.validation_state is not ValidationState.PASSED:
        return SeverityResult(
            severity=None,
            platform_mapping=None,
            reason_codes=("SEVERITY_REJECTED_VALIDATION_NOT_PASSED",),
        )
    if signal.scope_state is not ScopeState.IN_SCOPE:
        return SeverityResult(
            severity=None,
            platform_mapping=None,
            reason_codes=("SEVERITY_REJECTED_NOT_IN_SCOPE",),
        )
    if not signal.impact_kinds:
        return SeverityResult(
            severity=InternalSeverity.P3,
            platform_mapping=_PLATFORM_MAPPING[InternalSeverity.P3],
            reason_codes=("NO_DEMONSTRATED_IMPACT",),
        )

    impact_values = frozenset(item.value for item in signal.impact_kinds)
    if (
        ImpactKind.ACCOUNT_TAKEOVER_PATH.value in impact_values
        or signal.affected_scope in {"ADMIN", "INFRASTRUCTURE"}
        or signal.data_sensitivity == "BULK_SENSITIVE"
    ):
        severity = InternalSeverity.P0
        reason = "TERMINAL_OR_BULK_IMPACT"
    elif (
        ImpactKind.AUTH_BYPASS.value in impact_values
        or ImpactKind.DATA_WRITE.value in impact_values
        or ImpactKind.STATE_CORRUPTION.value in impact_values
        or signal.data_sensitivity == "SENSITIVE"
    ):
        severity = InternalSeverity.P1
        reason = "HIGH_VALUE_AUTH_OR_WRITE_IMPACT"
    elif ImpactKind.DATA_READ.value in impact_values:
        severity = InternalSeverity.P2
        reason = "BOUNDED_DATA_READ_IMPACT"
    else:
        severity = InternalSeverity.P3
        reason = "LOW_OR_EXTERNAL_CALLBACK_ONLY_IMPACT"

    return SeverityResult(
        severity=severity,
        platform_mapping=_PLATFORM_MAPPING[severity],
        reason_codes=(reason,),
    )


_SEVERITY_RANK = {
    InternalSeverity.P0: 0,
    InternalSeverity.P1: 1,
    InternalSeverity.P2: 2,
    InternalSeverity.P3: 3,
}


def bound_severity(
    demonstrated: SeverityResult,
    requested: SeverityResult,
) -> SeverityResult:
    """Caller fields must not exceed the class supported by demonstrated impact.

    `demonstrated` is classified from ImpactGraph/Evidence-derived kinds with
    default (non-escalating) sensitivity/scope. `requested` may include
    caller-supplied `data_sensitivity` / `affected_scope`. The result is never
    more severe than `demonstrated`.
    """

    if not demonstrated.scored or demonstrated.severity is None:
        return demonstrated
    if not requested.scored or requested.severity is None:
        return requested
    if _SEVERITY_RANK[requested.severity] < _SEVERITY_RANK[demonstrated.severity]:
        return SeverityResult(
            severity=demonstrated.severity,
            platform_mapping=demonstrated.platform_mapping,
            reason_codes=(*demonstrated.reason_codes, "CALLER_SEVERITY_CLAMPED"),
        )
    return requested


def _coerce_validation(value: ValidationState | str) -> ValidationState:
    if isinstance(value, ValidationState):
        return value
    try:
        return ValidationState(str(value))
    except ValueError as exc:
        raise ResearchInputError(f"unknown validation state {value!r}") from exc


def _coerce_scope(value: ScopeState | str) -> ScopeState:
    if isinstance(value, ScopeState):
        return value
    try:
        return ScopeState(str(value))
    except ValueError as exc:
        raise ResearchInputError(f"unknown scope state {value!r}") from exc


def _coerce_impact_kind(value: ImpactKind | str) -> ImpactKind:
    if isinstance(value, ImpactKind):
        return value
    try:
        return ImpactKind(str(value))
    except ValueError as exc:
        raise ResearchInputError(f"unknown impact kind {value!r}") from exc


def _require_choice(value: object, field_name: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ResearchInputError(f"{field_name} must be one of {sorted(allowed)}")
    return value
