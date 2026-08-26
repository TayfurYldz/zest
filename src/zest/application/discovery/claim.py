"""Transactionally claim a SELECTED frontier generation. Python races are not enough."""

from __future__ import annotations

from datetime import datetime

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.data.errors import PersistenceConflictError
from zest.data.records import FrontierEventRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.discovery.frontier import (
    FrontierEvent,
    legal_frontier_transition,
    next_selection_generation,
)
from zest.research.discovery.types import FrontierEventKind


def claim_frontier_selected(
    uow: UnitOfWork,
    frontier_id: str,
    *,
    created_at: datetime,
) -> FrontierEventRecord:
    locked = uow.frontier_items.lock(frontier_id)
    if locked is None:
        raise ApplicationError("frontier item not found")
    events = uow.frontier_events.list_for_frontier(frontier_id)
    latest_kind = FrontierEventKind(events[-1].event_kind) if events else None
    if not legal_frontier_transition(latest_kind, FrontierEventKind.SELECTED):
        raise ApplicationError("frontier is not eligible for selection")
    mapped = tuple(
        FrontierEvent(
            event_id=item.event_id,
            frontier_id=item.frontier_id,
            research_run_id=item.research_run_id,
            event_kind=FrontierEventKind(item.event_kind),
            sequence=item.sequence,
            selection_generation=item.selection_generation,
            execution_attempt_id=item.execution_attempt_id,
            reason_code=item.reason_code,
        )
        for item in events
    )
    generation = next_selection_generation(mapped)
    record = FrontierEventRecord(
        event_id=new_opaque_id(),
        frontier_id=frontier_id,
        research_run_id=locked.research_run_id,
        event_kind="SELECTED",
        sequence=(events[-1].sequence + 1) if events else 1,
        created_at=created_at,
        selection_generation=generation,
    )
    try:
        uow.frontier_events.insert(record)
    except PersistenceConflictError as exc:
        raise ApplicationError("frontier selection lost the concurrent claim") from exc
    uow.frontier_items.set_cache_state(frontier_id, "SELECTED", record.sequence)
    return record
