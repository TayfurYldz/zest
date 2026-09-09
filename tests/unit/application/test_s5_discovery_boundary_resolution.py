from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.discovery.runner import SurfaceDiscoveryRunner, SurfaceDiscoveryStart
from zest.application.orchestration_obligations import unresolved_control_obligations
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import FrontierEventRecord, FrontierItemRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.tools.capabilities import HTTP_TRANSACTION_CAPABILITY
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_authorization_run

LOCAL_ORIGIN = "http://127.0.0.1:9"
OOS_ORIGIN = "http://example.com"


def _bounds() -> DiscoveryBounds:
    return DiscoveryBounds(
        max_discovery_cycles=8,
        max_frontier_items=32,
        max_new_facts_per_cycle=16,
        max_browser_actions=16,
        max_http_transactions=16,
        max_per_route_revisit=1,
        max_identity_variants=3,
        max_transition_depth=4,
        max_graph_depth_from_seed=8,
        max_template_inference_fanout=4,
        max_duplicate_observations=8,
    )


def _config() -> DiscoveryRunConfig:
    return DiscoveryRunConfig(
        research_run_id="run-1",
        seed_target_reference=LOCAL_ORIGIN + "/",
        normalized_origin=LOCAL_ORIGIN,
        normalized_path="/",
        bounds=_bounds(),
    )


def _scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("allow-local", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(*, explicit_example_oos: bool) -> CompiledScope:
    rules = [
        CompiledScopeRule(
            rule_id="allow-local",
            effect=ScopeRuleEffect.ALLOW,
            scheme="http",
            host="127.0.0.1",
            host_pattern=None,
            port=9,
            path_prefix=None,
            source_reference="scope-src",
            expires_at=None,
        )
    ]
    if explicit_example_oos:
        rules.append(
            CompiledScopeRule(
                rule_id="deny-example",
                effect=ScopeRuleEffect.OUT_OF_SCOPE,
                scheme="http",
                host="example.com",
                host_pattern=None,
                port=80,
                path_prefix=None,
                source_reference="scope-src",
                expires_at=None,
            )
        )
    return CompiledScope(rules=tuple(rules))


def _frontier(*, goal_kind: str, attributes: dict, action: str, capability: str, side_effect: int, path: str = "/") -> FrontierItemRecord:
    return FrontierItemRecord(
        frontier_id="front-boundary",
        research_run_id="run-1",
        strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        goal_kind=goal_kind,
        candidate_origin=LOCAL_ORIGIN,
        candidate_path=path,
        identity_id="ANONYMOUS",
        proposed_capability=capability,
        proposed_action=action,
        expected_side_effect=side_effect,
        budget_class=side_effect,
        structural_signature="sig-boundary",
        dedupe_identity="dedupe-boundary",
        created_at=CREATED_AT,
        attributes=attributes,
    )


def _replace_seed_with(store: _Store, item: FrontierItemRecord) -> None:
    store.frontier_items.clear()
    store.frontier_events.clear()
    store.frontier_sources.clear()
    store.frontier_items[item.frontier_id] = item
    store.frontier_events["ev-created"] = FrontierEventRecord(
        event_id="ev-created",
        frontier_id=item.frontier_id,
        research_run_id="run-1",
        event_kind="CREATED",
        sequence=1,
        created_at=CREATED_AT,
    )
    store.frontier_events["ev-eligible"] = FrontierEventRecord(
        event_id="ev-eligible",
        frontier_id=item.frontier_id,
        research_run_id="run-1",
        event_kind="ELIGIBLE",
        sequence=2,
        created_at=CREATED_AT,
    )


def _redirect_worker(request) -> WorkerInvocationOutcome:
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=CREATED_AT,
        completed_at=CREATED_AT,
        worker_result={
            "contract_version": "v1",
            "correlation": dict(request["correlation"]),
            "worker_id": "boundary-test-worker",
            "status": "REAUTHORIZATION_REQUIRED",
            "started_at": CREATED_AT.isoformat(),
            "completed_at": CREATED_AT.isoformat(),
            "raw_result": {
                "attempted_network_requests": 1,
                "method": "GET",
                "status": 302,
                "followed": False,
            },
            "diagnostics": {
                "followed": False,
                "requires_core_re_evaluation": True,
                "channel": "REDIRECT",
                "raw_location": OOS_ORIGIN + "/",
                "response_url": LOCAL_ORIGIN + "/redirect-cross",
                "location": OOS_ORIGIN + "/",
                "self_authorized": False,
            },
        },
        exit_code=0,
    )


