#!/usr/bin/env python3
"""Apply the evidence-backed S5 discovery authority-boundary fix.

Guarded source patcher for the recovered Phase8 baseline. It changes only the
stabilization checkout supplied by the operator; it never touches /opt/zest/current,
PostgreSQL, services, scope data, or authorization state.

The patch deliberately preserves Worker/Core containment. It only resolves a
reauthorization branch automatically when the *frozen compiled scope* proves an
explicit out-of-scope rule match. UNKNOWN/ambiguous scope remains human-gated.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

EXPECTED_RUNNER_BLOB = "049ad0aa8eb3bcb88028e3442365737f962b81a4"
EXPECTED_PROBE_BLOB = "35e58c1a4791cf011949945160fd3bcc7131e8f6"

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "src/zest/application/discovery/runner.py"
PROBE = ROOT / "scripts/stabilization/phase8_start_to_terminal_probe.py"
TEST = ROOT / "tests/unit/application/test_s5_discovery_boundary_resolution.py"


def blob(path: Path) -> str:
    return subprocess.check_output(
        ["git", "hash-object", str(path)], cwd=ROOT, text=True
    ).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_runner() -> None:
    if blob(RUNNER) != EXPECTED_RUNNER_BLOB:
        raise SystemExit(
            f"runner provenance mismatch: expected {EXPECTED_RUNNER_BLOB}, got {blob(RUNNER)}"
        )
    text = RUNNER.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "from zest.application.program_research_context import ProgramPolicyView\n",
        "from zest.application.program_research_context import ProgramPolicyView\n"
        "from zest.application.scope_reauthorization import evaluate_reauthorization_request\n",
        "runner import scope reauthorization",
    )
    text = replace_once(
        text,
        "from zest.core.approval import ApprovalView\n"
        "from zest.core.scope import ScopeEvaluationInput\n"
        "from zest.core.scope_compiler import CompiledScope\n",
        "from zest.core.approval import ApprovalView\n"
        "from zest.core.enums import ScopeClassification, ScopeDecision\n"
        "from zest.core.scope import ScopeEvaluationInput\n"
        "from zest.core.scope_compiler import CompiledScope, evaluate_scope_candidate\n",
        "runner scope imports",
    )
    text = replace_once(
        text,
        "from zest.data.records import (\n"
        "    FrontierEventRecord,\n",
        "from zest.data.records import (\n"
        "    ExperimentExecutionState,\n"
        "    FrontierEventRecord,\n",
        "runner experiment state import",
    )
    text = replace_once(
        text,
        "from zest.platform.secrets import CompositeSecretPort\n",
        "from zest.platform.secrets import CompositeSecretPort\n"
        "from zest.platform.url_normalize import normalize_url\n",
        "runner URL normalizer import",
    )

    predispatch_old = '''            if chosen.candidate_origin != start.config.normalized_origin:\n                self._block(uow, chosen.frontier_id, "BLOCKED_SCOPE", now)\n                eligible_after = _snapshot_eligible_count(uow, research_run_id)\n                uow.commit()\n                return SurfaceDiscoveryCycleResult(\n                    research_run_id,\n                    "BLOCKED_SCOPE",\n                    chosen.frontier_id,\n                    None,\n                    False,\n                    eligible_before=eligible_before,\n                    eligible_after=eligible_after,\n                    selected_goal_kind=chosen.goal_kind.value,\n                    selected_path=chosen.candidate_path,\n                    compiled_capability=chosen.proposed_capability,\n                )\n            claim_frontier_selected(uow, chosen.frontier_id, created_at=now)\n'''
    predispatch_new = '''            if chosen.candidate_origin != start.config.normalized_origin:\n                self._block(uow, chosen.frontier_id, "BLOCKED_SCOPE", now)\n                eligible_after = _snapshot_eligible_count(uow, research_run_id)\n                uow.commit()\n                return SurfaceDiscoveryCycleResult(\n                    research_run_id,\n                    "BLOCKED_SCOPE",\n                    chosen.frontier_id,\n                    None,\n                    False,\n                    eligible_before=eligible_before,\n                    eligible_after=eligible_after,\n                    selected_goal_kind=chosen.goal_kind.value,\n                    selected_path=chosen.candidate_path,\n                    compiled_capability=chosen.proposed_capability,\n                )\n            chosen_record = uow.frontier_items.get(chosen.frontier_id)\n            if chosen_record is not None and _known_control_destination_is_explicit_oos(\n                chosen_record, start.compiled_scope\n            ):\n                self._append_event(\n                    uow,\n                    chosen.frontier_id,\n                    "BLOCKED_SCOPE",\n                    now,\n                    reason_code="KNOWN_CONTROL_DESTINATION_OUT_OF_SCOPE",\n                )\n                eligible_after = _snapshot_eligible_count(uow, research_run_id)\n                uow.commit()\n                return SurfaceDiscoveryCycleResult(\n                    research_run_id,\n                    "BLOCKED_SCOPE",\n                    chosen.frontier_id,\n                    None,\n                    False,\n                    eligible_before=eligible_before,\n                    eligible_after=eligible_after,\n                    selected_goal_kind=chosen.goal_kind.value,\n                    selected_path=chosen.candidate_path,\n                    compiled_capability=chosen.proposed_capability,\n                )\n            claim_frontier_selected(uow, chosen.frontier_id, created_at=now)\n'''
    text = replace_once(text, predispatch_old, predispatch_new, "runner pre-dispatch boundary")

    post_unknown_old = '''        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:\n            return SurfaceDiscoveryCycleResult(\n                research_run_id, "UNKNOWN_OUTCOME", record.frontier_id, experiment_id, True\n            )\n        with self._uow_factory.open() as uow:\n'''
    post_unknown_new = '''        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:\n            return SurfaceDiscoveryCycleResult(\n                research_run_id, "UNKNOWN_OUTCOME", record.frontier_id, experiment_id, True\n            )\n        reauthorization_explicit_oos = False\n        if (\n            loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED\n            and start.compiled_scope is not None\n            and isinstance(loop.reauthorization_request, Mapping)\n        ):\n            reauthorization_check = evaluate_reauthorization_request(\n                loop.reauthorization_request, start.compiled_scope\n            )\n            reauthorization_explicit_oos = _explicit_out_of_scope(reauthorization_check)\n        with self._uow_factory.open() as uow:\n'''
    text = replace_once(text, post_unknown_old, post_unknown_new, "runner post-worker scope check")

    reauth_event_old = '''                if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:\n                    self._append_event(uow, record.frontier_id, "AWAITING_REAUTHORIZATION", now)\n                elif loop.status is ResearchLoopStatus.DISPATCH_DENIED:\n'''
    reauth_event_new = '''                if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:\n                    if reauthorization_explicit_oos:\n                        uow.experiments.set_execution_state(\n                            experiment_id, ExperimentExecutionState.BLOCKED.value\n                        )\n                        self._append_event(\n                            uow,\n                            record.frontier_id,\n                            "BLOCKED_SCOPE",\n                            now,\n                            reason_code="REAUTHORIZATION_TARGET_OUT_OF_SCOPE",\n                        )\n                    else:\n                        self._append_event(\n                            uow, record.frontier_id, "AWAITING_REAUTHORIZATION", now\n                        )\n                elif loop.status is ResearchLoopStatus.DISPATCH_DENIED:\n'''
    text = replace_once(text, reauth_event_old, reauth_event_new, "runner reauth disposition")

    reauth_return_old = '''        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:\n            return SurfaceDiscoveryCycleResult(\n                research_run_id,\n                "REAUTHORIZATION_REQUIRED",\n                record.frontier_id,\n                experiment_id,\n                worker_invoked,\n                eligible_before=eligible_before,\n                eligible_after=eligible_after,\n                selected_goal_kind=record.goal_kind,\n                selected_path=record.candidate_path,\n                compiled_capability=plan.required_capability,\n                worker_status=loop.status.value,\n                observation_id=observation_id,\n                discovery_exhausted=False,\n            )\n'''
    reauth_return_new = '''        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:\n            return SurfaceDiscoveryCycleResult(\n                research_run_id,\n                (\n                    "BLOCKED_SCOPE"\n                    if reauthorization_explicit_oos\n                    else "REAUTHORIZATION_REQUIRED"\n                ),\n                record.frontier_id,\n                experiment_id,\n                worker_invoked,\n                eligible_before=eligible_before,\n                eligible_after=eligible_after,\n                selected_goal_kind=record.goal_kind,\n                selected_path=record.candidate_path,\n                compiled_capability=plan.required_capability,\n                worker_status=loop.status.value,\n                observation_id=observation_id,\n                discovery_exhausted=False,\n            )\n'''
    text = replace_once(text, reauth_return_old, reauth_return_new, "runner reauth return")

    helper_anchor = '''\ndef _bound_stop_reason(uow, config: DiscoveryRunConfig) -> str | None:\n'''
    helpers = '''\ndef _explicit_out_of_scope(check) -> bool:\n    """Only an explicit frozen-scope exclusion may be auto-denied.\n\n    No matching rule is UNKNOWN in CompiledScope and therefore does not satisfy\n    this predicate. Expired/ambiguous scope is human-gated, never auto-allowed.\n    """\n\n    return (\n        check.decision is ScopeDecision.DENY\n        and check.classification is ScopeClassification.OUT_OF_SCOPE\n        and bool(check.matched_rule_ids)\n    )\n\n\ndef _known_control_destination_is_explicit_oos(\n    record: FrontierItemRecord, compiled_scope: CompiledScope | None\n) -> bool:\n    if compiled_scope is None or record.goal_kind != DiscoveryGoalKind.INSPECT_CONTROL.value:\n        return False\n    attributes = record.attributes if isinstance(record.attributes, Mapping) else {}\n    href_origin = str(attributes.get("href_origin") or "").rstrip("/")\n    if not href_origin.startswith(("http://", "https://")):\n        return False\n    href_path = str(attributes.get("href_path") or "/")\n    if not href_path.startswith("/"):\n        href_path = "/" + href_path\n    candidate = normalize_url(href_origin + href_path)\n    return _explicit_out_of_scope(evaluate_scope_candidate(candidate, compiled_scope))\n\n\ndef _bound_stop_reason(uow, config: DiscoveryRunConfig) -> str | None:\n'''
    text = replace_once(text, helper_anchor, helpers, "runner helper insertion")

    RUNNER.write_text(text, encoding="utf-8")


def patch_probe() -> None:
    if blob(PROBE) != EXPECTED_PROBE_BLOB:
        raise SystemExit(
            f"probe provenance mismatch: expected {EXPECTED_PROBE_BLOB}, got {blob(PROBE)}"
        )
    text = PROBE.read_text(encoding="utf-8")
    old = '''        record["final_detail_before_cleanup"] = final_detail\n        try:\n            analysis = unwrap(http_json(zest, "GET", f"/api/runs/{rid}/analysis"))\n'''
    new = '''        record["final_detail_before_cleanup"] = final_detail\n        classification_detail = dict(final_detail)\n        try:\n            analysis = unwrap(http_json(zest, "GET", f"/api/runs/{rid}/analysis"))\n'''
    text = replace_once(text, old, new, "probe pre-cleanup classification snapshot")
    old = '''        layer_a, layer_b, diagnostics = evaluate(final_detail, analysis, hits, origin)\n        record["layer_a"] = layer_a\n        record["layer_b"] = layer_b\n        record["diagnostics"] = diagnostics\n        classification, class_reason = classify(\n            final_detail,\n            analysis,\n'''
    new = '''        layer_a, layer_b, diagnostics = evaluate(\n            classification_detail, analysis, hits, origin\n        )\n        record["layer_a"] = layer_a\n        record["layer_b"] = layer_b\n        record["diagnostics"] = diagnostics\n        classification, class_reason = classify(\n            classification_detail,\n            analysis,\n'''
    text = replace_once(text, old, new, "probe classify pre-cleanup detail")
    PROBE.write_text(text, encoding="utf-8")


def write_regression_test() -> None:
    if TEST.exists():
        raise SystemExit(f"refusing to overwrite existing {TEST.relative_to(ROOT)}")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")


TEST_CONTENT = r'''from __future__ import annotations

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
'''


def main() -> int:
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True)
    if status.strip():
        raise SystemExit("working tree must be clean before applying S5 fix")
    patch_runner()
    patch_probe()
    write_regression_test()
    subprocess.run(
        [
            "python3",
            "-m",
            "py_compile",
            str(RUNNER),
            str(PROBE),
            str(TEST),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print("S5_PATCH_APPLIED=PASS")
    print("changed_paths=3")
    subprocess.run(["git", "diff", "--stat"], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
