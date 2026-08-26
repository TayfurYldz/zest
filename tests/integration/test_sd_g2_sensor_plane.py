"""SD-G2 sensor/acquisition plane integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
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
from zest.application.identity import new_opaque_id
from zest.application.sensor.admit import (
    AdmitSensorObservations,
    SensorAdmissionError,
)
from zest.application.sensor.runner import SensorAcquisitionRunner
from zest.core.enums import ReasonCode, ScopeClassification, ScopeRuleEffect
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    ProgramPolicyRecord,
    ScopeRuleV2Record,
    SensorObservationRecord,
)
from zest.research.sensor import (
    CTLogSensor,
    CertificateMetaSensor,
    DNSSensor,
    TechnologyFingerprintSensor,
    WaybackArchiveSensor,
)
from zest.research.sensor.fixture_loader import FileFixtureLoader
from zest.research.sensor.types import ScopeCensusView, build_observation
from zest.research.target_model import TargetEpistemicStatus

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
class SDG2SensorPlaneIntegrationTests(unittest.TestCase):
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

    def test_runner_persists_sensor_observations(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )

        self.assertEqual(len(result.observations), 5)
        self.assertEqual(result.budget_units_consumed, 5)
        for obs in result.observations:
            self.assertEqual(obs.epistemic_status, TargetEpistemicStatus.UNTRUSTED_EXTERNAL)

        with PostgresUnitOfWork(self.engine) as uow:
            records = uow.sensor_observations.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(len(records), 5)
        for record in records:
            self.assertEqual(record.epistemic_status, "UNTRUSTED_EXTERNAL")
            self.assertEqual(record.research_run_id, "run-1")
            self.assertEqual(record.target_reference, "https://example.com")

    def test_admission_creates_fact_and_source(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        observation = result.observations[0]

        admission = AdmitSensorObservations(uow_factory)
        admitted = admission.execute(
            observation,
            research_run_id="run-1",
            identity_id="ANONYMOUS",
            scope_classification="IN_SCOPE",
        )

        self.assertTrue(admitted.fact_id)
        self.assertEqual(admitted.observation_id, observation.observation_id)

        with PostgresUnitOfWork(self.engine) as uow:
            fact = uow.discovery_facts.get(admitted.fact_id)
            sources = uow.discovery_fact_sources.list_for_fact(admitted.fact_id)
            uow.rollback()

        self.assertIsNotNone(fact)
        self.assertEqual(fact.epistemic_status, "OBSERVED")
        self.assertEqual(fact.research_run_id, "run-1")
        sources = list(sources)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].sensor_observation_id, observation.observation_id)
        self.assertIsNone(sources[0].observation_id)

    def test_admission_caps_epistemic_at_observed(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        runner = SensorAcquisitionRunner(uow_factory, self._sensors())
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://example.com",
            scope_view=_in_scope_view(),
        )
        observation = result.observations[0]
        self.assertEqual(observation.epistemic_status, TargetEpistemicStatus.UNTRUSTED_EXTERNAL)

        admission = AdmitSensorObservations(uow_factory)
        admitted = admission.execute(
            observation,
            research_run_id="run-1",
            scope_classification="UNKNOWN",
        )

        with PostgresUnitOfWork(self.engine) as uow:
            fact = uow.discovery_facts.get(admitted.fact_id)
            uow.rollback()

        self.assertIsNotNone(fact)
        self.assertEqual(fact.epistemic_status, "OBSERVED")
        self.assertEqual(fact.attributes.get("source_status"), "UNTRUSTED_EXTERNAL")

    def test_admission_rejects_forbidden_keys_and_creates_no_fact(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        observation = build_observation(
            observation_id=new_opaque_id(),
            sensor_id="sensor.dns",
            target_reference="https://example.com",
            research_run_id="run-1",
            payload={"token": "should-be-rejected"},
            source_metadata={},
        )

        admission = AdmitSensorObservations(uow_factory)
        with self.assertRaises(SensorAdmissionError):
            admission.execute(
                observation, research_run_id="run-1", scope_classification="UNKNOWN"
            )

        with PostgresUnitOfWork(self.engine) as uow:
            facts = uow.discovery_facts.list_for_research_run("run-1")
            sources = uow.discovery_fact_sources.list_for_fact("does-not-matter")
            uow.rollback()

        self.assertEqual(len(facts), 0)

    def test_no_direct_sensor_to_fact_write_path(self) -> None:
        """Sensors cannot create DiscoveryFacts; only admission can.

        This is an architectural invariant test: sensor modules in the research
        layer never import or reference discovery_fact, discovery_fact_source,
        finding modules, or admission machinery. SensorAcquisitionRunner persists
        raw SensorObservation records only; it never touches discovery_fact or
        discovery_fact_source tables.
        """
        import ast

        sensor_dir = Path(__file__).resolve().parents[2] / "src" / "zest" / "research" / "sensor"
        for source in sensor_dir.glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            from_imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn(
                "zest.application",
                from_imports,
                f"{source.name} imports application layer",
            )
            self.assertNotIn(
                "zest.data",
                from_imports,
                f"{source.name} imports data layer",
            )
            self.assertNotIn(
                "zest.research.finding_proposal",
                from_imports,
                f"{source.name} imports finding modules",
            )
            self.assertTrue(
                all("discovery_fact" not in name for name in imports),
                f"{source.name} references discovery_fact",
            )
            self.assertTrue(
                all("finding" not in name for name in imports),
                f"{source.name} references finding",
            )

        runner_source = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "zest"
            / "application"
            / "sensor"
            / "runner.py"
        )
        runner_text = runner_source.read_text(encoding="utf-8")
        self.assertNotIn("discovery_facts", runner_text)
        self.assertNotIn("discovery_fact_sources", runner_text)
        self.assertNotIn("AdmitSensorObservations", runner_text)


if __name__ == "__main__":
    unittest.main()
