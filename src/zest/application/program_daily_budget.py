"""Program daily LLM budget allocation and read-only usage views.

The ledger is the single source of truth; no mutable counter table is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.budget import (
    BudgetCheck,
    ProgramDailyBudget,
    check_program_daily_budget,
)
from zest.core.enums import ReasonCode
from zest.core.pricing import UnknownModelPriceError, estimate_cost
from zest.data.records import BudgetConsumptionRecord, IssuedBudgetRecord


def program_daily_budget_id(program_id: str, budget_date: str) -> str:
    return f"program-daily:{program_id}:{budget_date}"


@dataclass(frozen=True)
class AllocateProgramDailyBudgetCommand:
    program_id: str
    budget_date: str
    limit_microdollars: int


@dataclass(frozen=True)
class AllocateProgramDailyBudgetResult:
    budget_id: str
    limit_microdollars: int


class AllocateProgramDailyBudget:
    """Create or replace the immutable daily cost envelope for a program."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: AllocateProgramDailyBudgetCommand) -> AllocateProgramDailyBudgetResult:
        budget_id = program_daily_budget_id(command.program_id, command.budget_date)
        with self._uow_factory.open() as uow:
            policy = uow.program_policies.get(command.program_id)
            if policy is None:
                raise ApplicationError("program not found")
            existing = uow.issued_budgets.get(budget_id)
            if existing is not None:
                uow.rollback()
                return AllocateProgramDailyBudgetResult(
                    budget_id=budget_id,
                    limit_microdollars=command.limit_microdollars,
                )
            record = IssuedBudgetRecord(
                budget_id=budget_id,
                research_run_id=None,
                max_requests=0,
                max_tool_calls=0,
                max_runtime_ms=0,
                max_concurrency=0,
                issued_at=self._clock.now(),
            )
            uow.issued_budgets.insert(record)
            uow.commit()
        return AllocateProgramDailyBudgetResult(
            budget_id=budget_id,
            limit_microdollars=command.limit_microdollars,
        )


@dataclass(frozen=True)
class ProgramDailyBudgetCall:
    request_id: str | None
    resource_type: str
    model_id: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class ProgramDailyBudgetView:
    program_id: str
    budget_date: str
    limit_microdollars: int
    spent_microdollars: int
    remaining_microdollars: int
    tokens_in: int
    tokens_out: int
    model_call_count: int
    last_calls: tuple[ProgramDailyBudgetCall, ...]


