from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.discovery.config import record_from_config
from zest.application.discovery.lifecycle import (
    count_runnable_discovery_frontier,
    discovery_is_exhausted,
)
from zest.data.records import FrontierEventRecord, FrontierItemRecord
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


def _config() -> DiscoveryRunConfig:
    return DiscoveryRunConfig(
        research_run_id="run-1",
        seed_target_reference="http://127.0.0.1:9/",
        normalized_origin="http://127.0.0.1:9",
        normalized_path="/",
        bounds=DiscoveryBounds(
            max_discovery_cycles=4,
            max_frontier_items=8,
            max_new_facts_per_cycle=4,
            max_browser_actions=4,
            max_http_transactions=4,
            max_per_route_revisit=1,
            max_identity_variants=0,
            max_transition_depth=1,
            max_graph_depth_from_seed=1,
            max_template_inference_fanout=1,
            max_duplicate_observations=1,
        ),
    )


def _item(frontier_id: str) -> FrontierItemRecord:
    return FrontierItemRecord(
        frontier_id=frontier_id,
        research_run_id="run-1",
        strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        goal_kind="CHARACTERIZE_HTTP_OPERATION",
        candidate_origin="http://127.0.0.1:9",
        candidate_path="/api/session.js",
        identity_id="ANONYMOUS",
        proposed_capability="http.transaction",
        proposed_action="read",
        expected_side_effect=0,
        budget_class=0,
        structural_signature="sig-" + frontier_id,
        dedupe_identity="dedupe-" + frontier_id,
        created_at=CREATED_AT,
        current_state="ELIGIBLE",
        state_version=2,
    )


def _event(
    event_id: str,
    frontier_id: str,
    kind: str,
    sequence: int,
    *,
    selection_generation: int | None = None,
) -> FrontierEventRecord:
    return FrontierEventRecord(
        event_id=event_id,
        frontier_id=frontier_id,
        research_run_id="run-1",
        event_kind=kind,
        sequence=sequence,
        created_at=CREATED_AT,
        selection_generation=selection_generation,
    )


class DiscoveryLifecycleTests(unittest.TestCase):
    def test_missing_config_is_not_exhaustion(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store=store)
        with factory.open() as uow:
            self.assertEqual(count_runnable_discovery_frontier(uow, "run-1"), 0)
            self.assertFalse(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()

    def test_count_and_exhaustion_from_persisted_frontier_events(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.discovery_run_configs["run-1"] = record_from_config(_config(), created_at=CREATED_AT)
        store.frontier_items["front-1"] = _item("front-1")
        store.frontier_events["evt-1"] = _event("evt-1", "front-1", "CREATED", 1)
        store.frontier_events["evt-2"] = _event("evt-2", "front-1", "ELIGIBLE", 2)
        factory = FakeUnitOfWorkFactory(store=store)
        with factory.open() as uow:
            self.assertEqual(count_runnable_discovery_frontier(uow, "run-1"), 1)
            self.assertFalse(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()
        store.frontier_events["evt-3"] = _event(
            "evt-3", "front-1", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["evt-4"] = _event("evt-4", "front-1", "OBSERVED", 4)
        with factory.open() as uow:
            self.assertEqual(count_runnable_discovery_frontier(uow, "run-1"), 0)
            self.assertTrue(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()


if __name__ == "__main__":
    unittest.main()
