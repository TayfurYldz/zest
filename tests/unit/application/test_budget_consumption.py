from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.budget_consumption import (
    BudgetConsumptionRejected,
    RecordBudgetConsumption,
    RecordBudgetConsumptionCommand,
)
from zest.data.records import BudgetConsumptionRecord
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _command(**overrides) -> RecordBudgetConsumptionCommand:
    values = dict(
        budget_id="budget-1",
        research_run_id="run-1",
        resource_type="REQUEST",
        amount=1,
        unit="count",
        provenance="unit-test",
        request_id="req-1",
    )
    values.update(overrides)
    return RecordBudgetConsumptionCommand(**values)


class BudgetConsumptionTests(unittest.TestCase):
    def test_append_only_and_replay_does_not_double_charge(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        use_case = RecordBudgetConsumption(FakeUnitOfWorkFactory(store), clock=FixedClock())
        first = use_case.execute(_command())
        second = use_case.execute(_command())
        self.assertFalse(first.already_recorded)
        self.assertTrue(second.already_recorded)
        self.assertEqual(len(store.budget_consumptions), 1)
        self.assertEqual(first.usage.requests, 1)
        self.assertEqual(second.usage.requests, 1)

    def test_zero_allowance_blocks(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        issued = store.issued_budgets["budget-1"]
        store.issued_budgets["budget-1"] = type(issued)(
            budget_id=issued.budget_id,
            research_run_id=issued.research_run_id,
            max_requests=0,
            max_tool_calls=issued.max_tool_calls,
            max_runtime_ms=issued.max_runtime_ms,
            max_concurrency=issued.max_concurrency,
            issued_at=issued.issued_at,
        )
        use_case = RecordBudgetConsumption(FakeUnitOfWorkFactory(store), clock=FixedClock())
        with self.assertRaises(BudgetConsumptionRejected):
            use_case.execute(_command())
        self.assertEqual(len(store.budget_consumptions), 0)

    def test_overspend_beyond_issued_allowance(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        use_case = RecordBudgetConsumption(FakeUnitOfWorkFactory(store), clock=FixedClock())
        use_case.execute(_command())
        with self.assertRaises(BudgetConsumptionRejected):
            use_case.execute(_command(request_id="req-2"))
        self.assertEqual(len(store.budget_consumptions), 1)

    def test_insert_within_allowance_is_idempotent_for_same_request(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        record = BudgetConsumptionRecord(
            consumption_id="cons-1",
            budget_id="budget-1",
            research_run_id="run-1",
            resource_type="WORKER_INVOCATION",
            amount=1,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="test",
            request_id="req-dup",
        )
        with factory.open() as uow:
            issued = uow.issued_budgets.get("budget-1")
            uow.budget_consumptions.insert_within_allowance(record, issued)
            uow.budget_consumptions.insert_within_allowance(
                BudgetConsumptionRecord(
                    consumption_id="cons-2",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="WORKER_INVOCATION",
                    amount=1,
                    unit="count",
                    occurred_at=CREATED_AT,
                    provenance="test-replay",
                    request_id="req-dup",
                ),
                issued,
            )
            uow.commit()
        self.assertEqual(len(store.budget_consumptions), 1)

    def test_cost_is_not_invented(self) -> None:
        with self.assertRaises(Exception):
            BudgetConsumptionRecord(
                consumption_id="cons-cost",
                budget_id="budget-1",
                research_run_id="run-1",
                resource_type="COST",
                amount=1,
                unit="count",
                occurred_at=CREATED_AT,
                provenance="test",
                request_id="req-cost",
            )


if __name__ == "__main__":
    unittest.main()
