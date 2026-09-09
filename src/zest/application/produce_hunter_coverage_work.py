"""Production Hunter/Coverage harvest. Not a Worker dispatcher."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.coverage.debt_view import CoverageDebtView, rebuild_coverage_graph
from zest.application.coverage.live_debt import RefreshLiveCoverageDebt, RefreshLiveCoverageDebtCommand
from zest.application.discovery.lifecycle import discovery_can_exit
from zest.application.hunter_coverage_exhaustion import retire_resolved_hunter_candidates
from zest.application.hunter_coverage_opportunity_source import (
    HunterCoverageOpportunitySource,
    HunterCoverageOpportunitySourceCommand,
)
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.run_hunt_cycle import RunHuntCycle, RunHuntCycleCommand
from zest.application.run_hunt_scheduler import RunHuntScheduler, RunHuntSchedulerCommand
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION


@dataclass(frozen=True)
class ProduceHunterCoverageWorkResult:
    research_run_id: str
    skipped: bool
    skip_reason: str | None
    snapshot_id: str | None
    matrix_hash: str | None
    recommended_count: int
    hypotheses_generated: int
    v3_queued: int
    candidates_created: int


class ProduceHunterCoverageWork:
    """Refresh live coverage, rank hunt cells, validate tiers, propose candidates.

    Called from SelectResearchOpportunities after discovery exit. Does not
    dispatch Workers and does not own V3 queue worker dispatch.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._coverage_view = CoverageDebtView(uow_factory, clock=self._clock)
        self._refresh = RefreshLiveCoverageDebt(
            uow_factory, clock=self._clock, coverage_view=self._coverage_view
        )
        self._scheduler = RunHuntScheduler(uow_factory, clock=self._clock)
        self._cycle = RunHuntCycle(uow_factory, clock=self._clock)
        self._source = HunterCoverageOpportunitySource(uow_factory, clock=self._clock)

    def execute(self, research_run_id: str) -> ProduceHunterCoverageWorkResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(research_run_id)
            if run is None:
                uow.rollback()
                return _skipped(research_run_id, "RESEARCH_RUN_NOT_FOUND")
            families = uow.hunter_families.list_enabled()
            facts = uow.discovery_facts.list_for_research_run(research_run_id)
            config = uow.discovery_run_configs.get(research_run_id)
            exit_allowed = discovery_can_exit(uow, research_run_id) if config is not None else True
            snapshots = uow.coverage_debt_snapshots.list_for_research_run(research_run_id)
            latest_snapshot = (
                sorted(snapshots, key=lambda item: item.created_at)[-1] if snapshots else None
            )
            graph = rebuild_coverage_graph(uow, research_run_id, SURFACE_DISCOVERY_STRATEGY_VERSION)
            uow.rollback()
        if not families:
            return _skipped(research_run_id, "NO_HUNTER_FAMILIES")
        if not facts:
            return _skipped(research_run_id, "NO_DISCOVERY_FACTS")
        if not exit_allowed:
            return _skipped(research_run_id, "DISCOVERY_NOT_EXITED")

        summary = self._coverage_view.execute(research_run_id, persist=False)
        snapshot_id = None
        if latest_snapshot is None or latest_snapshot.matrix_hash != summary.matrix_hash:
            impact = self._refresh.execute(
                RefreshLiveCoverageDebtCommand(research_run_id=research_run_id)
            )
            snapshot_id = impact.current_snapshot_id
        else:
            snapshot_id = latest_snapshot.snapshot_id

        scheduled = self._scheduler.execute(
            RunHuntSchedulerCommand(research_run_id=research_run_id, graph=graph)
        )
        cycle = self._cycle.execute(
            RunHuntCycleCommand(
                research_run_id=research_run_id,
                graph=graph,
                schedule=scheduled.recommended,
            )
        )
        after = self._coverage_view.execute(research_run_id, persist=False)
        if after.matrix_hash != summary.matrix_hash:
            impact = self._refresh.execute(
                RefreshLiveCoverageDebtCommand(research_run_id=research_run_id)
            )
            snapshot_id = impact.current_snapshot_id
        after_schedule = self._scheduler.execute(
            RunHuntSchedulerCommand(research_run_id=research_run_id, graph=graph)
        )
        proposed = self._source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id=research_run_id,
                scored_cells=after_schedule.scored_cells,
            )
        )
        with self._uow_factory.open() as uow:
            retire_resolved_hunter_candidates(
                uow, research_run_id, now=self._clock.now()
            )
            uow.commit()
        return ProduceHunterCoverageWorkResult(
            research_run_id=research_run_id,
            skipped=False,
            skip_reason=None,
            snapshot_id=snapshot_id,
            matrix_hash=scheduled.matrix_hash,
            recommended_count=scheduled.recommended_count,
            hypotheses_generated=cycle.generated,
            v3_queued=cycle.v3_queued,
            candidates_created=proposed.candidates_created,
        )


def _skipped(research_run_id: str, reason: str) -> ProduceHunterCoverageWorkResult:
    return ProduceHunterCoverageWorkResult(
        research_run_id=research_run_id,
        skipped=True,
        skip_reason=reason,
        snapshot_id=None,
        matrix_hash=None,
        recommended_count=0,
        hypotheses_generated=0,
        v3_queued=0,
        candidates_created=0,
    )
