"""Append-only budget consumption. IssuedBudget remains the immutable envelope."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.budget import BudgetUsage
from zest.data.budget_ledger import usage_from_consumptions
from zest.data.errors import BudgetOverspendError, PersistenceConflictError
from zest.data.records import BudgetConsumptionRecord


class BudgetConsumptionRejected(ApplicationError):
    """Consumption would exceed IssuedBudget. Not a research conclusion."""


@dataclass(frozen=True)
class RecordBudgetConsumptionCommand:
    budget_id: str
    research_run_id: str
    resource_type: str
    amount: int
    unit: str
    provenance: str
    experiment_id: str | None = None
    request_id: str | None = None
    resource_metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RecordBudgetConsumptionResult:
    consumption_id: str | None
    already_recorded: bool
    usage: BudgetUsage


class RecordBudgetConsumption:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self, command: RecordBudgetConsumptionCommand
    ) -> RecordBudgetConsumptionResult:
        consumption_id = new_opaque_id()
        with self._uow_factory.open() as uow:
            issued = uow.issued_budgets.get(command.budget_id)
            if issued is None:
                raise ApplicationError("issued budget not found")
            if command.research_run_id is not None and issued.research_run_id != command.research_run_id:
                raise ApplicationError("issued budget not found for research run")
            record = BudgetConsumptionRecord(
                consumption_id=consumption_id,
                budget_id=command.budget_id,
                research_run_id=command.research_run_id,
                resource_type=command.resource_type,
                amount=command.amount,
                unit=command.unit,
                occurred_at=self._clock.now(),
                provenance=command.provenance,
                experiment_id=command.experiment_id,
                request_id=command.request_id,
                resource_metadata=command.resource_metadata,
            )
            before = uow.budget_consumptions.list_for_budget(command.budget_id)
            duplicate = any(
                item.request_id == command.request_id
                and item.resource_type == command.resource_type
                and command.request_id is not None
                for item in before
            )
            if duplicate:
                uow.rollback()
                after = before
            elif command.resource_type in {"MODEL_TOKENS_IN", "MODEL_TOKENS_OUT", "MODEL_ESCALATION_DECISION"}:
                # Token/escalation records are tracked against the program daily
                # budget; allowance was already checked before the model call.
                uow.budget_consumptions.insert(record)
                after = uow.budget_consumptions.list_for_budget(command.budget_id)
                uow.commit()
            else:
                try:
                    uow.budget_consumptions.insert_within_allowance(record, issued)
                except BudgetOverspendError as exc:
                    uow.rollback()
                    raise BudgetConsumptionRejected(str(exc)) from exc
                except PersistenceConflictError:
                    duplicate = True
                after = uow.budget_consumptions.list_for_budget(command.budget_id)
                uow.commit()
        return RecordBudgetConsumptionResult(
            consumption_id=None if duplicate else consumption_id,
            already_recorded=duplicate,
            usage=usage_from_consumptions(after),
        )


def load_budget_usage(uow_factory: UnitOfWorkFactory, budget_id: str) -> BudgetUsage:
    with uow_factory.open() as uow:
        records = uow.budget_consumptions.list_for_budget(budget_id)
        uow.rollback()
    return usage_from_consumptions(records)
