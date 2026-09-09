from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.worker_runtime.python.browser_engine import InMemoryBrowserEngine
from zest.worker_runtime.python.browser_page import execute_browser_page
from support.worker_requests import valid_worker_request

ORIGIN = "http://127.0.0.1:9"
THIRD = "http://127.0.0.1:8"


def _envelope():
    return {
        "normalized_scheme": "http",
        "normalized_host": "127.0.0.1",
        "normalized_port": 9,
        "document_path": "/",
        "origin_wide": True,
        "allowed_path_prefixes": ["/"],
        "denied_path_prefixes": [],
        "loopback_only": True,
        "source_scope_rule_ids": ["rule-allow"],
    }


def _request(action: str, arguments: dict, **overrides):
    payload = valid_worker_request(
        worker_capability="browser.page",
        action=action,
        arguments=arguments,
        side_effect_level=1 if action == "interact" else 0,
        network_envelope=_envelope(),
        max_attempted_requests=16,
    )
    payload.update(overrides)
    if "network_envelope" not in overrides:
        payload["network_envelope"] = _envelope()
    return payload


class PassiveBoundaryEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = InMemoryBrowserEngine()
        self.engine.start()

    def tearDown(self) -> None:
        self.engine.stop()

    def _execute(self, request):
        return execute_browser_page(request, engine=self.engine)

    def _assert_boundary(self, diagnostics, *, resource_type: str | None = None) -> None:
        self.assertIsNotNone(diagnostics)
        self.assertFalse(diagnostics["self_authorized"])
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["authority_granted"])
        boundary = diagnostics["blocked_boundaries"][0]
        self.assertFalse(boundary["egress_occurred"])
        self.assertFalse(boundary["reauth_required"])
        self.assertFalse(boundary["envelope_allowed"])
        self.assertTrue(boundary["blocked_before_egress"])
        self.assertTrue(boundary["main_observation_preserved"])
        self.assertEqual(boundary["route_decision"], "BLOCK_PASSIVE_BOUNDARY")
        if resource_type is not None:
            self.assertEqual(boundary["resource_type"], resource_type)

    def test_a1_oos_image_preserves_main_observation(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/",
            {"html": "<p>home</p><a name='home' href='/'>home</a>"},
            {"resources": [{"url": f"{THIRD}/pixel.gif", "resource_type": "image"}]},
        )
        status, raw, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self._assert_boundary(diagnostics, resource_type="image")
        self.assertIn("snapshot_fingerprint", raw)
        names = {item["name"] for item in raw["controls"]}
        self.assertIn("home", names)

    def test_a2_oos_stylesheet_and_font(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/",
            {"html": "<p name='home'>home</p>"},
            {
                "resources": [
                    {"url": f"{THIRD}/font.css", "resource_type": "stylesheet"},
                    {"url": f"{THIRD}/x.woff", "resource_type": "font"},
                ]
            },
        )
        status, raw, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/"})
        )
        self.assertEqual(status, "SUCCEEDED")
        kinds = {item["resource_type"] for item in diagnostics["blocked_boundaries"]}
        self.assertEqual(kinds, {"stylesheet", "font"})
        self.assertTrue(all(not item["reauth_required"] for item in diagnostics["blocked_boundaries"]))
        self.assertIn("snapshot_fingerprint", raw)

    def test_a3_oos_script_is_blocked_without_authority(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/",
            {"html": "<p name='home'>home</p>"},
            {"resources": [{"url": f"{THIRD}/beacon.js", "resource_type": "script"}]},
        )
        status, raw, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self._assert_boundary(diagnostics, resource_type="script")
        self.assertIn("snapshot_fingerprint", raw)

    def test_a4_main_frame_oos_redirect_requires_reauth(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/leave-redirect",
            {"html": "<p>leaving</p>"},
            {"redirect": f"{THIRD}/arrive"},
        )
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/leave-redirect"})
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(diagnostics["channel"], "REDIRECT")

    def test_a5_explicit_oos_link_navigation_requires_reauth(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/",
            {"html": "<a href='http://127.0.0.1:8/arrive' name='leave'>leave</a>"},
        )
        status, raw, _ = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/"})
        )
        self.assertEqual(status, "SUCCEEDED")
        link = next(item for item in raw["controls"] if item["name"] == "leave")
        status, _, diagnostics = self._execute(
            _request(
                "interact",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/",
                    "browser_context_reference": raw["browser_context_reference"],
                    "page_reference": raw["page_reference"],
                    "element_reference": link["element_reference"],
                    "snapshot_fingerprint": raw["snapshot_fingerprint"],
                    "kind": "click",
                },
            )
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_a6_same_origin_resource_has_no_boundary(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/",
            {"html": "<p name='home'>home</p>"},
            {"resources": [{"url": f"{ORIGIN}/assets/app.css", "resource_type": "stylesheet"}]},
        )
        status, raw, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertNotIn("blocked_boundaries", raw)
        types = {item["resource_type"] for item in raw["network_events"]}
        self.assertIn("stylesheet", types)


if __name__ == "__main__":
    unittest.main()
