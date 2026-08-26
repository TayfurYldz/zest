"""SD-G3 attack surface graph v2 integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

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
from zest.application.discovery.snapshot_views import (
    AttackSurfaceSummary,
    summarize_attack_surface,
)
from zest.application.identity import new_opaque_id
from zest.application.sensor.admit import AdmitSensorObservations
from zest.application.sensor.runner import SensorAcquisitionRunner
from zest.core.enums import ReasonCode, ScopeClassification, ScopeRuleEffect
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AttackSurfaceSnapshotRecord,
    ProgramPolicyRecord,
    ScopeRuleV2Record,
)
from zest.research.discovery.graph import graph_hash, rebuild_attack_surface_graph
from zest.research.sensor import (
    CTLogSensor,
    CertificateMetaSensor,
    DNSSensor,
    TechnologyFingerprintSensor,
    WaybackArchiveSensor,
)
from zest.research.sensor.fixture_loader import FileFixtureLoader
from zest.research.sensor.types import ScopeCensusView

TEST_URL = configured_test_url()
FIXTURE_DIR = _REPO / "tests" / "fixtures" / "sensor"


def _in_scope_view() -> ScopeCensusView:
    return ScopeCensusView(
        classification=ScopeClassification.IN_SCOPE,
        reason_code=ReasonCode.ALLOWED,
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG3SurfaceGraphIntegrationTests(unittest.TestCase):
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
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id="prog-1",
                    loopback_fixture=False,
                    max_response_bytes=4096,
                    timeout_ms=2000,
                    created_at=NOW,
                    updated_at=NOW,
                    action_policy={},
                )
            )
            uow.scope_rules_v2.insert(
                ScopeRuleV2Record(
                    rule_id="rule-allow-example",
                    program_id="prog-1",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="example.com",
                    source_reference="scope-src",
                    created_at=NOW,
                )
            )
            uow.commit()

    def _sensors(self):
        loader = FileFixtureLoader(FIXTURE_DIR)
        return [
            DNSSensor(loader),
            CTLogSensor(loader),
            WaybackArchiveSensor(loader),
            CertificateMetaSensor(loader),
            TechnologyFingerprintSensor(loader),
        ]

    def test_census_to_admission_to_graph_hash_matches(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        self.assertEqual(len(result.observations), 5)

        admission = AdmitSensorObservations(uow_factory)
        for observation in result.observations:
            admission.execute(
                observation,
                research_run_id="run-1",
                identity_id="ANONYMOUS",
                scope_classification="IN_SCOPE",
            )

        with PostgresUnitOfWork(self.engine) as uow:
            summary = summarize_attack_surface(uow, "run-1")
            uow.rollback()

        self.assertIsInstance(summary, AttackSurfaceSummary)
        self.assertEqual(summary.research_run_id, "run-1")
        self.assertGreater(summary.node_count, 0)
        self.assertEqual(len(summary.graph_hash), 64)
        self.assertIn("HOSTNAME", summary.kind_counts)
        self.assertIn("CERT", summary.kind_counts)
        self.assertIn("TECH", summary.kind_counts)
        self.assertIn("IN_SCOPE", summary.scope_classification_counts)

    def test_snapshot_persists_and_reloads(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        admission = AdmitSensorObservations(uow_factory)
        for observation in result.observations:
            admission.execute(
                observation,
                research_run_id="run-1",
                identity_id="ANONYMOUS",
                scope_classification="IN_SCOPE",
            )

        with PostgresUnitOfWork(self.engine) as uow:
            summary = summarize_attack_surface(uow, "run-1")
            snapshot = AttackSurfaceSnapshotRecord(
                snapshot_id=new_opaque_id(),
                research_run_id="run-1",
                strategy_version=summary.strategy_version,
                node_count=summary.node_count,
                edge_count=summary.edge_count,
                graph_hash=summary.graph_hash,
                created_at=NOW,
            )
            uow.attack_surface_snapshots.insert(snapshot)
            uow.commit()

        with PostgresUnitOfWork(self.engine) as uow:
            loaded = uow.attack_surface_snapshots.get(snapshot.snapshot_id)
            snapshots = uow.attack_surface_snapshots.list_for_research_run("run-1")
            uow.rollback()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.graph_hash, summary.graph_hash)
        self.assertEqual(loaded.node_count, summary.node_count)
        self.assertEqual(loaded.edge_count, summary.edge_count)
        self.assertEqual(len(snapshots), 1)

    def test_rebuild_from_ledger_matches_persisted_hash(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        admission = AdmitSensorObservations(uow_factory)
        for observation in result.observations:
            admission.execute(
                observation,
                research_run_id="run-1",
                identity_id="ANONYMOUS",
                scope_classification="IN_SCOPE",
            )

        with PostgresUnitOfWork(self.engine) as uow:
            summary = summarize_attack_surface(uow, "run-1")
            snapshot = AttackSurfaceSnapshotRecord(
                snapshot_id=new_opaque_id(),
                research_run_id="run-1",
                strategy_version=summary.strategy_version,
                node_count=summary.node_count,
                edge_count=summary.edge_count,
                graph_hash=summary.graph_hash,
                created_at=NOW,
            )
            uow.attack_surface_snapshots.insert(snapshot)
            uow.commit()

        # Rebuild a second time from the durable ledger.
        with PostgresUnitOfWork(self.engine) as uow:
            second_summary = summarize_attack_surface(uow, "run-1")
            uow.rollback()

        self.assertEqual(second_summary.graph_hash, summary.graph_hash)


if __name__ == "__main__":
    unittest.main()