class S5DiscoveryBoundaryResolutionTests(unittest.TestCase):
    def test_known_explicit_oos_anchor_is_terminally_blocked_without_worker(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store)
        runner = SurfaceDiscoveryRunner(factory, worker)
        start = SurfaceDiscoveryStart(
            config=_config(), compiled_scope=_compiled_scope(explicit_example_oos=True)
        )
        runner.ensure_started(start)
        _replace_seed_with(
            store,
            _frontier(
                goal_kind="INSPECT_CONTROL",
                attributes={
                    "tag": "a",
                    "name": "oos",
                    "role": "",
                    "input_type": "",
                    "href_origin": OOS_ORIGIN,
                    "href_path": "/",
                },
                action="interact",
                capability="browser.page",
                side_effect=1,
            ),
        )

        result = runner.run_cycle(
            start,
            budget_id="budget-1",
            target_reference=LOCAL_ORIGIN + "/",
            scope=_scope(),
        )

        self.assertEqual(result.stop_reason, "BLOCKED_SCOPE")
        self.assertFalse(result.worker_invoked)
        self.assertEqual(worker.calls, [])
        kinds = [item.event_kind for item in store.frontier_events.values()]
        self.assertIn("BLOCKED_SCOPE", kinds)
        blocked = [item for item in store.frontier_events.values() if item.event_kind == "BLOCKED_SCOPE"]
        self.assertEqual(blocked[-1].reason_code, "KNOWN_CONTROL_DESTINATION_OUT_OF_SCOPE")

    def test_runtime_redirect_to_explicit_oos_is_denied_without_human_obligation(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store, handler=_redirect_worker)
        runner = SurfaceDiscoveryRunner(factory, worker)
        start = SurfaceDiscoveryStart(
            config=_config(), compiled_scope=_compiled_scope(explicit_example_oos=True)
        )
        runner.ensure_started(start)
        _replace_seed_with(
            store,
            _frontier(
                goal_kind="CHARACTERIZE_HTTP_OPERATION",
                attributes={"method": "GET", "auto_replay": False},
                action="read",
                capability=HTTP_TRANSACTION_CAPABILITY,
                side_effect=0,
                path="/redirect-cross",
            ),
        )

        result = runner.run_cycle(
            start,
            budget_id="budget-1",
            target_reference=LOCAL_ORIGIN + "/",
            scope=_scope(),
        )

        self.assertEqual(result.stop_reason, "BLOCKED_SCOPE")
        self.assertEqual(len(worker.calls), 1)
        self.assertIsNotNone(result.experiment_id)
        self.assertEqual(store.experiments[result.experiment_id].execution_state, "BLOCKED")
        worker_results = list(store.worker_results.values())
        self.assertEqual(len(worker_results), 1)
        self.assertEqual(worker_results[0].status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(len(store.control_events), 1)
        self.assertEqual(next(iter(store.control_events.values())).event_kind, "NEW_ORIGIN_BOUNDARY")
        obligations = unresolved_control_obligations(
            attempts=tuple(store.execution_attempts.values()),
            experiments=tuple(store.experiments.values()),
            worker_results=tuple(store.worker_results.values()),
        )
        self.assertEqual(obligations, ())
        blocked = [item for item in store.frontier_events.values() if item.event_kind == "BLOCKED_SCOPE"]
        self.assertEqual(blocked[-1].reason_code, "REAUTHORIZATION_TARGET_OUT_OF_SCOPE")

    def test_runtime_redirect_without_explicit_scope_classification_stays_human_gated(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store, handler=_redirect_worker)
        runner = SurfaceDiscoveryRunner(factory, worker)
        start = SurfaceDiscoveryStart(
            config=_config(), compiled_scope=_compiled_scope(explicit_example_oos=False)
        )
        runner.ensure_started(start)
        _replace_seed_with(
            store,
            _frontier(
                goal_kind="CHARACTERIZE_HTTP_OPERATION",
                attributes={"method": "GET", "auto_replay": False},
                action="read",
                capability=HTTP_TRANSACTION_CAPABILITY,
                side_effect=0,
                path="/redirect-cross",
            ),
        )

        result = runner.run_cycle(
            start,
            budget_id="budget-1",
            target_reference=LOCAL_ORIGIN + "/",
            scope=_scope(),
        )

        self.assertEqual(result.stop_reason, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(store.experiments[result.experiment_id].execution_state, "AUTHORIZATION_CHECK")
        kinds = [item.event_kind for item in store.frontier_events.values()]
        self.assertIn("AWAITING_REAUTHORIZATION", kinds)
        obligations = unresolved_control_obligations(
            attempts=tuple(store.execution_attempts.values()),
            experiments=tuple(store.experiments.values()),
            worker_results=tuple(store.worker_results.values()),
        )
        self.assertTrue(any(item.code == "REAUTHORIZATION_REQUIRED" for item in obligations))


if __name__ == "__main__":
    unittest.main()