class ProgramDailyBudgetUsage:
    """Read-only view of today's LLM spending for a program."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, program_id: str, budget_date: str | None = None) -> ProgramDailyBudgetView:
        target_date = budget_date or datetime.now(timezone.utc).date().isoformat()
        budget_id = program_daily_budget_id(program_id, target_date)
        day_start = datetime.combine(
            date.fromisoformat(target_date), time.min, tzinfo=timezone.utc
        )
        day_end = datetime.combine(
            date.fromisoformat(target_date), time.max, tzinfo=timezone.utc
        )
        with self._uow_factory.open() as uow:
            policy = uow.program_policies.get(program_id)
            if policy is None:
                raise ApplicationError("program not found")
            limit = policy.daily_llm_budget_microdollars
            if limit is None:
                limit = 0
            envelope = uow.issued_budgets.get(budget_id)
            if envelope is None:
                # No allocation yet means zero spent.
                return ProgramDailyBudgetView(
                    program_id=program_id,
                    budget_date=target_date,
                    limit_microdollars=limit,
                    spent_microdollars=0,
                    remaining_microdollars=limit,
                    tokens_in=0,
                    tokens_out=0,
                    model_call_count=0,
                    last_calls=(),
                )
            records = uow.budget_consumptions.list_for_budget(budget_id)
            uow.rollback()

        day_records = [
            item for item in records if day_start <= item.occurred_at <= day_end
        ]
        spent = _sum_estimated_cost(day_records)
        tokens_in = _sum_tokens(day_records, "MODEL_TOKENS_IN")
        tokens_out = _sum_tokens(day_records, "MODEL_TOKENS_OUT")
        model_call_count = sum(
            item.amount for item in day_records if item.resource_type == "MODEL_CALL"
        )
        last_calls = tuple(
            ProgramDailyBudgetCall(
                request_id=item.request_id,
                resource_type=item.resource_type,
                model_id=(item.resource_metadata or {}).get("model_id"),
                occurred_at=item.occurred_at,
            )
            for item in sorted(day_records, key=lambda r: r.occurred_at, reverse=True)[:10]
        )
        check = check_program_daily_budget(
            ProgramDailyBudget(
                program_id=program_id,
                date=target_date,
                limit_microdollars=limit,
                spent_microdollars=spent,
            ),
            program_id,
        )
        remaining = limit - spent if check.allowed_to_continue else 0
        return ProgramDailyBudgetView(
            program_id=program_id,
            budget_date=target_date,
            limit_microdollars=limit,
            spent_microdollars=spent,
            remaining_microdollars=max(remaining, 0),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_call_count=model_call_count,
            last_calls=last_calls,
        )


def _sum_tokens(records: list[BudgetConsumptionRecord], resource_type: str) -> int:
    return sum(
        item.amount
        for item in records
        if item.resource_type == resource_type
    )


def _sum_estimated_cost(records: list[BudgetConsumptionRecord]) -> int:
    """Sum estimated cost from paired MODEL_TOKENS_IN/OUT records.

    Pairs are matched by request_id.  If only one side exists, the missing side is 0.
    """
    by_request: dict[str | None, dict[str, int]] = {}
    for item in records:
        if item.resource_type not in {"MODEL_TOKENS_IN", "MODEL_TOKENS_OUT"}:
            continue
        bucket = by_request.setdefault(item.request_id, {"in": 0, "out": 0, "model_id": None})
        if item.resource_type == "MODEL_TOKENS_IN":
            bucket["in"] += item.amount
        else:
            bucket["out"] += item.amount
        if bucket["model_id"] is None and item.resource_metadata is not None:
            bucket["model_id"] = item.resource_metadata.get("model_id")

    total = 0
    for bucket in by_request.values():
        model_id = bucket["model_id"]
        try:
            total += estimate_cost(model_id, bucket["in"], bucket["out"])
        except UnknownModelPriceError:
            # Fail-closed: unknown model_id spending is counted as the full limit.
            total += 2**63 - 1
    return total


class CheckProgramDailyBudget:
    """Fail-closed check before a model call."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, program_id: str, estimated_cost_microdollars: int = 0) -> BudgetCheck:
        today = self._clock.now().date().isoformat()
        budget_id = program_daily_budget_id(program_id, today)
        with self._uow_factory.open() as uow:
            policy = uow.program_policies.get(program_id)
            if policy is None:
                # No program policy means the daily budget gate is not configured;
                # preserve legacy behavior and do not block the call.
                uow.rollback()
                return BudgetCheck(
                    allowed_to_continue=True,
                    reason_code=ReasonCode.ALLOWED,
                    budget_id=budget_id,
                )
            limit = policy.daily_llm_budget_microdollars
            if limit is None:
                uow.rollback()
                # Operator has not set a daily limit; live model calls are denied.
                return BudgetCheck(
                    allowed_to_continue=False,
                    reason_code=ReasonCode.BUDGET_EXHAUSTED,
                    budget_id=budget_id,
                )
            envelope = uow.issued_budgets.get(budget_id)
            if envelope is None:
                uow.rollback()
                return BudgetCheck(
                    allowed_to_continue=False,
                    reason_code=ReasonCode.BUDGET_EXHAUSTED,
                    budget_id=budget_id,
                )
            records = uow.budget_consumptions.list_for_budget(budget_id)
            uow.rollback()
        spent = _sum_estimated_cost(records) + estimated_cost_microdollars
        return check_program_daily_budget(
            ProgramDailyBudget(
                program_id=program_id,
                date=today,
                limit_microdollars=limit,
                spent_microdollars=spent,
            ),
            program_id,
        )
