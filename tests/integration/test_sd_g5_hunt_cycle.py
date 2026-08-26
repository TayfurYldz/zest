"""SD-G5 HunterFamily registry + hunt cycle integration.

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
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.discovery.snapshot_views import (
    _fact_from_record,
    _inference_from_record,
)
from zest.application.run_hunt_cycle import RunHuntCycle, RunHuntCycleCommand
from zest.application.sensor.admit import AdmitSensorObservations
from zest.application.sensor.runner import SensorAcquisitionRunner
from zest.core.enums import ReasonCode, ScopeClassification, ScopeRuleEffect
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import ProgramPolicyRecord, ScopeRuleV2Record
from zest.research.discovery.graph import rebuild_attack_surface_graph
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
class SDG5HuntCycleIntegrationTests(unittest.TestCase):
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

    def test_cycle_generates_hypotheses_from_registry(self) -> None:
        self._run_census_and_admit()

        with PostgresUnitOfWork(self.engine) as uow:
            fact_records = uow.discovery_facts.list_for_research_run("run-1")
            inference_records = uow.discovery_inferences.list_for_research_run("run-1")
            facts = tuple(_fact_from_record(uow, row) for row in fact_records)
            inferences = tuple(_inference_from_record(row) for row in inference_records)
            graph = rebuild_attack_surface_graph(
                research_run_id="run-1",
                strategy_version="surface.discovery.v1",
                facts=facts,
                inferences=inferences,
            )
            uow.rollback()

        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        use_case = RunHuntCycle(uow_factory)
        result = use_case.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=graph,
            )
        )

        self.assertGreater(result.generated, 0)
        self.assertEqual(result.v1_passed, result.generated)
        self.assertEqual(result.v2_passed, result.generated)
        self.assertGreaterEqual(result.v3_queued, 0)
        self.assertFalse(result.no_op)

        with PostgresUnitOfWork(self.engine) as uow:
            hypotheses = uow.hypotheses.list_for_research_run("run-1")
            queue_items = uow.hunt_v3_queue.list_for_research_run("run-1")
            uow.rollback()

        # Filter out the spine hypothesis seeded by the harness.
        hunt_hypotheses = [h for h in hypotheses if h.origin_reference.startswith("hf-")]
        self.assertEqual(len(hunt_hypotheses), result.generated)

        # Audit events are insert-only; query directly by correlation_id.
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT event_type FROM audit_event WHERE correlation_id = :run_id"
                ),
                {"run_id": "run-1"},
            ).fetchall()
        event_types = {row.event_type for row in rows}
        self.assertIn("HUNT_HYPOTHESIS_GENERATED", event_types)
        self.assertIn("HUNT_TIER_V1_PASSED", event_types)
        self.assertIn("HUNT_TIER_V2_PASSED", event_types)
        if result.v3_queued > 0:
            self.assertIn("HUNT_TIER_V3_QUEUED", event_types)
            self.assertEqual(len(queue_items), result.v3_queued)
            for item in queue_items:
                self.assertEqual(item.state, "PENDING")
                self.assertEqual(item.research_run_id, "run-1")
            matrix_items = [
                item for item in queue_items if item.capability == "mutation.matrix"
            ]
            self.assertTrue(matrix_items)
            for item in matrix_items:
                self.assertEqual(item.action, "plan")
                self.assertEqual(item.side_effect_level, 0)
                self.assertGreaterEqual(item.arguments["cell_count"], 30)
                self.assertEqual(len(item.arguments["cells"]), item.arguments["cell_count"])
                self.assertIn("cell_id", item.arguments["cells"][0])
                self.assertEqual(len(item.arguments["matrix_hash"]), 64)
                self.assertEqual(
                    item.arguments["worker_dispatch"],
                    "forbidden_until_operator_approval",
                )
                self.assertNotIn("payload", item.arguments)
                self.assertNotIn("body", item.arguments)
        else:
            self.assertEqual(len(queue_items), 0)

    def test_registry_seed_contains_baseline_and_sd_g12_families(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            families = uow.hunter_families.list_enabled()
            uow.rollback()

        names = {f.name for f in families}
        self.assertTrue(
            {
                "OBJECT_AUTHORIZATION",
                "WORKFLOW_STATE_TRANSITION",
                "EXPOSED_API_SPEC",
                "UNPROTECTED_HOSTNAME",
                "TECH_KNOWN_CVE_SURFACE",
            }
            <= names
        )
        self.assertTrue(
            {
                "SQL_INJECTION",
                "SERVER_SIDE_TEMPLATE_INJECTION",
                "FILE_INCLUDE_AND_PATH_TRAVERSAL",
                "MASS_ASSIGNMENT",
                "JWT_CRYPTO_AND_CLAIM_CONFUSION",
                "CORS_CREDENTIAL_EXFILTRATION_CHAIN",
                "GRAPHQL_AUTHORIZATION_AND_INJECTION",
                "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION",
                "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE",
            }
            <= names
        )
        self.assertTrue(
            {
                "HTTP_REQUEST_SMUGGLING_DESYNC",
                "HTTP_CACHE_POISONING_DECEPTION",
            }
            <= names
        )

    def test_no_op_cycle_when_graph_is_empty(self) -> None:
        from zest.research.discovery.graph import AttackSurfaceGraph

        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        use_case = RunHuntCycle(uow_factory)
        empty_graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(),
            edges=(),
        )

        result = use_case.execute(
            RunHuntCycleCommand(research_run_id="run-1", graph=empty_graph)
        )

        self.assertTrue(result.no_op)
        self.assertEqual(result.generated, 0)
        self.assertEqual(result.v3_queued, 0)


if __name__ == "__main__":
    unittest.main()
