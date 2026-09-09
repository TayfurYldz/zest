"""Deterministic hypothesis generation from AttackSurfaceGraph + HunterFamily registry.

No model call in the default path. Optional LLM enrichment is budget-gated
through the existing price-class routing policy, not implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, HypothesisRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.coverage.types import CoverageState
from zest.research.discovery.graph import AttackSurfaceGraph
from zest.research.scheduler.types import ScoredCell
from zest.research.selection import (
    HunterFamilyView,
    claim_from_template,
    families_for_node,
)


HUNT_HYPOTHESIS_GENERATED = "HUNT_HYPOTHESIS_GENERATED"
IDENTITY_EXPANSION_CAPPED = "IDENTITY_EXPANSION_CAPPED"
HYPOTHESIS_GENERATOR_ACTOR_ID = "control-plane:hunt-generator"

# D8: deterministic noise discipline. Not a capability limit; remaining
# identities remain UNTESTED in the coverage matrix and are addressed in later
# cycles as the graph/scheduling evolves.
MAX_IDENTITIES_PER_NODE = 8
ANONYMOUS_IDENTITY = "ANONYMOUS"


@dataclass(frozen=True)
class GenerateHuntHypothesesCommand:
    research_run_id: str
    graph: AttackSurfaceGraph
    registry: tuple[HunterFamilyView, ...] | None = None
    # Optional schedule from RunHuntScheduler. When supplied, hypotheses are
    # generated only for the scheduled cells instead of scanning the full graph.
    schedule: tuple[ScoredCell, ...] | None = None


@dataclass(frozen=True)
class GenerateHuntHypothesesResult:
    research_run_id: str
    hypothesis_ids: tuple[str, ...]
    generated_count: int
    # (hypothesis_id, node_id, family_id, identity_id) for each generated hypothesis.
    hypothesis_sources: tuple[tuple[str, str, str, str], ...]


class GenerateHuntHypotheses:
    """Produce Hypothesis records from graph nodes and the data-driven family registry.

    Each hypothesis is bound to a specific identity (SD-G9). Identity-less nodes
    are emitted as ANONYMOUS. Per-node identity expansion is capped to prevent
    combinatorial noise (D8).

    This is deterministic, static logic: it does not call a model by default and
    does not create FindingProposals or Candidates. Each hypothesis is an
    untrusted claim ready for V1/V2/V3 validation tiers.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: GenerateHuntHypothesesCommand) -> GenerateHuntHypothesesResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            registry = command.registry
            if registry is None:
                registry = tuple(_to_view(item) for item in uow.hunter_families.list_enabled())
            hypothesis_ids: list[str] = []
            hypothesis_sources: list[tuple[str, str, str, str]] = []

            if command.schedule is not None:
                self._generate_from_schedule(
                    command, registry, uow, now, hypothesis_ids, hypothesis_sources
                )
            else:
                self._generate_from_graph(
                    command, registry, uow, now, hypothesis_ids, hypothesis_sources
                )

            uow.commit()
        return GenerateHuntHypothesesResult(
            research_run_id=command.research_run_id,
            hypothesis_ids=tuple(hypothesis_ids),
            generated_count=len(hypothesis_ids),
            hypothesis_sources=tuple(hypothesis_sources),
        )

    def _generate_from_graph(
        self,
        command: GenerateHuntHypothesesCommand,
        registry: tuple[HunterFamilyView, ...],
        uow: UnitOfWork,
        now,
        hypothesis_ids: list[str],
        hypothesis_sources: list[tuple[str, str, str, str]],
    ) -> None:
        for node in command.graph.nodes:
            identities = _identity_ids_for_node(node)
            capped = len(identities) > MAX_IDENTITIES_PER_NODE
            if capped:
                identities = identities[:MAX_IDENTITIES_PER_NODE]
            for family in families_for_node(node, command.graph, registry):
                for identity_id in identities:
                    self._emit_hypothesis(
                        command, node, family, identity_id, uow, now,
                        hypothesis_ids, hypothesis_sources,
                    )
                if capped:
                    uow.audit_events.insert(
                        _capped_audit(
                            audit_id=new_opaque_id(),
                            occurred_at=now,
                            research_run_id=command.research_run_id,
                            family_id=family.family_id,
                            node_canonical_key=node.canonical_key,
                            total_identities=len(_identity_ids_for_node(node)),
                            max_identities=MAX_IDENTITIES_PER_NODE,
                        )
                    )

    def _generate_from_schedule(
        self,
        command: GenerateHuntHypothesesCommand,
        registry: tuple[HunterFamilyView, ...],
        uow: UnitOfWork,
        now,
        hypothesis_ids: list[str],
        hypothesis_sources: list[tuple[str, str, str, str]],
    ) -> None:
        node_by_key = {node.canonical_key: node for node in command.graph.nodes}
        family_by_id = {family.family_id: family for family in registry}
        for scored in command.schedule:
            cell = scored.cell
            if cell.state in {CoverageState.COVERED, CoverageState.NOT_APPLICABLE}:
                continue
            node = node_by_key.get(cell.node_canonical_key)
            family = family_by_id.get(cell.family_id)
            if node is None or family is None:
                continue
            identity_id = cell.identity_id
            self._emit_hypothesis(
                command, node, family, identity_id, uow, now,
                hypothesis_ids, hypothesis_sources,
            )

    def _emit_hypothesis(
        self,
        command: GenerateHuntHypothesesCommand,
        node,
        family: HunterFamilyView,
        identity_id: str,
        uow: UnitOfWork,
        now,
        hypothesis_ids: list[str],
        hypothesis_sources: list[tuple[str, str, str, str]],
    ) -> None:
        claim = claim_from_template(
            node,
            family,
            extra_context={"identity_id": identity_id},
        )
        for existing in uow.hypotheses.list_for_research_run(command.research_run_id):
            if (
                existing.origin_reference == family.family_id
                and existing.identity_id == identity_id
                and existing.claim == claim
            ):
                return
        hypothesis_id = new_opaque_id()
        hypothesis_ids.append(hypothesis_id)
        hypothesis_sources.append(
            (hypothesis_id, node.node_id, family.family_id, identity_id)
        )
        uow.hypotheses.insert(
            HypothesisRecord(
                hypothesis_id=hypothesis_id,
                research_run_id=command.research_run_id,
                claim=claim,
                origin_reference=family.family_id,
                identity_id=identity_id,
                created_at=now,
            )
        )
        uow.audit_events.insert(
            _generation_audit(
                audit_id=new_opaque_id(),
                occurred_at=now,
                research_run_id=command.research_run_id,
                hypothesis_id=hypothesis_id,
                family_id=family.family_id,
                node_canonical_key=node.canonical_key,
                identity_id=identity_id,
                claim=claim,
                validation_tier=family.validation_tier,
            )
        )


