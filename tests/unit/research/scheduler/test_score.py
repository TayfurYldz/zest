"""SD-G9 HunterScore scheduler core unit tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.scheduler.score import schedule, score_cell
from zest.research.scheduler.types import (
    BudgetView,
    FamilyStats,
    HunterScoreInput,
    NodeFreshness,
)


class ScoreCellTests(unittest.TestCase):
    def _cell(
        self,
        node: str = "node-1",
        identity: str = "alice",
        family: str = "hf-object-authz",
        state: CoverageState = CoverageState.UNTESTED,
    ) -> CoverageCell:
        return CoverageCell(
            node_canonical_key=node,
            identity_id=identity,
            family_id=family,
            state=state,
            missing_evidence=("NO_HYPOTHESIS",),
        )

    def test_untested_scores_higher_than_hypothesized(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        untested = score_cell(
            self._cell(state=CoverageState.UNTESTED),
            family_stats={},
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        hypothesized = score_cell(
            self._cell(state=CoverageState.HYPOTHESIZED),
            family_stats={},
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertGreater(untested.total_score, hypothesized.total_score)
        self.assertIn("state_weight=50", untested.explanation[0])
        self.assertIn("state_weight=40", hypothesized.explanation[0])

    def test_covered_and_not_applicable_score_zero(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        for state in (CoverageState.COVERED, CoverageState.NOT_APPLICABLE):
            with self.subTest(state=state):
                result = score_cell(
                    self._cell(state=state),
                    family_stats={},
                    freshness_by_node={},
                    budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                    reference_time=reference,
                )
                self.assertEqual(result.total_score, 0)

    def test_family_success_bonus(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        stats = {"hf-object-authz": FamilyStats(family_id="hf-object-authz", supported_count=2, falsified_count=1)}
        result = score_cell(
            self._cell(state=CoverageState.UNTESTED),
            family_stats={item.family_id: item for item in stats.values()},
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertEqual(result.family_success_bonus, 5)
        self.assertEqual(result.family_exploration_bonus, 0)
        self.assertIn("family_supported=2 family_falsified=1", result.explanation[1])
        self.assertIn("bounded_success_bonus=5", result.explanation[1])

    def test_family_success_bonus_is_bounded(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        stats = {
            "hf-hot": FamilyStats(
                family_id="hf-hot",
                supported_count=100,
                falsified_count=0,
            )
        }
        result = score_cell(
            self._cell(family="hf-hot", state=CoverageState.UNTESTED),
            family_stats=stats,
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertEqual(result.family_success_bonus, 20)
        self.assertIn("raw_success_bonus=500", result.explanation[1])
        self.assertIn("bounded_success_bonus=20", result.explanation[1])

    def test_missing_family_stats_get_exploration_bonus(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        result = score_cell(
            self._cell(family="hf-new", state=CoverageState.UNTESTED),
            family_stats={},
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertEqual(result.family_success_bonus, 0)
        self.assertEqual(result.family_exploration_bonus, 5)
        self.assertIn("family_stats_missing", result.explanation[1])

    def test_freshness_bonus_decreases_with_age(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        fresh = score_cell(
            self._cell(node="fresh-node"),
            family_stats={},
            freshness_by_node={
                "fresh-node": NodeFreshness(
                    node_canonical_key="fresh-node",
                    first_seen_at=reference - timedelta(hours=1),
                    latest_activity_at=reference - timedelta(hours=1),
                )
            },
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        stale = score_cell(
            self._cell(node="stale-node"),
            family_stats={},
            freshness_by_node={
                "stale-node": NodeFreshness(
                    node_canonical_key="stale-node",
                    first_seen_at=reference - timedelta(hours=48),
                    latest_activity_at=reference - timedelta(hours=48),
                )
            },
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertGreater(fresh.freshness_bonus, stale.freshness_bonus)
        self.assertEqual(stale.freshness_bonus, 0)

    def test_freshness_uses_latest_activity_not_first_seen(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        result = score_cell(
            self._cell(node="old-but-changed"),
            family_stats={},
            freshness_by_node={
                "old-but-changed": NodeFreshness(
                    node_canonical_key="old-but-changed",
                    first_seen_at=reference - timedelta(days=30),
                    latest_activity_at=reference - timedelta(hours=2),
                )
            },
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertGreater(result.freshness_bonus, 0)
        self.assertIn("first_seen_age_hours=720.00", result.explanation[2])
        self.assertIn("latest_activity_age_hours=2.00", result.explanation[2])

    def test_budget_exhausted_prefers_cheap_path(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        exhausted = BudgetView(daily_llm_budget_microdollars=1000, consumed_microdollars=1000)
        available = BudgetView(daily_llm_budget_microdollars=1000, consumed_microdollars=0)

        cheap = score_cell(
            self._cell(state=CoverageState.UNTESTED),
            family_stats={},
            freshness_by_node={},
            budget_view=exhausted,
            reference_time=reference,
        )
        cheap_available = score_cell(
            self._cell(state=CoverageState.UNTESTED),
            family_stats={},
            freshness_by_node={},
            budget_view=available,
            reference_time=reference,
        )
        self.assertEqual(cheap.budget_suitability_bonus, 5)
        self.assertEqual(cheap_available.budget_suitability_bonus, 0)

        v3 = score_cell(
            self._cell(state=CoverageState.V2_PASSED),
            family_stats={},
            freshness_by_node={},
            budget_view=exhausted,
            reference_time=reference,
        )
        self.assertEqual(v3.budget_suitability_bonus, -20)

    def test_explanation_includes_total_score(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        result = score_cell(
            self._cell(),
            family_stats={},
            freshness_by_node={},
            budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
            reference_time=reference,
        )
        self.assertTrue(result.explanation)
        self.assertIn(f"total_score={result.total_score}", result.explanation[-1])


class ScheduleTests(unittest.TestCase):
    def _cell(
        self,
        node: str,
        identity: str,
        family: str,
        state: CoverageState,
    ) -> CoverageCell:
        return CoverageCell(
            node_canonical_key=node,
            identity_id=identity,
            family_id=family,
            state=state,
            missing_evidence=("NO_HYPOTHESIS",),
        )

    def test_omits_covered_and_not_applicable(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-1", "alice", "hf-a", CoverageState.COVERED),
            self._cell("node-1", "alice", "hf-a", CoverageState.NOT_APPLICABLE),
            self._cell("node-1", "alice", "hf-a", CoverageState.UNTESTED),
        )
        result = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=(),
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                reference_time=reference,
            )
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].cell.state, CoverageState.UNTESTED)

    def test_deterministic_across_permutations(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-a", "alice", "hf-a", CoverageState.HYPOTHESIZED),
            self._cell("node-b", "bob", "hf-b", CoverageState.UNTESTED),
            self._cell("node-c", "alice", "hf-a", CoverageState.V1_PASSED),
        )
        results = []
        for _ in range(5):
            result = schedule(
                HunterScoreInput(
                    cells=cells,
                    family_stats=(),
                    freshness_by_node={},
                    budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                    reference_time=reference,
                )
            )
            results.append(tuple(item.cell for item in result))
        self.assertEqual(len(set(results)), 1)

    def test_tie_break_is_deterministic(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-z", "alice", "hf-a", CoverageState.UNTESTED),
            self._cell("node-a", "alice", "hf-a", CoverageState.UNTESTED),
            self._cell("node-m", "alice", "hf-a", CoverageState.UNTESTED),
        )
        result = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=(),
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                reference_time=reference,
            )
        )
        keys = [item.cell.node_canonical_key for item in result]
        self.assertEqual(keys, ["node-a", "node-m", "node-z"])

    def test_family_success_can_reorder_without_unbounded_dominance(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-1", "alice", "hf-loser", CoverageState.HYPOTHESIZED),
            self._cell("node-1", "alice", "hf-winner", CoverageState.HYPOTHESIZED),
        )
        family_stats = (
            FamilyStats(family_id="hf-loser", supported_count=0, falsified_count=5),
            FamilyStats(family_id="hf-winner", supported_count=5, falsified_count=0),
        )
        result = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=family_stats,
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                reference_time=reference,
            )
        )
        self.assertEqual(result[0].cell.family_id, "hf-winner")
        self.assertEqual(result[1].cell.family_id, "hf-loser")
        self.assertEqual(result[0].score.family_success_bonus, 20)
        self.assertEqual(result[1].score.family_success_bonus, -20)

    def test_high_history_family_does_not_starve_untested_new_family(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-1", "alice", "hf-hot", CoverageState.V3_QUEUED),
            self._cell("node-2", "alice", "hf-new", CoverageState.UNTESTED),
        )
        family_stats = (
            FamilyStats(family_id="hf-hot", supported_count=100, falsified_count=0),
        )
        result = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=family_stats,
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=None, consumed_microdollars=0),
                reference_time=reference,
            )
        )
        self.assertEqual(result[0].cell.family_id, "hf-new")
        self.assertEqual(result[0].score.family_exploration_bonus, 5)
        self.assertEqual(result[1].score.family_success_bonus, 20)

    def test_budget_exhausted_prefers_cheap_path(self) -> None:
        reference = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        cells = (
            self._cell("node-1", "alice", "hf-a", CoverageState.V2_PASSED),
            self._cell("node-1", "alice", "hf-a", CoverageState.V1_PASSED),
        )
        result_exhausted = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=(),
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=1000, consumed_microdollars=1000),
                reference_time=reference,
            )
        )
        result_available = schedule(
            HunterScoreInput(
                cells=cells,
                family_stats=(),
                freshness_by_node={},
                budget_view=BudgetView(daily_llm_budget_microdollars=1000, consumed_microdollars=0),
                reference_time=reference,
            )
        )
        # In both cases V1_PASSED ranks above V2_PASSED because it is the cheap path.
        self.assertEqual(result_exhausted[0].cell.state, CoverageState.V1_PASSED)
        self.assertEqual(result_available[0].cell.state, CoverageState.V1_PASSED)
        # Budget exhaustion widens the gap by penalizing the V3-bound cell.
        exhausted_gap = result_exhausted[0].score.total_score - result_exhausted[1].score.total_score
        available_gap = result_available[0].score.total_score - result_available[1].score.total_score
        self.assertGreater(exhausted_gap, available_gap)
        self.assertEqual(result_exhausted[1].score.budget_suitability_bonus, -20)
        self.assertEqual(result_exhausted[0].score.budget_suitability_bonus, 5)


if __name__ == "__main__":
    unittest.main()
