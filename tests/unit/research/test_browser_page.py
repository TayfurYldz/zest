from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.browser_lineage import http_template_from_network_event
from zest.research.browser_page import (
    plan_browser_interact,
    plan_browser_navigate,
    plan_browser_observe,
)
from zest.research.compiler import ExperimentCompileError
from zest.research.types import ResearchInputError
from zest.tools.registry import load_capability_registry

ORIGIN = "http://127.0.0.1:9"


class BrowserPageCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        load_capability_registry.cache_clear()

    def test_valid_observe_navigate_interact(self) -> None:
        observe = plan_browser_observe(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=ORIGIN,
            path="/app",
        )
        navigate = plan_browser_navigate(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=ORIGIN,
            path="/app",
        )
        interact = plan_browser_interact(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=ORIGIN,
            path="/app",
            browser_context_reference="ctx-1",
            page_reference="page-1",
            element_reference="el-0",
            snapshot_fingerprint="fp",
            kind="click",
        )
        self.assertEqual(observe.required_capability, "browser.page")
        self.assertEqual(observe.action, "observe")
        self.assertEqual(observe.side_effect_level, 0)
        self.assertEqual(navigate.action, "navigate")
        self.assertEqual(navigate.side_effect_level, 0)
        self.assertEqual(interact.action, "interact")
        self.assertEqual(interact.side_effect_level, 1)
        self.assertIsNotNone(observe.capability_definition_fingerprint)

    def test_unknown_action_rejected(self) -> None:
        from zest.research.browser_page import plan_browser_page, BrowserPageIntent

        with self.assertRaises(ResearchInputError):
            plan_browser_page(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                action="evaluate",
                intent=BrowserPageIntent(authorized_origin=ORIGIN, path="/app"),
            )

    def test_extra_args_rejected(self) -> None:
        from zest.research.compiler import ExperimentIntent, compile_experiment_intent

        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="browser.page",
                    action="observe",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": ORIGIN,
                        "path": "/app",
                        "script": "alert(1)",
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="browser.page.v1",
                )
            )
        self.assertEqual(ctx.exception.reason_code, "UNEXPECTED_ARGUMENT")

    def test_invalid_target_path_rejected(self) -> None:
        with self.assertRaises(ExperimentCompileError):
            plan_browser_navigate(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                authorized_origin=ORIGIN,
                path="http://127.0.0.1/app",
            )

    def test_interact_cannot_become_se0(self) -> None:
        from zest.research.browser_page import plan_browser_page, BrowserPageIntent

        with self.assertRaises(ExperimentCompileError) as ctx:
            plan_browser_page(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                action="interact",
                intent=BrowserPageIntent(
                    authorized_origin=ORIGIN,
                    path="/app",
                    browser_context_reference="ctx-1",
                    page_reference="page-1",
                    element_reference="el-0",
                    snapshot_fingerprint="fp",
                    kind="click",
                ),
                requested_side_effect=0,
            )
        self.assertEqual(ctx.exception.reason_code, "RISK_UNDERSTATEMENT")

    def test_arbitrary_selector_and_js_forbidden(self) -> None:
        from zest.research.compiler import ExperimentIntent, compile_experiment_intent

        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="browser.page",
                    action="observe",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": ORIGIN,
                        "path": "/app",
                        "javascript": "document.querySelector('a')",
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="browser.page.v1",
                )
            )

    def test_g19_lineage_helper_rejects_cookies_and_bodies(self) -> None:
        template = http_template_from_network_event(
            {
                "representability": "REPRESENTABLE",
                "method": "GET",
                "path": "/app",
            },
            authorized_origin=ORIGIN,
        )
        self.assertEqual(template.method, "GET")
        self.assertEqual(template.path, "/app")
        with self.assertRaises(ResearchInputError):
            http_template_from_network_event(
                {
                    "representability": "REPRESENTABLE",
                    "method": "GET",
                    "path": "/app",
                    "headers": {"Cookie": "sid=secret"},
                },
                authorized_origin=ORIGIN,
            )
        with self.assertRaises(ResearchInputError):
            http_template_from_network_event(
                {
                    "representability": "NOT_REPRESENTABLE",
                    "method": "POST",
                    "path": "/app",
                },
                authorized_origin=ORIGIN,
            )


if __name__ == "__main__":
    unittest.main()
