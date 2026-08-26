"""Refresh live coverage debt from temporal changes. Does not create Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from zest.application.coverage.debt_view import CoverageDebtView, CoverageDebtSummary
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, ChangeEventRecord, CoverageDebtSnapshotRecord
from zest.research.coverage.live import (
    CoverageChangeEventView,
    CoverageDebtSnapshotView,
    LiveCoverageDebtImpact,
    assess_live_coverage_debt,
)


class _CoverageDebtView(Protocol):
    def execute(self, research_run_id: str, *, persist: bool = False) -> CoverageDebtSummary: ...


@dataclass(frozen=True)
class RefreshLiveCoverageDebtCommand:
    research_run_id: str


class RefreshLiveCoverageDebt:
    """Persist a current coverage snapshot and record advisory change impact."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
        coverage_view: _CoverageDebtView | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id
        self._coverage_view = coverage_view or CoverageDebtView(uow_factory, clock=self._clock)

    def execute(self, command: RefreshLiveCoverageDebtCommand) -> LiveCoverageDebtImpact:
        summary = self._coverage_view.execute(command.research_run_id, persist=True)
        if summary.snapshot_id is None:
            raise RuntimeError("persistent coverage refresh did not create a snapshot")

        with self._uow_factory.open() as uow:
            snapshots = uow.coverage_debt_snapshots.list_for_research_run(command.research_run_id)
            current_record = uow.coverage_debt_snapshots.get(summary.snapshot_id)
            if current_record is None:
                raise RuntimeError("coverage snapshot was not persisted")
            previous_record = _previous_snapshot(snapshots, current_record.snapshot_id)
            changes = _changes_since(
                uow.change_events.list_for_research_run(command.research_run_id),
                previous_record,
                current_record,
            )
            impact = assess_live_coverage_debt(
                current=_snapshot_view(current_record),
                previous=_snapshot_view(previous_record) if previous_record is not None else None,
                change_events=tuple(_change_view(record) for record in changes),
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="LIVE_COVERAGE_DEBT_REFRESHED",
                    subject_type="coverage_debt_snapshot",
                    subject_id=current_record.snapshot_id,
                    payload={
                        "previous_snapshot_id": impact.previous_snapshot_id,
                        "current_matrix_hash": impact.current_matrix_hash,
                        "total_debt_after": impact.total_debt_after,
                        "total_debt_delta": impact.total_debt_delta,
                        "change_event_ids": list(impact.change_event_ids),
                        "reason_codes": list(impact.reason_codes),
                        "not_a_vulnerability": True,
                        "not_evidence": True,
                        "not_candidate": True,
                        "not_finding": True,
                    },
                )
            )
            uow.commit()
        return impact


def _previous_snapshot(
    snapshots: list[CoverageDebtSnapshotRecord],
    current_snapshot_id: str,
) -> CoverageDebtSnapshotRecord | None:
    before_current = [
        snapshot for snapshot in snapshots if snapshot.snapshot_id != current_snapshot_id
    ]
    if not before_current:
        return None
    return sorted(before_current, key=lambda snapshot: snapshot.created_at)[-1]


def _changes_since(
    changes: list[ChangeEventRecord],
    previous: CoverageDebtSnapshotRecord | None,
    current: CoverageDebtSnapshotRecord,
) -> list[ChangeEventRecord]:
    lower = previous.created_at if previous is not None else None
    return [
        change
        for change in sorted(changes, key=lambda item: item.created_at)
        if (lower is None or change.created_at > lower)
        and change.created_at <= current.created_at
    ]


def _snapshot_view(record: CoverageDebtSnapshotRecord) -> CoverageDebtSnapshotView:
    return CoverageDebtSnapshotView(
        snapshot_id=record.snapshot_id,
        research_run_id=record.research_run_id,
        matrix_hash=record.matrix_hash,
        cell_counts=record.cell_counts,
        total_debt=record.total_debt,
        created_at=record.created_at,
    )


def _change_view(record: ChangeEventRecord) -> CoverageChangeEventView:
    return CoverageChangeEventView(
        change_event_id=record.change_event_id,
        research_run_id=record.research_run_id,
        category=record.category,
        statement=record.statement,
        source_refs=record.source_refs,
        created_at=record.created_at,
    )
