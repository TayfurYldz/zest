"""Capture a diagnostic snapshot by observation reference. Not a full SoR copy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, SnapshotMemberRecord, SnapshotRecord
from zest.research.temporal import ResearchSnapshot, SnapshotOutcome, capture_diagnostic_snapshot


@dataclass(frozen=True)
class CaptureDiagnosticSnapshotCommand:
    research_run_id: str
    target_identity: str
    observation_ids: tuple[str, ...]
    snapshot_id: str | None = None
    captured_at: datetime | None = None


class CaptureDiagnosticSnapshot:
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
        self, command: CaptureDiagnosticSnapshotCommand
    ) -> tuple[SnapshotOutcome, ResearchSnapshot | None]:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            views = load_target_observation_views(uow, command.research_run_id)
            captured_at = command.captured_at or self._clock.now()
            outcome, snapshot, _codes = capture_diagnostic_snapshot(
                snapshot_id=command.snapshot_id or new_opaque_id(),
                research_run_id=command.research_run_id,
                program_id=run.program_id,
                target_identity=command.target_identity,
                observation_ids=command.observation_ids,
                captured_at=captured_at,
                views=views,
            )
            if outcome is SnapshotOutcome.CAPTURED and snapshot is not None:
                uow.snapshots.insert(
                    SnapshotRecord(
                        snapshot_id=snapshot.snapshot_id,
                        research_run_id=snapshot.research_run_id,
                        program_id=snapshot.program_id,
                        target_identity=snapshot.target_identity,
                        captured_at=snapshot.captured_at,
                        strategy_version=snapshot.strategy_version,
                        created_at=captured_at,
                    ),
                    tuple(
                        SnapshotMemberRecord(
                            snapshot_id=snapshot.snapshot_id,
                            observation_id=observation_id,
                            created_at=captured_at,
                        )
                        for observation_id in snapshot.observation_ids
                    ),
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=captured_at,
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="SNAPSHOT_CAPTURED",
                        subject_type="snapshot",
                        subject_id=snapshot.snapshot_id,
                        payload={"not_a_vulnerability": True, "not_full_sor_copy": True},
                    )
                )
            uow.commit()
        return outcome, snapshot
