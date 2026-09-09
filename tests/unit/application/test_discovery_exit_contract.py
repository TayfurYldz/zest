from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.discovery.config import record_from_config
from zest.application.discovery.lifecycle import (
    apply_discovery_exit_dispositions,
    discovery_can_exit,
    discovery_exit_audit,
    discovery_is_exhausted,
)
from zest.data.records import FrontierEventRecord, FrontierItemRecord
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.discovery.frontier import (
    REASON_DEDUPLICATED,
    REASON_DEFERRED_TO_RESEARCH,
    REASON_UNSUPPORTED_CAPABILITY,
    select_eligible_frontier,
)
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
            max_discovery_cycles=8,
            max_frontier_items=16,
            max_new_facts_per_cycle=8,
            max_browser_actions=8,
            max_http_transactions=8,
            max_per_route_revisit=1,
            max_identity_variants=0,
            max_transition_depth=1,
            max_graph_depth_from_seed=1,
            max_template_inference_fanout=1,
            max_duplicate_observations=1,
        ),
    )


def _item(
    frontier_id: str,
    *,
    capability: str = "http.transaction",
    side_effect: int = 0,
    dedupe: str | None = None,
    action: str = "read",
) -> FrontierItemRecord:
    return FrontierItemRecord(
        frontier_id=frontier_id,
        research_run_id="run-1",
        strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        goal_kind="CHARACTERIZE_HTTP_OPERATION",
        candidate_origin="http://127.0.0.1:9",
        candidate_path="/" + frontier_id,
        identity_id="ANONYMOUS",
        proposed_capability=capability,
        proposed_action=action,
        expected_side_effect=side_effect,
        budget_class=side_effect,
        structural_signature="sig-" + frontier_id,
        dedupe_identity=dedupe or ("dedupe-" + frontier_id),
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
    reason_code: str | None = None,
    selection_generation: int | None = None,
) -> FrontierEventRecord:
    return FrontierEventRecord(
        event_id=event_id,
        frontier_id=frontier_id,
        research_run_id="run-1",
        event_kind=kind,
        sequence=sequence,
        created_at=CREATED_AT,
        reason_code=reason_code,
        selection_generation=selection_generation,
    )


def _seed_eligible(store: _Store, item: FrontierItemRecord) -> None:
    store.frontier_items[item.frontier_id] = item
    store.frontier_events[item.frontier_id + "-c"] = _event(
        item.frontier_id + "-c", item.frontier_id, "CREATED", 1
    )
    store.frontier_events[item.frontier_id + "-e"] = _event(
        item.frontier_id + "-e", item.frontier_id, "ELIGIBLE", 2
    )


def _open(store: _Store):
    seed_authorization_run(store)
    store.discovery_run_configs["run-1"] = record_from_config(_config(), created_at=CREATED_AT)
    return FakeUnitOfWorkFactory(store=store)


def _reconcile(audit) -> None:
    accounted = (
        audit.runnable_discovery
        + audit.in_flight
        + audit.waiting_human
        + audit.executed
        + audit.blocked_scope
        + audit.blocked_policy
        + audit.budget_blocked
        + audit.failed
        + audit.deduplicated
        + audit.handed_off
        + audit.unsupported
        + audit.deferred
        + audit.unexplained
    )
    if accounted != audit.frontier_total:
        raise AssertionError(
            f"frontier categories {accounted} != total {audit.frontier_total}"
        )


