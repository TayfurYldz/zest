"""Pure execution authorization. ExecutionDecision is not an execution result."""

from dataclasses import dataclass

from zest.core.approval import ApprovalCheck, ApprovalView, check_approval
from zest.core.authorization import (
    AuthorizationSourceView,
    check_authorization,
)
from zest.core.budget import BudgetUsage, IssuedBudget, check_budget
from zest.core.capability import (
    CapabilityAuthorizationCheck,
    CapabilityAuthorizationView,
    check_capability_authorization,
)
from zest.core.enums import (
    ExecutionDecisionKind,
    ReasonCode,
    ScopeDecision,
    SideEffectLevel,
)
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id
from zest.core.scope import ScopeEvaluationInput, check_scope
from zest.tools.registry import CapabilityRegistry


def _parse_side_effect_level(value: SideEffectLevel | int) -> SideEffectLevel:
    if isinstance(value, SideEffectLevel):
        return value
    try:
        return SideEffectLevel(value)
    except ValueError as exc:
        raise CoreInputError("side_effect_level must be 0, 1, 2, or 3") from exc


@dataclass(frozen=True)
class ExecutionRequest:
    authorization_source: AuthorizationSourceView | None
    scope: ScopeEvaluationInput
    issued_budget: IssuedBudget
    budget_usage: BudgetUsage
    requested_budget_id: str
    side_effect_level: SideEffectLevel | int
    requested_subject: str
    capability: CapabilityAuthorizationView | None
    approval: ApprovalView | None = None


@dataclass(frozen=True)
class ExecutionDecision:
    """Core authorization outcome. Not a WorkerResult and not a Finding."""

    decision: ExecutionDecisionKind
    reason_code: ReasonCode
    authorization_source_id: str | None
    matched_scope_rule_ids: tuple[str, ...]
    budget_id: str | None
    side_effect_level: SideEffectLevel
    approval_id: str | None = None


def _decision(
    kind: ExecutionDecisionKind,
    reason: ReasonCode,
    *,
    authorization_source_id: str | None,
    matched_scope_rule_ids: tuple[str, ...],
    budget_id: str | None,
    side_effect_level: SideEffectLevel,
    approval_id: str | None = None,
) -> ExecutionDecision:
    return ExecutionDecision(
        decision=kind,
        reason_code=reason,
        authorization_source_id=authorization_source_id,
        matched_scope_rule_ids=matched_scope_rule_ids,
        budget_id=budget_id,
        side_effect_level=side_effect_level,
        approval_id=approval_id,
    )


def evaluate_execution(
    request: ExecutionRequest,
    *,
    capability_registry: CapabilityRegistry | None = None,
) -> ExecutionDecision:
    """capability registry → authorization → scope → budget → side-effect → approval.

    DENY outranks REQUIRE_HUMAN_REVIEW. Level 3 is denied even with Approval.
    CapabilityAuthorizationView is a claim; Tools registry is policy truth.
    """
    if not isinstance(request, ExecutionRequest):
        raise CoreInputError("request must be ExecutionRequest")
    if request.issued_budget is None:
        raise CoreInputError("issued_budget is required")
    if request.budget_usage is None:
        raise CoreInputError("budget_usage is required")
    if request.scope is None:
        raise CoreInputError("scope evaluation is required")

    level = _parse_side_effect_level(request.side_effect_level)
    require_opaque_id(request.requested_subject, "requested_subject")
    require_opaque_id(request.requested_budget_id, "requested_budget_id")

    capability: CapabilityAuthorizationCheck = check_capability_authorization(
        request.capability, level, registry=capability_registry
    )
    auth = check_authorization(request.authorization_source)
    scope = check_scope(request.scope)
    budget = check_budget(
        request.issued_budget, request.budget_usage, request.requested_budget_id
    )

    def finish(
        kind: ExecutionDecisionKind,
        reason: ReasonCode,
        approval_id: str | None = None,
    ) -> ExecutionDecision:
        return _decision(
            kind,
            reason,
            authorization_source_id=auth.authorization_source_id,
            matched_scope_rule_ids=scope.matched_rule_ids,
            budget_id=budget.budget_id,
            side_effect_level=level,
            approval_id=approval_id,
        )

    if not capability.allowed_to_continue:
        return finish(ExecutionDecisionKind.DENY, capability.reason_code)

    if not auth.allowed_to_continue:
        return finish(ExecutionDecisionKind.DENY, auth.reason_code)

    if scope.decision is ScopeDecision.DENY:
        return finish(ExecutionDecisionKind.DENY, scope.reason_code)

    if not budget.allowed_to_continue:
        return finish(ExecutionDecisionKind.DENY, budget.reason_code)

    if level is SideEffectLevel.LEVEL_3:
        return finish(
            ExecutionDecisionKind.DENY, ReasonCode.SIDE_EFFECT_LEVEL_DENIED
        )

    if scope.decision is ScopeDecision.REQUIRE_HUMAN_REVIEW:
        return finish(
            ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW, scope.reason_code
        )

    if level in (SideEffectLevel.LEVEL_0, SideEffectLevel.LEVEL_1):
        return finish(ExecutionDecisionKind.ALLOW, ReasonCode.ALLOWED)

    approval: ApprovalCheck = check_approval(
        request.approval, request.requested_subject
    )
    if approval.require_human_review:
        return finish(
            ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW,
            approval.reason_code,
            approval.approval_id,
        )
    if not approval.authorizes:
        return finish(
            ExecutionDecisionKind.DENY, approval.reason_code, approval.approval_id
        )
    return finish(
        ExecutionDecisionKind.ALLOW, ReasonCode.ALLOWED, approval.approval_id
    )
