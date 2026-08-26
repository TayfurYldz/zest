"""Compare two diagnostic snapshots into a ChangeEvent. Change is not a vulnerability."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.snapshot_views import load_research_snapshot
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, ChangeEventRecord
from zest.research.temporal import ChangeDecision, ChangeOutcome, compare_diagnostic_snapshots


@dataclass(frozen=True)
class CompareDiagnosticSnapshotsCommand:
    research_run_id: str
    baseline_snapshot_id: str
    variant_snapshot_id: str


class CompareDiagnosticSnapshots:
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

    def execute(self, command: CompareDiagnosticSnapshotsCommand) -> ChangeDecision:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            baseline = load_research_snapshot(uow, command.baseline_snapshot_id)
            variant = load_research_snapshot(uow, command.variant_snapshot_id)
            if baseline is None or variant is None:
                uow.commit()
                return ChangeDecision(
                    outcome=ChangeOutcome.REJECTED_MISSING_SOURCE,
                    reason_codes=("SNAPSHOT_NOT_FOUND",),
                    change_event=None,
                )
            views = load_target_observation_views(uow, command.research_run_id)
            decision = compare_diagnostic_snapshots(
                baseline, variant, views, change_event_id=new_opaque_id()
            )
            if decision.outcome is ChangeOutcome.COMPARED and decision.change_event is not None:
                event = decision.change_event
                uow.change_events.insert(
                    ChangeEventRecord(
                        change_event_id=event.change_event_id,
                        research_run_id=event.research_run_id,
                        baseline_snapshot_id=event.baseline_snapshot_id,
                        variant_snapshot_id=event.variant_snapshot_id,
                        category=event.category.value,
                        statement=event.statement,
                        source_refs=event.source_refs,
                        strategy_version=event.strategy_version,
                        created_at=self._clock.now(),
                    )
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="CHANGE_EVENT_RECORDED",
                        subject_type="change_event",
                        subject_id=event.change_event_id,
                        payload={
                            "category": event.category.value,
                            "not_a_vulnerability": True,
                            "not_evidence": True,
                        },
                    )
                )
            uow.commit()
        return decision