class DiscoveryExitContractTests(unittest.TestCase):
    def test_e1_runnable_work_blocks_exit_until_observed(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-a"))
        _seed_eligible(store, _item("front-b"))
        with factory.open() as uow:
            self.assertFalse(discovery_can_exit(uow, "run-1"))
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.runnable_discovery, 2)
            self.assertFalse(audit.discovery_exit_allowed)
            uow.rollback()
        store.frontier_events["front-a-s"] = _event(
            "front-a-s", "front-a", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-a-o"] = _event("front-a-o", "front-a", "OBSERVED", 4)
        store.frontier_events["front-b-s"] = _event(
            "front-b-s", "front-b", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-b-o"] = _event("front-b-o", "front-b", "OBSERVED", 4)
        with factory.open() as uow:
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.runnable_discovery, 0)
            self.assertEqual(audit.executed, 2)
            self.assertEqual(audit.orphan_discovery_work, 0)
            self.assertTrue(audit.discovery_exit_allowed)
            self.assertTrue(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()

    def test_e2_se2_se3_receive_research_handoff(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-se3", side_effect=3, action="mutate"))
        with factory.open() as uow:
            from zest.research.discovery.frontier import SURFACE_DISCOVERY_MAX_SIDE_EFFECT
            from zest.application.discovery.lifecycle import _item_from_record, _event_from_record

            items = tuple(_item_from_record(row) for row in uow.frontier_items.list_for_research_run("run-1"))
            events = {
                row.frontier_id: tuple(
                    _event_from_record(event)
                    for event in uow.frontier_events.list_for_frontier(row.frontier_id)
                )
                for row in uow.frontier_items.list_for_research_run("run-1")
            }
            self.assertIsNone(
                select_eligible_frontier(items, events, max_side_effect=SURFACE_DISCOVERY_MAX_SIDE_EFFECT)
            )
            self.assertFalse(discovery_can_exit(uow, "run-1"))
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            latest = uow.frontier_events.list_for_frontier("front-se3")[-1]
            self.assertEqual(latest.event_kind, "DEFERRED_TO_RESEARCH")
            self.assertEqual(latest.reason_code, REASON_DEFERRED_TO_RESEARCH)
            self.assertEqual(audit.handed_off, 1)
            self.assertEqual(audit.orphan_discovery_work, 0)
            self.assertTrue(discovery_can_exit(uow, "run-1"))
            uow.commit()

    def test_e4_reauth_blocks_clean_exit_until_denied_branch(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-wait"))
        _seed_eligible(store, _item("front-other"))
        store.frontier_events["front-wait-s"] = _event(
            "front-wait-s", "front-wait", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-wait-a"] = _event(
            "front-wait-a", "front-wait", "AWAITING_REAUTHORIZATION", 4
        )
        with factory.open() as uow:
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertGreaterEqual(audit.waiting_human, 1)
            self.assertFalse(audit.discovery_exit_allowed)
            uow.rollback()
        store.frontier_events["front-wait-d"] = _event(
            "front-wait-d", "front-wait", "BLOCKED_AUTH", 5, reason_code="HUMAN_DENY"
        )
        with factory.open() as uow:
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.waiting_human, 0)
            self.assertEqual(audit.blocked_policy, 1)
            self.assertEqual(audit.runnable_discovery, 1)
            self.assertFalse(audit.discovery_exit_allowed)
            uow.rollback()

    def test_e5_unsupported_capability_disposition(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-oast", capability="oast.callback"))
        with factory.open() as uow:
            self.assertFalse(discovery_can_exit(uow, "run-1"))
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            latest = uow.frontier_events.list_for_frontier("front-oast")[-1]
            self.assertEqual(latest.event_kind, "UNSUPPORTED")
            self.assertEqual(latest.reason_code, REASON_UNSUPPORTED_CAPABILITY)
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.unsupported, 1)
            self.assertEqual(audit.unexplained, 0)
            self.assertTrue(discovery_can_exit(uow, "run-1"))
            uow.commit()

    def test_e6_duplicate_is_superseded_to_canonical(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-canon", dedupe="same-work"))
        _seed_eligible(store, _item("front-dup", dedupe="same-work"))
        with factory.open() as uow:
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            dup = uow.frontier_events.list_for_frontier("front-dup")[-1]
            self.assertEqual(dup.event_kind, "SUPERSEDED")
            self.assertTrue(dup.reason_code.startswith(REASON_DEDUPLICATED))
            self.assertIn("front-canon", dup.reason_code)
            remaining = uow.frontier_events.list_for_frontier("front-canon")[-1]
            self.assertEqual(remaining.event_kind, "ELIGIBLE")
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.deduplicated, 1)
            self.assertEqual(audit.runnable_discovery, 1)
            self.assertFalse(discovery_can_exit(uow, "run-1"))
            uow.commit()

    def test_e7_failure_is_not_executed_success(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-fail"))
        store.frontier_events["front-fail-s"] = _event(
            "front-fail-s", "front-fail", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-fail-f"] = _event(
            "front-fail-f", "front-fail", "FAILED_TERMINAL", 4, reason_code="TIMED_OUT"
        )
        with factory.open() as uow:
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertEqual(audit.failed, 1)
            self.assertEqual(audit.executed, 0)
            self.assertTrue(audit.discovery_exit_allowed)
            self.assertNotEqual(audit.failed, audit.executed)
            uow.rollback()

    def test_e8_recovery_same_exit_decision(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-a"))
        with factory.open() as uow:
            first = discovery_can_exit(uow, "run-1")
            uow.rollback()
        with factory.open() as uow:
            second = discovery_can_exit(uow, "run-1")
            uow.rollback()
        self.assertEqual(first, second)
        self.assertFalse(first)
        store.frontier_events["front-a-s"] = _event(
            "front-a-s", "front-a", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-a-o"] = _event("front-a-o", "front-a", "OBSERVED", 4)
        with factory.open() as uow:
            after = discovery_can_exit(uow, "run-1")
            uow.rollback()
        with factory.open() as uow:
            self.assertEqual(after, discovery_can_exit(uow, "run-1"))
            self.assertTrue(after)
            uow.rollback()

    def test_e8_r2_waiting_human_and_e8_r3_deferred_preserved(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-wait"))
        store.frontier_events["front-wait-s"] = _event(
            "front-wait-s", "front-wait", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-wait-a"] = _event(
            "front-wait-a", "front-wait", "AWAITING_REAUTHORIZATION", 4
        )
        with factory.open() as uow:
            waiting = discovery_exit_audit(uow, "run-1")
            uow.rollback()
        with factory.open() as uow:
            waiting_again = discovery_exit_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(waiting.waiting_human, waiting_again.waiting_human)
        self.assertFalse(waiting.discovery_exit_allowed)
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-se2", side_effect=2))
        with factory.open() as uow:
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            uow.commit()
        with factory.open() as uow:
            apply_discovery_exit_dispositions(uow, "run-1", created_at=CREATED_AT)
            kinds = [item.event_kind for item in uow.frontier_events.list_for_frontier("front-se2")]
            self.assertEqual(kinds.count("DEFERRED_TO_RESEARCH"), 1)
            self.assertTrue(discovery_can_exit(uow, "run-1"))
            uow.rollback()

    def test_e9_exhaustion_is_not_run_completion(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-done"))
        store.frontier_events["front-done-s"] = _event(
            "front-done-s", "front-done", "SELECTED", 3, selection_generation=1
        )
        store.frontier_events["front-done-o"] = _event("front-done-o", "front-done", "OBSERVED", 4)
        with factory.open() as uow:
            audit = discovery_exit_audit(uow, "run-1")
            _reconcile(audit)
            self.assertTrue(audit.discovery_exit_allowed)
            self.assertEqual(audit.orphan_discovery_work, 0)
            self.assertTrue(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()

    def test_eligible_se_le_1_is_not_exhaustion_by_itself(self) -> None:
        store = _Store()
        factory = _open(store)
        _seed_eligible(store, _item("front-se2", side_effect=2))
        with factory.open() as uow:
            from zest.application.discovery.lifecycle import count_runnable_discovery_frontier

            self.assertEqual(count_runnable_discovery_frontier(uow, "run-1"), 0)
            self.assertFalse(discovery_is_exhausted(uow, "run-1"))
            uow.rollback()


if __name__ == "__main__":
    unittest.main()
