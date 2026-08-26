from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

import pathsetup  # noqa: F401

from zest.application.coverage.debt_view import CoverageDebtSummary
from zest.application.coverage.live_debt import (
    RefreshLiveCoverageDebt,
    RefreshLiveCoverageDebtCommand,
)
from zest.data.records import ChangeEventRecord, CoverageDebtSnapshotRecord
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store

NOW = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self):
        return NOW + timedelta(minutes=10)


class StubCoverageView:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def execute(self, research_run_id: str, *, persist: bool = False) -> CoverageDebtSummary:
        assert persist is True
        record = CoverageDebtSnapshotRecord(
            snapshot_id="snap-current",
            research_run_id=research_run_id,
            matrix_hash="b" * 64,
            cell_counts={"UNTESTED": 3},
            total_debt=3,
            created_at=NOW + timedelta(minutes=10),
        )
        self.store.coverage_debt_snapshots[record.snapshot_id] = record
        return CoverageDebtSummary(
            research_run_id=research_run_id,
            strategy_version="surface.discovery.v1",
            matrix_hash=record.matrix_hash,
            total_debt=record.total_debt,
            cell_counts=dict(record.cell_counts),
            family_debt={},
            top_nodes=[],
            snapshot_id=record.snapshot_id,
        )


class SDG15RefreshLiveCoverageDebtTests(unittest.TestCase):
    def test_refresh_persists_audit_without_creating_finding_state(self) -> None:
        store = _Store()
        store.coverage_debt_snapshots["snap-previous"] = CoverageDebtSnapshotRecord(
            snapshot_id="snap-previous",
            research_run_id="run-1",
            matrix_hash="a" * 64,
            cell_counts={"UNTESTED": 1},
            total_debt=1,
            created_at=NOW,
        )
        store.change_events["change-1"] = ChangeEventRecord(
            change_event_id="change-1",
            research_run_id="run-1",
            baseline_snapshot_id="snapshot-a",
            variant_snapshot_id="snapshot-b",
            category="ADDED",
            statement="A diagnostic route was added.",
            source_refs=("obs-1",),
            strategy_version="temporal.diagnostic.echo.v1",
            created_at=NOW + timedelta(minutes=5),
        )

        service = RefreshLiveCoverageDebt(
            FakeUnitOfWorkFactory(store),
            clock=FixedClock(),
            coverage_view=StubCoverageView(store),
        )
        impact = service.execute(RefreshLiveCoverageDebtCommand("run-1"))

        self.assertEqual(impact.previous_snapshot_id, "snap-previous")
        self.assertEqual(impact.current_snapshot_id, "snap-current")
        self.assertEqual(impact.total_debt_delta, 2)
        self.assertEqual(impact.change_event_ids, ("change-1",))
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.candidates, {})
        self.assertEqual(store.findings, {})
        audit = next(iter(store.audit_events.values()))
        self.assertEqual(audit.event_type, "LIVE_COVERAGE_DEBT_REFRESHED")
        self.assertTrue(audit.payload["not_evidence"])
        self.assertTrue(audit.payload["not_finding"])


if __name__ == "__main__":
    unittest.main()
