"""Persist a planned Experiment and its immutable ExperimentPlan specification.

Does not authorize, dispatch, assess, or create Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from research_os.application.capability_binding import require_new_plan_bindings
from research_os.application.errors import ApplicationError
from research_os.application.plan_records import experiment_plan_record_for
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.data.errors import PersistenceConflictError
from research_os.data.records import ExperimentExecutionState, ExperimentRecord
from research_os.data.unit_of_work import UnitOfWork
from research_os.research.types import ExperimentPlan


@dataclass(frozen=True)
class PreparePlannedExperimentCommand:
    experiment_id: str
    research_run_id: str
    plan: ExperimentPlan


@dataclass(frozen=True)
class PreparePlannedExperimentResult:
    experiment_id: str
    hypothesis_id: str
    evaluation_strategy: str


class PreparePlannedExperiment:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self,
        command: PreparePlannedExperimentCommand,
        *,
        unit_of_work: UnitOfWork | None = None,
    ) -> PreparePlannedExperimentResult:
        now = self._clock.now()
        plan = command.plan
        require_new_plan_bindings(plan)

        def _write(uow: UnitOfWork) -> PreparePlannedExperimentResult:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            hypothesis = uow.hypotheses.get(plan.hypothesis_id)
            if hypothesis is None or hypothesis.research_run_id != command.research_run_id:
                raise ApplicationError("hypothesis not found for research run")
            budget = uow.issued_budgets.get(plan.requested_budget_id)
            if budget is None or budget.research_run_id != command.research_run_id:
                raise ApplicationError("issued budget not found for research run")
            existing = uow.experiments.get(command.experiment_id)
            if existing is None:
                experiment = ExperimentRecord(
                    experiment_id=command.experiment_id,
                    research_run_id=command.research_run_id,
                    hypothesis_id=plan.hypothesis_id,
                    budget_id=plan.requested_budget_id,
                    execution_state=ExperimentExecutionState.PLANNED.value,
                    created_at=now,
                )
                uow.experiments.insert(experiment)
                uow.experiment_plans.insert(
                    experiment_plan_record_for(experiment, plan, created_at=now)
                )
            return PreparePlannedExperimentResult(
                experiment_id=command.experiment_id,
                hypothesis_id=plan.hypothesis_id,
                evaluation_strategy=plan.evaluation_strategy,
            )

        if unit_of_work is None:
            try:
                with self._uow_factory.open() as uow:
                    result = _write(uow)
                    uow.commit()
                return result
            except PersistenceConflictError:
                with self._uow_factory.open() as uow:
                    existing = uow.experiments.get(command.experiment_id)
                    plan_row = uow.experiment_plans.get(command.experiment_id)
                    uow.rollback()
                if existing is None or plan_row is None:
                    raise
                return PreparePlannedExperimentResult(
                    experiment_id=command.experiment_id,
                    hypothesis_id=existing.hypothesis_id,
                    evaluation_strategy=plan.evaluation_strategy,
                )
        return _write(unit_of_work)
