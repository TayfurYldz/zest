"""Build Core capability claims from the Tools registry. Not policy truth."""

from __future__ import annotations

from typing import Mapping

from zest.core.capability import CapabilityAuthorizationView
from zest.research.types import ExperimentPlan
from zest.tools.integration_allowlist import (
    INTEGRATION_CAPABILITY_VERSION,
    integration_capability_ids,
    integration_definition_fingerprint,
    lookup_integration_capability,
)
from zest.tools.registry import (
    WORKER_EXECUTOR_CLASS,
    CapabilityRegistry,
    load_capability_registry,
    validate_action_arguments,
)


class CapabilityBindingError(ValueError):
    """Plan cannot be authorized against the current capability registry."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


def capability_view_for(
    capability_id: str,
    action: str,
    *,
    effective_side_effect: int | None = None,
    capability_version: str | None = None,
    definition_fingerprint: str | None = None,
    registry: CapabilityRegistry | None = None,
) -> CapabilityAuthorizationView:
    catalog = registry if registry is not None else load_capability_registry()
    found = catalog.lookup(capability_id, action)
    if found is None:
        definition = catalog.get(capability_id)
        if definition is None:
            raise CapabilityBindingError("UNKNOWN_CAPABILITY", "unknown capability")
        raise CapabilityBindingError("UNKNOWN_ACTION", "unknown action")
    definition, action_def = found
    if definition.executor_class != WORKER_EXECUTOR_CLASS:
        raise CapabilityBindingError("UNKNOWN_CAPABILITY", "unknown capability")
    effective = (
        action_def.minimum_side_effect_level
        if effective_side_effect is None
        else effective_side_effect
    )
    return CapabilityAuthorizationView(
        capability_id=definition.capability_id,
        action=action_def.action_id,
        capability_version=capability_version or definition.version,
        definition_fingerprint=definition_fingerprint or definition.definition_fingerprint,
        authoritative_minimum_side_effect=action_def.minimum_side_effect_level,
        effective_side_effect=effective,
    )


def integration_capability_view_for(
    capability_id: str,
    action: str,
    *,
    effective_side_effect: int | None = None,
) -> CapabilityAuthorizationView:
    entry = lookup_integration_capability(capability_id, action)
    if entry is None:
        if capability_id in integration_capability_ids():
            raise CapabilityBindingError("UNKNOWN_ACTION", "unknown action")
        raise CapabilityBindingError("UNKNOWN_CAPABILITY", "unknown capability")
    effective = (
        entry.minimum_side_effect_level
        if effective_side_effect is None
        else effective_side_effect
    )
    return CapabilityAuthorizationView(
        capability_id=entry.capability_id,
        action=entry.action,
        capability_version=INTEGRATION_CAPABILITY_VERSION,
        definition_fingerprint=integration_definition_fingerprint(entry.capability_id),
        authoritative_minimum_side_effect=entry.minimum_side_effect_level,
        effective_side_effect=effective,
    )


def capability_view_for_plan(
    plan: ExperimentPlan,
    *,
    registry: CapabilityRegistry | None = None,
) -> CapabilityAuthorizationView:
    catalog = registry if registry is not None else load_capability_registry()
    found = catalog.lookup(plan.required_capability, plan.action)
    if found is None:
        definition = catalog.get(plan.required_capability)
        if definition is None:
            raise CapabilityBindingError("UNKNOWN_CAPABILITY", "unknown capability")
        raise CapabilityBindingError("UNKNOWN_ACTION", "unknown action")
    definition, action_def = found
    if definition.executor_class != WORKER_EXECUTOR_CLASS:
        raise CapabilityBindingError("UNKNOWN_CAPABILITY", "unknown capability")
    bound = plan.capability_version is not None and plan.capability_definition_fingerprint is not None
    if bound:
        return CapabilityAuthorizationView(
            capability_id=plan.required_capability,
            action=plan.action,
            capability_version=plan.capability_version,
            definition_fingerprint=plan.capability_definition_fingerprint,
            authoritative_minimum_side_effect=action_def.minimum_side_effect_level,
            effective_side_effect=plan.side_effect_level,
        )
    _assert_legacy_compatible(plan, action_def)
    return CapabilityAuthorizationView(
        capability_id=definition.capability_id,
        action=action_def.action_id,
        capability_version=definition.version,
        definition_fingerprint=definition.definition_fingerprint,
        authoritative_minimum_side_effect=action_def.minimum_side_effect_level,
        effective_side_effect=plan.side_effect_level,
    )


def _assert_legacy_compatible(plan: ExperimentPlan, action_def) -> None:
    if plan.side_effect_level < action_def.minimum_side_effect_level:
        raise CapabilityBindingError(
            "RISK_UNDERSTATEMENT",
            "legacy plan side effect is below current action minimum",
        )
    if plan.side_effect_level > action_def.maximum_side_effect_level:
        raise CapabilityBindingError(
            "RISK_EXCEEDS_CAPABILITY",
            "legacy plan side effect exceeds current action maximum",
        )
    issue = validate_action_arguments(action_def.argument_schema, plan.arguments)
    if issue is not None:
        raise CapabilityBindingError(issue.reason_code, issue.message)


def require_new_plan_bindings(plan: ExperimentPlan) -> None:
    if not plan.capability_version or not plan.capability_definition_fingerprint:
        raise CapabilityBindingError(
            "DEFINITION_FINGERPRINT_MISMATCH",
            "new experiment plans must persist capability version and fingerprint",
        )
