"""Fail-closed recovery for completed MODEL_USAGE_LIMITED blocks.

This module does not probe a provider and grants no execution authority.
It only classifies durable provenance and performs the narrow BLOCKED->READY
state transition after an external runtime owner has independently
re-qualified model capacity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import (
    Clock,
    SystemClock,
    UnitOfWorkFactory,
)
from zest.core.enums import ActorType
from zest.data.records import (
    AuditEventRecord,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ResearchAdmissionRecord,
    ResearchOrchestrationRecord,
)
from zest.research.admission import AdmissionOutcome
from zest.research.orchestration import (
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)


@dataclass(frozen=True)
class ModelUsageCapacityRecoveryProof:
    admission_record_id: str


@dataclass(frozen=True)
class ModelUsageCapacityRecoveryResult:
    research_run_id: str
    recovered: bool
    admission_record_id: str | None


_UNSAFE_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptState.AUTHORIZED.value,
        ExecutionAttemptState.DISPATCHING.value,
        ExecutionAttemptState.UNKNOWN_OUTCOME.value,
    }
)


def model_usage_capacity_recovery_proof(
    orchestration: ResearchOrchestrationRecord,
    admissions: list[ResearchAdmissionRecord],
    attempts: list[ExecutionAttemptRecord],
) -> ModelUsageCapacityRecoveryProof | None:
    """Return proof only for one exact, completed usage-limit block.

    Admission repository ordering is intentionally ignored. created_at is
    the chronology source. Ambiguous latest timestamps fail closed.
    """

    if (
        orchestration.state
        != OrchestrationState.BLOCKED.value
        or orchestration.stop_reason
        != StopReason.RATE_LIMITED.value
        or orchestration.current_phase
        != OrchestrationPhase.CYCLE_COMPLETE.value
        or orchestration.active_cycle_id is not None
        or orchestration.last_phase
        != "model_runtime_outcome"
    ):
        return None

    if any(
        attempt.state
        in _UNSAFE_ATTEMPT_STATES
        for attempt in attempts
    ):
        return None

    if not admissions:
        return None

    # Anything newer than the checkpoint means our orchestration snapshot
    # is not a complete causal view. Do not guess.
    if any(
        item.created_at
        > orchestration.checkpoint_at
        for item in admissions
    ):
        return None

    latest_at = max(
        item.created_at
        for item in admissions
    )

    latest = [
        item
        for item in admissions
        if item.created_at == latest_at
    ]

    # Opaque admission ids are identity, not chronology.
    if len(latest) != 1:
        return None

    admission = latest[0]

    if (
        admission.outcome
        != AdmissionOutcome.MODEL_INVOCATION_FAILED.value
        or admission.reason_code
        != "MODEL_USAGE_LIMITED"
    ):
        return None

    return ModelUsageCapacityRecoveryProof(
        admission_record_id=(
            admission.admission_record_id
        )
    )


class RecoverModelUsageCapacity:
    """Perform the durable recovery transition after live capacity proof.

    The caller is responsible for provider/live-model requalification.
    This use case deliberately contains no provider I/O.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(
        self,
        research_run_id: str,
    ) -> ModelUsageCapacityRecoveryResult:
        now = self._clock.now()

        with self._uow_factory.open() as uow:
            current = (
                uow.research_orchestrations.get(
                    research_run_id
                )
            )

            if current is None:
                uow.rollback()
                raise ApplicationError(
                    "orchestration not found"
                )

            admissions = (
                uow.research_admissions
                .list_for_research_run(
                    research_run_id
                )
            )

            attempts = (
                uow.execution_attempts
                .list_for_research_run(
                    research_run_id
                )
            )

            proof = (
                model_usage_capacity_recovery_proof(
                    current,
                    admissions,
                    attempts,
                )
            )

            if proof is None:
                uow.rollback()

                return (
                    ModelUsageCapacityRecoveryResult(
                        research_run_id=(
                            research_run_id
                        ),
                        recovered=False,
                        admission_record_id=None,
                    )
                )

            updated = replace(
                current,
                state=OrchestrationState.READY.value,
                stop_reason=None,
                pause_reason=None,
                last_phase=(
                    "model_usage_capacity_recovered"
                ),
                current_phase=(
                    OrchestrationPhase
                    .CYCLE_COMPLETE.value
                ),
                active_cycle_id=None,
                updated_at=now,
                checkpoint_at=now,
            )

            # This is intentionally UNFENCED. A live or foreign lease must
            # reject the write. Only an unowned/expired BLOCKED row may move.
            uow.research_orchestrations.save(
                updated,
                require_unowned_or_expired=True,
            )

            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=(
                        ActorType.CONTROL_PLANE.value
                    ),
                    event_type=(
                        "MODEL_USAGE_CAPACITY_RECOVERED"
                    ),
                    subject_type="research_run",
                    subject_id=research_run_id,
                    payload={
                        "admission_record_id": (
                            proof.admission_record_id
                        ),
                        "previous_state": (
                            current.state
                        ),
                        "previous_stop_reason": (
                            current.stop_reason
                        ),
                        "cycle_number": (
                            current.cycle_number
                        ),
                        "cycle_incremented": False,
                        "active_cycle_restored": False,
                        "authority_expanded": False,
                        "not_authorization": True,
                        "not_research_truth": True,
                    },
                )
            )

            uow.commit()

        return ModelUsageCapacityRecoveryResult(
            research_run_id=research_run_id,
            recovered=True,
            admission_record_id=(
                proof.admission_record_id
            ),
        )
