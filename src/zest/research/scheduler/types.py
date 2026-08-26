"""HunterScore scheduler domain types. Deterministic, explainable, LLM-free."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.types import ResearchInputError


@dataclass(frozen=True)
class FamilyStats:
    """Historical ledger summary for one hunter family.

    Counts are derived from append-only hypothesis assessments.
    """

    family_id: str
    supported_count: int
    falsified_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.family_id, str) or not self.family_id.strip():
            raise ResearchInputError("family_id must be a non-empty string")
        if not isinstance(self.supported_count, int) or isinstance(self.supported_count, bool) or self.supported_count < 0:
            raise ResearchInputError("supported_count must be a non-negative int")
        if not isinstance(self.falsified_count, int) or isinstance(self.falsified_count, bool) or self.falsified_count < 0:
            raise ResearchInputError("falsified_count must be a non-negative int")


@dataclass(frozen=True)
class BudgetView:
    """Read-only daily LLM budget snapshot for the scheduling decision."""

    daily_llm_budget_microdollars: int | None
    consumed_microdollars: int

    def __post_init__(self) -> None:
        if self.daily_llm_budget_microdollars is not None and (
            not isinstance(self.daily_llm_budget_microdollars, int)
            or isinstance(self.daily_llm_budget_microdollars, bool)
            or self.daily_llm_budget_microdollars < 0
        ):
            raise ResearchInputError("daily_llm_budget_microdollars must be a non-negative int or None")
        if not isinstance(self.consumed_microdollars, int) or isinstance(self.consumed_microdollars, bool) or self.consumed_microdollars < 0:
            raise ResearchInputError("consumed_microdollars must be a non-negative int")

    @property
    def is_exhausted(self) -> bool:
        """True when a configured daily LLM budget has been fully consumed."""
        if self.daily_llm_budget_microdollars is None:
            return False
        return self.consumed_microdollars >= self.daily_llm_budget_microdollars


@dataclass(frozen=True)
class NodeFreshness:
    """Observation timestamps for one node.

    first_seen_at is identity/history context. latest_activity_at drives hunt
    freshness because an old asset can become high-value again after a new
    observation or deployment change.
    """

    node_canonical_key: str
    first_seen_at: datetime | None
    latest_activity_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_canonical_key, str) or not self.node_canonical_key.strip():
            raise ResearchInputError("node_canonical_key must be a non-empty string")
        if self.first_seen_at is not None and not isinstance(self.first_seen_at, datetime):
            raise ResearchInputError("first_seen_at must be a datetime or None")
        if self.latest_activity_at is not None and not isinstance(self.latest_activity_at, datetime):
            raise ResearchInputError("latest_activity_at must be a datetime or None")


@dataclass(frozen=True)
class HunterScore:
    """Deterministic score with a full component breakdown.

    K1: every factor that influenced the score is referenceable and explainable.
    """

    cell: CoverageCell
    total_score: int
    state_weight: int
    family_success_bonus: int
    family_exploration_bonus: int
    freshness_bonus: int
    budget_suitability_bonus: int
    explanation: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.total_score, int) or isinstance(self.total_score, bool):
            raise ResearchInputError("total_score must be an int")
        if not isinstance(self.state_weight, int):
            raise ResearchInputError("state_weight must be an int")
        if not isinstance(self.family_success_bonus, int):
            raise ResearchInputError("family_success_bonus must be an int")
        if not isinstance(self.family_exploration_bonus, int):
            raise ResearchInputError("family_exploration_bonus must be an int")
        if not isinstance(self.freshness_bonus, int):
            raise ResearchInputError("freshness_bonus must be an int")
        if not isinstance(self.budget_suitability_bonus, int):
            raise ResearchInputError("budget_suitability_bonus must be an int")
        if not isinstance(self.explanation, tuple):
            raise ResearchInputError("explanation must be a tuple")


@dataclass(frozen=True)
class ScoredCell:
    """A coverage cell paired with its deterministic HunterScore."""

    cell: CoverageCell
    score: HunterScore

    def __post_init__(self) -> None:
        if not isinstance(self.cell, CoverageCell):
            raise ResearchInputError("cell must be a CoverageCell")
        if not isinstance(self.score, HunterScore):
            raise ResearchInputError("score must be a HunterScore")


@dataclass(frozen=True)
class HunterScoreInput:
    """Input bundle for the scheduler. All values are read-only projections."""

    cells: tuple[CoverageCell, ...]
    family_stats: tuple[FamilyStats, ...]
    freshness_by_node: Mapping[str, NodeFreshness | datetime | None]
    budget_view: BudgetView
    reference_time: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.cells, tuple):
            raise ResearchInputError("cells must be a tuple")
        if not isinstance(self.family_stats, tuple):
            raise ResearchInputError("family_stats must be a tuple")
        if not isinstance(self.freshness_by_node, Mapping):
            raise ResearchInputError("freshness_by_node must be a mapping")
        if not isinstance(self.budget_view, BudgetView):
            raise ResearchInputError("budget_view must be a BudgetView")
        if not isinstance(self.reference_time, datetime):
            raise ResearchInputError("reference_time must be a datetime")
