"""SD-G15 live coverage debt integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

from datetime import timedelta
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.coverage.debt_view import CoverageDebtSummary
from zest.application.coverage.live_debt import (
    RefreshLiveCoverageDebt,
    RefreshLiveCoverageDebtCommand,
)
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    ChangeEventRecord,
    CoverageDebtSnapshotRecord,
    SnapshotRecord,
)

TEST_URL = configured_test_url()


class FixedClock:
    def now(self):
        return NOW + timedelta(minutes=20)


class StubCoverageView:
    def __init__(self, uow_factory: PostgresUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(self, research_run_id: str, *, persist: bool = False) -> CoverageDebtSummary:
        assert persist is True
        record = CoverageDebtSnapshotRecord(
            snapshot_id="snap-current",
            research_run_id=research_run_id,
            matrix_hash="b" * 64,
            cell_counts={"UNTESTED": 3, "COVERED": 1},
            total_debt=3,
            created_at=NOW + timedelta(minutes=20),
        )
        with self._uow_factory.open() as uow:
            uow.coverage_debt_snapshots.insert(record)
            uow.commit()
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


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG15LiveCoverageIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.coverage_debt_snapshots.insert(
                CoverageDebtSnapshotRecord(
                    snapshot_id="snap-previous",
                    research_run_id="run-1",
                    matrix_hash="a" * 64,
                    cell_counts={"UNTESTED": 1, "COVERED": 1},
                    total_debt=1,
                    created_at=NOW + timedelta(minutes=1),
                )
            )
            uow.snapshots.insert(
                SnapshotRecord(
                    snapshot_id="baseline-snapshot",
                    research_run_id="run-1",
                    program_id="prog-1",
                    target_identity="identity-1",
                    captured_at=NOW + timedelta(minutes=2),
                    strategy_version="temporal.diagnostic.echo.v1",
                    created_at=NOW + timedelta(minutes=2),
                ),
                (),
            )
            uow.snapshots.insert(
                SnapshotRecord(
                    snapshot_id="variant-snapshot",
                    research_run_id="run-1",
                    program_id="prog-1",
                    target_identity="identity-1",
                    captured_at=NOW + timedelta(minutes=3),
                    strategy_version="temporal.diagnostic.echo.v1",
                    created_at=NOW + timedelta(minutes=3),
                ),
                (),
            )
            uow.change_events.insert(
                ChangeEventRecord(
                    change_event_id="change-1",
                    research_run_id="run-1",
                    baseline_snapshot_id="baseline-snapshot",
                    variant_snapshot_id="variant-snapshot",
                    category="ADDED",
                    statement="A diagnostic route was added.",
                    source_refs=("obs-1",),
                    strategy_version="temporal.diagnostic.echo.v1",
                    created_at=NOW + timedelta(minutes=10),
                )
            )
            uow.commit()

    def test_live_refresh_records_advisory_coverage_impact_only(self) -> None:
        assert self.engine is not None
        uow_factory = PostgresUnitOfWorkFactory(self.engine)

        impact = RefreshLiveCoverageDebt(
            uow_factory,
            clock=FixedClock(),
            coverage_view=StubCoverageView(uow_factory),
        ).execute(RefreshLiveCoverageDebtCommand("run-1"))

        self.assertEqual(impact.previous_snapshot_id, "snap-previous")
        self.assertEqual(impact.current_snapshot_id, "snap-current")
        self.assertEqual(impact.total_debt_delta, 2)
        self.assertEqual(impact.change_event_ids, ("change-1",))
        self.assertTrue(impact.not_a_vulnerability)

        with PostgresUnitOfWork(self.engine) as uow:
            audits = [
                audit
                for audit in uow.audit_events.list_for_subject_type(
                    "coverage_debt_snapshot"
                )
                if audit.subject_id == "snap-current"
            ]
            evidence_count = len(uow.evidence.list_for_research_run("run-1"))
            candidate_count = len(uow.candidates.list_for_research_run("run-1"))
            finding_count = len(uow.findings.list_for_research_run("run-1"))
            uow.rollback()

        self.assertEqual(evidence_count, 0)
        self.assertEqual(candidate_count, 0)
        self.assertEqual(finding_count, 0)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].event_type, "LIVE_COVERAGE_DEBT_REFRESHED")
        self.assertTrue(audits[0].payload["not_evidence"])
        self.assertTrue(audits[0].payload["not_finding"])


if __name__ == "__main__":
    unittest.main()
