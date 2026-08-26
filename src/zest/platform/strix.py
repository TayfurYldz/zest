"""Strix Integration port. Strix is not Research Brain, Core, Memory, or Finding authority.

Platform owns the typed envelope. Concrete adapters live in Integrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol


class StrixRuntimeStatus(Enum):
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    DENIED = "DENIED"
    AUTH_FAILED = "AUTH_FAILED"
    TIMED_OUT = "TIMED_OUT"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CONTENT_POLICY_BLOCKED = "CONTENT_POLICY_BLOCKED"
    SCOPE_RECHECK_REQUIRED = "SCOPE_RECHECK_REQUIRED"


ALLOWED_STRIX_CAPABILITIES = frozenset({"strix.diagnostic.ping"})
UNRESTRICTED_CAPABILITY_MARKERS = frozenset({"*", "all", "unrestricted", "shell", "any"})


@dataclass(frozen=True)
class StrixExecutionRequest:
    """Controlled execution envelope. Not free-form shell authority."""

    research_run_id: str
    experiment_id: str
    correlation_id: str
    request_id: str
    capability: str
    authorized_target_reference: str
    budget_id: str
    side_effect_level: int
    authorization_decision_reference: str
    allowed_capabilities: tuple[str, ...]
    artifact_constraints: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "research_run_id",
            "experiment_id",
            "correlation_id",
            "request_id",
            "capability",
            "authorized_target_reference",
            "budget_id",
            "authorization_decision_reference",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.side_effect_level not in (0, 1, 2, 3):
            raise ValueError("side_effect_level must be 0, 1, 2, or 3")
        if not isinstance(self.allowed_capabilities, tuple) or not self.allowed_capabilities:
            raise ValueError("allowed_capabilities must be a non-empty tuple")
        if self.artifact_constraints is None:
            object.__setattr__(self, "artifact_constraints", {"not_evidence": True})
        else:
            object.__setattr__(self, "artifact_constraints", dict(self.artifact_constraints))


@dataclass(frozen=True)
class StrixExecutionOutcome:
    """Runtime/tool outcome. Not Observation, Evidence, Candidate, or Finding."""

    status: StrixRuntimeStatus
    untrusted: bool
    capability: str
    reason_codes: tuple[str, ...]
    payload: Mapping[str, object] | None = None

    @property
    def completed(self) -> bool:
        return self.status is StrixRuntimeStatus.COMPLETED


class StrixIntegration(Protocol):
    """Replaceable Strix runtime. Implementations must not live in Research or Core."""

    def execute(self, request: StrixExecutionRequest) -> StrixExecutionOutcome: ...


class StrixProcessClass(Enum):
    """Process classification. Not Observation and not a research conclusion."""

    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    CRASHED = "CRASHED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"


def classify_strix_process(
    *,
    executable_found: bool,
    exit_code: int | None,
    timed_out: bool,
    cancelled: bool,
) -> StrixProcessClass:
    """Classify a Strix child process. Does not auto-install Strix."""

    if not executable_found:
        return StrixProcessClass.UNAVAILABLE
    if cancelled:
        return StrixProcessClass.CANCELLED
    if timed_out:
        return StrixProcessClass.TIMED_OUT
    if exit_code is None:
        return StrixProcessClass.CRASHED
    if exit_code == 0:
        return StrixProcessClass.COMPLETED
    if exit_code < 0:
        return StrixProcessClass.CRASHED
    return StrixProcessClass.PROTOCOL_ERROR