def _identity_ids_for_node(node) -> tuple[str, ...]:
    if node.identity_ids:
        return node.identity_ids
    return (ANONYMOUS_IDENTITY,)


def _to_view(record) -> HunterFamilyView:
    """Map a HunterFamilyRecord to the research-layer view."""
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


def _generation_audit(
    *,
    audit_id: str,
    occurred_at,
    research_run_id: str,
    hypothesis_id: str,
    family_id: str,
    node_canonical_key: str,
    identity_id: str,
    claim: str,
    validation_tier: str,
) -> AuditEventRecord:
    return AuditEventRecord(
        audit_event_id=audit_id,
        occurred_at=occurred_at,
        actor_id=HYPOTHESIS_GENERATOR_ACTOR_ID,
        actor_type=ActorType.CONTROL_PLANE.value,
        event_type=HUNT_HYPOTHESIS_GENERATED,
        subject_type="hypothesis",
        subject_id=hypothesis_id,
        correlation_id=research_run_id,
        payload={
            "research_run_id": research_run_id,
            "family_id": family_id,
            "node_canonical_key": node_canonical_key,
            "identity_id": identity_id,
            "claim": claim,
            "validation_tier": validation_tier,
            "source": "deterministic_template",
        },
    )


def _capped_audit(
    *,
    audit_id: str,
    occurred_at,
    research_run_id: str,
    family_id: str,
    node_canonical_key: str,
    total_identities: int,
    max_identities: int,
) -> AuditEventRecord:
    return AuditEventRecord(
        audit_event_id=audit_id,
        occurred_at=occurred_at,
        actor_id=HYPOTHESIS_GENERATOR_ACTOR_ID,
        actor_type=ActorType.CONTROL_PLANE.value,
        event_type=IDENTITY_EXPANSION_CAPPED,
        subject_type="hunt_generation",
        subject_id=node_canonical_key,
        correlation_id=research_run_id,
        payload={
            "research_run_id": research_run_id,
            "family_id": family_id,
            "node_canonical_key": node_canonical_key,
            "total_identities": total_identities,
            "max_identities": max_identities,
            "reason_code": "IDENTITY_EXPANSION_CAPPED",
        },
    )