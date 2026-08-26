from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.tools.browser_page_policy import (
    BROWSER_PAGE_MAX_NETWORK_REQUESTS,
    validate_browser_page_arguments,
)
from zest.tools.registry import load_capability_registry


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
        self.assertEqual(observe.network_policy["max_requests"], BROWSER_PAGE_MAX_NETWORK_REQUESTS)
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


if __name__ == "__main__":
    unittest.main()
