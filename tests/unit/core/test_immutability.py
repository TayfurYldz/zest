from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

import pathsetup  # noqa: F401

from zest.core import BudgetUsage, IssuedBudget
from fixtures import issued_budget


class ImmutabilityTests(unittest.TestCase):
    def test_issued_budget_is_frozen(self) -> None:
        budget = issued_budget()
        with self.assertRaises(FrozenInstanceError):
            budget.max_requests = 99  # type: ignore[misc]

    def test_budget_usage_is_frozen(self) -> None:
        usage = BudgetUsage(0, 0, 0, 0)
        with self.assertRaises(FrozenInstanceError):
            usage.requests = 1  # type: ignore[misc]

    def test_new_issued_budget_is_independent(self) -> None:
        first = IssuedBudget("b1", 1, 1, 1, 1)
        second = IssuedBudget("b2", 2, 2, 2, 2)
        self.assertEqual(first.max_requests, 1)
        self.assertEqual(second.max_requests, 2)


if __name__ == "__main__":
    unittest.main()
