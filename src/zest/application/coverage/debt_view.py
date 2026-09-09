"""Coverage debt view use-case: rebuild graph, load registry, compute matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.coverage.hypothesis_view import build_coverage_hypothesis_view
from zest.application.discovery.snapshot_views import summarize_attack_surface
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.records import CoverageDebtSnapshotRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.coverage.debt import compute_coverage_debt
from zest.research.coverage.types import CoverageMatrix, CoverageState
from zest.research.selection import HunterFamilyView


@dataclass(frozen=True)
class CoverageDebtSummary:
    """Operator-facing summary of coverage debt for one research run."""

    research_run_id: str
    strategy_version: str
    matrix_hash: str
    total_debt: int
    cell_counts: dict[str, int]
    family_debt: dict[str, int]
    top_nodes: list[dict[str, Any]]
    snapshot_id: str | None = None


class CoverageDebtView:
    """Read-only use-case: compute coverage debt from the ledger.

    Set persist=True to write a CoverageDebtSnapshotRecord. The full matrix
    remains rebuildable from the ledger; the snapshot is only a durable hash +
    count summary.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self,
        research_run_id: str,
        *,
        persist: bool = False,
    ) -> CoverageDebtSummary:
        with self._uow_factory.open() as uow:
            summary = summarize_attack_surface(uow, research_run_id)
            graph = self._rebuild_graph(uow, research_run_id, summary.strategy_version)
            registry = self._load_registry(uow)
            hypotheses_view = build_coverage_hypothesis_view(uow, research_run_id)
            matrix = compute_coverage_debt(graph, registry, hypotheses_view)
            snapshot_id: str | None = None
            if persist:
                snapshot_id = new_opaque_id()
                record = CoverageDebtSnapshotRecord(
                    snapshot_id=snapshot_id,
                    research_run_id=matrix.research_run_id,
                    matrix_hash=matrix.matrix_hash,
                    cell_counts=dict(matrix.cell_counts),
                    total_debt=matrix.total_debt,
                    created_at=self._clock.now(),
                )
                uow.coverage_debt_snapshots.insert(record)
                uow.commit()
            else:
                uow.rollback()
        return _to_summary(matrix, snapshot_id=snapshot_id)

    def _rebuild_graph(
        self,
        uow: UnitOfWork,
        research_run_id: str,
        strategy_version: str,
    ) -> Any:
        return rebuild_coverage_graph(uow, research_run_id, strategy_version)

    def _load_registry(self, uow: UnitOfWork) -> tuple[HunterFamilyView, ...]:
        records = uow.hunter_families.list_enabled()
        # Append-only versioning: keep the latest version per family_id.
        latest: dict[str, HunterFamilyView] = {}
        for record in records:
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


def rebuild_coverage_graph(uow: UnitOfWork, research_run_id: str, strategy_version: str) -> Any:
    """Rebuild the attack-surface graph from authoritative discovery records."""

    from zest.application.discovery.snapshot_views import _fact_from_record, _inference_from_record
    from zest.research.discovery.graph import rebuild_attack_surface_graph

    facts = uow.discovery_facts.list_for_research_run(research_run_id)
    inferences = uow.discovery_inferences.list_for_research_run(research_run_id)
    domain_facts = tuple(_fact_from_record(uow, row) for row in facts)
    domain_inferences = tuple(_inference_from_record(row) for row in inferences)
    return rebuild_attack_surface_graph(
        research_run_id=research_run_id,
        strategy_version=strategy_version,
        facts=domain_facts,
        inferences=domain_inferences,
    )


def _to_summary(
    matrix: CoverageMatrix,
    *,
    snapshot_id: str | None = None,
) -> CoverageDebtSummary:
    family_debt: dict[str, int] = {}
    node_debt: dict[str, int] = {}
    debt_states = {
        CoverageState.UNTESTED.value,
        CoverageState.HYPOTHESIZED.value,
        CoverageState.V1_PASSED.value,
        CoverageState.V2_PASSED.value,
        CoverageState.V3_QUEUED.value,
    }
    for cell in matrix.cells:
        if cell.state.value in debt_states:
            family_debt[cell.family_id] = family_debt.get(cell.family_id, 0) + 1
            node_debt[cell.node_canonical_key] = node_debt.get(cell.node_canonical_key, 0) + 1

    top_nodes = sorted(
        ({"node_canonical_key": key, "debt": count} for key, count in node_debt.items()),
        key=lambda item: item["debt"],
        reverse=True,
    )[:10]

    return CoverageDebtSummary(
        research_run_id=matrix.research_run_id,
        strategy_version=matrix.strategy_version,
        matrix_hash=matrix.matrix_hash,
        total_debt=matrix.total_debt,
        cell_counts=dict(matrix.cell_counts),
        family_debt=family_debt,
        top_nodes=top_nodes,
        snapshot_id=snapshot_id,
    )
