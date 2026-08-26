"""Reconstruct usage from the append-only consumption ledger.

The ledger is the usage truth. IssuedBudget remains the immutable envelope.
MODEL_CALL is not a Worker REQUEST. COST is not recorded without an issued cost unit.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.core.budget import BudgetUsage
from zest.data.errors import BudgetOverspendError
from zest.data.records import BudgetConsumptionRecord, IssuedBudgetRecord, ResearchOrchestrationRecord


@dataclass(frozen=True)
class LedgerTotals:
    model_calls: int
    worker_requests: int
    worker_invocations: int
    execution_time_ms: int
    artifact_bytes: int

    def to_budget_usage(self) -> BudgetUsage:
        """Core BudgetUsage sees Worker REQUEST/tool/time only. MODEL_CALL is excluded."""

        return BudgetUsage(
            requests=self.worker_requests,
            tool_calls=self.worker_invocations,
            runtime_ms=self.execution_time_ms,
            concurrency=0,
        )


OrchestrationUsage = LedgerTotals


def ledger_totals(records: list[BudgetConsumptionRecord]) -> LedgerTotals:
    model_calls = 0
    worker_requests = 0
    worker_invocations = 0
    execution_time_ms = 0
    artifact_bytes = 0
    for record in records:
        if record.resource_type == "MODEL_CALL":
            model_calls += record.amount
        elif record.resource_type == "REQUEST":
            worker_requests += record.amount
        elif record.resource_type == "WORKER_INVOCATION":
            worker_invocations += record.amount
        elif record.resource_type == "EXECUTION_TIME":
            execution_time_ms += record.amount
        elif record.resource_type == "ARTIFACT_BYTES":
            artifact_bytes += record.amount
    return LedgerTotals(
        model_calls=model_calls,
        worker_requests=worker_requests,
        worker_invocations=worker_invocations,
        execution_time_ms=execution_time_ms,
        artifact_bytes=artifact_bytes,
    )


def usage_from_consumptions(records: list[BudgetConsumptionRecord]) -> BudgetUsage:
    return ledger_totals(records).to_budget_usage()


def remaining_for_resource(issued: IssuedBudgetRecord, usage: BudgetUsage, resource_type: str) -> int:
    if resource_type == "REQUEST":
        return issued.max_requests - usage.requests
    if resource_type == "WORKER_INVOCATION":
        return issued.max_tool_calls - usage.tool_calls
    if resource_type == "EXECUTION_TIME":
        return issued.max_runtime_ms - usage.runtime_ms
    raise BudgetOverspendError(f"no issued allowance for resource_type {resource_type}")


def remaining_model_calls(orchestration: ResearchOrchestrationRecord, totals: LedgerTotals) -> int:
    return orchestration.max_model_calls - totals.model_calls


def assert_within_allowance(
    issued: IssuedBudgetRecord,
    existing: list[BudgetConsumptionRecord],
    incoming: BudgetConsumptionRecord,
    *,
    orchestration: ResearchOrchestrationRecord | None = None,
) -> None:
    totals = ledger_totals(existing)
    if incoming.resource_type == "MODEL_CALL":
        if orchestration is None:
            raise BudgetOverspendError("MODEL_CALL requires locked orchestration allowance")
        if incoming.research_run_id != orchestration.research_run_id:
            raise BudgetOverspendError("MODEL_CALL research_run_id does not match orchestration")
        if incoming.amount > remaining_model_calls(orchestration, totals):
            raise BudgetOverspendError("consumption would exceed issued model-call allowance")
        return
    usage = totals.to_budget_usage()
    remaining = remaining_for_resource(issued, usage, incoming.resource_type)
    if incoming.amount > remaining:
        raise BudgetOverspendError("consumption would exceed issued allowance")
