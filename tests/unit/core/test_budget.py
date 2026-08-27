from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    BudgetAllocationError,
    BudgetUsage,
    ExecutionDecisionKind,
    InvalidBudgetError,
    IssuedBudget,
    ReasonCode,
    allocate_experiment_budget,
    evaluate_execution,
)
from core.fixtures import base_request, issued_budget


class BudgetTests(unittest.TestCase):
    def test_zero_request_allowance_denies(self) -> None:
        issued = IssuedBudget("budget-1", 0, 10, 60_000, 2)
        decision = evaluate_execution(
            base_request(issued_budget=issued, requested_budget_id="budget-1")
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_EXHAUSTED)

    def test_zero_runtime_denies(self) -> None:
        issued = IssuedBudget("budget-1", 10, 10, 0, 2)
        decision = evaluate_execution(
            base_request(issued_budget=issued, requested_budget_id="budget-1")
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_EXHAUSTED)

    def test_zero_concurrency_denies(self) -> None:
        issued = IssuedBudget("budget-1", 10, 10, 60_000, 0)
        decision = evaluate_execution(
            base_request(issued_budget=issued, requested_budget_id="budget-1")
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_EXHAUSTED)

    def test_negative_budget_construction_rejected(self) -> None:
        with self.assertRaises(InvalidBudgetError):
            IssuedBudget("budget-1", -1, 10, 60_000, 2)

    def test_negative_usage_construction_rejected(self) -> None:
        with self.assertRaises(InvalidBudgetError):
            BudgetUsage(-1, 0, 0, 0)

    def test_experiment_allocation_cannot_exceed_parent(self) -> None:
        parent = issued_budget()
        with self.assertRaises(BudgetAllocationError):
            allocate_experiment_budget(
                parent,
                "exp-budget",
                max_requests=parent.max_requests + 1,
                max_tool_calls=1,
                max_runtime_ms=1,
                max_concurrency=1,
            )

    def test_experiment_allocation_within_parent_ok(self) -> None:
        parent = issued_budget()
        child = allocate_experiment_budget(
            parent, "exp-budget", 1, 1, 1, 1
        )
        self.assertEqual(child.budget_id, "exp-budget")
        self.assertEqual(child.max_requests, 1)

    def test_mismatched_budget_id_denies(self) -> None:
        decision = evaluate_execution(base_request(requested_budget_id="other"))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_MISMATCH)

    def test_exhausted_usage_denies(self) -> None:
        issued = issued_budget()
        usage = BudgetUsage(
            requests=issued.max_requests,
            tool_calls=0,
            runtime_ms=0,
            concurrency=0,
        )
        decision = evaluate_execution(base_request(budget_usage=usage))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.BUDGET_EXHAUSTED)


if __name__ == "__main__":
    unittest.main()
