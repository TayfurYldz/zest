"""SD-G8 Coverage Debt integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.coverage.debt_view import CoverageDebtView
from zest.application.sensor.admit import AdmitSensorObservations
from zest.application.sensor.runner import SensorAcquisitionRunner
from zest.core.enums import ReasonCode, ScopeClassification, ScopeRuleEffect
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    HunterFamilyRecord,
    ProgramPolicyRecord,
    ScopeRuleV2Record,
)
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
class SDG8CoverageDebtIntegrationTests(unittest.TestCase):
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

    def _seed_hunter_family(self) -> None:
        family_id = f"hf-hostname-exposed-{self._testMethodName}"
        with PostgresUnitOfWork(self.engine) as uow:
            uow.hunter_families.insert(
                HunterFamilyRecord(
                    family_id=family_id,
                    name="Exposed Hostname",
                    target_node_kinds=("HOSTNAME",),
                    preconditions={"scope_classification": "IN_SCOPE"},
                    claim_template="hostname {canonical_key} is in scope and reachable",
                    evidence_requirements={},
                    validation_tier="V1",
                    enabled=True,
                    version=1,
                    created_at=NOW,
                )
            )
            uow.commit()

    def _run_census_and_admit(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        self.assertGreater(len(result.observations), 0)

        admission = AdmitSensorObservations(uow_factory)
        for observation in result.observations:
            admission.execute(
                observation,
                research_run_id="run-1",
                identity_id="ANONYMOUS",
                scope_classification="IN_SCOPE",
            )

    def test_coverage_debt_computed_and_snapshot_persisted(self) -> None:
        self._run_census_and_admit()
        self._seed_hunter_family()

        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        view = CoverageDebtView(uow_factory, clock=FixedClock())
        summary = view.execute("run-1", persist=True)

        self.assertEqual(summary.research_run_id, "run-1")
        self.assertEqual(len(summary.matrix_hash), 64)
        self.assertIsNotNone(summary.snapshot_id)
        self.assertIn("cell_counts", dir(summary))
        self.assertGreaterEqual(summary.total_debt, 0)

        with PostgresUnitOfWork(self.engine) as uow:
            loaded = uow.coverage_debt_snapshots.get(summary.snapshot_id)
            snapshots = uow.coverage_debt_snapshots.list_for_research_run("run-1")
            uow.rollback()

        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.matrix_hash, summary.matrix_hash)
        self.assertEqual(loaded.total_debt, summary.total_debt)
        self.assertEqual(loaded.cell_counts, summary.cell_counts)
        self.assertEqual(len(snapshots), 1)

    def test_coverage_debt_rebuild_matches_persisted_hash(self) -> None:
        self._run_census_and_admit()
        self._seed_hunter_family()

        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        view = CoverageDebtView(uow_factory, clock=FixedClock())
        first = view.execute("run-1", persist=True)

        second = view.execute("run-1", persist=False)
        self.assertEqual(second.matrix_hash, first.matrix_hash)
        self.assertEqual(second.total_debt, first.total_debt)

    def test_coverage_debt_table_is_append_only(self) -> None:
        """Coverage snapshot table is listed in APPEND_ONLY_TABLES."""
        from zest.data.postgres.tables import APPEND_ONLY_TABLES

        self.assertIn("coverage_debt_snapshot", APPEND_ONLY_TABLES)


if __name__ == "__main__":
    unittest.main()
