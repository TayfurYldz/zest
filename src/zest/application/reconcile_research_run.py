"""Bounded reconciliation of durable incomplete execution/orchestration state.

Does not guess external side effects. UNKNOWN_OUTCOME is fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.application.errors import ApplicationError
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.retry_policy import automatic_retry_allowed
from zest.data.records import ExecutionAttemptState


class ReconciliationResolution(Enum):
    SAFE_TO_RETRY = "SAFE_TO_RETRY"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"
    MARK_OPERATIONAL_FAILURE = "MARK_OPERATIONAL_FAILURE"
    NO_ACTION = "NO_ACTION"
    RESUME_EXISTING = "RESUME_EXISTING"
    SAFE_TO_ADVANCE = "SAFE_TO_ADVANCE"
    INTEGRITY_ERROR = "INTEGRITY_ERROR"


@dataclass(frozen=True)
class ReconciliationItem:
    subject_type: str
    subject_id: str
    resolution: ReconciliationResolution
    reason: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "resolution": self.resolution.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReconcileResearchRunCommand:
    research_run_id: str
    stale_running: bool = False


@dataclass(frozen=True)
class ReconcileResearchRunResult:
    items: tuple[ReconciliationItem, ...]


class ReconcileResearchRun:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: ReconcileResearchRunCommand) -> ReconcileResearchRunResult:
        del self._clock
        items: list[ReconciliationItem] = []
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            attempts = uow.execution_attempts.list_for_research_run(command.research_run_id)
            for attempt in attempts:
                if attempt.state == ExecutionAttemptState.AUTHORIZED.value:
                    if automatic_retry_allowed(
                        attempt_state=attempt.state,
                        side_effect_level=attempt.side_effect_level,
                    ):
                        resolution = ReconciliationResolution.SAFE_TO_RETRY
                        reason = "authorized attempt never dispatched; retry policy allows it"
                    elif attempt.side_effect_level == 0:
                        resolution = ReconciliationResolution.SAFE_TO_RETRY
                        reason = "AUTHORIZED and never dispatched; level-0 may be retried after Core re-evaluation"
                    else:
                        resolution = ReconciliationResolution.REQUIRE_HUMAN_REVIEW
                        reason = "AUTHORIZED side-effectful attempt was never dispatched"
                    items.append(
                        ReconciliationItem(
                            "execution_attempt",
                            attempt.attempt_id,
                            resolution,
                            reason,
                        )
                    )
                elif attempt.state == ExecutionAttemptState.DISPATCHING.value:
                    items.append(
                        ReconciliationItem(
                            "execution_attempt",
                            attempt.attempt_id,
                            ReconciliationResolution.UNKNOWN_OUTCOME,
                            "DISPATCHING with unknown external result; do not blindly retry",
                        )
                    )
                elif attempt.state == ExecutionAttemptState.UNKNOWN_OUTCOME.value:
                    items.append(
                        ReconciliationItem(
                            "execution_attempt",
                            attempt.attempt_id,
                            ReconciliationResolution.REQUIRE_HUMAN_REVIEW
                            if attempt.side_effect_level > 0
                            else ReconciliationResolution.UNKNOWN_OUTCOME,
                            "side-effectful UNKNOWN remains fail-closed",
                        )
                    )
            orchestration = uow.research_orchestrations.get(command.research_run_id)
            if orchestration is not None:
                if orchestration.state == "RUNNING":
                    if command.stale_running:
                        items.append(
                            ReconciliationItem(
                                "research_orchestration",
                                command.research_run_id,
                                ReconciliationResolution.MARK_OPERATIONAL_FAILURE,
                                "stale RUNNING checkpoint after process restart",
                            )
                        )
                    else:
                        items.append(
                            ReconciliationItem(
                                "research_orchestration",
                                command.research_run_id,
                                ReconciliationResolution.NO_ACTION,
                                "RUNNING checkpoint is current for an in-process tick",
                            )
                        )
                hypotheses = uow.hypotheses.list_for_research_run(command.research_run_id)
                experiments = uow.experiments.list_for_research_run(command.research_run_id)
                phase = orchestration.current_phase
                if (
                    orchestration.last_hypothesis_id is None
                    and hypotheses
                    and phase in {"CYCLE_READY", "OPPORTUNITY_SELECTED"}
                ):
                    items.append(
                        ReconciliationItem(
                            "hypothesis",
                            hypotheses[-1].hypothesis_id,
                            ReconciliationResolution.INTEGRITY_ERROR,
                            "hypothesis exists without orchestration hypothesis checkpoint; records preserved",
                        )
                    )
                elif orchestration.last_hypothesis_id is not None:
                    items.append(
                        ReconciliationItem(
                            "hypothesis",
                            orchestration.last_hypothesis_id,
                            ReconciliationResolution.RESUME_EXISTING,
                            "resume existing hypothesis from checkpoint",
                        )
                    )
                if (
                    orchestration.last_experiment_id is None
                    and experiments
                    and phase in {"CYCLE_READY", "OPPORTUNITY_SELECTED", "HYPOTHESIS_ADMITTED"}
                ):
                    items.append(
                        ReconciliationItem(
                            "experiment",
                            experiments[-1].experiment_id,
                            ReconciliationResolution.INTEGRITY_ERROR,
                            "experiment exists without orchestration experiment checkpoint; records preserved",
                        )
                    )
                elif orchestration.last_experiment_id is not None:
                    items.append(
                        ReconciliationItem(
                            "experiment",
                            orchestration.last_experiment_id,
                            ReconciliationResolution.RESUME_EXISTING,
                            "resume existing experiment from checkpoint",
                        )
                    )
                if phase == "CYCLE_COMPLETE":
                    items.append(
                        ReconciliationItem(
                            "research_orchestration",
                            command.research_run_id,
                            ReconciliationResolution.SAFE_TO_ADVANCE,
                            "cycle complete; next cycle may begin",
                        )
                    )
            uow.rollback()
        return ReconcileResearchRunResult(items=tuple(items))
