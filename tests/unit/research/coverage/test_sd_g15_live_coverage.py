from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import pathsetup  # noqa: F401

from zest.research.coverage.live import (
    CoverageChangeEventView,
    CoverageDebtSnapshotView,
    assess_live_coverage_debt,
)
from zest.research.types import ResearchInputError

NOW = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _snapshot(
    snapshot_id: str,
    *,
    total_debt: int,
    counts: dict[str, int],
    matrix_hash: str,
    created_at: datetime = NOW,
) -> CoverageDebtSnapshotView:
    return CoverageDebtSnapshotView(
        snapshot_id=snapshot_id,
        research_run_id="run-1",
        matrix_hash=matrix_hash,
        cell_counts=counts,
        total_debt=total_debt,
        created_at=created_at,
    )


class SDG15LiveCoverageDebtTests(unittest.TestCase):
    def test_compares_snapshot_counts_without_promoting_change_to_finding(self) -> None:
        previous = _snapshot(
            "snap-old",
            total_debt=2,
            counts={"UNTESTED": 2, "COVERED": 1},
            matrix_hash="a" * 64,
        )
        current = _snapshot(
            "snap-new",
            total_debt=4,
            counts={"UNTESTED": 3, "HYPOTHESIZED": 1, "COVERED": 1},
            matrix_hash="b" * 64,
            created_at=NOW + timedelta(minutes=5),
        )
        change = CoverageChangeEventView(
            change_event_id="change-1",
            research_run_id="run-1",
            category="ADDED",
            statement="A diagnostic route was added.",
            source_refs=("obs-1",),
            created_at=NOW + timedelta(minutes=1),
        )

        impact = assess_live_coverage_debt(
            current=current,
            previous=previous,
            change_events=(change,),
        )

        self.assertEqual(impact.total_debt_delta, 2)
        self.assertEqual(impact.cell_count_delta["UNTESTED"], 1)
        self.assertEqual(impact.cell_count_delta["HYPOTHESIZED"], 1)
        self.assertEqual(impact.change_event_ids, ("change-1",))
        self.assertIn("COVERAGE_DEBT_INCREASED", impact.reason_codes)
        self.assertTrue(impact.not_a_vulnerability)
        self.assertTrue(impact.not_evidence)
        self.assertTrue(impact.not_candidate)
        self.assertTrue(impact.not_finding)

    def test_first_snapshot_is_a_baseline_not_a_vulnerability(self) -> None:
        current = _snapshot(
            "snap-new",
            total_debt=1,
            counts={"UNTESTED": 1},
            matrix_hash="c" * 64,
        )

        impact = assess_live_coverage_debt(
            current=current,
            previous=None,
            change_events=(),
        )

        self.assertIsNone(impact.total_debt_delta)
        self.assertEqual(impact.cell_count_delta, {"UNTESTED": 1})
        self.assertIn("LIVE_COVERAGE_BASELINE_CREATED", impact.reason_codes)
        self.assertTrue(impact.not_a_vulnerability)

    def test_rejects_cross_run_change_event(self) -> None:
        current = _snapshot(
            "snap-new",
            total_debt=1,
            counts={"UNTESTED": 1},
            matrix_hash="d" * 64,
        )
        change = CoverageChangeEventView(
            change_event_id="change-1",
            research_run_id="run-2",
            category="ADDED",
            statement="A diagnostic route was added.",
            source_refs=("obs-1",),
            created_at=NOW,
        )

        with self.assertRaises(ResearchInputError):
            assess_live_coverage_debt(
                current=current,
                previous=None,
                change_events=(change,),
            )

    def test_rejects_vulnerability_label_in_change_statement(self) -> None:
        with self.assertRaises(ResearchInputError):
            CoverageChangeEventView(
                change_event_id="change-1",
                research_run_id="run-1",
                category="ADDED",
                statement="A vulnerability was introduced.",
                source_refs=("obs-1",),
                created_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
