"""Select bounded diagnostic research opportunities. Does not dispatch a Worker."""

from __future__ import annotations

from dataclasses import dataclass

from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.application.registry_external_anomaly_source import (
    admit_registry_external_anomaly_candidates,
)
from research_os.core.enums import ActorType
from research_os.data.errors import PersistenceConflictError
from research_os.data.records import (
    AuditEventRecord,
    OpportunitySelectionCandidateRecord,
    ResearchOpportunityRecord,
    ResearchSelectionRecord,
)
from research_os.research.exploration import (
    DiagnosticOpportunitySources,
    NegativeKnowledge,
    OpportunityMode,
    ResearchOpportunity,
    ResearchPolicyBudget,
    ResearchSelectionDecision,
    SelectionOutcome,
    dimensions_from_mapping,
    opportunity_structural_identity,
    OpportunityKind,
    propose_diagnostic_opportunities,
    select_research_opportunities,
)

# Only outcomes that will not change on a later cycle (the candidate row's own
# content is fixed) retire a candidate. Capacity/context-dependent outcomes
# (DEFER, BLOCKED_BUDGET, SKIP_LOW_INFORMATION, NEEDS_MORE_CONTEXT) leave it
# PENDING so a still-relevant Hunter/Coverage gap gets reconsidered on a later
# cycle instead of being silently discarded because this cycle's budget/
# negative-knowledge context happened not to select it.
_CANDIDATE_TERMINAL_OUTCOMES = {
    SelectionOutcome.SELECT: "ADMITTED",
    SelectionOutcome.SKIP_DUPLICATE: "NOT_ADMITTED",
    SelectionOutcome.BLOCKED_POLICY: "NOT_ADMITTED",
}


def _opportunity_from_candidate(
    record: OpportunitySelectionCandidateRecord,
) -> ResearchOpportunity:
    """Reconstruct the domain ResearchOpportunity a candidate row proposes.

    The candidate's own `candidate_id` is reused as the domain
    `opportunity_id` (and, if SELECTed, as the resulting canonical
    `ResearchOpportunityRecord.opportunity_id`) so a Hunter/Coverage-sourced
    opportunity has one stable identity end to end instead of a fresh one
    minted per cycle, unlike ephemeral diagnostic proposals.
    """

    return ResearchOpportunity(
        opportunity_id=record.candidate_id,
        research_run_id=record.research_run_id,
        opportunity_kind=OpportunityKind(record.opportunity_kind),
        mode=OpportunityMode(record.mode),
        source_refs=record.source_refs,
        proposed_direction=record.proposed_direction,
        unresolved_question=record.unresolved_question,
        expected_information_value_description=record.expected_information_value_description,
        assumptions=record.assumptions,
        dimensions=dimensions_from_mapping(record.dimensions),
        context_signature=record.context_signature,
        novelty_composition_marker=False,
        prior_attempt_refs=(),
        strategy_version=record.strategy_version,
        structural_identity=record.structural_identity,
    )


@dataclass(frozen=True)
class SelectResearchOpportunitiesCommand:
    research_run_id: str
    budget: ResearchPolicyBudget | None = None


@dataclass(frozen=True)
class SelectResearchOpportunitiesResult:
    decisions: tuple[ResearchSelectionDecision, ...]

    @property
    def selected(self) -> tuple[ResearchSelectionDecision, ...]:
        return tuple(item for item in self.decisions if item.selected)


