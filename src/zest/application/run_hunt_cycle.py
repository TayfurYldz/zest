"""Single hunt cycle: graph → hypotheses → V1/V2/V3 tiers → V3 queue.

A cycle is intentionally stateless between invocations; all durable state lives
in the append-only ledger. No model call by default.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.generate_hunt_hypotheses import (
    GenerateHuntHypotheses,
    GenerateHuntHypothesesCommand,
)
from zest.application.hunt_validation import (
    ValidateHuntTiers,
    ValidateHuntTiersCommand,
)
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.records import HunterFamilyRecord
from zest.research.discovery.graph import AttackSurfaceGraph
from zest.research.scheduler.types import ScoredCell
from zest.research.selection import HunterFamilyView


@dataclass(frozen=True)
class RunHuntCycleCommand:
    research_run_id: str
    graph: AttackSurfaceGraph
    registry: tuple[HunterFamilyView, ...] | None = None
    # Optional ranked schedule from RunHuntScheduler. When supplied, the cycle
    # generates hypotheses only for the scheduled cells instead of scanning the
    # full graph. The V3 approval gate remains unchanged.
    schedule: tuple[ScoredCell, ...] | None = None


@dataclass(frozen=True)
class RunHuntCycleResult:
    research_run_id: str
    generated: int
    v1_passed: int
    v2_passed: int
    v3_queued: int
    queue_ids: tuple[str, ...]
    no_op: bool


class RunHuntCycle:
    """Execute one hunt cycle against the attack-surface graph and family registry."""

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._generator = GenerateHuntHypotheses(uow_factory, clock=clock)
        self._validator = ValidateHuntTiers(uow_factory, clock=clock)

    def execute(self, command: RunHuntCycleCommand) -> RunHuntCycleResult:
        # Load registry from the append-only data store when not supplied (tests may inject).
        registry = command.registry
        if registry is None:
            with self._uow_factory.open() as uow:
                registry = tuple(_to_view(item) for item in uow.hunter_families.list_enabled())

        generated = self._generator.execute(
            GenerateHuntHypothesesCommand(
                research_run_id=command.research_run_id,
                graph=command.graph,
                registry=registry,
                schedule=command.schedule,
            )
        )

        v1_count = 0
        v2_count = 0
        v3_count = 0
        queue_ids: list[str] = []

        for hypothesis_id, node_id, family_id, _identity_id in generated.hypothesis_sources:
            family = next((item for item in registry if item.family_id == family_id), None)
            node = next((item for item in command.graph.nodes if item.node_id == node_id), None)
            if family is None or node is None:
                continue

            result = self._validator.execute(
                ValidateHuntTiersCommand(
                    research_run_id=command.research_run_id,
                    hypothesis_id=hypothesis_id,
                    family=family,
                    node_id=node.node_id,
                    graph=command.graph,
                )
            )
            if result.v1_passed:
                v1_count += 1
            if result.v2_passed:
                v2_count += 1
            if result.v3_queued and result.queue_id is not None:
                v3_count += 1
                queue_ids.append(result.queue_id)

        return RunHuntCycleResult(
            research_run_id=command.research_run_id,
            generated=generated.generated_count,
            v1_passed=v1_count,
            v2_passed=v2_count,
            v3_queued=v3_count,
            queue_ids=tuple(queue_ids),
            no_op=generated.generated_count == 0,
        )


def _to_view(record: HunterFamilyRecord) -> HunterFamilyView:
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