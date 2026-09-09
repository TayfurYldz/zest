"""Typed research work source adapters. Not a second scheduler and not Core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from zest.application.identity import new_opaque_id
from zest.data.records import OpportunitySelectionCandidateRecord
from zest.research.discovery.frontier import FrontierEventKind
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)
from zest.research.types import ResearchInputError

DISCOVERY_HANDOFF_SOURCE_SYSTEM = "DISCOVERY_HANDOFF"
DISCOVERY_HANDOFF_STRATEGY_VERSION = "discovery_handoff.v1"


@dataclass(frozen=True)
class HarvestResult:
    source_id: str
    created: int
    skipped_duplicate: int


class ResearchWorkSource(Protocol):
    source_id: str

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        """Materialize durable candidates. Does not select or execute."""


class DiscoveryHandoffWorkSource:
    """Consume DEFERRED_TO_RESEARCH frontier events into the shared candidate pool."""

    source_id = DISCOVERY_HANDOFF_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        seen = {
            item.structural_identity
            for item in uow.opportunity_selection_candidates.list_for_research_run(
                research_run_id
            )
        } | {
            item.structural_identity
            for item in uow.research_opportunities.list_for_research_run(research_run_id)
        }
        created = 0
        skipped_duplicate = 0
        for frontier in uow.frontier_items.list_for_research_run(research_run_id):
            events = uow.frontier_events.list_for_frontier(frontier.frontier_id)
            if not events:
                continue
            latest = max(events, key=lambda item: item.sequence)
            if latest.event_kind != FrontierEventKind.DEFERRED_TO_RESEARCH.value:
                continue
            record = _candidate_from_frontier(frontier, now=now)
            if record.structural_identity in seen:
                skipped_duplicate += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(
            source_id=self.source_id,
            created=created,
            skipped_duplicate=skipped_duplicate,
        )


def _candidate_from_frontier(frontier, *, now: datetime) -> OpportunitySelectionCandidateRecord:
    if not isinstance(frontier.frontier_id, str) or not frontier.frontier_id.strip():
        raise ResearchInputError("frontier_id is required")
    proposed_direction = (
        f"Execute discovery-handed-off work {frontier.goal_kind} via "
        f"{frontier.proposed_capability} at side-effect {frontier.expected_side_effect}."
    )
    context_signature = f"discovery_handoff:{frontier.frontier_id}"
    identity = opportunity_structural_identity(
        kind=OpportunityKind.DISCOVERY_HANDOFF,
        source_refs=(frontier.frontier_id,),
        context_signature=context_signature,
        proposed_direction=proposed_direction,
    )
    se = frontier.expected_side_effect
    information = OrdinalLevel.HIGH if se >= 2 else OrdinalLevel.MEDIUM
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=frontier.research_run_id,
        source_system=DISCOVERY_HANDOFF_SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.DISCOVERY_HANDOFF.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(frontier.frontier_id,),
        proposed_direction=proposed_direction,
        unresolved_question=(
            "What in-scope information does this handed-off discovery unit produce?"
        ),
        expected_information_value_description=(
            f"capability={frontier.proposed_capability}; se={se}; "
            f"goal={frontier.goal_kind}"
        ),
        assumptions=(
            "discovery_handoff is not authorization",
            f"required_capability:{frontier.proposed_capability}",
            f"side_effect_class:{se}",
            f"owner:RESEARCH",
        ),
        dimensions=OpportunityDimensions(
            expected_information_value=information,
            security_relevance_potential=OrdinalLevel.MEDIUM if se >= 2 else OrdinalLevel.LOW,
            novelty_composition=OrdinalLevel.MEDIUM,
            unresolved_uncertainty=OrdinalLevel.HIGH,
            chain_potential=OrdinalLevel.MEDIUM if se >= 2 else OrdinalLevel.LOW,
            evidence_coverage=OrdinalLevel.LOW,
            execution_cost=OrdinalLevel.LOW,
            side_effect_requirement=se,
            duplicate_risk=OrdinalLevel.LOW,
            previous_failed_attempts=0,
        ).to_mapping(),
        context_signature=context_signature,
        structural_identity=identity,
        strategy_version=DISCOVERY_HANDOFF_STRATEGY_VERSION,
        created_at=now,
    )


from zest.application.identity_auth_workflow_sources import (
    AuthenticationWorkSource,
    AuthorizationWorkSource,
    WorkflowWorkSource,
)


def default_research_work_sources() -> tuple[ResearchWorkSource, ...]:
    from zest.application.mutation_protocol_sources import (
        MutationWorkSource,
        ProtocolWorkSource,
    )
    from zest.application.oast_source import OastWorkSource
    from zest.application.dic_sources import (
        ChainWorkSource,
        DifferentialWorkSource,
        InvariantWorkSource,
    )

    return (
        DiscoveryHandoffWorkSource(),
        AuthenticationWorkSource(),
        AuthorizationWorkSource(),
        WorkflowWorkSource(),
        MutationWorkSource(),
        ProtocolWorkSource(),
        OastWorkSource(),
        DifferentialWorkSource(),
        InvariantWorkSource(),
        ChainWorkSource(),
    )
