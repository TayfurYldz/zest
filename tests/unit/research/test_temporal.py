from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.research.target_model import TargetObservationView
from zest.research.temporal import (
    ChangeCategory,
    ChangeOutcome,
    SnapshotOutcome,
    capture_diagnostic_snapshot,
    compare_diagnostic_snapshots,
)


def _view(**overrides) -> TargetObservationView:
    values = dict(
        observation_id="obs-a",
        research_run_id="run-1",
        experiment_id="exp-1",
        observation_kind="diagnostic.echo",
        payload={"echoed": "alpha"},
        capability="diagnostic.echo",
        action="echo",
        actor_handle="worker-local",
        resource_handle="target-1",
        submitted_input="alpha",
    )
    values.update(overrides)
    return TargetObservationView(**values)


T1 = datetime(2026, 8, 17, 1, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=1)


class DiagnosticTemporalTests(unittest.TestCase):
    def test_compatible_snapshots_produce_change_event(self) -> None:
        views = (
            _view(),
            _view(
                observation_id="obs-b",
                experiment_id="exp-2",
                payload={"echoed": "beta"},
                submitted_input="beta",
            ),
        )
        captured_a, snap_a, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-1",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-a",),
            captured_at=T1,
            views=views,
        )
        captured_b, snap_b, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-2",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-b",),
            captured_at=T2,
            views=views,
        )
        self.assertEqual(captured_a, SnapshotOutcome.CAPTURED)
        self.assertEqual(captured_b, SnapshotOutcome.CAPTURED)
        assert snap_a is not None and snap_b is not None
        decision = compare_diagnostic_snapshots(
            snap_a, snap_b, views, change_event_id="chg-1"
        )
        self.assertEqual(decision.outcome, ChangeOutcome.COMPARED)
        assert decision.change_event is not None
        self.assertIn(
            decision.change_event.category,
            {ChangeCategory.BEHAVIOR_CHANGED, ChangeCategory.STATE_CHANGED, ChangeCategory.ADDED},
        )
        self.assertNotIn("vulnerability", decision.change_event.statement.lower())

    def test_cross_program_is_rejected(self) -> None:
        views = (_view(),)
        _, snap_a, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-1",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-a",),
            captured_at=T1,
            views=views,
        )
        _, snap_b, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-2",
            research_run_id="run-1",
            program_id="prog-2",
            target_identity="target-1",
            observation_ids=("obs-a",),
            captured_at=T2,
            views=views,
        )
        assert snap_a is not None and snap_b is not None
        decision = compare_diagnostic_snapshots(
            snap_a, snap_b, views, change_event_id="chg-1"
        )
        self.assertEqual(decision.outcome, ChangeOutcome.REJECTED_CROSS_PROGRAM)

    def test_incompatible_target_is_rejected(self) -> None:
        views = (_view(),)
        _, snap_a, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-1",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-a",),
            captured_at=T1,
            views=views,
        )
        _, snap_b, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-2",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-other",
            observation_ids=("obs-a",),
            captured_at=T2,
            views=views,
        )
        assert snap_a is not None and snap_b is not None
        decision = compare_diagnostic_snapshots(
            snap_a, snap_b, views, change_event_id="chg-1"
        )
        self.assertEqual(decision.outcome, ChangeOutcome.REJECTED_INCOMPATIBLE_TARGET)

    def test_cross_run_is_rejected(self) -> None:
        views = (
            _view(),
            _view(observation_id="obs-b", research_run_id="run-2"),
        )
        _, snap_a, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-1",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-a",),
            captured_at=T1,
            views=(_view(),),
        )
        _, snap_b, _ = capture_diagnostic_snapshot(
            snapshot_id="snap-2",
            research_run_id="run-2",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-b",),
            captured_at=T2,
            views=(views[1],),
        )
        assert snap_a is not None and snap_b is not None
        decision = compare_diagnostic_snapshots(
            snap_a, snap_b, views, change_event_id="chg-1"
        )
        self.assertEqual(decision.outcome, ChangeOutcome.REJECTED_CROSS_RUN)

    def test_hallucinated_observation_is_rejected(self) -> None:
        outcome, snapshot, codes = capture_diagnostic_snapshot(
            snapshot_id="snap-1",
            research_run_id="run-1",
            program_id="prog-1",
            target_identity="target-1",
            observation_ids=("obs-missing",),
            captured_at=T1,
            views=(_view(),),
        )
        self.assertEqual(outcome, SnapshotOutcome.REJECTED_EMPTY)
        self.assertIsNone(snapshot)
        self.assertIn("HALLUCINATED_OBSERVATION", codes)


if __name__ == "__main__":
    unittest.main()
