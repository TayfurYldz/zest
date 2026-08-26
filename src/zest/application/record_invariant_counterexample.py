"""Record a context-bound invariant counterexample. Does not globally falsify."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, InvariantCounterexampleRefRecord
from zest.research.invariant import (
    InvariantCounterexample,
    InvariantHypothesis,
    InvariantKind,
    InvariantStatus,
    apply_invariant_counterexample,
)


@dataclass(frozen=True)
class RecordInvariantCounterexampleCommand:
    invariant_id: str
    source_ref: str
    applicability_context: dict[str, object]


class RecordInvariantCounterexample:
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

    def execute(self, command: RecordInvariantCounterexampleCommand) -> InvariantHypothesis:
        with self._uow_factory.open() as uow:
            record = uow.invariant_hypotheses.get(command.invariant_id)
            if record is None:
                raise ApplicationError("invariant hypothesis not found")
            observation = uow.observations.get(command.source_ref)
            if observation is None:
                raise ApplicationError("counterexample source not found")
            result = uow.worker_results.get(observation.worker_result_id)
            if result is None or result.research_run_id != record.research_run_id:
                raise ApplicationError("counterexample source is cross-run")
            current = InvariantHypothesis(
                invariant_id=record.invariant_id,
                research_run_id=record.research_run_id,
                invariant_kind=InvariantKind[record.invariant_kind],
                status=InvariantStatus[record.status],
                subject_refs=record.subject_refs,
                expected_behavior=record.expected_behavior,
                source_refs=record.source_refs,
                applicability_context=dict(record.applicability_context),
                assumptions=record.assumptions,
                counterexample_refs=record.counterexample_refs,
                falsification_direction=record.falsification_direction,
                proposer_provenance=record.proposer_provenance,
                strategy_version=record.strategy_version,
            )
            counterexample = InvariantCounterexample(
                counterexample_id=new_opaque_id(),
                invariant_id=command.invariant_id,
                source_ref=command.source_ref,
                applicability_context=dict(command.applicability_context),
            )
            updated = apply_invariant_counterexample(current, counterexample)
            uow.invariant_hypotheses.add_counterexample(
                InvariantCounterexampleRefRecord(
                    counterexample_id=counterexample.counterexample_id,
                    invariant_id=counterexample.invariant_id,
                    source_ref=counterexample.source_ref,
                    applicability_context=dict(counterexample.applicability_context),
                    created_at=self._clock.now(),
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="INVARIANT_COUNTEREXAMPLE_RECORDED",
                    subject_type="invariant_hypothesis",
                    subject_id=updated.invariant_id,
                    payload={
                        "source_ref": command.source_ref,
                        "context_bound": True,
                        "not_globally_false": True,
                    },
                )
            )
            uow.commit()
        return updated
