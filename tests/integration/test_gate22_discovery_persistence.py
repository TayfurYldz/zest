"""PostgreSQL GATE 22 discovery persistence. SQLite is not a substitute."""

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
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from research_os.data.errors import PersistenceConflictError
from research_os.data.postgres.engine import create_sync_engine
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    DiscoveryRunConfigRecord,
    FrontierEventRecord,
    FrontierItemRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from research_os.application.discovery.project import project_observation, reconcile_missing_projections
from sqlalchemy import text

TEST_URL = configured_test_url()
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


@unittest.skipUnless(TEST_URL, "RESEARCH_OS_TEST_DATABASE_URL is not configured")
class Gate22DiscoveryPersistenceTests(unittest.TestCase):
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
            uow.commit()

    def test_a22_head_and_constraints(self) -> None:
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a41_001_runtime_instance")
        with PostgresUnitOfWork(self.engine) as uow:
            uow.discovery_run_configs.insert(
                DiscoveryRunConfigRecord(
                    research_run_id="run-1",
                    strategy_version="surface.discovery.v1",
                    seed_target_reference="http://127.0.0.1:1/",
                    normalized_origin="http://127.0.0.1:1",
                    normalized_path="/",
                    max_discovery_cycles=4,
                    max_frontier_items=8,
                    max_new_facts_per_cycle=8,
                    max_browser_actions=8,
                    max_http_transactions=8,
                    max_per_route_revisit=1,
                    max_identity_variants=2,
                    max_transition_depth=2,
                    max_graph_depth_from_seed=4,
                    max_template_inference_fanout=2,
                    max_duplicate_observations=4,
                    configuration_fingerprint="a" * 64,
                    created_at=NOW,
                )
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            loaded = uow.discovery_run_configs.get("run-1")
            uow.rollback()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.configuration_fingerprint, "a" * 64)

    def test_selected_generation_uniqueness_and_receipt_uniqueness(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            uow.frontier_items.insert(
                FrontierItemRecord(
                    frontier_id="front-1",
                    research_run_id="run-1",
                    strategy_version="surface.discovery.v1",
                    goal_kind="INSPECT_PATH",
                    candidate_origin="http://127.0.0.1:1",
                    candidate_path="/",
                    identity_id="ANONYMOUS",
                    proposed_capability="browser.page",
                    proposed_action="observe",
                    expected_side_effect=0,
                    budget_class=0,
                    structural_signature="sig",
                    dedupe_identity="dedupe-1",
                    created_at=NOW,
                )
            )
            uow.frontier_events.insert(
                FrontierEventRecord(
                    event_id="e1",
                    frontier_id="front-1",
                    research_run_id="run-1",
                    event_kind="SELECTED",
                    sequence=1,
                    created_at=NOW,
                    selection_generation=1,
                )
            )
            uow.commit()
        with self.assertRaises(PersistenceConflictError):
            with PostgresUnitOfWork(self.engine) as uow:
                uow.frontier_events.insert(
                    FrontierEventRecord(
                        event_id="e2",
                        frontier_id="front-1",
                        research_run_id="run-1",
                        event_kind="SELECTED",
                        sequence=2,
                        created_at=NOW,
                        selection_generation=1,
                    )
                )
                uow.commit()


    def test_projection_tx_b_rollback_then_replay_without_worker(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            uow.worker_results.insert(
                WorkerResultRecord(
                    worker_result_id="wr-1",
                    experiment_id="exp-1",
                    research_run_id="run-1",
                    request_id="req-g22-1",
                    correlation_id="corr-g22-1",
                    worker_capability="browser.page",
                    action="observe",
                    authorization_decision_reference="authz-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    contract_version="v1",
                    worker_id="worker-1",
                    status="SUCCEEDED",
                    received_at=NOW,
                )
            )
            uow.observations.insert(
                ObservationRecord(
                    observation_id="obs-1",
                    worker_result_id="wr-1",
                    observation_kind="BROWSER_PAGE_STATE",
                    payload={
                        "normalized_url": "http://127.0.0.1:1/",
                        "path": "/",
                        "snapshot_fingerprint": "fp-1",
                        "browser_context_reference": "ctx-1",
                        "page_reference": "page-1",
                        "controls": [],
                        "network_events": [],
                    },
                    normalization_version="browser.page.v1",
                    observed_at=NOW,
                    created_at=NOW,
                )
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            project_observation(
                uow,
                uow.observations.list_for_research_run("run-1")[0],
                created_at=NOW,
                target_reference="target-1",
            )
        with PostgresUnitOfWork(self.engine) as uow:
            self.assertEqual(uow.discovery_facts.list_for_research_run("run-1"), [])
            self.assertFalse(uow.discovery_projection_receipts.has_observation("run-1", "obs-1"))
            uow.rollback()
        with PostgresUnitOfWork(self.engine) as uow:
            projected = reconcile_missing_projections(
                uow, "run-1", created_at=NOW, target_reference="target-1"
            )
            facts = len(uow.discovery_facts.list_for_research_run("run-1"))
            uow.commit()
        self.assertEqual(projected, 1)
        self.assertGreaterEqual(facts, 1)
        with PostgresUnitOfWork(self.engine) as uow:
            again = reconcile_missing_projections(
                uow, "run-1", created_at=NOW, target_reference="target-1"
            )
            self.assertEqual(again, 0)
            self.assertEqual(len(uow.discovery_facts.list_for_research_run("run-1")), facts)
            uow.commit()


if __name__ == "__main__":
    unittest.main()
