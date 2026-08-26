"""Resolve durable control signatures to live ephemeral refs. Never persist el-N."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.research.types import ResearchInputError


class ControlResolutionOutcome(Enum):
    MATCH = "MATCH"
    ZERO_MATCHES = "ZERO_MATCHES"
    AMBIGUOUS = "AMBIGUOUS"
    STALE_FINGERPRINT = "STALE_FINGERPRINT"
    PROCESS_GENERATION_CHANGED = "PROCESS_GENERATION_CHANGED"
    PAGE_CONTEXT_ABSENT = "PAGE_CONTEXT_ABSENT"


@dataclass(frozen=True)
class LiveControlView:
    element_reference: str
    snapshot_fingerprint: str
    tag: str
    name: str
    role: str
    input_type: str


@dataclass(frozen=True)
class DurableControlSignature:
    origin: str
    path: str
    tag: str
    name: str
    role: str
    input_type: str


@dataclass(frozen=True)
class ControlResolution:
    outcome: ControlResolutionOutcome
    live_element_reference: str | None
    match_count: int

    @property
    def may_interact(self) -> bool:
        return self.outcome is ControlResolutionOutcome.MATCH and self.live_element_reference is not None


def control_signature_key(signature: DurableControlSignature) -> tuple[str, str, str, str, str, str]:
    return (
        signature.origin,
        signature.path,
        signature.tag,
        signature.name,
        signature.role,
        signature.input_type,
    )


def resolve_control_ref(
    signature: DurableControlSignature,
    live_controls: tuple[LiveControlView, ...],
    *,
    current_page_fingerprint: str | None,
    expected_page_fingerprint: str | None,
    lease_present: bool,
    process_generation_changed: bool,
) -> ControlResolution:
    if not isinstance(signature, DurableControlSignature):
        raise ResearchInputError("signature must be DurableControlSignature")
    if not lease_present:
        return ControlResolution(ControlResolutionOutcome.PAGE_CONTEXT_ABSENT, None, 0)
    if process_generation_changed:
        return ControlResolution(ControlResolutionOutcome.PROCESS_GENERATION_CHANGED, None, 0)
    if not current_page_fingerprint:
        return ControlResolution(ControlResolutionOutcome.PAGE_CONTEXT_ABSENT, None, 0)
    if (
        expected_page_fingerprint is not None
        and current_page_fingerprint != expected_page_fingerprint
    ):
        return ControlResolution(ControlResolutionOutcome.STALE_FINGERPRINT, None, 0)
    matches = [
        item
        for item in live_controls
        if item.tag == signature.tag
        and item.name == signature.name
        and item.role == signature.role
        and item.input_type == signature.input_type
    ]
    if len(matches) == 0:
        return ControlResolution(ControlResolutionOutcome.ZERO_MATCHES, None, 0)
    if len(matches) > 1:
        return ControlResolution(ControlResolutionOutcome.AMBIGUOUS, None, len(matches))
    live_ref = matches[0].element_reference
    if live_ref.startswith("el-") is False and not live_ref:
        return ControlResolution(ControlResolutionOutcome.ZERO_MATCHES, None, 0)
    return ControlResolution(ControlResolutionOutcome.MATCH, live_ref, 1)
