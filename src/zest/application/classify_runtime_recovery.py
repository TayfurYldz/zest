"""Classify whether zestd may attach a supervisor after a restart.

Does not treat expired lease or RUNNING as operational failure. Attempt
classification is reused from ReconcileResearchRun. Daemon never retries
DISPATCHING or UNKNOWN_OUTCOME. This is operational recovery, not research
truth and not a Finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.application.errors import ApplicationError
from zest.application.model_usage_capacity_recovery import (
    model_usage_capacity_recovery_proof,
)
from zest.application.ports import UnitOfWorkFactory
from zest.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationItem,
)
from zest.data.records import ExecutionAttemptState
from zest.research.orchestration import (
    TERMINAL_ORCHESTRATION_STATES,
    OrchestrationState,
)

_NON_RESUMABLE_STATES = frozenset(
    {
        OrchestrationState.PAUSED.value,
        OrchestrationState.WAITING_HUMAN.value,
        OrchestrationState.BLOCKED.value,
        *TERMINAL_ORCHESTRATION_STATES,
    }
)


class RuntimeRecoveryAction(Enum):
    SAFE_RESUME = "SAFE_RESUME"
    SAFE_RETRY_AFTER_REAUTHORIZATION = "SAFE_RETRY_AFTER_REAUTHORIZATION"
    SAFE_RETRY_AFTER_MODEL_CAPACITY = "SAFE_RETRY_AFTER_MODEL_CAPACITY"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    DO_NOT_RESUME = "DO_NOT_RESUME"


@dataclass(frozen=True)
class RuntimeRecoveryDecision:
    research_run_id: str
    action: RuntimeRecoveryAction
    reason: str
    items: tuple[ReconciliationItem, ...]


class ClassifyRuntimeRecovery:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory
        self._reconcile = ReconcileResearchRun(uow_factory)

    def execute(self, research_run_id: str) -> RuntimeRecoveryDecision:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(research_run_id)
            if run is None:
                uow.rollback()
                raise ApplicationError("research run not found")
            orchestration = uow.research_orchestrations.get(research_run_id)
            attempts = uow.execution_attempts.list_for_research_run(research_run_id)
            admissions = uow.research_admissions.list_for_research_run(
                research_run_id
            )
            uow.rollback()
        if orchestration is None:
            return RuntimeRecoveryDecision(
                research_run_id,
                RuntimeRecoveryAction.DO_NOT_RESUME,
                "no orchestration checkpoint",
                (),
            )
        usage_capacity_proof = (
            model_usage_capacity_recovery_proof(
                orchestration,
                admissions,
                attempts,
            )
        )

        if usage_capacity_proof is not None:
            return RuntimeRecoveryDecision(
                research_run_id,
                RuntimeRecoveryAction.SAFE_RETRY_AFTER_MODEL_CAPACITY,
                (
                    "exact completed MODEL_USAGE_LIMITED block "
                    "requires independent model-capacity requalification; "
                    "admission="
                    + usage_capacity_proof.admission_record_id
                ),
                (),
            )

        if orchestration.state in _NON_RESUMABLE_STATES:
            return RuntimeRecoveryDecision(
                research_run_id,
                RuntimeRecoveryAction.DO_NOT_RESUME,
                f"orchestration state {orchestration.state} is not auto-resumable",
                (),
            )
        if orchestration.state not in {
            OrchestrationState.READY.value,
            OrchestrationState.RUNNING.value,
        }:
            return RuntimeRecoveryDecision(
                research_run_id,
                RuntimeRecoveryAction.DO_NOT_RESUME,
                f"orchestration state {orchestration.state} is not recoverable",
                (),
            )
        outcome = self._reconcile.execute(
            ReconcileResearchRunCommand(research_run_id=research_run_id, stale_running=False)
        )
        items = outcome.items
        for attempt in attempts:
            if attempt.state == ExecutionAttemptState.DISPATCHING.value:
                return RuntimeRecoveryDecision(
                    research_run_id,
                    RuntimeRecoveryAction.RECONCILIATION_REQUIRED,
                    "DISPATCHING with unknown external result; do not blindly retry",
                    items,
                )
            if attempt.state == ExecutionAttemptState.UNKNOWN_OUTCOME.value:
                if attempt.side_effect_level > 0:
                    return RuntimeRecoveryDecision(
                        research_run_id,
                        RuntimeRecoveryAction.HUMAN_REQUIRED,
                        "side-effectful UNKNOWN_OUTCOME remains fail-closed",
                        items,
                    )
                return RuntimeRecoveryDecision(
                    research_run_id,
                    RuntimeRecoveryAction.RECONCILIATION_REQUIRED,
                    "UNKNOWN_OUTCOME is not proof the request did not happen",
                    items,
                )
            if attempt.state == ExecutionAttemptState.AUTHORIZED.value:
                return RuntimeRecoveryDecision(
                    research_run_id,
                    RuntimeRecoveryAction.SAFE_RETRY_AFTER_REAUTHORIZATION,
                    "AUTHORIZED attempt never dispatched; Core must re-authorize before dispatch",
                    items,
                )
        return RuntimeRecoveryDecision(
            research_run_id,
            RuntimeRecoveryAction.SAFE_RESUME,
            "durable checkpoint may resume; Worker is not replayed from RAM",
            items,
        )