class SelectResearchOpportunities:
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
        self, command: SelectResearchOpportunitiesCommand
    ) -> SelectResearchOpportunitiesResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            differentials = uow.differential_observations.list_for_research_run(
                command.research_run_id
            )
            invariants = uow.invariant_hypotheses.list_for_research_run(command.research_run_id)
            chains = uow.chain_hypotheses.list_for_research_run(command.research_run_id)
            changes = uow.change_events.list_for_research_run(command.research_run_id)
            hypotheses = uow.hypotheses.list_for_research_run(command.research_run_id)
            assessments = uow.hypothesis_assessments.list_for_research_run(
                command.research_run_id
            )
            negatives: list[NegativeKnowledge] = []
            followup_direction = (
                "Continue the existing diagnostic hypothesis with a control echo."
            )
            for assessment in assessments:
                if assessment.assessment_outcome != "CONTRADICTS_PREDICTION":
                    continue
                context = f"hypothesis:{assessment.hypothesis_id}"
                identity = opportunity_structural_identity(
                    kind=OpportunityKind.HYPOTHESIS_FOLLOWUP,
                    source_refs=(assessment.hypothesis_id,),
                    context_signature=context,
                    proposed_direction=followup_direction,
                )
                negatives.append(
                    NegativeKnowledge(
                        structural_identity=identity,
                        context_signature=context,
                        strategy_version="exploration.diagnostic.echo.v1",
                        assessment_ref=assessment.assessment_id,
                    )
                )
            previously = frozenset(
                item.structural_identity
                for item in uow.research_opportunities.list_for_research_run(
                    command.research_run_id
                )
            )
            now = self._clock.now()
            anomaly_result = admit_registry_external_anomaly_candidates(
                uow,
                research_run_id=command.research_run_id,
                now=now,
                actor_id=self._actor_id,
            )
            identity_diff_ids = frozenset(anomaly_result.identity_differential_ids)
            generated = propose_diagnostic_opportunities(
                command.research_run_id,
                DiagnosticOpportunitySources(
                    differential_ids=tuple(
                        item.differential_id
                        for item in differentials
                        if item.differential_id not in identity_diff_ids
                    ),
                    invariant_ids=tuple(item.invariant_id for item in invariants),
                    chain_ids=tuple(item.chain_id for item in chains),
                    change_event_ids=tuple(item.change_event_id for item in changes),
                    hypothesis_ids=tuple(item.hypothesis_id for item in hypotheses),
                    negative_knowledge=tuple(negatives),
                ),
                id_prefix=new_opaque_id(),
            )
            # MR-1 (Slice 3): additive union of Hunter/Coverage-sourced, still-
            # PENDING candidates alongside the freshly generated diagnostics.
            # `select_research_opportunities()` itself is completely
            # unmodified; diagnostics are listed first in the input tuple so
            # they retain first claim on shared budget slots exactly as
            # before this change, and candidates only fill remaining
            # capacity.
            pending_candidates = [
                item
                for item in uow.opportunity_selection_candidates.list_for_research_run(
                    command.research_run_id
                )
                if item.outcome == "PENDING"
            ]
            candidates_by_opportunity_id = {
                item.candidate_id: item for item in pending_candidates
            }
            candidate_opportunities = tuple(
                _opportunity_from_candidate(item) for item in pending_candidates
            )
            decisions = select_research_opportunities(
                generated + candidate_opportunities,
                research_run_id=command.research_run_id,
                budget=command.budget,
                negative_knowledge=tuple(negatives),
                previously_selected_identities=previously,
            )
            for decision in decisions:
                opportunity = decision.opportunity
                if decision.outcome is SelectionOutcome.SELECT:
                    try:
                        uow.research_opportunities.insert(
                            ResearchOpportunityRecord(
                                opportunity_id=opportunity.opportunity_id,
                                research_run_id=opportunity.research_run_id,
                                opportunity_kind=opportunity.opportunity_kind.value,
                                mode=opportunity.mode.value,
                                source_refs=opportunity.source_refs,
                                proposed_direction=opportunity.proposed_direction,
                                unresolved_question=opportunity.unresolved_question,
                                expected_information_value_description=(
                                    opportunity.expected_information_value_description
                                ),
                                assumptions=opportunity.assumptions,
                                dimensions=opportunity.dimensions.to_mapping(),
                                context_signature=opportunity.context_signature,
                                novelty_composition_marker=opportunity.novelty_composition_marker,
                                prior_attempt_refs=opportunity.prior_attempt_refs,
                                structural_identity=opportunity.structural_identity,
                                strategy_version=opportunity.strategy_version,
                                created_at=now,
                            )
                        )
                    except PersistenceConflictError:
                        existing = [
                            item
                            for item in uow.research_opportunities.list_for_research_run(
                                command.research_run_id
                            )
                            if item.structural_identity == opportunity.structural_identity
                        ]
                        if not existing:
                            raise
                uow.research_selections.insert(
                    ResearchSelectionRecord(
                        selection_id=new_opaque_id(),
                        research_run_id=command.research_run_id,
                        opportunity_id=opportunity.opportunity_id,
                        outcome=decision.outcome.value,
                        reason_codes=decision.reason_codes,
                        structural_identity=opportunity.structural_identity,
                        created_at=now,
                    )
                )
                candidate = candidates_by_opportunity_id.get(opportunity.opportunity_id)
                if candidate is not None:
                    terminal_outcome = _CANDIDATE_TERMINAL_OUTCOMES.get(decision.outcome)
                    if terminal_outcome is not None:
                        uow.opportunity_selection_candidates.mark_decided(
                            candidate.candidate_id,
                            outcome=terminal_outcome,
                            resulting_opportunity_id=(
                                opportunity.opportunity_id
                                if terminal_outcome == "ADMITTED"
                                else None
                            ),
                            decided_at=now,
                        )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RESEARCH_OPPORTUNITIES_SELECTED",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "selected": sum(1 for item in decisions if item.selected),
                        "not_authorization": True,
                        "not_a_vulnerability": True,
                    },
                )
            )
            uow.commit()
        return SelectResearchOpportunitiesResult(decisions=decisions)
