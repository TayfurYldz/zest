"""Core: authorization, policy, scope, budget, and Approval semantics.

Python types here are not language-neutral architectural contracts.
Cross-boundary Worker truth remains `contracts/` JSON Schema.
"""

from zest.core.approval import (
    ApprovalView,
    RecordedApprovalEvaluation,
    check_approval,
    evaluate_recorded_approval,
)
from zest.core.authorization import (
    AuthorizationSourceView,
    check_authorization,
)
from zest.core.budget import (
    BudgetUsage,
    IssuedBudget,
    allocate_experiment_budget,
    check_budget,
)
from zest.core.capability import CapabilityAuthorizationView
from zest.core.enums import (
    ActorType,
    ApprovalDecision,
    AuthorizationSourceState,
    ExecutionDecisionKind,
    ReasonCode,
    ScopeClassification,
    ScopeDecision,
    ScopeRuleEffect,
    SideEffectLevel,
)
from zest.core.errors import (
    BudgetAllocationError,
    CoreInputError,
    InvalidBudgetError,
)
from zest.core.execution import ExecutionDecision, ExecutionRequest, evaluate_execution
from zest.core.identity import Actor
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch, check_scope
from zest.core.scope_compiler import (
    ScopeCandidate,
    ScopeRuleDefinition,
    compile_scope_rules,
    evaluate_scope_candidate,
)

__all__ = [
    "Actor",
    "ActorType",
    "ApprovalDecision",
    "ApprovalView",
    "RecordedApprovalEvaluation",
    "AuthorizationSourceState",
    "AuthorizationSourceView",
    "BudgetAllocationError",
    "BudgetUsage",
    "CapabilityAuthorizationView",
    "CoreInputError",
    "ExecutionDecision",
    "ExecutionDecisionKind",
    "ExecutionRequest",
    "InvalidBudgetError",
    "IssuedBudget",
    "ReasonCode",
    "ScopeCandidate",
    "ScopeClassification",
    "ScopeDecision",
    "ScopeEvaluationInput",
    "ScopeRuleDefinition",
    "ScopeRuleEffect",
    "ScopeRuleMatch",
    "SideEffectLevel",
    "allocate_experiment_budget",
    "check_approval",
    "evaluate_recorded_approval",
    "check_authorization",
    "check_budget",
    "check_scope",
    "compile_scope_rules",
    "evaluate_execution",
    "evaluate_scope_candidate",
]
