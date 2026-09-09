"""Surface-discovery continuation and exit derived from persisted SoR state.

Process-local Start command is not authoritative. DiscoveryRunConfig plus
FrontierEvent reconstruct ownership and exhaustion after restart.
SurfaceDiscoveryRunner remains the only consumer of runnable discovery items.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zest.application.discovery.config import config_from_record
from zest.application.discovery.runner import SurfaceDiscoveryStart
from zest.application.identity import new_opaque_id
from zest.application.orchestration_obligations import unresolved_control_obligations
from zest.core.scope_compiler import CompiledScope
from zest.data.records import FrontierEventRecord
from zest.research.discovery.frontier import (
    CATEGORY_BUDGET_BLOCKED,
    CATEGORY_BLOCKED_POLICY,
    CATEGORY_BLOCKED_SCOPE,
    CATEGORY_DEDUPLICATED,
    CATEGORY_DEFERRED,
    CATEGORY_EXECUTED,
    CATEGORY_FAILED,
    CATEGORY_HANDED_OFF,
    CATEGORY_IN_FLIGHT,
    CATEGORY_RUNNABLE,
    CATEGORY_UNEXPLAINED,
    CATEGORY_UNSUPPORTED,
    CATEGORY_WAITING_HUMAN,
    DISCOVERY_EXECUTABLE_CAPABILITIES,
    REASON_DEDUPLICATED,
    REASON_DEFERRED_TO_RESEARCH,
    REASON_UNSUPPORTED_CAPABILITY,
    SURFACE_DISCOVERY_MAX_SIDE_EFFECT,
    FrontierEvent,
    FrontierItem,
    classify_frontier_ownership,
    latest_event,
    legal_frontier_transition,
    runnable_discovery_frontier_items,
)
from zest.research.discovery.types import DiscoveryFactKind, DiscoveryGoalKind, FrontierEventKind


@dataclass(frozen=True)
class DiscoveryExitAudit:
    """PostgreSQL-authoritative discovery exit snapshot. Not a WorkerResult."""

    frontier_total: int
    runnable_discovery: int
    in_flight: int
    waiting_human: int
    owned_by_research: int
    executed: int
    blocked_scope: int
    blocked_policy: int
    budget_blocked: int
    failed: int
    deduplicated: int
    handed_off: int
    unsupported: int
    deferred: int
    blocked_boundary: int
    unexplained: int
    orphan_discovery_work: int
    discovery_exit_considered: bool
    discovery_exit_allowed: bool
    discovery_exit_block_reasons: tuple[str, ...]
    terminal_dispositions: int

    def as_payload(self) -> dict[str, object]:
        return {
            "frontier_total": self.frontier_total,
            "runnable_discovery": self.runnable_discovery,
            "in_flight": self.in_flight,
            "waiting_human": self.waiting_human,
            "owned_by_research": self.owned_by_research,
            "executed": self.executed,
            "blocked_scope": self.blocked_scope,
            "blocked_policy": self.blocked_policy,
            "budget_blocked": self.budget_blocked,
            "failed": self.failed,
            "deduplicated": self.deduplicated,
            "handed_off": self.handed_off,
            "unsupported": self.unsupported,
            "deferred": self.deferred,
            "blocked_boundary": self.blocked_boundary,
            "unexplained": self.unexplained,
            "orphan_discovery_work": self.orphan_discovery_work,
            "terminal_dispositions": self.terminal_dispositions,
            "discovery_exit_considered": self.discovery_exit_considered,
            "discovery_exit_allowed": self.discovery_exit_allowed,
            "discovery_exit_block_reasons": list(self.discovery_exit_block_reasons),
        }


def count_runnable_discovery_frontier(uow, research_run_id: str) -> int:
    """How many discovery-owned items are still ELIGIBLE at SE<=1."""

    return len(_runnable_items(uow, research_run_id))


def discovery_can_exit(uow, research_run_id: str) -> bool:
    """True only when every discovery-owned unit of work has a disposition."""

    return discovery_exit_audit(uow, research_run_id, considered=True).discovery_exit_allowed


def discovery_is_exhausted(uow, research_run_id: str) -> bool:
    """True when discovery started and the exit contract is satisfied.

    Missing config means discovery never started, which is not exhaustion.
    eligible_count(SE<=1)==0 is not sufficient by itself.
    """

    if uow.discovery_run_configs.get(research_run_id) is None:
        return False
    return discovery_can_exit(uow, research_run_id)


def discovery_exit_audit(
    uow, research_run_id: str, *, considered: bool = True
) -> DiscoveryExitAudit:
    records = uow.frontier_items.list_for_research_run(research_run_id)
    views = []
    for record in records:
        events = tuple(
            _event_from_record(event)
            for event in uow.frontier_events.list_for_frontier(record.frontier_id)
        )
        views.append(classify_frontier_ownership(_item_from_record(record), events))
    counts = {key: 0 for key in _AUDIT_CATEGORIES}
    for view in views:
        counts[view.category] = counts.get(view.category, 0) + 1
    waiting_human = counts[CATEGORY_WAITING_HUMAN]
    obligations = unresolved_control_obligations(
        attempts=uow.execution_attempts.list_for_research_run(research_run_id),
        experiments=uow.experiments.list_for_research_run(research_run_id),
        worker_results=uow.worker_results.list_for_research_run(research_run_id),
    )
    if any(item.code == "REAUTHORIZATION_REQUIRED" for item in obligations):
        waiting_human = max(waiting_human, sum(1 for item in obligations if item.code == "REAUTHORIZATION_REQUIRED"))
    blocked_boundary = _blocked_boundary_count(uow, research_run_id)
    unexplained = counts.get(CATEGORY_UNEXPLAINED, 0)
    in_flight = counts[CATEGORY_IN_FLIGHT]
    runnable = counts[CATEGORY_RUNNABLE]
    orphan = unexplained + in_flight + runnable
    block_reasons: list[str] = []
    if uow.discovery_run_configs.get(research_run_id) is None:
        block_reasons.append("DISCOVERY_NEVER_STARTED")
    if runnable:
        block_reasons.append("RUNNABLE_DISCOVERY_REMAINS")
    if in_flight:
        block_reasons.append("DISCOVERY_IN_FLIGHT")
    if waiting_human:
        block_reasons.append("WAITING_HUMAN_UNRESOLVED")
    if unexplained:
        block_reasons.append("UNEXPLAINED_DISCOVERY_WORK")
    allowed = not block_reasons and uow.discovery_run_configs.get(research_run_id) is not None
    terminal = (
        counts[CATEGORY_EXECUTED]
        + counts[CATEGORY_BLOCKED_SCOPE]
        + counts[CATEGORY_BLOCKED_POLICY]
        + counts[CATEGORY_BUDGET_BLOCKED]
        + counts[CATEGORY_FAILED]
        + counts[CATEGORY_DEDUPLICATED]
        + counts[CATEGORY_HANDED_OFF]
        + counts[CATEGORY_UNSUPPORTED]
        + counts[CATEGORY_DEFERRED]
    )
    return DiscoveryExitAudit(
        frontier_total=len(records),
        runnable_discovery=runnable,
        in_flight=in_flight,
        waiting_human=waiting_human,
        owned_by_research=counts[CATEGORY_HANDED_OFF],
        executed=counts[CATEGORY_EXECUTED],
        blocked_scope=counts[CATEGORY_BLOCKED_SCOPE],
        blocked_policy=counts[CATEGORY_BLOCKED_POLICY],
        budget_blocked=counts[CATEGORY_BUDGET_BLOCKED],
        failed=counts[CATEGORY_FAILED],
        deduplicated=counts[CATEGORY_DEDUPLICATED],
        handed_off=counts[CATEGORY_HANDED_OFF],
        unsupported=counts[CATEGORY_UNSUPPORTED],
        deferred=counts[CATEGORY_DEFERRED],
        blocked_boundary=blocked_boundary,
        unexplained=unexplained,
        orphan_discovery_work=orphan,
        discovery_exit_considered=considered,
        discovery_exit_allowed=allowed,
        discovery_exit_block_reasons=tuple(block_reasons),
        terminal_dispositions=terminal,
    )


def apply_discovery_exit_dispositions(uow, research_run_id: str, *, created_at: datetime) -> int:
    """Give every non-runnable discovery-owned item an explicit event. Idempotent."""

    records = uow.frontier_items.list_for_research_run(research_run_id)
    events_by = {
        item.frontier_id: tuple(
            _event_from_record(event)
            for event in uow.frontier_events.list_for_frontier(item.frontier_id)
        )
        for item in records
    }
    domain = [_item_from_record(item) for item in records]
    changed = 0
    seen_dedupe: dict[str, str] = {}
    for item in sorted(domain, key=lambda row: row.frontier_id):
        latest = latest_event(events_by.get(item.frontier_id, ()))
        if latest is not None and latest.event_kind is FrontierEventKind.ELIGIBLE:
            canonical = seen_dedupe.get(item.dedupe_identity)
            if canonical is None:
                seen_dedupe[item.dedupe_identity] = item.frontier_id
            elif canonical != item.frontier_id:
                _append(
                    uow,
                    item.frontier_id,
                    FrontierEventKind.SUPERSEDED,
                    created_at,
                    reason_code=f"{REASON_DEDUPLICATED}:{canonical}",
                    latest=latest,
                )
                changed += 1
                continue
        if latest is None or latest.event_kind is FrontierEventKind.CREATED:
            if (
                item.budget_class > SURFACE_DISCOVERY_MAX_SIDE_EFFECT
                or item.expected_side_effect > SURFACE_DISCOVERY_MAX_SIDE_EFFECT
            ):
                _append(
                    uow,
                    item.frontier_id,
                    FrontierEventKind.DEFERRED_TO_RESEARCH,
                    created_at,
                    reason_code=REASON_DEFERRED_TO_RESEARCH,
                    latest=latest,
                )
                changed += 1
                continue
            if item.proposed_capability not in DISCOVERY_EXECUTABLE_CAPABILITIES:
                _append(
                    uow,
                    item.frontier_id,
                    FrontierEventKind.UNSUPPORTED,
                    created_at,
                    reason_code=REASON_UNSUPPORTED_CAPABILITY,
                    latest=latest,
                )
                changed += 1
                continue
            if latest is not None and latest.event_kind is FrontierEventKind.CREATED:
                _append(
                    uow,
                    item.frontier_id,
                    FrontierEventKind.ELIGIBLE,
                    created_at,
                    latest=latest,
                )
                changed += 1
            continue
        if latest.event_kind is FrontierEventKind.FAILED_TRANSIENT:
            _append(
                uow,
                item.frontier_id,
                FrontierEventKind.FAILED_TERMINAL,
                created_at,
                reason_code="NO_AUTOMATIC_RETRY",
                latest=latest,
            )
            changed += 1
            continue
        if latest.event_kind is not FrontierEventKind.ELIGIBLE:
            continue
        if (
            item.budget_class > SURFACE_DISCOVERY_MAX_SIDE_EFFECT
            or item.expected_side_effect > SURFACE_DISCOVERY_MAX_SIDE_EFFECT
        ):
            _append(
                uow,
                item.frontier_id,
                FrontierEventKind.DEFERRED_TO_RESEARCH,
                created_at,
                reason_code=REASON_DEFERRED_TO_RESEARCH,
                latest=latest,
            )
            changed += 1
            continue
        if item.proposed_capability not in DISCOVERY_EXECUTABLE_CAPABILITIES:
            _append(
                uow,
                item.frontier_id,
                FrontierEventKind.UNSUPPORTED,
                created_at,
                reason_code=REASON_UNSUPPORTED_CAPABILITY,
                latest=latest,
            )
            changed += 1
    return changed


def surface_discovery_start_from_persisted(
    uow,
    research_run_id: str,
    *,
    compiled_scope: CompiledScope | None,
    identities: tuple[str, ...] | None = None,
    session_context_by_identity=None,
    identity_by_id=None,
) -> SurfaceDiscoveryStart | None:
    """Rebuild SurfaceDiscoveryStart from SoR. None if discovery never started."""

    record = uow.discovery_run_configs.get(research_run_id)
    if record is None:
        return None
    kwargs = {}
    if identities is not None:
        kwargs["identities"] = identities
    if session_context_by_identity is not None:
        kwargs["session_context_by_identity"] = session_context_by_identity
    if identity_by_id is not None:
        kwargs["identity_by_id"] = identity_by_id
    return SurfaceDiscoveryStart(
        config=config_from_record(record),
        compiled_scope=compiled_scope,
        **kwargs,
    )


_AUDIT_CATEGORIES = (
    CATEGORY_RUNNABLE,
    CATEGORY_IN_FLIGHT,
    CATEGORY_WAITING_HUMAN,
    CATEGORY_EXECUTED,
    CATEGORY_BLOCKED_SCOPE,
    CATEGORY_BLOCKED_POLICY,
    CATEGORY_BUDGET_BLOCKED,
    CATEGORY_FAILED,
    CATEGORY_DEDUPLICATED,
    CATEGORY_HANDED_OFF,
    CATEGORY_UNSUPPORTED,
    CATEGORY_DEFERRED,
    CATEGORY_UNEXPLAINED,
)


def _append(
    uow,
    frontier_id: str,
    kind: FrontierEventKind,
    created_at: datetime,
    *,
    reason_code: str | None = None,
    latest: FrontierEvent | None,
) -> None:
    if not legal_frontier_transition(None if latest is None else latest.event_kind, kind):
        return
    existing = uow.frontier_events.list_for_frontier(frontier_id)
    research_run_id = (
        existing[0].research_run_id
        if existing
        else uow.frontier_items.get(frontier_id).research_run_id
    )
    sequence = (existing[-1].sequence + 1) if existing else 1
    uow.frontier_events.insert(
        FrontierEventRecord(
            event_id=new_opaque_id(),
            frontier_id=frontier_id,
            research_run_id=research_run_id,
            event_kind=kind.value,
            sequence=sequence,
            created_at=created_at,
            reason_code=reason_code,
        )
    )
    uow.frontier_items.set_cache_state(frontier_id, kind.value, sequence)


def _blocked_boundary_count(uow, research_run_id: str) -> int:
    facts = uow.discovery_facts.list_for_research_run(research_run_id)
    return sum(
        1
        for item in facts
        if item.fact_kind == DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE.value
        or (
            item.fact_kind == DiscoveryFactKind.PAGE_STATE.value
            and isinstance(item.attributes, dict)
            and item.attributes.get("containment_degraded")
        )
    )


def _runnable_items(uow, research_run_id: str) -> tuple[FrontierItem, ...]:
    records = uow.frontier_items.list_for_research_run(research_run_id)
    events_by: dict[str, tuple[FrontierEvent, ...]] = {}
    items: list[FrontierItem] = []
    for record in records:
        events_by[record.frontier_id] = tuple(
            _event_from_record(event)
            for event in uow.frontier_events.list_for_frontier(record.frontier_id)
        )
        items.append(_item_from_record(record))
    return runnable_discovery_frontier_items(
        tuple(items),
        events_by,
        max_side_effect=SURFACE_DISCOVERY_MAX_SIDE_EFFECT,
    )


def _item_from_record(record) -> FrontierItem:
    return FrontierItem(
        frontier_id=record.frontier_id,
        research_run_id=record.research_run_id,
        goal_kind=DiscoveryGoalKind(record.goal_kind),
        candidate_origin=record.candidate_origin,
        candidate_path=record.candidate_path,
        identity_id=record.identity_id,
        proposed_capability=record.proposed_capability,
        proposed_action=record.proposed_action,
        expected_side_effect=record.expected_side_effect,
        budget_class=record.budget_class,
        structural_signature=record.structural_signature,
        dedupe_identity=record.dedupe_identity,
        strategy_version=record.strategy_version,
        session_context_id=record.session_context_id,
        scope_hint=record.scope_hint,
        attributes=record.attributes,
    )


def _event_from_record(record) -> FrontierEvent:
    return FrontierEvent(
        event_id=record.event_id,
        frontier_id=record.frontier_id,
        research_run_id=record.research_run_id,
        event_kind=FrontierEventKind(record.event_kind),
        sequence=record.sequence,
        selection_generation=record.selection_generation,
        execution_attempt_id=record.execution_attempt_id,
        reason_code=record.reason_code,
    )
