from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.worker_runtime.python.browser_engine import (
    BrowserRuntimeLimits,
    InMemoryBrowserEngine,
)
from zest.worker_runtime.python.browser_envelope import parse_envelope
from zest.worker_runtime.python.browser_page import execute_browser_page
from zest.worker_runtime.python.capabilities import execute as execute_packaged_worker
from support.worker_requests import valid_worker_request

ORIGIN = "http://127.0.0.1:9"
COOKIE = "alice-session-material"


def _envelope(path: str = "/app", *, origin_wide: bool = True, denied=(), allowed=()):
    return {
        "normalized_scheme": "http",
        "normalized_host": "127.0.0.1",
        "normalized_port": 9,
        "document_path": path,
        "origin_wide": origin_wide,
        "allowed_path_prefixes": list(allowed) or (["/"] if origin_wide else [path]),
        "denied_path_prefixes": list(denied),
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
    if "max_attempted_requests" not in overrides:
        payload["max_attempted_requests"] = 16
    return payload


class BrowserPageWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = InMemoryBrowserEngine()
        self.engine.start()
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {
                "html": (
                    "<button id='go'>go</button>"
                    "<input name='q' placeholder='search' value='secret-input'>"
                    "<input type='password' name='pw' value='hidden-password'>"
                    "<input type='hidden' name='csrf' value='hidden-token'>"
                )
            },
        )

    def tearDown(self) -> None:
        self.engine.stop()

    def _execute(self, request):
        return execute_browser_page(request, engine=self.engine)

    def test_navigate_and_observe_bounded_controls(self) -> None:
        status, raw, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertGreaterEqual(raw["attempted_network_requests"], 1)
        self.assertIn("snapshot_fingerprint", raw)
        blob = str(raw).lower()
        self.assertNotIn("secret-input", blob)
        self.assertNotIn("hidden-password", blob)
        self.assertNotIn("hidden-token", blob)
        self.assertNotIn(COOKIE, blob)
        names = {item["name"] for item in raw["controls"]}
        self.assertIn("q", names)
        for control in raw["controls"]:
            self.assertNotIn("value", control)

    def test_packaged_worker_accepts_production_observe_shape_before_runtime(self) -> None:
        request = valid_worker_request(
            worker_capability="browser.page",
            action="observe",
            target_reference="https://www.dyson.tw/",
            arguments={"authorized_origin": "https://www.dyson.tw", "path": "/"},
            side_effect_level=0,
        )

        status, raw, diagnostics = execute_packaged_worker(request)

        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(raw, {})
        self.assertIsNotNone(diagnostics)
        self.assertNotEqual(diagnostics.get("reason_code"), "SCHEMA_MISMATCH")
        self.assertIn("network_envelope", diagnostics.get("error", ""))

    def test_observe_allows_moderate_modern_page_fanout(self) -> None:
        resources = [
            {"url": f"{ORIGIN}/asset-{index}.js", "resource_type": "script"}
            for index in range(15)
        ]
        self.engine.seed_page(
            f"{ORIGIN}/store",
            {"html": "<button name='buy'>buy</button>"},
            {"resources": resources},
        )

        status, raw, diagnostics = self._execute(
            _request(
                "observe",
                {"authorized_origin": ORIGIN, "path": "/store"},
                max_attempted_requests=150,
            )
        )

        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["attempted_network_requests"], 16)
        self.assertNotIn("partial", raw)

    def test_observe_budget_cap_after_document_emits_partial_snapshot(self) -> None:
        resources = [
            {"url": f"{ORIGIN}/asset-{index}.js", "resource_type": "script"}
            for index in range(8)
        ]
        self.engine.seed_page(
            f"{ORIGIN}/store",
            {"html": "<button name='buy'>buy</button>"},
            {"resources": resources},
        )

        status, raw, diagnostics = self._execute(
            _request(
                "observe",
                {"authorized_origin": ORIGIN, "path": "/store"},
                max_attempted_requests=4,
            )
        )

        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNotNone(diagnostics)
        self.assertTrue(raw["partial"])
        self.assertTrue(raw["budget_exhausted"])
        self.assertFalse(raw["coverage_complete"])
        self.assertEqual(raw["attempted_network_requests"], 4)
        self.assertEqual(raw["capped_network_requests"], 4)
        self.assertIn("snapshot_fingerprint", raw)

    def test_https_origin_is_supported_and_other_schemes_remain_blocked(self) -> None:
        https_origin = "https://127.0.0.1:443"
        self.engine.seed_page(
            f"{https_origin}/secure",
            {"html": "<p>secure</p>"},
        )

        https_envelope = {
            **_envelope(path="/secure"),
            "normalized_scheme": "https",
            "normalized_port": 443,
        }

        status, raw, diagnostics = self._execute(
            _request(
                "navigate",
                {
                    "authorized_origin": https_origin,
                    "path": "/secure",
                },
                network_envelope=https_envelope,
            )
        )

        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertGreaterEqual(raw["attempted_network_requests"], 1)

        status, _, diagnostics = self._execute(
            _request(
                "navigate",
                {
                    "authorized_origin": "ftp://127.0.0.1",
                    "path": "/secure",
                },
            )
        )

        self.assertEqual(status, "BLOCKED")
        self.assertIn("scheme", diagnostics["error"])

    def test_stale_element_and_snapshot_rejected(self) -> None:
        status, raw, _ = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "SUCCEEDED")
        interact = _request(
            "interact",
            {
                "authorized_origin": ORIGIN,
                "path": "/app",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": "el-0",
                "snapshot_fingerprint": "stale",
                "kind": "click",
            },
        )
        status, _, diagnostics = self._execute(interact)
        self.assertEqual(status, "BLOCKED")
        self.assertIn("snapshot", diagnostics["error"])
        status, _, diagnostics = self._execute(
            _request(
                "interact",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/app",
                    "browser_context_reference": raw["browser_context_reference"],
                    "page_reference": raw["page_reference"],
                    "element_reference": "el-missing",
                    "snapshot_fingerprint": raw["snapshot_fingerprint"],
                    "kind": "click",
                },
            )
        )
        self.assertEqual(status, "BLOCKED")

    def test_alice_lease_cannot_be_bob(self) -> None:
        alice = _request(
            "navigate",
            {
                "authorized_origin": ORIGIN,
                "path": "/app",
                "identity_id": "id-alice",
                "session_context_reference": "sess-alice",
            },
            resolved_secret_values={"session_cookie": COOKIE},
        )
        status, raw, _ = self._execute(alice)
        self.assertEqual(status, "SUCCEEDED")
        bob = _request(
            "observe",
            {
                "authorized_origin": ORIGIN,
                "path": "/app",
                "identity_id": "id-bob",
                "session_context_reference": "sess-alice",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
            },
            resolved_secret_values={"session_cookie": COOKIE},
        )
        status, _, diagnostics = self._execute(bob)
        self.assertEqual(status, "BLOCKED")
        self.assertIn("binding", diagnostics["error"])

    def test_run_and_origin_mismatch_fail_closed(self) -> None:
        status, raw, _ = self._execute(
            _request(
                "navigate",
                {"authorized_origin": ORIGIN, "path": "/app", "identity_id": "id-alice"},
            )
        )
        self.assertEqual(status, "SUCCEEDED")
        other_run = _request(
            "observe",
            {
                "authorized_origin": ORIGIN,
                "path": "/app",
                "identity_id": "id-alice",
                "browser_context_reference": raw["browser_context_reference"],
            },
        )
        other_run["correlation"] = dict(other_run["correlation"])
        other_run["correlation"]["research_run_id"] = "run-other"
        status, _, diagnostics = self._execute(other_run)
        self.assertEqual(status, "BLOCKED")
        wrong_origin = _request(
            "observe",
            {
                "authorized_origin": "http://127.0.0.1:8",
                "path": "/app",
                "identity_id": "id-alice",
                "browser_context_reference": raw["browser_context_reference"],
            },
            network_envelope={**_envelope(), "normalized_port": 8},
        )
        status, _, diagnostics = self._execute(wrong_origin)
        self.assertEqual(status, "BLOCKED")

    def test_missing_session_material_after_restart(self) -> None:
        request = _request(
            "navigate",
            {
                "authorized_origin": ORIGIN,
                "path": "/app",
                "session_context_reference": "sess-alice",
            },
        )
        status, _, diagnostics = self._execute(request)
        self.assertEqual(status, "BLOCKED")
        self.assertTrue(diagnostics["reauthentication_required"])

    def test_excluded_path_and_cross_origin_stop(self) -> None:
        self.engine.seed_page(f"{ORIGIN}/ok", {"html": "<p>ok</p>"})
        self.engine.seed_page(
            f"{ORIGIN}/ok",
            {"html": "<p>ok</p>"},
            {
                "resources": [
                    {"url": f"{ORIGIN}/excluded/secret", "resource_type": "script"},
                ]
            },
        )
        denied = _request(
            "navigate",
            {"authorized_origin": ORIGIN, "path": "/ok"},
            network_envelope=_envelope(path="/ok", origin_wide=False, denied=["/excluded"], allowed=["/ok"]),
        )
        status, raw, diagnostics = self._execute(denied)
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(diagnostics["self_authorized"])
        self.assertFalse(diagnostics["followed"])
        self.assertEqual(diagnostics["blocked_boundaries"][0]["route_decision"], "BLOCK_PASSIVE_BOUNDARY")
        self.assertFalse(diagnostics["blocked_boundaries"][0]["reauth_required"])
        self.assertFalse(diagnostics["blocked_boundaries"][0]["egress_occurred"])
        self.assertIn("snapshot_fingerprint", raw)
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {"html": "<p>app</p>"},
            {"redirect": "http://example.com/out"},
        )
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(diagnostics["channel"], "REDIRECT")
        self.assertFalse(diagnostics["followed"])

    def test_popup_iframe_websocket_sw_download(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {"html": '<button data-popup-url="/excluded/secret">open</button>'},
        )
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(diagnostics["channel"], "POPUP")
        self.engine.close_all()
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {"html": "<p>app</p>"},
            {"iframe_src": "/excluded/secret"},
        )
        status, raw, diagnostics = self._execute(
            _request(
                "navigate",
                {"authorized_origin": ORIGIN, "path": "/app"},
                network_envelope=_envelope(denied=["/excluded"], allowed=["/app"], origin_wide=False),
            )
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(diagnostics["blocked_boundaries"][0]["boundary_kind"], "EMBEDDED_SUBDOCUMENT")
        self.assertFalse(diagnostics["blocked_boundaries"][0]["reauth_required"])
        self.assertIn("snapshot_fingerprint", raw)
        self.engine.close_all()
        self.engine.seed_page(f"{ORIGIN}/app", {"html": "<p>app</p>"}, {"websocket": True})
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("websocket", diagnostics["error"])
        self.engine.close_all()
        self.engine.seed_page(f"{ORIGIN}/app", {"html": "<p>app</p>"}, {"service_worker": True})
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "BLOCKED")
        self.engine.close_all()
        self.engine.seed_page(f"{ORIGIN}/app", {"html": "<p>app</p>"}, {"download": True})
        status, _, diagnostics = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("download", diagnostics["error"])

    def test_budget_counts_every_attempted_request(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {"html": "<p>app</p>"},
            {
                "resources": [
                    {"url": f"{ORIGIN}/a.js", "resource_type": "script"},
                    {"url": f"{ORIGIN}/b.css", "resource_type": "stylesheet"},
                    {"url": f"{ORIGIN}/c.png", "resource_type": "image"},
                ]
            },
        )
        status, raw, _ = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertEqual(raw["attempted_network_requests"], 4)
        status, _, diagnostics = self._execute(
            _request(
                "navigate",
                {"authorized_origin": ORIGIN, "path": "/app"},
                max_attempted_requests=2,
            )
        )
        self.assertEqual(status, "BUDGET_EXHAUSTED")

    def test_huge_dom_is_bounded(self) -> None:
        controls = [{"tag": "button", "name": f"n{i}"} for i in range(80)]
        self.engine.seed_page(f"{ORIGIN}/app", {"html": "<p>app</p>", "controls": controls})
        status, raw, _ = self._execute(
            _request("navigate", {"authorized_origin": ORIGIN, "path": "/app"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertEqual(len(raw["controls"]), 32)

    def test_password_fill_rejected_and_cookie_never_emitted(self) -> None:
        status, raw, _ = self._execute(
            _request(
                "navigate",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/app",
                    "session_context_reference": "sess-alice",
                },
                resolved_secret_values={"session_cookie": COOKIE},
            )
        )
        self.assertEqual(status, "SUCCEEDED")
        password = next(item for item in raw["controls"] if item["input_type"] == "password")
        status, _, diagnostics = self._execute(
            _request(
                "interact",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/app",
                    "session_context_reference": "sess-alice",
                    "browser_context_reference": raw["browser_context_reference"],
                    "page_reference": raw["page_reference"],
                    "element_reference": password["element_reference"],
                    "snapshot_fingerprint": raw["snapshot_fingerprint"],
                    "kind": "fill",
                    "value": "hello",
                },
                resolved_secret_values={"session_cookie": COOKIE},
            )
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("password", diagnostics["error"])
        blob = str(raw) + str(diagnostics)
        self.assertNotIn(COOKIE, blob)

    def test_spa_outside_envelope_freezes(self) -> None:
        self.engine.seed_page(
            f"{ORIGIN}/app",
            {"html": "<button>go</button>"},
            {"spa_url": f"{ORIGIN}/excluded/secret"},
        )
        status, raw, _ = self._execute(
            _request(
                "navigate",
                {"authorized_origin": ORIGIN, "path": "/app"},
                network_envelope=_envelope(origin_wide=False, allowed=["/app"], denied=["/excluded"]),
            )
        )
        self.assertEqual(status, "SUCCEEDED")
        status, _, diagnostics = self._execute(
            _request(
                "interact",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/app",
                    "browser_context_reference": raw["browser_context_reference"],
                    "page_reference": raw["page_reference"],
                    "element_reference": raw["controls"][0]["element_reference"],
                    "snapshot_fingerprint": raw["snapshot_fingerprint"],
                    "kind": "click",
                },
                network_envelope=_envelope(origin_wide=False, allowed=["/app"], denied=["/excluded"]),
            )
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(diagnostics["channel"], "SPA")

    def test_network_events_omit_secret_headers(self) -> None:
        status, raw, _ = self._execute(
            _request(
                "navigate",
                {
                    "authorized_origin": ORIGIN,
                    "path": "/app",
                    "session_context_reference": "sess-alice",
                },
                resolved_secret_values={"session_cookie": COOKIE},
            )
        )
        self.assertEqual(status, "SUCCEEDED")
        for event in raw["network_events"]:
            self.assertNotIn("headers", event)
            self.assertNotIn("cookie", str(event).lower())
            self.assertNotIn(COOKIE, str(event))

    def test_sequential_navigate_evicts_oldest_context_at_cap(self) -> None:
        first = self._execute(_request("navigate", {"authorized_origin": ORIGIN, "path": "/app"}))
        second = self._execute(_request("navigate", {"authorized_origin": ORIGIN, "path": "/app"}))
        third = self._execute(_request("navigate", {"authorized_origin": ORIGIN, "path": "/app"}))
        self.assertEqual(first[0], "SUCCEEDED")
        self.assertEqual(second[0], "SUCCEEDED")
        self.assertEqual(third[0], "SUCCEEDED")
        self.assertEqual(len(self.engine._states), 2)

    def test_envelope_parser_rejects_wildcards(self) -> None:
        self.assertIsNone(parse_envelope({**_envelope(), "normalized_host": "*.example"}))
        limits = BrowserRuntimeLimits(max_attempted_network_requests_per_action=1)
        self.assertEqual(limits.max_pages_per_context, 1)


if __name__ == "__main__":
    unittest.main()
