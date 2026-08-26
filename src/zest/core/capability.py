"""Capability authorization claim bound to an ExecutionRequest. Not policy truth."""

from dataclasses import dataclass

from zest.core.enums import ReasonCode, SideEffectLevel
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id
from zest.tools.integration_allowlist import (
    integration_capability_ids,
    lookup_integration_capability,
)
from zest.tools.registry import (
    WORKER_EXECUTOR_CLASS,
    CapabilityRegistry,
    load_capability_registry,
)


@dataclass(frozen=True)
class CapabilityAuthorizationView:
    """Application claim/audit binding. Core re-validates against the Tools registry."""

    capability_id: str
    action: str
    capability_version: str
    definition_fingerprint: str
    authoritative_minimum_side_effect: int
    effective_side_effect: int

    def __post_init__(self) -> None:
        require_opaque_id(self.capability_id, "capability_id")
        require_opaque_id(self.action, "action")
        require_opaque_id(self.capability_version, "capability_version")
        require_opaque_id(self.definition_fingerprint, "definition_fingerprint")
        if self.authoritative_minimum_side_effect not in (0, 1, 2, 3):
            raise CoreInputError("authoritative_minimum_side_effect must be 0, 1, 2, or 3")
        if self.effective_side_effect not in (0, 1, 2, 3):
            raise CoreInputError("effective_side_effect must be 0, 1, 2, or 3")


@dataclass(frozen=True)
class CapabilityAuthorizationCheck:
    allowed_to_continue: bool
    reason_code: ReasonCode


def check_capability_authorization(
    view: CapabilityAuthorizationView | None,
    request_side_effect: SideEffectLevel,
    *,
    registry: CapabilityRegistry | None = None,
) -> CapabilityAuthorizationCheck:
    """Independent registry lookup. The view is not policy truth."""

    if view is None:
        return CapabilityAuthorizationCheck(
            False, ReasonCode.CAPABILITY_AUTHORIZATION_MISSING
        )
    catalog = registry if registry is not None else load_capability_registry()
    definition = catalog.get(view.capability_id)
    if definition is None:
        return _check_integration_allowlist(view, request_side_effect)
    if definition.executor_class != WORKER_EXECUTOR_CLASS:
        return CapabilityAuthorizationCheck(False, ReasonCode.UNKNOWN_CAPABILITY)
    action = definition.action(view.action)
    if action is None:
        return CapabilityAuthorizationCheck(False, ReasonCode.UNKNOWN_ACTION)
    if view.capability_version != definition.version:
        return CapabilityAuthorizationCheck(
            False, ReasonCode.UNSUPPORTED_CAPABILITY_VERSION
        )
    if view.definition_fingerprint != definition.definition_fingerprint:
        return CapabilityAuthorizationCheck(
            False, ReasonCode.DEFINITION_FINGERPRINT_MISMATCH
        )
    if view.authoritative_minimum_side_effect != action.minimum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_UNDERSTATEMENT)
    if view.effective_side_effect < action.minimum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_UNDERSTATEMENT)
    if view.effective_side_effect > action.maximum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_EXCEEDS_CAPABILITY)
    if int(request_side_effect) != view.effective_side_effect:
        return CapabilityAuthorizationCheck(False, ReasonCode.SIDE_EFFECT_BINDING_MISMATCH)
    return CapabilityAuthorizationCheck(True, ReasonCode.ALLOWED)


def _check_integration_allowlist(
    view: CapabilityAuthorizationView,
    request_side_effect: SideEffectLevel,
) -> CapabilityAuthorizationCheck:
    """Identity + side-effect only. Not Worker definition policy."""

    entry = lookup_integration_capability(view.capability_id, view.action)
    if entry is None:
        if view.capability_id in integration_capability_ids():
            return CapabilityAuthorizationCheck(False, ReasonCode.UNKNOWN_ACTION)
        return CapabilityAuthorizationCheck(False, ReasonCode.UNKNOWN_CAPABILITY)
    if view.authoritative_minimum_side_effect != entry.minimum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_UNDERSTATEMENT)
    if view.effective_side_effect < entry.minimum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_UNDERSTATEMENT)
    if view.effective_side_effect > entry.maximum_side_effect_level:
        return CapabilityAuthorizationCheck(False, ReasonCode.RISK_EXCEEDS_CAPABILITY)
    if int(request_side_effect) != view.effective_side_effect:
        return CapabilityAuthorizationCheck(False, ReasonCode.SIDE_EFFECT_BINDING_MISMATCH)
    return CapabilityAuthorizationCheck(True, ReasonCode.ALLOWED)
