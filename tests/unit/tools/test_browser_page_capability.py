from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.discovery.compile_plan import compile_frontier_plan
from zest.data.records import FrontierItemRecord
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.tools.browser_page_policy import (
    BROWSER_PAGE_MAX_NETWORK_REQUESTS,
    BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS,
    validate_browser_page_arguments,
)
from zest.tools.registry import load_capability_registry
from zest.worker_runtime.python.packaged_registry import (
    load_packaged_capabilities,
    validate_arguments as validate_worker_arguments,
)
from support.spine import CREATED_AT


class BrowserPageCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        load_capability_registry.cache_clear()

    def test_registry_contains_browser_page_actions_and_bounds(self) -> None:
        registry = load_capability_registry()
        capability = registry.get("browser.page")
        assert capability is not None
        self.assertEqual(capability.executor_class, "WORKER")
        observe = capability.action("observe")
        navigate = capability.action("navigate")
        interact = capability.action("interact")
        assert observe is not None and navigate is not None and interact is not None
        self.assertEqual(observe.minimum_side_effect_level, 0)
        self.assertEqual(observe.maximum_side_effect_level, 0)
        self.assertEqual(navigate.minimum_side_effect_level, 0)
        self.assertEqual(navigate.maximum_side_effect_level, 0)
        self.assertEqual(interact.minimum_side_effect_level, 1)
        self.assertEqual(interact.maximum_side_effect_level, 1)
        self.assertEqual(observe.network_policy["max_requests"], BROWSER_PAGE_MAX_OBSERVE_NETWORK_REQUESTS)
        self.assertEqual(navigate.network_policy["max_requests"], BROWSER_PAGE_MAX_NETWORK_REQUESTS)
        self.assertEqual(interact.network_policy["max_requests"], BROWSER_PAGE_MAX_NETWORK_REQUESTS)
        self.assertTrue(observe.network_policy["loopback_only"])
        self.assertEqual(observe.network_policy["redirect"], "STOP")
        self.assertEqual(observe.normalizer_reference, "browser.page.v1")

    def test_arbitrary_selector_and_javascript_rejected(self) -> None:
        issue = validate_browser_page_arguments(
            "observe",
            {
                "authorized_origin": "http://127.0.0.1:9",
                "path": "/app",
                "selector": "css=button",
            },
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue.reason_code, "UNEXPECTED_ARGUMENT")
        issue = validate_browser_page_arguments(
            "interact",
            {
                "authorized_origin": "http://127.0.0.1:9",
                "path": "/app",
                "browser_context_reference": "ctx-1",
                "page_reference": "page-1",
                "element_reference": "el-0",
                "snapshot_fingerprint": "fp",
                "kind": "fill",
                "value": "javascript:alert(1)",
            },
        )
        self.assertIsNotNone(issue)

    def test_production_discovery_inspect_path_compiles_to_worker_valid_observe(self) -> None:
        plan = compile_frontier_plan(
            FrontierItemRecord(
                frontier_id="front-1",
                research_run_id="run-1",
                strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
                goal_kind="INSPECT_PATH",
                candidate_origin="https://www.dyson.tw",
                candidate_path="/",
                identity_id="ANONYMOUS",
                proposed_capability="browser.page",
                proposed_action="observe",
                expected_side_effect=0,
                budget_class=0,
                structural_signature="path:https://www.dyson.tw/",
                dedupe_identity="path:https://www.dyson.tw/",
                created_at=CREATED_AT,
                attributes={},
            ),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="https://www.dyson.tw/",
        )

        self.assertEqual(plan.required_capability, "browser.page")
        self.assertEqual(plan.action, "observe")
        self.assertEqual(
            plan.arguments,
            {"authorized_origin": "https://www.dyson.tw", "path": "/"},
        )
        self.assertIsNone(validate_browser_page_arguments(plan.action, plan.arguments))

        packaged = load_packaged_capabilities()["browser.page"]
        self.assertEqual(plan.capability_version, packaged.version)
        self.assertEqual(
            plan.capability_definition_fingerprint,
            packaged.definition_fingerprint,
        )
        schema_issue = validate_worker_arguments(
            packaged.actions["observe"].argument_schema,
            plan.arguments,
        )
        self.assertIsNone(schema_issue)


if __name__ == "__main__":
    unittest.main()
