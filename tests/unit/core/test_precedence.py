from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    AuthorizationSourceState,
    AuthorizationSourceView,
    BudgetUsage,
    ExecutionDecisionKind,
    ReasonCode,
    ScopeEvaluationInput,
    ScopeRuleEffect,
    ScopeRuleMatch,
    SideEffectLevel,
    evaluate_execution,
)
from core.fixtures import base_request, human_approval, issued_budget


class PrecedenceTests(unittest.TestCase):
    def test_scope_deny_plus_missing_approval_is_deny(self) -> None:
        scope = ScopeEvaluationInput(
            matches=(
                ScopeRuleMatch("r-deny", ScopeRuleEffect.DENY, True, "src"),
            ),
            ambiguous=False,
        )
        decision = evaluate_execution(
            base_request(
                scope=scope,
                side_effect_level=SideEffectLevel.LEVEL_0,
                approval=None,
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_inactive_authorization_plus_otherwise_valid_is_deny(self) -> None:
        source = AuthorizationSourceView(
            "as-1", "program-1", AuthorizationSourceState.REVOKED
        )
        decision = evaluate_execution(
            base_request(
                authorization_source=source,
                side_effect_level=SideEffectLevel.LEVEL_0,
                approval=human_approval(),
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.AUTHORIZATION_INACTIVE)

    def test_budget_exhausted_plus_level_2_is_deny(self) -> None:
        issued = issued_budget()
        usage = BudgetUsage(issued.max_requests, 0, 0, 0)
        decision = evaluate_execution(
            base_request(
                budget_usage=usage,
                side_effect_level=SideEffectLevel.LEVEL_0,
                approval=None,
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
