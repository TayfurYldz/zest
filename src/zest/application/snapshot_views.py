"""Load ResearchSnapshot domain objects from SoR references."""

from __future__ import annotations

from zest.data.unit_of_work import UnitOfWork
from zest.research.temporal import ResearchSnapshot, TEMPORAL_STRATEGY_VERSION


def load_research_snapshot(uow: UnitOfWork, snapshot_id: str) -> ResearchSnapshot | None:
    record = uow.snapshots.get(snapshot_id)
    if record is None:
        return None
    members = uow.snapshots.list_members(snapshot_id)
    return ResearchSnapshot(
        snapshot_id=record.snapshot_id,
        research_run_id=record.research_run_id,
        program_id=record.program_id,
        target_identity=record.target_identity,
        observation_ids=tuple(item.observation_id for item in members),
        captured_at=record.captured_at,
        strategy_version=record.strategy_version or TEMPORAL_STRATEGY_VERSION,
        attributes={"not_a_vulnerability": True, "not_full_sor_copy": True},
    )
