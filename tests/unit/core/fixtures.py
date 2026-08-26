from __future__ import annotations

import pathsetup  # noqa: F401

from zest.core import (
    ActorType,
    ApprovalDecision,
    ApprovalView,
    AuthorizationSourceState,
    AuthorizationSourceView,
    BudgetUsage,
    ExecutionRequest,
    IssuedBudget,
    ScopeEvaluationInput,
    ScopeRuleEffect,
    ScopeRuleMatch,
    SideEffectLevel,
)
from zest.core.capability import CapabilityAuthorizationView
from zest.tools.registry import load_capability_registry


def active_source() -> AuthorizationSourceView:
    return AuthorizationSourceView("as-1", "program-1", AuthorizationSourceState.ACTIVE)


def allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def issued_budget() -> IssuedBudget:
    return IssuedBudget("budget-1", 10, 10, 60_000, 2)


def zero_usage() -> BudgetUsage:
    return BudgetUsage(0, 0, 0, 0)


def human_approval(subject: str = "action-1") -> ApprovalView:
    return ApprovalView(
        approval_id="appr-1",
        subject_reference=subject,
        decision=ApprovalDecision.APPROVE,
        decided_by="operator-1",
        actor_type=ActorType.HUMAN_OPERATOR,
        recorded=True,
    )


def capability_view_for_side_effect(level: SideEffectLevel | int) -> CapabilityAuthorizationView:
    registry = load_capability_registry()
    parsed = int(level)
    if parsed == 1:
        definition = registry.get("http.state_transition")
        action_id = "probe"
        effective = 1
    else:
        definition = registry.get("diagnostic.echo")
        action_id = "echo"
        effective = parsed
    assert definition is not None
    action = definition.action(action_id)
    assert action is not None
    return CapabilityAuthorizationView(
        capability_id=definition.capability_id,
        action=action_id,
        capability_version=definition.version,
        definition_fingerprint=definition.definition_fingerprint,
        authoritative_minimum_side_effect=action.minimum_side_effect_level,
        effective_side_effect=effective,
    )


def base_request(**overrides) -> ExecutionRequest:
    issued = issued_budget()
    level = overrides.get("side_effect_level", SideEffectLevel.LEVEL_0)
    values = {
        "authorization_source": active_source(),
        "scope": allow_scope(),
        "issued_budget": issued,
        "budget_usage": zero_usage(),
        "requested_budget_id": issued.budget_id,
        "side_effect_level": SideEffectLevel.LEVEL_0,
        "requested_subject": "action-1",
        "capability": capability_view_for_side_effect(level),
        "approval": None,
    }
    values.update(overrides)
    if "capability" not in overrides:
        values["capability"] = capability_view_for_side_effect(values["side_effect_level"])
    return ExecutionRequest(**values)
