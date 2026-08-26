"""Compare diagnostic observations with explicit changed dimensions. Not Evidence."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.snapshot_views import load_research_snapshot
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, DifferentialObservationRecord
from zest.research.differential import (
    DifferentialCase,
    DifferentialDecision,
    DifferentialDimension,
    DifferentialOutcome,
    compare_diagnostic_differential,
)


@dataclass(frozen=True)
class CompareDiagnosticDifferentialCommand:
    case: DifferentialCase


class CompareDiagnosticDifferential:
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
        self, command: CompareDiagnosticDifferentialCommand
    ) -> DifferentialDecision:
        case = command.case
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(case.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            for observation_id in (
                case.baseline_observation_ids + case.variant_observation_ids
            ):
                observation = uow.observations.get(observation_id)
                if observation is None:
                    continue
                result = uow.worker_results.get(observation.worker_result_id)
                if result is not None and result.research_run_id != case.research_run_id:
                    uow.commit()
                    return DifferentialDecision(
                        outcome=DifferentialOutcome.REJECTED_CROSS_RUN,
                        reason_codes=("CROSS_RUN_SOURCE",),
                        observation=None,
                    )
            views = load_target_observation_views(uow, case.research_run_id)
            snapshots = ()
            if DifferentialDimension.TIME in case.changed_dimensions:
                loaded = []
                for snapshot_id in (case.baseline_snapshot_id, case.variant_snapshot_id):
                    if snapshot_id is None:
                        continue
                    snapshot = load_research_snapshot(uow, snapshot_id)
                    if snapshot is not None:
                        loaded.append(snapshot)
                snapshots = tuple(loaded)
            decision = compare_diagnostic_differential(
                case, views, differential_id=new_opaque_id(), snapshots=snapshots
            )
            if (
                decision.outcome is DifferentialOutcome.COMPARED
                and decision.observation is not None
            ):
                observation = decision.observation
                uow.differential_observations.insert(
                    DifferentialObservationRecord(
                        differential_id=observation.differential_id,
                        research_run_id=observation.research_run_id,
                        case_id=observation.case_id,
                        baseline_observation_ids=observation.baseline_observation_ids,
                        variant_observation_ids=observation.variant_observation_ids,
                        changed_dimensions=tuple(
                            dim.value for dim in observation.changed_dimensions
                        ),
                        common_dimensions=tuple(
                            dim.value for dim in observation.common_dimensions
                        ),
                        observed_differences=dict(observation.observed_differences),
                        observed_similarities=dict(observation.observed_similarities),
                        interpretation=observation.interpretation.value,
                        source_refs=observation.source_refs,
                        strategy_version=observation.strategy_version,
                        alternative_explanation_slots=observation.alternative_explanation_slots,
                        created_at=self._clock.now(),
                    )
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="DIFFERENTIAL_COMPARED",
                        subject_type="differential_observation",
                        subject_id=observation.differential_id,
                        payload={
                            "interpretation": observation.interpretation.value,
                            "not_a_vulnerability": True,
                            "not_evidence": True,
                        },
                    )
                )
            uow.commit()
        return decision
