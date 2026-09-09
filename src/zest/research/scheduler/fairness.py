"""Deterministic multi-engine ranking. Not authorization and not a score oracle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from zest.research.exploration import (
    EXPLORATION_STRATEGY_VERSION,
    OpportunityKind,
    ResearchOpportunity,
)

_ORDINAL = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Other-kind SELECT cycles an eligible kind may wait before starvation boost.
KIND_STARVATION_BOUND = 4

PLUMBING_KINDS = frozenset(
    {
        OpportunityKind.HYPOTHESIS_FOLLOWUP,
        OpportunityKind.DIFFERENTIAL_FOLLOWUP,
        OpportunityKind.INVARIANT_CHALLENGE,
        OpportunityKind.CHAIN_EXTENSION,
        OpportunityKind.NEGATIVE_KNOWLEDGE_REVISIT,
        OpportunityKind.CONTROL_EXPERIMENT,
    }
)

KIND_ENGINE_LABEL = {
    OpportunityKind.HUNTER_COVERAGE_GAP: "HUNTER",
    OpportunityKind.MUTATION_VARIANT: "MUTATION",
    OpportunityKind.CHAIN: "CHAIN",
    OpportunityKind.CHAIN_EXTENSION: "CHAIN",
    OpportunityKind.OAST_INTERACTION: "OAST",
    OpportunityKind.PROTOCOL_STEP: "PROTOCOL",
    OpportunityKind.AUTHENTICATION: "AUTHENTICATION",
    OpportunityKind.AUTHORIZATION_DIFFERENTIAL: "AUTHORIZATION",
    OpportunityKind.WORKFLOW_STATE_TRANSITION: "WORKFLOW",
    OpportunityKind.SURFACE_DISCOVERY: "DISCOVERY",
}


@dataclass(frozen=True)
class DurableSelectEvent:
    """One durable SELECT used to reconstruct starvation age. Not process memory."""

    kind: str
    created_at: datetime
    selection_id: str


@dataclass(frozen=True)
class FairnessCandidateAudit:
    opportunity_id: str
    candidate_kind: str
    engine: str
    eligible: bool
    ineligible_reason_codes: tuple[str, ...]
    rank_before_fairness: int | None
    rank_after_fairness: int | None
    fairness_boost_applied: bool
    starvation_age: int
    pending_cycles: int


@dataclass(frozen=True)
class FairnessCycleAudit:
    last_selected_kind: str | None
    starvation_bound: int
    candidates: tuple[FairnessCandidateAudit, ...]
    selected_opportunity_id: str | None
    selection_reason: tuple[str, ...]
    why_selected: str


def is_plumbing_opportunity(opportunity: ResearchOpportunity) -> bool:
    """Generic diagnostic.echo / control work. Available, never first-claim."""

    if opportunity.strategy_version == EXPLORATION_STRATEGY_VERSION:
        return True
    return opportunity.opportunity_kind in PLUMBING_KINDS


def engine_label(kind: OpportunityKind | str) -> str:
    if isinstance(kind, str):
        try:
            kind = OpportunityKind(kind)
        except ValueError:
            return kind
    return KIND_ENGINE_LABEL.get(kind, kind.value)


def kind_starvation_ages(
    *,
    pending_kind_first_seen: Mapping[str, datetime],
    select_events: tuple[DurableSelectEvent, ...],
) -> dict[str, int]:
    """Count other-kind SELECTs while a kind has been pending eligible.

    Age starts when the kind first became pending, not at run start.
    After the kind itself is SELECTed, age counts only later other-kind SELECTs.
    """

    events = tuple(
        sorted(select_events, key=lambda item: (item.created_at, item.selection_id))
    )
    last_index_by_kind: dict[str, int] = {}
    for index, event in enumerate(events):
        last_index_by_kind[event.kind] = index
    ages: dict[str, int] = {}
    for kind, first_seen in pending_kind_first_seen.items():
        last_index = last_index_by_kind.get(kind)
        if last_index is None:
            ages[kind] = sum(
                1 for event in events if event.kind != kind and event.created_at >= first_seen
            )
        else:
            ages[kind] = sum(
                1
                for index, event in enumerate(events)
                if index > last_index and event.kind != kind
            )
    return ages


def fairness_sort_key(
    opportunity: ResearchOpportunity,
    *,
    recently_selected_kinds: tuple[str, ...] = (),
    kind_starvation_ages: Mapping[str, int] | None = None,
    starvation_bound: int = KIND_STARVATION_BOUND,
) -> tuple:
    ages = kind_starvation_ages or {}
    age = int(ages.get(opportunity.opportunity_kind.value, 0))
    starved = age >= starvation_bound
    plumbing = 1 if is_plumbing_opportunity(opportunity) else 0
    recent_window = recently_selected_kinds[-4:]
    same_engine_recent = 1 if opportunity.opportunity_kind.value in recent_window else 0
    information = _ORDINAL[opportunity.dimensions.expected_information_value.value]
    uncertainty = _ORDINAL[opportunity.dimensions.unresolved_uncertainty.value]
    coverage_debt = _ORDINAL[opportunity.dimensions.evidence_coverage.value]
    chain = _ORDINAL[opportunity.dimensions.chain_potential.value]
    cost = _ORDINAL[opportunity.dimensions.execution_cost.value]
    return (
        0 if starved else 1,
        -age if starved else 0,
        plumbing,
        same_engine_recent,
        information,
        uncertainty,
        coverage_debt,
        chain,
        cost,
        opportunity.structural_identity,
    )


def ranked_opportunities(
    opportunities: tuple[ResearchOpportunity, ...],
    *,
    recently_selected_kinds: tuple[str, ...] = (),
    kind_starvation_ages: Mapping[str, int] | None = None,
    starvation_bound: int = KIND_STARVATION_BOUND,
) -> tuple[ResearchOpportunity, ...]:
    return tuple(
        sorted(
            opportunities,
            key=lambda item: fairness_sort_key(
                item,
                recently_selected_kinds=recently_selected_kinds,
                kind_starvation_ages=kind_starvation_ages,
                starvation_bound=starvation_bound,
            ),
        )
    )


def build_fairness_cycle_audit(
    *,
    eligible: tuple[ResearchOpportunity, ...],
    ineligible: tuple[tuple[ResearchOpportunity, tuple[str, ...]], ...],
    recently_selected_kinds: tuple[str, ...],
    ages: Mapping[str, int],
    selected_opportunity_id: str | None,
    selection_reason: tuple[str, ...],
    starvation_bound: int = KIND_STARVATION_BOUND,
) -> FairnessCycleAudit:
    before = ranked_opportunities(
        eligible,
        recently_selected_kinds=recently_selected_kinds,
        kind_starvation_ages={},
        starvation_bound=starvation_bound,
    )
    after = ranked_opportunities(
        eligible,
        recently_selected_kinds=recently_selected_kinds,
        kind_starvation_ages=ages,
        starvation_bound=starvation_bound,
    )
    rank_before = {item.opportunity_id: index + 1 for index, item in enumerate(before)}
    rank_after = {item.opportunity_id: index + 1 for index, item in enumerate(after)}
    candidates: list[FairnessCandidateAudit] = []
    for opportunity in eligible:
        kind = opportunity.opportunity_kind.value
        age = int(ages.get(kind, 0))
        before_rank = rank_before.get(opportunity.opportunity_id)
        after_rank = rank_after.get(opportunity.opportunity_id)
        boosted = (
            before_rank is not None
            and after_rank is not None
            and after_rank < before_rank
        )
        candidates.append(
            FairnessCandidateAudit(
                opportunity_id=opportunity.opportunity_id,
                candidate_kind=kind,
                engine=engine_label(opportunity.opportunity_kind),
                eligible=True,
                ineligible_reason_codes=(),
                rank_before_fairness=before_rank,
                rank_after_fairness=after_rank,
                fairness_boost_applied=boosted,
                starvation_age=age,
                pending_cycles=age,
            )
        )
    for opportunity, codes in ineligible:
        kind = opportunity.opportunity_kind.value
        candidates.append(
            FairnessCandidateAudit(
                opportunity_id=opportunity.opportunity_id,
                candidate_kind=kind,
                engine=engine_label(opportunity.opportunity_kind),
                eligible=False,
                ineligible_reason_codes=codes,
                rank_before_fairness=None,
                rank_after_fairness=None,
                fairness_boost_applied=False,
                starvation_age=int(ages.get(kind, 0)),
                pending_cycles=int(ages.get(kind, 0)),
            )
        )
    why = "NO_SELECTION_THIS_TICK"
    if selected_opportunity_id:
        selected_audit = next(
            (
                item
                for item in candidates
                if item.opportunity_id == selected_opportunity_id
            ),
            None,
        )
        if selected_audit is not None and selected_audit.fairness_boost_applied:
            why = "KIND_STARVATION_PREVENTION"
        elif "KIND_STARVATION_BOOST" in selection_reason:
            why = "KIND_STARVATION_PREVENTION"
        else:
            why = "INFORMATION_VALUE_RANK"
    last_kind = recently_selected_kinds[-1] if recently_selected_kinds else None
    return FairnessCycleAudit(
        last_selected_kind=last_kind,
        starvation_bound=starvation_bound,
        candidates=tuple(candidates),
        selected_opportunity_id=selected_opportunity_id,
        selection_reason=selection_reason,
        why_selected=why,
    )
