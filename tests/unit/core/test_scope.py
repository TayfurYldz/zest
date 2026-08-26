from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    ExecutionDecisionKind,
    ReasonCode,
    ScopeEvaluationInput,
    ScopeRuleEffect,
    ScopeRuleMatch,
    evaluate_execution,
)
from fixtures import base_request


def _scope(*matches: ScopeRuleMatch, ambiguous: bool = False) -> ScopeEvaluationInput:
    return ScopeEvaluationInput(matches=matches, ambiguous=ambiguous)


class ScopeTests(unittest.TestCase):
    def test_explicit_allow_is_candidate_allow(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, True, "src")
                )
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.ALLOW)
        self.assertIn("r-allow", decision.matched_scope_rule_ids)

    def test_no_explicit_allow_denies(self) -> None:
        decision = evaluate_execution(
            base_request(scope=_scope(ambiguous=False))
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_unmatched_allow_does_not_count(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, False, "src")
                )
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_explicit_deny_beats_allow(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, True, "src"),
                    ScopeRuleMatch("r-deny", ScopeRuleEffect.DENY, True, "src"),
                )
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_out_of_scope_beats_allow(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, True, "src"),
                    ScopeRuleMatch("r-oos", ScopeRuleEffect.OUT_OF_SCOPE, True, "src"),
                )
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_ambiguous_requires_human_review(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, True, "src"),
                    ambiguous=True,
                )
            )
        )
        self.assertEqual(
            decision.decision, ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_AMBIGUOUS)

    def test_ambiguous_without_allow_requires_human_review(self) -> None:
        decision = evaluate_execution(base_request(scope=_scope(ambiguous=True)))
        self.assertEqual(
            decision.decision, ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_AMBIGUOUS)

    def test_ambiguous_plus_deny_is_deny(self) -> None:
        decision = evaluate_execution(
            base_request(
                scope=_scope(
                    ScopeRuleMatch("r-allow", ScopeRuleEffect.ALLOW, True, "src"),
                    ScopeRuleMatch("r-deny", ScopeRuleEffect.DENY, True, "src"),
                    ambiguous=True,
                )
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)


if __name__ == "__main__":
    unittest.main()
