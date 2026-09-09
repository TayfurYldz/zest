from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.transition_a.browser_page import (
    BROWSER_PAGE_OBSERVATION_KIND,
    BrowserPageNormalizer,
)
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.records import ExperimentRecord, IssuedBudgetRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.browser_page import plan_browser_navigate, plan_browser_observe
from zest.tools.browser_page_policy import (
    BROWSER_PAGE_MAX_NETWORK_REQUESTS,
    BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS,
)
from zest.tools.registry import load_capability_registry
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import seed_spine

CREATED_AT = datetime(2026, 8, 17, tzinfo=timezone.utc)
ORIGIN = "http://127.0.0.1:9"


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(origin: str = ORIGIN, path_prefix: str | None = "/"):
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix=path_prefix,
                source_reference="scope-src",
            ),
        )
    )


def _snapshot_result(request, attempted: int = 3) -> dict:
    return {
        "contract_version": "v1",
        "correlation": dict(request["correlation"]),
        "worker_id": "local-python-browser",
        "status": "SUCCEEDED",
        "started_at": "2026-08-17T00:00:00+00:00",
        "completed_at": "2026-08-17T00:00:01+00:00",
        "raw_result": {
            "attempted_network_requests": attempted,
            "browser_context_reference": "ctx-1",
            "page_reference": "page-1",
            "snapshot_fingerprint": "fp",
            "normalized_url": f"{ORIGIN}/app",
            "ready_state": "complete",
            "frame_count": 1,
            "controls": [
                {
                    "element_reference": "el-0",
                    "snapshot_fingerprint": "fp",
                    "tag": "button",
                    "role": "",
                    "input_type": "",
                    "disabled": False,
                    "checked": False,
                    "name": "save",
                    "aria_label": "",
                    "placeholder": "",
                }
            ],
            "network_events": [
                {
                    "event_id": "ne-1",
                    "method": "GET",
                    "resource_type": "document",
                    "normalized_target": f"{ORIGIN}/app",
                    "path": "/app",
                    "status_code": 200,
                    "request_bytes": 0,
                    "response_bytes": 12,
                    "redirect": False,
                    "representability": "REPRESENTABLE",
                }
            ],
            "snapshot_schema_version": "browser.page.snapshot.v1",
        },
    }


def _use_case(store: _Store | None = None, *, max_requests: int = 32):
    store = store or _Store()
    seed_spine(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=max_requests,
        max_tool_calls=10,
        max_runtime_ms=10_000,
        max_concurrency=2,
        issued_at=CREATED_AT,
    )

    def handler(request):
        assert "network_envelope" in request
        assert request["max_attempted_requests"] >= 1
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=CREATED_AT,
            completed_at=CREATED_AT,
            worker_result=_snapshot_result(request),
            exit_code=0,
        )

    worker = RecordingWorkerPort(store=store, handler=handler)
    factory = FakeUnitOfWorkFactory(store=store)
    return ExecutePlannedExperiment(factory, worker, clock=FixedClock()), factory, worker, store


class BrowserPageApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        load_capability_registry.cache_clear()

    def test_dispatch_attaches_envelope_and_reserves_request_budget(self) -> None:
        use_case, factory, port, store = _use_case(max_requests=20)
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_browser_navigate(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    authorized_origin=ORIGIN,
                    path="/app",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)
        request = port.calls[0]["request"]
        self.assertIn("network_envelope", request)
        self.assertEqual(request["network_envelope"]["normalized_host"], "127.0.0.1")
        self.assertEqual(request["max_attempted_requests"], 16)
        self.assertLessEqual(request["max_attempted_requests"], BROWSER_PAGE_MAX_NETWORK_REQUESTS)
        requests = [
            item
            for item in store.budget_consumptions.values()
            if item.resource_type == "REQUEST"
        ]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].amount, 16)
        observations = list(factory.store.observations.values())
        self.assertEqual(observations[0].observation_kind, BROWSER_PAGE_OBSERVATION_KIND)
        self.assertNotIn("cookie", str(observations[0].payload).lower())

    def test_observe_reserves_moderate_browser_page_fanout(self) -> None:
        use_case, _, port, store = _use_case(max_requests=150)
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_browser_observe(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="https://www.dyson.tw/",
                    authorized_origin="https://www.dyson.tw",
                    path="/",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope("https://www.dyson.tw", "/"),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(
            port.calls[0]["request"]["max_attempted_requests"],
            BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS,
        )
        requests = [
            item
            for item in store.budget_consumptions.values()
            if item.resource_type == "REQUEST"
        ]
        self.assertEqual(requests[0].amount, BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS)

    def test_observe_fanout_lifecycle_produces_observation_not_no_observation(self) -> None:
        def run_once(worker_result_factory):
            store = _Store()
            seed_spine(store)
            store.issued_budgets["budget-1"] = IssuedBudgetRecord(
                budget_id="budget-1",
                research_run_id="run-1",
                max_requests=150,
                max_tool_calls=10,
                max_runtime_ms=10_000,
                max_concurrency=2,
                issued_at=CREATED_AT,
            )

            def handler(request):
                self.assertEqual(
                    request["max_attempted_requests"],
                    BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS,
                )
                return WorkerInvocationOutcome(
                    invocation_status=InvocationStatus.COMPLETED,
                    started_at=CREATED_AT,
                    completed_at=CREATED_AT,
                    worker_result=worker_result_factory(request),
                    exit_code=0,
                )

            worker = RecordingWorkerPort(store=store, handler=handler)
            use_case = ExecutePlannedExperiment(
                FakeUnitOfWorkFactory(store=store), worker, clock=FixedClock()
            )
            outcome = use_case.execute(
                ExecutePlannedExperimentCommand(
                    experiment_id="exp-1",
                    plan=plan_browser_observe(
                        "hyp-1",
                        budget_id="budget-1",
                        target_reference="https://www.dyson.tw/",
                        authorized_origin="https://www.dyson.tw",
                        path="/",
                    ),
                    scope=_allow_scope(),
                    compiled_scope=_compiled_scope("https://www.dyson.tw", "/"),
                )
            )
            return outcome, store

        outcome, store = run_once(lambda request: _snapshot_result(request, attempted=16))
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertNotEqual(outcome.status, ResearchLoopStatus.NO_OBSERVATION)
        observation = next(iter(store.observations.values()))
        self.assertEqual(observation.observation_kind, BROWSER_PAGE_OBSERVATION_KIND)
        self.assertEqual(observation.payload["attempted_network_requests"], 16)
        self.assertNotIn("partial", observation.payload)

        def partial_result(request):
            result = _snapshot_result(request, attempted=BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS)
            result["raw_result"]["partial"] = True
            result["raw_result"]["budget_exhausted"] = True
            result["raw_result"]["capped_network_requests"] = BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS
            result["raw_result"]["coverage_complete"] = False
            return result

        partial_outcome, partial_store = run_once(partial_result)
        self.assertEqual(partial_outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertNotEqual(partial_outcome.status, ResearchLoopStatus.NO_OBSERVATION)
        partial_observation = next(iter(partial_store.observations.values()))
        self.assertTrue(partial_observation.payload["partial"])
        self.assertTrue(partial_observation.payload["budget_exhausted"])
        self.assertFalse(partial_observation.payload["coverage_complete"])

    def test_concurrent_reservation_cannot_overspend(self) -> None:
        store = _Store()
        use_case, _, port, store = _use_case(store, max_requests=16)
        first = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_browser_navigate(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    authorized_origin=ORIGIN,
                    path="/app",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(),
            )
        )
        self.assertEqual(first.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        store.experiments["exp-2"] = ExperimentRecord(
            experiment_id="exp-2",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            execution_state="PLANNED",
            created_at=CREATED_AT,
        )
        second = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=plan_browser_navigate(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    authorized_origin=ORIGIN,
                    path="/app",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(),
            )
        )
        self.assertEqual(second.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(second.core_reason_code, ReasonCode.BUDGET_EXHAUSTED)
        self.assertEqual(len(port.calls), 1)

    def test_reauthorization_does_not_fabricate_observation(self) -> None:
        store = _Store()
        seed_spine(store)
        store.issued_budgets["budget-1"] = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=16,
            max_tool_calls=10,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=CREATED_AT,
        )

        def handler(request):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=CREATED_AT,
                completed_at=CREATED_AT,
                worker_result={
                    "contract_version": "v1",
                    "correlation": dict(request["correlation"]),
                    "worker_id": "local-python-browser",
                    "status": "REAUTHORIZATION_REQUIRED",
                    "started_at": "2026-08-17T00:00:00+00:00",
                    "completed_at": "2026-08-17T00:00:01+00:00",
                    "raw_result": {"attempted_network_requests": 1, "followed": False},
                    "diagnostics": {
                        "followed": False,
                        "requires_core_re_evaluation": True,
                        "channel": "REDIRECT",
                        "raw_location": "/excluded",
                        "response_url": f"{ORIGIN}/app",
                        "location": f"{ORIGIN}/excluded",
                        "self_authorized": False,
                    },
                },
                exit_code=0,
            )

        worker = RecordingWorkerPort(store=store, handler=handler)
        use_case = ExecutePlannedExperiment(
            FakeUnitOfWorkFactory(store=store), worker, clock=FixedClock()
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_browser_navigate(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    authorized_origin=ORIGIN,
                    path="/app",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        self.assertEqual(len(store.observations), 0)

    def test_normalizer_rejects_secrets_and_verdicts(self) -> None:
        normalizer = BrowserPageNormalizer("observe")
        with self.assertRaises(MalformedNormalizedPayloadError):
            normalizer.normalize(
                {},
                {
                    "status": "SUCCEEDED",
                    "completed_at": "2026-08-17T00:00:01+00:00",
                    "raw_result": {
                        "attempted_network_requests": 1,
                        "browser_context_reference": "ctx",
                        "page_reference": "page",
                        "snapshot_fingerprint": "fp",
                        "normalized_url": f"{ORIGIN}/app",
                        "cookie": "sid=secret",
                    },
                },
            )
        drafts = normalizer.normalize(
            {},
            {
                "status": "REAUTHORIZATION_REQUIRED",
                "completed_at": "2026-08-17T00:00:01+00:00",
                "raw_result": {},
            },
        )
        self.assertEqual(drafts, ())

    def test_normalizer_preserves_partial_budget_metadata(self) -> None:
        normalizer = BrowserPageNormalizer("observe")
        result = _snapshot_result({"correlation": {}}, attempted=64)
        result["raw_result"]["partial"] = True
        result["raw_result"]["budget_exhausted"] = True
        result["raw_result"]["capped_network_requests"] = 64
        result["raw_result"]["coverage_complete"] = False

        drafts = normalizer.normalize({}, result)

        self.assertEqual(len(drafts), 1)
        payload = drafts[0].payload
        self.assertTrue(payload["partial"])
        self.assertTrue(payload["budget_exhausted"])
        self.assertEqual(payload["capped_network_requests"], 64)
        self.assertFalse(payload["coverage_complete"])
        self.assertFalse(payload["page_complete"])


if __name__ == "__main__":
    unittest.main()
