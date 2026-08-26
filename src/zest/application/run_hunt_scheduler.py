"""RunHuntScheduler: deterministic priority queue from coverage debt.

The scheduler consumes the coverage-debt matrix and emits a ranked list of
recommended hunt cells. It does NOT write to the V3 queue; the V3 approval gate
remains in RunHuntCycle (G5/G6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from zest.application.coverage.hypothesis_view import (
    build_coverage_hypothesis_view,
)
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, HypothesisRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.coverage.debt import compute_coverage_debt
from zest.research.coverage.types import CoverageMatrix
from zest.research.discovery.graph import AttackSurfaceGraph
from zest.research.scheduler.score import schedule as schedule_cells
from zest.research.scheduler.types import (
    BudgetView,
    FamilyStats,
    HunterScoreInput,
    NodeFreshness,
    ScoredCell,
)
from zest.research.selection import HunterFamilyView


HUNT_SCHEDULE_RECOMMENDED = "HUNT_SCHEDULE_RECOMMENDED"
SCHEDULER_ACTOR_ID = "control-plane:hunt-scheduler"
DEFAULT_TOP_N = 100


@dataclass(frozen=True)
class RunHuntSchedulerCommand:
    research_run_id: str
    graph: AttackSurfaceGraph
    registry: tuple[HunterFamilyView, ...] | None = None
    daily_llm_budget_microdollars: int | None = None
    consumed_microdollars: int = 0
    top_n: int | None = None


@dataclass(frozen=True)
class RunHuntSchedulerResult:
    research_run_id: str
    matrix_hash: str
    scored_cells: tuple[ScoredCell, ...]
    recommended: tuple[ScoredCell, ...]
    recommended_count: int
    no_op: bool


class RunHuntScheduler:
    """Rank (node, identity, family) cells by HunterScore and audit the recommendation.

    All durable state lives in the append-only ledger. The scheduler is LLM-free
    and deterministic: the same graph + registry + ledger always yields the same
    ranked list.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: RunHuntSchedulerCommand) -> RunHuntSchedulerResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            registry = command.registry
            if registry is None:
                registry = tuple(_to_view(item) for item in uow.hunter_families.list_enabled())

            hypothesis_view = build_coverage_hypothesis_view(uow, command.research_run_id)
            matrix = compute_coverage_debt(command.graph, registry, hypothesis_view)

            family_stats = _build_family_stats(uow, command.research_run_id)
            freshness_by_node = _build_freshness_by_node(uow, command.graph)
            budget_view = BudgetView(
                daily_llm_budget_microdollars=command.daily_llm_budget_microdollars,
                consumed_microdollars=command.consumed_microdollars,
            )

            scored = schedule_cells(
                HunterScoreInput(
                    cells=matrix.cells,
                    family_stats=family_stats,
                    freshness_by_node=freshness_by_node,
                    budget_view=budget_view,
                    reference_time=now,
                )
            )

            top_n = command.top_n if command.top_n is not None else DEFAULT_TOP_N
            recommended = scored[:top_n]
            no_op = len(recommended) == 0

            uow.audit_events.insert(
                _schedule_audit(
                    audit_id=new_opaque_id(),
                    occurred_at=now,
                    research_run_id=command.research_run_id,
                    matrix_hash=matrix.matrix_hash,
                    recommended=recommended,
                    no_op=no_op,
                )
            )
            uow.commit()

        return RunHuntSchedulerResult(
            research_run_id=command.research_run_id,
            matrix_hash=matrix.matrix_hash,
            scored_cells=scored,
            recommended=recommended,
            recommended_count=len(recommended),
            no_op=no_op,
        )


def _build_family_stats(
    uow: UnitOfWork, research_run_id: str
) -> tuple[FamilyStats, ...]:
    """Supported/falsified counts per family from the latest assessment per hypothesis."""

    hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
    hypothesis_family: dict[str, str] = {
        item.hypothesis_id: (item.origin_reference or "")
        for item in hypotheses
    }

    supported: dict[str, int] = {}
    falsified: dict[str, int] = {}
    assessments = uow.hypothesis_assessments.list_for_research_run(research_run_id)
    by_hypothesis: Mapping[str, list] = {}
    for assessment in assessments:
        by_hypothesis.setdefault(assessment.hypothesis_id, []).append(assessment)

    for hypothesis_id, family_id in hypothesis_family.items():
        if not family_id:
            continue
        hypothesis_assessments = by_hypothesis.get(hypothesis_id, [])
        if not hypothesis_assessments:
            continue
        latest = sorted(
            hypothesis_assessments, key=lambda item: item.created_at, reverse=True
        )[0]
        if latest.assessment_outcome == "CONSISTENT_WITH_PREDICTION":
            supported[family_id] = supported.get(family_id, 0) + 1
        elif latest.assessment_outcome == "CONTRADICTS_PREDICTION":
            falsified[family_id] = falsified.get(family_id, 0) + 1

    family_ids = set(supported.keys()) | set(falsified.keys())
    return tuple(
        FamilyStats(
            family_id=family_id,
            supported_count=supported.get(family_id, 0),
            falsified_count=falsified.get(family_id, 0),
        )
        for family_id in sorted(family_ids)
    )


def _build_freshness_by_node(
    uow: UnitOfWork, graph: AttackSurfaceGraph
) -> Mapping[str, NodeFreshness]:
    """Map node canonical key to first-seen and latest-activity timestamps."""

    observations = uow.sensor_observations.list_for_research_run(graph.research_run_id)
    collected_at_by_id: dict[str, datetime] = {
        item.observation_id: item.collected_at for item in observations
    }

    result: dict[str, NodeFreshness] = {}
    for node in graph.nodes:
        earliest: datetime | None = None
        latest: datetime | None = None
        for ref in node.provenance_refs:
            prefix = "sensor_observation:"
            if ref.startswith(prefix):
                observation_id = ref[len(prefix) :]
                collected_at = collected_at_by_id.get(observation_id)
                if collected_at is not None and (
                    earliest is None or collected_at < earliest
                ):
                    earliest = collected_at
                if collected_at is not None and (latest is None or collected_at > latest):
                    latest = collected_at
        result[node.canonical_key] = NodeFreshness(
            node_canonical_key=node.canonical_key,
            first_seen_at=earliest,
            latest_activity_at=latest,
        )
    return result


def _to_view(record) -> HunterFamilyView:
    return HunterFamilyView(
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


def _schedule_audit(
    *,
    audit_id: str,
    occurred_at: datetime,
    research_run_id: str,
    matrix_hash: str,
    recommended: tuple[ScoredCell, ...],
    no_op: bool,
) -> AuditEventRecord:
    payload: dict[str, Any] = {
        "research_run_id": research_run_id,
        "matrix_hash": matrix_hash,
        "no_op": no_op,
        "recommended_count": len(recommended),
        "recommended": [
            {
                "node_canonical_key": item.cell.node_canonical_key,
                "identity_id": item.cell.identity_id,
                "family_id": item.cell.family_id,
                "state": item.cell.state.value,
                "total_score": item.score.total_score,
            }
            for item in recommended
        ],
    }
    return AuditEventRecord(
        audit_event_id=audit_id,
        occurred_at=occurred_at,
        actor_id=SCHEDULER_ACTOR_ID,
        actor_type=ActorType.CONTROL_PLANE.value,
        event_type=HUNT_SCHEDULE_RECOMMENDED,
        subject_type="research_run",
        subject_id=research_run_id,
        correlation_id=research_run_id,
        payload=payload,
    )
