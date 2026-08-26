"""Deterministic HunterScore formula and scheduling order.

No LLM. No randomness. Every score is explainable by reference to ledger/graph data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.scheduler.types import (
    BudgetView,
    FamilyStats,
    HunterScore,
    HunterScoreInput,
    NodeFreshness,
    ScoredCell,
)
from zest.research.types import ResearchInputError


# K1: formula coefficients are hard-coded data. Any change is visible here.
STATE_WEIGHT = {
    CoverageState.UNTESTED: 50,
    CoverageState.HYPOTHESIZED: 40,
    CoverageState.V1_PASSED: 30,
    CoverageState.V2_PASSED: 20,
    CoverageState.V3_QUEUED: 10,
    CoverageState.COVERED: 0,
    CoverageState.NOT_APPLICABLE: 0,
}

FAMILY_SUCCESS_MULTIPLIER = 5
FAMILY_SUCCESS_MAX_BONUS = 20
FAMILY_SUCCESS_MAX_PENALTY = -20
FAMILY_EXPLORATION_BONUS = 5
FRESHNESS_MAX_HOURS = 24
FRESHNESS_MULTIPLIER = 1

# When daily LLM budget is exhausted, V3-expensive cells are deprioritized
# and cheap-path (V1/V2-only) cells receive a small bump.
BUDGET_EXHAUSTED_V3_PENALTY = -20
BUDGET_EXHAUSTED_CHEAP_BONUS = 5

_V3_BOUND_STATES = {CoverageState.V2_PASSED, CoverageState.V3_QUEUED}
_CHEAP_STATES = {
    CoverageState.UNTESTED,
    CoverageState.HYPOTHESIZED,
    CoverageState.V1_PASSED,
}


def score_cell(
    cell: CoverageCell,
    *,
    family_stats: Mapping[str, FamilyStats],
    freshness_by_node: Mapping[str, NodeFreshness | datetime | None],
    budget_view: BudgetView,
    reference_time: datetime,
) -> HunterScore:
    """Compute a deterministic HunterScore for one coverage debt cell."""

    if not isinstance(cell, CoverageCell):
        raise ResearchInputError("cell must be a CoverageCell")

    state_weight = STATE_WEIGHT.get(cell.state, 0)

    if cell.state in {CoverageState.COVERED, CoverageState.NOT_APPLICABLE}:
        explanation = (
            f"state={cell.state.value} state_weight={state_weight}",
            "no_debt_cell success_bonus=0 exploration_bonus=0",
            "freshness_not_applied freshness_bonus=0",
            "budget_not_applied budget_bonus=0",
            "total_score=0",
        )
        return HunterScore(
            cell=cell,
            total_score=0,
            state_weight=state_weight,
            family_success_bonus=0,
            family_exploration_bonus=0,
            freshness_bonus=0,
            budget_suitability_bonus=0,
            explanation=explanation,
        )

    stats = family_stats.get(cell.family_id)
    if stats is None:
        family_success_bonus = 0
        family_exploration_bonus = FAMILY_EXPLORATION_BONUS
        family_explanation = "family_stats_missing family_low_history=True"
    else:
        raw_success_bonus = FAMILY_SUCCESS_MULTIPLIER * (
            stats.supported_count - stats.falsified_count
        )
        family_success_bonus = min(
            FAMILY_SUCCESS_MAX_BONUS,
            max(FAMILY_SUCCESS_MAX_PENALTY, raw_success_bonus),
        )
        history_count = stats.supported_count + stats.falsified_count
        family_exploration_bonus = (
            FAMILY_EXPLORATION_BONUS
            if history_count < 2 and stats.falsified_count == 0
            else 0
        )
        family_explanation = (
            f"family_supported={stats.supported_count} "
            f"family_falsified={stats.falsified_count} "
            f"raw_success_bonus={raw_success_bonus} "
            f"bounded_success_bonus={family_success_bonus} "
            f"family_low_history={history_count < 2}"
        )

    first_seen: datetime | None = None
    latest_activity: datetime | None = None
    freshness = freshness_by_node.get(cell.node_canonical_key)
    if isinstance(freshness, NodeFreshness):
        first_seen = freshness.first_seen_at
        latest_activity = freshness.latest_activity_at or freshness.first_seen_at
    else:
        latest_activity = freshness

    freshness_bonus = 0
    freshness_explanation = "freshness_unknown"
    if latest_activity is not None:
        age_seconds = (reference_time - latest_activity).total_seconds()
        if age_seconds < 0:
            age_seconds = 0
        age_hours = age_seconds / 3600.0
        freshness_bonus = max(0, int((FRESHNESS_MAX_HOURS - age_hours) * FRESHNESS_MULTIPLIER))
        if first_seen is not None:
            first_seen_age_seconds = (reference_time - first_seen).total_seconds()
            if first_seen_age_seconds < 0:
                first_seen_age_seconds = 0
            first_seen_age_hours = first_seen_age_seconds / 3600.0
            freshness_explanation = (
                f"first_seen_age_hours={first_seen_age_hours:.2f} "
                f"latest_activity_age_hours={age_hours:.2f}"
            )
        else:
            freshness_explanation = f"latest_activity_age_hours={age_hours:.2f}"

    budget_suitability_bonus = 0
    budget_explanation = "budget_available"
    if budget_view.is_exhausted:
        if cell.state in _V3_BOUND_STATES:
            budget_suitability_bonus = BUDGET_EXHAUSTED_V3_PENALTY
            budget_explanation = "budget_exhausted_v3_penalty"
        elif cell.state in _CHEAP_STATES:
            budget_suitability_bonus = BUDGET_EXHAUSTED_CHEAP_BONUS
            budget_explanation = "budget_exhausted_cheap_bonus"
        else:
            budget_explanation = "budget_exhausted_no_adjustment"

    total_score = (
        state_weight
        + family_success_bonus
        + family_exploration_bonus
        + freshness_bonus
        + budget_suitability_bonus
    )

    explanation = (
        f"state={cell.state.value} state_weight={state_weight}",
        (
            f"{family_explanation} success_bonus={family_success_bonus} "
            f"exploration_bonus={family_exploration_bonus}"
        ),
        f"{freshness_explanation} freshness_bonus={freshness_bonus}",
        f"{budget_explanation} budget_bonus={budget_suitability_bonus}",
        f"total_score={total_score}",
    )

    return HunterScore(
        cell=cell,
        total_score=total_score,
        state_weight=state_weight,
        family_success_bonus=family_success_bonus,
        family_exploration_bonus=family_exploration_bonus,
        freshness_bonus=freshness_bonus,
        budget_suitability_bonus=budget_suitability_bonus,
        explanation=explanation,
    )


def schedule(input_data: HunterScoreInput) -> tuple[ScoredCell, ...]:
    """Rank coverage-debt cells by HunterScore, highest first.

    COVERED and NOT_APPLICABLE cells are omitted (no debt). Ties break by
    canonical key, identity, then family id for determinism.
    """

    if not isinstance(input_data, HunterScoreInput):
        raise ResearchInputError("input_data must be a HunterScoreInput")

    family_stats = {item.family_id: item for item in input_data.family_stats}

    scored: list[ScoredCell] = []
    for cell in input_data.cells:
        if cell.state in {CoverageState.COVERED, CoverageState.NOT_APPLICABLE}:
            continue
        score = score_cell(
            cell,
            family_stats=family_stats,
            freshness_by_node=input_data.freshness_by_node,
            budget_view=input_data.budget_view,
            reference_time=input_data.reference_time,
        )
        scored.append(ScoredCell(cell=cell, score=score))

    # Descending score; tie-break by deterministic cell key.
    scored.sort(
        key=lambda item: (
            -item.score.total_score,
            item.cell.node_canonical_key,
            item.cell.identity_id,
            item.cell.family_id,
        )
    )
    return tuple(scored)
