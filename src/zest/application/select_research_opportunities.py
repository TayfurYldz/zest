"""Select bounded diagnostic research opportunities. Does not dispatch a Worker."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.produce_hunter_coverage_work import ProduceHunterCoverageWork
from zest.application.registry_external_anomaly_source import (
    admit_registry_external_anomaly_candidates,
)
from zest.application.research_work_sources import default_research_work_sources
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    AuditEventRecord,
    OpportunitySelectionCandidateRecord,
    ResearchOpportunityRecord,
    ResearchSelectionRecord,
)
from zest.research.exploration import (
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
from zest.research.scheduler.fairness import (
    DurableSelectEvent,
    build_fairness_cycle_audit,
    engine_label,
    kind_starvation_ages,
)

# Only outcomes that will not change on a later cycle (the candidate row's own
# content is fixed) retire a candidate. Capacity/context-dependent outcomes
# (DEFER, BLOCKED_BUDGET, SKIP_LOW_INFORMATION, NEEDS_MORE_CONTEXT) leave it
# PENDING so a still-relevant Hunter/Coverage gap gets reconsidered on a later
# cycle instead of being silently discarded because this cycle's budget/
# negative-knowledge context happened not to select it.
_SURFACE_DISCOVERY_HYPOTHESIS_ORIGIN = "surface-discovery-v1"
_RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX = "research-work-fabric.v1:"


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
        ProduceHunterCoverageWork(self._uow_factory, clock=self._clock).execute(
            command.research_run_id
        )
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            differentials = [
                item
                for item in uow.differential_observations.list_for_research_run(
                    command.research_run_id
                )
                if item.strategy_version.startswith("differential.diagnostic")
            ]
            invariants = [
                item
                for item in uow.invariant_hypotheses.list_for_research_run(command.research_run_id)
                if item.strategy_version.startswith("invariant.diagnostic")
            ]
            chains = [
                item
                for item in uow.chain_hypotheses.list_for_research_run(command.research_run_id)
                if item.strategy_version.startswith("chain.diagnostic")
            ]
            changes = uow.change_events.list_for_research_run(command.research_run_id)
            hypotheses = uow.hypotheses.list_for_research_run(command.research_run_id)
            experiments = uow.experiments.list_for_research_run(
                command.research_run_id
            )
            worker_results = uow.worker_results.list_for_research_run(
                command.research_run_id
            )
            observations = uow.observations.list_for_research_run(
                command.research_run_id
            )

            # Observation provenance is:
            # Observation.worker_result_id
            #   -> WorkerResult.experiment_id
            #   -> Experiment.hypothesis_id.
            # Observation itself deliberately does not duplicate experiment_id.
            observed_worker_result_ids = frozenset(
                item.worker_result_id for item in observations
            )
            observation_backed_experiment_ids = frozenset(
                item.experiment_id
                for item in worker_results
                if item.worker_result_id in observed_worker_result_ids
            )

            successful_observation_hypothesis_ids = frozenset(
                item.hypothesis_id
                for item in experiments
                if item.execution_state == "EXECUTION_SUCCEEDED"
                and item.experiment_id in observation_backed_experiment_ids
            )

            surface_discovery_hypothesis_ids = frozenset(
                item.hypothesis_id
                for item in hypotheses
                if item.origin_reference
                == _SURFACE_DISCOVERY_HYPOTHESIS_ORIGIN
            )

            # BLOCKED remains truthful and experiment-local.
            #
            # Normal research hypotheses retain the existing fail-closed
            # hypothesis-level suppression.
            #
            # Surface discovery is special because one synthetic hypothesis
            # intentionally owns many sibling frontier experiments. A single
            # scope-blocked sibling must not erase independent successful,
            # observation-backed discovery work from the research-transition
            # pool.
            blocked_hypothesis_ids = frozenset(
                item.hypothesis_id
                for item in experiments
                if item.execution_state == "BLOCKED"
                and (
                    item.hypothesis_id
                    not in surface_discovery_hypothesis_ids
                    or item.hypothesis_id
                    not in successful_observation_hypothesis_ids
                )
            )
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
            for source in default_research_work_sources():
                source.harvest(
                    uow, research_run_id=command.research_run_id, now=now
                )
            recently_selected_kinds = _recent_selected_kinds(
                uow, command.research_run_id
            )
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
                    hypothesis_ids=tuple(
                        item.hypothesis_id
                        for item in hypotheses
                        if item.hypothesis_id not in blocked_hypothesis_ids
                        and not (item.origin_reference or "").startswith(
                            _RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX
                        )
                    ),
                    negative_knowledge=tuple(negatives),
                ),
                id_prefix=new_opaque_id(),
            )
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
            pool = generated + candidate_opportunities
            eligibility_blockers: dict[str, tuple[str, ...]] = {}
            pending_kind_first_seen = _pending_kind_first_seen(
                pending_candidates=pending_candidates,
                generated=generated,
                now=now,
                ineligible_ids=frozenset(eligibility_blockers),
            )
            ages = kind_starvation_ages(
                pending_kind_first_seen=pending_kind_first_seen,
                select_events=_durable_select_events(uow, command.research_run_id),
            )
            decisions = select_research_opportunities(
                pool,
                research_run_id=command.research_run_id,
                budget=command.budget,
                negative_knowledge=tuple(negatives),
                previously_selected_identities=previously,
                recently_selected_kinds=recently_selected_kinds,
                kind_starvation_ages=ages,
                eligibility_blockers=eligibility_blockers,
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
            selected_items = [item for item in decisions if item.selected]
            rejected = [
                {
                    "id": item.opportunity.opportunity_id,
                    "engine": engine_label(item.opportunity.opportunity_kind),
                    "reason": list(item.reason_codes),
                }
                for item in decisions
                if not item.selected
            ]
            eligible_pool = tuple(
                item
                for item in pool
                if item.opportunity_id not in eligibility_blockers
                and item.structural_identity not in previously
            )
            ineligible_pool = tuple(
                (item, eligibility_blockers[item.opportunity_id])
                for item in pool
                if item.opportunity_id in eligibility_blockers
            )
            fairness_audit = build_fairness_cycle_audit(
                eligible=eligible_pool,
                ineligible=ineligible_pool,
                recently_selected_kinds=recently_selected_kinds,
                ages=ages,
                selected_opportunity_id=(
                    selected_items[0].opportunity.opportunity_id if selected_items else None
                ),
                selection_reason=(
                    tuple(selected_items[0].reason_codes)
                    if selected_items
                    else ("NO_SELECTION_THIS_TICK",)
                ),
            )
            selected_candidate_audit = next(
                (
                    item
                    for item in fairness_audit.candidates
                    if item.opportunity_id == fairness_audit.selected_opportunity_id
                ),
                None,
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
                        "selected": len(selected_items),
                        "not_authorization": True,
                        "not_a_vulnerability": True,
                    },
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RESEARCH_SELECTION_TRACE",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "candidate_count": len(decisions),
                        "candidate_engines": sorted(
                            {
                                item.opportunity.opportunity_kind.value
                                for item in decisions
                            }
                        ),
                        "candidate_engine_labels": sorted(
                            {
                                engine_label(item.opportunity.opportunity_kind)
                                for item in decisions
                            }
                        ),
                        "candidate_kinds": sorted(
                            {
                                item.opportunity.opportunity_kind.value
                                for item in decisions
                            }
                        ),
                        "candidate_ids": [
                            item.opportunity.opportunity_id for item in decisions
                        ],
                        "fairness_candidates": [
                            {
                                "opportunity_id": item.opportunity_id,
                                "candidate_kind": item.candidate_kind,
                                "engine": item.engine,
                                "eligible": item.eligible,
                                "ineligible_reason_codes": list(
                                    item.ineligible_reason_codes
                                ),
                                "rank_before_fairness": item.rank_before_fairness,
                                "rank_after_fairness": item.rank_after_fairness,
                                "fairness_boost_applied": item.fairness_boost_applied,
                                "starvation_age": item.starvation_age,
                                "pending_cycles": item.pending_cycles,
                            }
                            for item in fairness_audit.candidates
                        ],
                        "last_selected_kind": fairness_audit.last_selected_kind,
                        "starvation_bound": fairness_audit.starvation_bound,
                        "fairness_boost_applied": (
                            False
                            if selected_candidate_audit is None
                            else selected_candidate_audit.fairness_boost_applied
                        ),
                        "starvation_age": (
                            None
                            if selected_candidate_audit is None
                            else selected_candidate_audit.starvation_age
                        ),
                        "pending_cycles": (
                            None
                            if selected_candidate_audit is None
                            else selected_candidate_audit.pending_cycles
                        ),
                        "rank_before_fairness": (
                            None
                            if selected_candidate_audit is None
                            else selected_candidate_audit.rank_before_fairness
                        ),
                        "rank_after_fairness": (
                            None
                            if selected_candidate_audit is None
                            else selected_candidate_audit.rank_after_fairness
                        ),
                        "rejected_candidates": rejected,
                        "selected_work_id": (
                            selected_items[0].opportunity.opportunity_id
                            if selected_items
                            else None
                        ),
                        "selected_engine": (
                            engine_label(selected_items[0].opportunity.opportunity_kind)
                            if selected_items
                            else None
                        ),
                        "candidate_kind": (
                            selected_items[0].opportunity.opportunity_kind.value
                            if selected_items
                            else None
                        ),
                        "selection_reason": (
                            list(selected_items[0].reason_codes)
                            if selected_items
                            else ["NO_SELECTION_THIS_TICK"]
                        ),
                        "why_selected": fairness_audit.why_selected,
                        "not_authorization": True,
                    },
                )
            )
            uow.commit()
        return SelectResearchOpportunitiesResult(decisions=decisions)


def _kind_for_selection(uow, item) -> str | None:
    opportunity = uow.research_opportunities.get(item.opportunity_id)
    if opportunity is not None:
        return opportunity.opportunity_kind
    candidate = uow.opportunity_selection_candidates.get(item.opportunity_id)
    if candidate is not None:
        return candidate.opportunity_kind
    return None


def _durable_select_events(uow, research_run_id: str) -> tuple[DurableSelectEvent, ...]:
    events: list[DurableSelectEvent] = []
    for item in uow.research_selections.list_for_research_run(research_run_id):
        if item.outcome != SelectionOutcome.SELECT.value:
            continue
        kind = _kind_for_selection(uow, item)
        if not kind:
            continue
        events.append(
            DurableSelectEvent(
                kind=kind,
                created_at=item.created_at,
                selection_id=item.selection_id,
            )
        )
    return tuple(events)


def _pending_kind_first_seen(
    *,
    pending_candidates,
    generated: tuple[ResearchOpportunity, ...],
    now,
    ineligible_ids: frozenset[str],
) -> dict[str, object]:
    first_seen: dict[str, object] = {}
    for record in pending_candidates:
        if record.candidate_id in ineligible_ids:
            continue
        kind = record.opportunity_kind
        created = record.created_at
        previous = first_seen.get(kind)
        if previous is None or created < previous:
            first_seen[kind] = created
    for opportunity in generated:
        if opportunity.opportunity_id in ineligible_ids:
            continue
        kind = opportunity.opportunity_kind.value
        first_seen.setdefault(kind, now)
    return first_seen


def _recent_selected_kinds(uow, research_run_id: str) -> tuple[str, ...]:
    events = sorted(
        _durable_select_events(uow, research_run_id),
        key=lambda item: (item.created_at, item.selection_id),
    )
    return tuple(item.kind for item in events)
