"""Draft registry-external exploratory hypotheses. Does not write HunterFamily."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, HypothesisRecord
from zest.research.exploratory import (
    ExploratoryHypothesisDraft,
    ExploratorySignal,
    ExploratorySignalKind,
    draft_registry_external_hypothesis,
)
from zest.research.selection import HunterFamilyView


@dataclass(frozen=True)
class ExploratorySignalInput:
    signal_id: str
    kind: str
    description: str
    source_refs: tuple[str, ...]
    target_node_kind: str
    attributes: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DraftExploratoryHypothesisCommand:
    research_run_id: str
    proposed_family_name: str
    proposed_family_rationale: str
    signals: tuple[ExploratorySignalInput, ...]
    correlation_id: str
    model_claimed_novelty: str | None = None


@dataclass(frozen=True)
class DraftExploratoryHypothesisResult:
    draft: ExploratoryHypothesisDraft
    hypothesis_id: str
    audit_event_id: str


class DraftExploratoryHypothesis:
    """Persist a HYPOTHESIZED draft and approval-required audit marker."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(
        self, command: DraftExploratoryHypothesisCommand
    ) -> DraftExploratoryHypothesisResult:
        now = self._clock.now()
        draft_id = new_opaque_id()
        hypothesis_id = new_opaque_id()
        audit_event_id = new_opaque_id()

        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")

            registry = tuple(_family_view(record) for record in uow.hunter_families.list_enabled())
            signals = tuple(_signal(command.research_run_id, item) for item in command.signals)
            draft = draft_registry_external_hypothesis(
                draft_id=draft_id,
                research_run_id=command.research_run_id,
                proposed_family_name=command.proposed_family_name,
                proposed_family_rationale=command.proposed_family_rationale,
                signals=signals,
                registry=registry,
                model_claimed_novelty=command.model_claimed_novelty,
            )

            uow.hypotheses.insert(
                HypothesisRecord(
                    hypothesis_id=hypothesis_id,
                    research_run_id=command.research_run_id,
                    claim=draft.hypothesis_claim,
                    created_at=now,
                    origin_reference=audit_event_id,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=audit_event_id,
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="EXPLORATORY_HYPOTHESIS_DRAFTED",
                    subject_type="exploratory_family_draft",
                    subject_id=draft.draft_id,
                    payload=draft.to_audit_payload(hypothesis_id=hypothesis_id),
                    correlation_id=command.correlation_id,
                )
            )
            uow.commit()
        return DraftExploratoryHypothesisResult(
            draft=draft,
            hypothesis_id=hypothesis_id,
            audit_event_id=audit_event_id,
        )


def _signal(research_run_id: str, item: ExploratorySignalInput) -> ExploratorySignal:
    return ExploratorySignal(
        signal_id=item.signal_id,
        research_run_id=research_run_id,
        kind=ExploratorySignalKind(item.kind),
        description=item.description,
        source_refs=item.source_refs,
        target_node_kind=item.target_node_kind,
        attributes=item.attributes,
    )


def _family_view(record) -> HunterFamilyView:
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
