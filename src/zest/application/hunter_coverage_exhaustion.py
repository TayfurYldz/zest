"""Authoritative Hunter/Coverage exhaustion. Snapshot counts are not truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zest.application.coverage.debt_view import rebuild_coverage_graph
from zest.application.coverage.hypothesis_view import build_coverage_hypothesis_view
from zest.research.compiler_registry import PROTOCOL_FAMILIES
from zest.research.coverage.debt import compute_coverage_debt
from zest.research.coverage.types import CoverageState
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.exploration import OpportunityKind
from zest.research.selection import HunterFamilyView

CONNECTED_ACTIONABLE_STATES = frozenset(
    {
        CoverageState.UNTESTED,
        CoverageState.HYPOTHESIZED,
        CoverageState.V1_PASSED,
        CoverageState.V2_PASSED,
        CoverageState.V3_QUEUED,
    }
)
RESOLVED_STATES = frozenset({CoverageState.COVERED, CoverageState.NOT_APPLICABLE})
TERMINAL_CONNECTED_DISPOSITIONS = frozenset(
    {
        "COVERED",
        "NOT_APPLICABLE",
        "MISSING_PRECONDITION",
        "BLOCKED_POLICY",
        "BLOCKED_SCOPE",
        "BLOCKED_SIDE_EFFECT_CEILING",
        "APPROVAL_REQUIRED",
        "SUPERSEDED",
        "BLOCKED_BY_CURRENT_AUTHORITY",
    }
)
EVENT_DISPOSITIONS = {
    "HUNT_CELL_COVERED": "COVERED",
    "HUNT_TIER_V1_REJECTED": "NOT_APPLICABLE",
    "HUNT_TIER_V2_REJECTED": "NOT_APPLICABLE",
    "RESEARCH_WORK_MISSING_PRECONDITION": "MISSING_PRECONDITION",
    "RESEARCH_WORK_BLOCKED_POLICY": "BLOCKED_POLICY",
    "RESEARCH_WORK_BLOCKED_SCOPE": "BLOCKED_SCOPE",
    "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING": "BLOCKED_SIDE_EFFECT_CEILING",
    "RESEARCH_WORK_APPROVAL_REQUIRED": "APPROVAL_REQUIRED",
    "RESEARCH_WORK_ENGINE_DEPENDENCY_PENDING": "ENGINE_DEPENDENCY_PENDING",
    "RESEARCH_WORK_CORE_DENIED": "BLOCKED_BY_CURRENT_AUTHORITY",
}


@dataclass(frozen=True)
class CoverageCellKey:
    family_id: str
    node_canonical_key: str
    identity_id: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.family_id, self.node_canonical_key, self.identity_id)


@dataclass(frozen=True)
class HunterCoverageExhaustionInventory:
    coverage_actionable: int
    coverage_resolved: int
    coverage_blocked: int
    coverage_missing_precondition: int
    engine_dependency_pending: int
    hunter_pending: int
    hunter_orphan: int
    hunter_selected_ownerless: int
    hunter_coverage_exhausted_for_connected_engines: bool
    connected_actionable_keys: frozenset[tuple[str, str, str]]
    protocol_pending_keys: frozenset[tuple[str, str, str]]
    dispositions: dict[tuple[str, str, str], str]


def hunter_coverage_exhaustion_inventory(
    uow, research_run_id: str
) -> HunterCoverageExhaustionInventory:
    families = _latest_families(uow)
    graph = rebuild_coverage_graph(uow, research_run_id, SURFACE_DISCOVERY_STRATEGY_VERSION)
    views = build_coverage_hypothesis_view(uow, research_run_id)
    matrix = compute_coverage_debt(graph, families, views)
    family_by_id = {item.family_id: item for item in families}
    dispositions = _cell_dispositions(uow, research_run_id)
    connected_actionable: set[tuple[str, str, str]] = set()
    protocol_pending: set[tuple[str, str, str]] = set()
    resolved = 0
    blocked = 0
    missing = 0
    for cell in matrix.cells:
        key = (cell.family_id, cell.node_canonical_key, cell.identity_id)
        family = family_by_id.get(cell.family_id)
        name = family.name if family is not None else ""
        disposition = dispositions.get(key)
        if cell.state in RESOLVED_STATES or disposition == "COVERED":
            resolved += 1
            continue
        if disposition == "NOT_APPLICABLE":
            resolved += 1
            continue
        if disposition == "MISSING_PRECONDITION":
            missing += 1
            continue
        if disposition in {
            "BLOCKED_POLICY",
            "BLOCKED_SCOPE",
            "BLOCKED_SIDE_EFFECT_CEILING",
            "APPROVAL_REQUIRED",
            "BLOCKED_BY_CURRENT_AUTHORITY",
        }:
            blocked += 1
            continue
        if name in PROTOCOL_FAMILIES:
            if cell.state in CONNECTED_ACTIONABLE_STATES:
                protocol_pending.add(key)
            continue
        if disposition == "ENGINE_DEPENDENCY_PENDING":
            protocol_pending.add(key)
            continue
        if cell.state in CONNECTED_ACTIONABLE_STATES:
            connected_actionable.add(key)
    hunter_pending = 0
    hunter_orphan = 0
    for candidate in uow.opportunity_selection_candidates.list_for_research_run(research_run_id):
        if candidate.source_system != "HUNTER_COVERAGE" or candidate.outcome != "PENDING":
            continue
        if len(candidate.source_refs) < 3:
            hunter_orphan += 1
            continue
        key = (
            candidate.source_refs[0],
            candidate.source_refs[1],
            candidate.source_refs[2],
        )
        if key in connected_actionable:
            hunter_pending += 1
        elif key not in protocol_pending and key not in dispositions:
            hunter_orphan += 1
    hunter_ids = {
        item.opportunity_id
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
        if item.opportunity_kind == OpportunityKind.HUNTER_COVERAGE_GAP.value
    }
    selected_ids = {
        item.opportunity_id
        for item in uow.research_selections.list_for_research_run(research_run_id)
        if item.outcome == "SELECT" and item.opportunity_id in hunter_ids
    }
    owned = _hunter_owned_opportunity_ids(uow, research_run_id)
    hunter_selected_ownerless = sum(1 for item in selected_ids if item not in owned)
    exhausted = (
        not connected_actionable
        and hunter_pending == 0
        and hunter_orphan == 0
        and hunter_selected_ownerless == 0
    )
    return HunterCoverageExhaustionInventory(
        coverage_actionable=len(connected_actionable),
        coverage_resolved=resolved,
        coverage_blocked=blocked,
        coverage_missing_precondition=missing,
        engine_dependency_pending=sum(
            1 for value in dispositions.values() if value == "ENGINE_DEPENDENCY_PENDING"
        ),
        hunter_pending=hunter_pending,
        hunter_orphan=hunter_orphan,
        hunter_selected_ownerless=hunter_selected_ownerless,
        hunter_coverage_exhausted_for_connected_engines=exhausted,
        connected_actionable_keys=frozenset(connected_actionable),
        protocol_pending_keys=frozenset(protocol_pending),
        dispositions=dispositions,
    )


def retire_resolved_hunter_candidates(uow, research_run_id: str, *, now: datetime) -> int:
    """Mark leftover PENDING Hunter candidates NOT_ADMITTED once their cell is terminal."""

    inventory = hunter_coverage_exhaustion_inventory(uow, research_run_id)
    retired = 0
    for candidate in uow.opportunity_selection_candidates.list_for_research_run(research_run_id):
        if (
            candidate.source_system not in {"HUNTER_COVERAGE", "PROTOCOL"}
            or candidate.outcome != "PENDING"
        ):
            continue
        if candidate.opportunity_kind == OpportunityKind.OAST_INTERACTION.value:
            continue
        if len(candidate.source_refs) < 3:
            continue
        key = (
            candidate.source_refs[0],
            candidate.source_refs[1],
            candidate.source_refs[2],
        )
        if key in inventory.connected_actionable_keys:
            continue
        if key in inventory.protocol_pending_keys:
            continue
        uow.opportunity_selection_candidates.mark_decided(
            candidate.candidate_id,
            outcome="NOT_ADMITTED",
            resulting_opportunity_id=None,
            decided_at=now,
        )
        retired += 1
    return retired


def cell_is_proposal_eligible(cell, *, family_name: str | None, disposition: str | None) -> bool:
    if cell.state not in CONNECTED_ACTIONABLE_STATES:
        return False
    if disposition in TERMINAL_CONNECTED_DISPOSITIONS:
        return False
    if disposition == "ENGINE_DEPENDENCY_PENDING":
        return False
    if family_name in PROTOCOL_FAMILIES:
        return True
    return True


def _latest_families(uow) -> tuple[HunterFamilyView, ...]:
    latest: dict[str, HunterFamilyView] = {}
    for record in uow.hunter_families.list_enabled():
        view = HunterFamilyView(
            family_id=record.family_id,
            name=record.name,
            target_node_kinds=record.target_node_kinds,
            preconditions=record.preconditions,
            claim_template=record.claim_template,
            evidence_requirements=record.evidence_requirements,
            validation_tier=record.validation_tier,
            enabled=record.enabled,
            version=record.version,
        )
        existing = latest.get(record.family_id)
        if existing is None or view.version > existing.version:
            latest[record.family_id] = view
    return tuple(latest.values())


def _cell_dispositions(uow, research_run_id: str) -> dict[tuple[str, str, str], str]:
    events = list(uow.audit_events.list_for_subject("research_run", research_run_id))
    events.extend(uow.audit_events.list_for_subject_type("hypothesis"))
    ranked: dict[tuple[str, str, str], tuple[datetime, str]] = {}
    for event in events:
        disposition = EVENT_DISPOSITIONS.get(event.event_type)
        if disposition is None:
            continue
        payload = event.payload or {}
        if event.subject_type == "research_run" and event.subject_id != research_run_id:
            continue
        if event.subject_type == "hypothesis":
            run_ref = payload.get("research_run_id") or event.correlation_id
            if run_ref not in {research_run_id, None}:
                continue
        key = _key_from_payload(payload)
        if key is None:
            continue
        previous = ranked.get(key)
        if previous is None or event.occurred_at >= previous[0]:
            ranked[key] = (event.occurred_at, disposition)
    return {key: value[1] for key, value in ranked.items()}


def _key_from_payload(payload: dict) -> tuple[str, str, str] | None:
    family_id = payload.get("family_id")
    node_key = payload.get("node_canonical_key")
    identity_id = payload.get("identity_id") or "ANONYMOUS"
    if family_id and node_key:
        return (str(family_id), str(node_key), str(identity_id))
    cell_id = payload.get("coverage_cell_id")
    if isinstance(cell_id, str) and cell_id.count(":") >= 2:
        node_key, identity_id, family_id = cell_id.rsplit(":", 2)
        if family_id and node_key:
            return (family_id, node_key, identity_id)
    return None


def _hunter_owned_opportunity_ids(uow, research_run_id: str) -> set[str]:
    owned: set[str] = set()
    for event in uow.audit_events.list_for_subject("research_run", research_run_id):
        if event.event_type not in {
            "RESEARCH_WORK_COMPILED",
            "RESEARCH_WORK_MISSING_PRECONDITION",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_ENGINE_DEPENDENCY_PENDING",
            "RESEARCH_WORK_APPROVAL_REQUIRED",
            "RESEARCH_WORK_DEFERRED_ENGINE_WIRING",
            "RESEARCH_WORK_CORE_DENIED",
            "HUNTER_FEEDBACK_APPLIED",
        }:
            continue
        payload = event.payload or {}
        work_id = payload.get("selected_work_id") or payload.get("opportunity_id")
        if isinstance(work_id, str) and work_id.strip():
            owned.add(work_id)
    return owned
