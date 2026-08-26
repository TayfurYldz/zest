"""SD-G4 program daily budget tests."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.program_daily_budget import (
    AllocateProgramDailyBudget,
    AllocateProgramDailyBudgetCommand,
    CheckProgramDailyBudget,
    ProgramDailyBudgetUsage,
    program_daily_budget_id,
)
from zest.data.records import (
    BudgetConsumptionRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
)
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT


TODAY = CREATED_AT.date().isoformat()


def _seed_policy(store: _Store, limit: int | None) -> None:
    store.program_policies["prog-1"] = ProgramPolicyRecord(
        program_id="prog-1",
        loopback_fixture=True,
        max_response_bytes=4096,
        timeout_ms=2000,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        daily_llm_budget_microdollars=limit,
    )


class AllocateProgramDailyBudgetTests(unittest.TestCase):
    def test_creates_daily_envelope(self) -> None:
        store = _Store()
        _seed_policy(store, 1_000_000)
        AllocateProgramDailyBudget(FakeUnitOfWorkFactory(store=store), clock=_FixedClock()).execute(
            AllocateProgramDailyBudgetCommand(
                program_id="prog-1", budget_date=TODAY, limit_microdollars=1_000_000
            )
        )
        budget_id = program_daily_budget_id("prog-1", TODAY)
        self.assertIn(budget_id, store.issued_budgets)
        envelope = store.issued_budgets[budget_id]
        self.assertIsNone(envelope.research_run_id)
        self.assertEqual(envelope.max_requests, 0)

    def test_missing_program_fails(self) -> None:
        store = _Store()
        with self.assertRaises(Exception):
            AllocateProgramDailyBudget(FakeUnitOfWorkFactory(store=store), clock=_FixedClock()).execute(
                AllocateProgramDailyBudgetCommand(
                    program_id="prog-1", budget_date=TODAY, limit_microdollars=1_000_000
                )
            )

    def test_allocation_is_idempotent(self) -> None:
        store = _Store()
        _seed_policy(store, 1_000_000)
        allocator = AllocateProgramDailyBudget(
            FakeUnitOfWorkFactory(store=store), clock=_FixedClock()
        )
        command = AllocateProgramDailyBudgetCommand(
            program_id="prog-1", budget_date=TODAY, limit_microdollars=1_000_000
        )

        first = allocator.execute(command)
        second = allocator.execute(command)

        self.assertEqual(first.budget_id, second.budget_id)
        self.assertEqual(
            list(store.issued_budgets).count(program_daily_budget_id("prog-1", TODAY)),
            1,
        )


class ProgramDailyBudgetUsageTests(unittest.TestCase):
    def test_no_allocation_returns_zero_spent(self) -> None:
        store = _Store()
        _seed_policy(store, 1_000_000)
        usage = ProgramDailyBudgetUsage(FakeUnitOfWorkFactory(store=store))
        view = usage.execute("prog-1", TODAY)
        self.assertEqual(view.spent_microdollars, 0)
        self.assertEqual(view.remaining_microdollars, 1_000_000)

    def test_token_records_sum_to_cost(self) -> None:
        store = _Store()
        _seed_policy(store, 1_000_000)
        budget_id = program_daily_budget_id("prog-1", TODAY)
        store.issued_budgets[budget_id] = IssuedBudgetRecord(
            budget_id=budget_id,
            research_run_id=None,
            max_requests=0,
            max_tool_calls=0,
            max_runtime_ms=0,
            max_concurrency=0,
            issued_at=CREATED_AT,
        )
        store.budget_consumptions["c1"] = BudgetConsumptionRecord(
            consumption_id="c1",
            budget_id=budget_id,
            research_run_id=None,
            resource_type="MODEL_TOKENS_IN",
            amount=1_000_000,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="test",
            request_id="r1",
            resource_metadata={"model_id": "local-fixture"},
        )
        usage = ProgramDailyBudgetUsage(FakeUnitOfWorkFactory(store=store))
        view = usage.execute("prog-1", TODAY)
        self.assertEqual(view.tokens_in, 1_000_000)
        self.assertEqual(view.spent_microdollars, 0)

    def test_unknown_model_counts_as_exhausted(self) -> None:
        store = _Store()
        _seed_policy(store, 1_000_000)
        budget_id = program_daily_budget_id("prog-1", TODAY)
        store.issued_budgets[budget_id] = IssuedBudgetRecord(
            budget_id=budget_id,
            research_run_id=None,
            max_requests=0,
            max_tool_calls=0,
            max_runtime_ms=0,
            max_concurrency=0,
            issued_at=CREATED_AT,
        )
        store.budget_consumptions["c1"] = BudgetConsumptionRecord(
            consumption_id="c1",
            budget_id=budget_id,
            research_run_id=None,
            resource_type="MODEL_TOKENS_IN",
            amount=10,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="test",
            request_id="r1",
            resource_metadata={"model_id": "unknown-model"},
        )
        usage = ProgramDailyBudgetUsage(FakeUnitOfWorkFactory(store=store))
        view = usage.execute("prog-1", TODAY)
        self.assertEqual(view.remaining_microdollars, 0)


class CheckProgramDailyBudgetTests(unittest.TestCase):
    def test_unset_limit_denies(self) -> None:
        store = _Store()
        _seed_policy(store, None)
        check = CheckProgramDailyBudget(FakeUnitOfWorkFactory(store=store))
        result = check.execute("prog-1")
        self.assertFalse(result.allowed_to_continue)

    def test_limit_reached_denies(self) -> None:
        store = _Store()
        _seed_policy(store, 100)
        budget_id = program_daily_budget_id("prog-1", TODAY)
        store.issued_budgets[budget_id] = IssuedBudgetRecord(
            budget_id=budget_id,
            research_run_id=None,
            max_requests=0,
            max_tool_calls=0,
            max_runtime_ms=0,
            max_concurrency=0,
            issued_at=CREATED_AT,
        )
        store.budget_consumptions["c1"] = BudgetConsumptionRecord(
            consumption_id="c1",
            budget_id=budget_id,
            research_run_id=None,
            resource_type="MODEL_TOKENS_IN",
            amount=1,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="test",
            request_id="r1",
            resource_metadata={"model_id": "gpt-4o-mini"},
        )
        check = CheckProgramDailyBudget(FakeUnitOfWorkFactory(store=store))
        result = check.execute("prog-1")
        self.assertFalse(result.allowed_to_continue)


class _FixedClock:
    def now(self):
        return CREATED_AT


if __name__ == "__main__":
    unittest.main()
