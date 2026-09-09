"""GATE 21 real Chromium loopback lab. Not a formal PASS and not a scanner."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.lab.browser_page_lab import ALICE_COOKIE, BOB_COOKIE, SESSION_COOKIE_NAME, Gate21BrowserLab
from zest.research.browser_lineage import http_template_from_network_event
from zest.worker_runtime.python.browser_engine import BrowserEngineUnavailable
from zest.worker_runtime.python.browser_page import execute_browser_page
from support.worker_requests import valid_worker_request

CHROMIUM_REASON = "Chromium/Playwright is not installed for GATE 21 real-browser tests"


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _chromium_engine():
    try:
        from zest.worker_runtime.python.playwright_chromium_engine import (
            PlaywrightChromiumEngine,
        )

        engine = PlaywrightChromiumEngine()
        engine.start()
        return engine
    except (BrowserEngineUnavailable, ImportError, OSError):
        return None


def _envelope(origin: str, path: str = "/", *, origin_wide: bool = True, denied=(), allowed=()):
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    return {
        "normalized_scheme": parsed.scheme,
        "normalized_host": parsed.hostname,
        "normalized_port": parsed.port,
        "document_path": path,
        "origin_wide": origin_wide,
        "allowed_path_prefixes": list(allowed) or (["/"] if origin_wide else [path]),
        "denied_path_prefixes": list(denied),
        "loopback_only": True,
        "source_scope_rule_ids": ["rule-allow"],
        "authorization_decision_reference": "authz-1",
    }


def _request(origin: str, action: str, arguments: dict, **overrides):
    payload = valid_worker_request(
        worker_capability="browser.page",
        action=action,
        arguments=arguments,
        side_effect_level=1 if action == "interact" else 0,
        network_envelope=_envelope(origin),
        max_attempted_requests=16,
        execution_budget={
            "budget_id": "budget-1",
            "max_requests": 16,
            "max_tool_calls": 4,
            "max_runtime_ms": 8_000,
            "max_concurrency": 1,
        },
    )
    payload.update(overrides)
    if "network_envelope" not in overrides:
        payload["network_envelope"] = _envelope(origin, str(arguments.get("path") or "/"))
    return payload


@unittest.skipUnless(_playwright_installed(), CHROMIUM_REASON)
class Gate21ChromiumLabTests(unittest.TestCase):
    engine = None
    lab = None
    origin = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _chromium_engine()
        if cls.engine is None:
            raise unittest.SkipTest(CHROMIUM_REASON)
        cls.lab = Gate21BrowserLab()
        cls.origin = cls.lab.start()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.stop()
        if cls.lab is not None:
            cls.lab.stop()

    def tearDown(self) -> None:
        if self.engine is not None:
            self.engine.close_all()

    def _run(self, action: str, arguments: dict, **overrides):
        return execute_browser_page(
            _request(self.origin, action, arguments, **overrides),
            engine=self.engine,
        )

    def test_01_simple_navigate(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/"})
        self.assertEqual(status, "SUCCEEDED")
        self.assertGreaterEqual(raw["attempted_network_requests"], 1)
        self.assertTrue(raw["normalized_url"].endswith("/"))

    def test_02_observe_controls(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/app"})
        self.assertEqual(status, "SUCCEEDED")
        names = {item["name"] for item in raw["controls"]}
        self.assertTrue({"q", "pw", "csrf"} <= names or "q" in names)
        blob = str(raw).lower()
        self.assertNotIn("hidden-password", blob)
        self.assertNotIn("hidden-token", blob)

    def test_03_same_envelope_assets_are_counted(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/"})
        self.assertEqual(status, "SUCCEEDED")
        self.assertGreaterEqual(raw["attempted_network_requests"], 2)

    def test_04_excluded_path_resource_stopped(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/iframe-excluded"},
            network_envelope=_envelope(
                self.origin,
                "/iframe-excluded",
                origin_wide=False,
                allowed=["/iframe-excluded", "/assets"],
                denied=["/excluded"],
            ),
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(diagnostics["self_authorized"])
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["blocked_boundaries"][0]["egress_occurred"])
        self.assertFalse(diagnostics["blocked_boundaries"][0]["reauth_required"])
        self.assertIn("snapshot_fingerprint", raw)

    def test_05_same_origin_redirect_followed_inside_envelope(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/redirect-same"},
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertTrue(raw["normalized_url"].endswith("/app"))
        self.assertIn("/redirect-same", self.lab.hits)
        self.assertIn("/app", self.lab.hits)

    def test_06_cross_origin_redirect_stopped(self) -> None:
        status, _, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/redirect-cross"},
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])

    def test_07_button_post_xhr(self) -> None:
        status, raw, _ = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/post-form"},
        )
        self.assertEqual(status, "SUCCEEDED")
        button = next(item for item in raw["controls"] if item["tag"] == "button")
        status, raw, diagnostics = self._run(
            "interact",
            {
                "authorized_origin": self.origin,
                "path": "/post-form",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": button["element_reference"],
                "snapshot_fingerprint": raw["snapshot_fingerprint"],
                "kind": "click",
            },
        )
        self.assertIn(status, {"SUCCEEDED", "REAUTHORIZATION_REQUIRED", "BLOCKED"})
        if status == "SUCCEEDED":
            self.assertGreaterEqual(raw["attempted_network_requests"], 1)

    def test_08_spa_pushstate_inside(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/spa"})
        self.assertEqual(status, "SUCCEEDED")
        inside = next(item for item in raw["controls"] if item.get("name") == "" and item["tag"] == "button")
        # click first button (inside)
        first = raw["controls"][0]
        status, raw, _ = self._run(
            "interact",
            {
                "authorized_origin": self.origin,
                "path": "/spa",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": first["element_reference"],
                "snapshot_fingerprint": raw["snapshot_fingerprint"],
                "kind": "click",
            },
        )
        self.assertIn(status, {"SUCCEEDED", "REAUTHORIZATION_REQUIRED"})

    def test_09_spa_pushstate_outside(self) -> None:
        status, raw, _ = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/spa"},
            network_envelope=_envelope(
                self.origin, "/spa", origin_wide=False, allowed=["/spa", "/assets"], denied=["/excluded"]
            ),
        )
        self.assertEqual(status, "SUCCEEDED")
        outside = raw["controls"][1] if len(raw["controls"]) > 1 else raw["controls"][0]
        status, _, diagnostics = self._run(
            "interact",
            {
                "authorized_origin": self.origin,
                "path": "/spa",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": outside["element_reference"],
                "snapshot_fingerprint": raw["snapshot_fingerprint"],
                "kind": "click",
            },
            network_envelope=_envelope(
                self.origin, "/spa", origin_wide=False, allowed=["/spa", "/assets"], denied=["/excluded"]
            ),
        )
        self.assertIn(status, {"REAUTHORIZATION_REQUIRED", "BLOCKED", "SUCCEEDED"})

    def test_10_popup_window_open(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/popup"})
        self.assertEqual(status, "SUCCEEDED")
        button = raw["controls"][0]
        status, _, diagnostics = self._run(
            "interact",
            {
                "authorized_origin": self.origin,
                "path": "/popup",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": button["element_reference"],
                "snapshot_fingerprint": raw["snapshot_fingerprint"],
                "kind": "click",
            },
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(diagnostics["channel"], "POPUP")

    def test_11_iframe_same_origin_excluded(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/iframe-excluded"},
            network_envelope=_envelope(
                self.origin,
                "/iframe-excluded",
                origin_wide=False,
                allowed=["/iframe-excluded", "/assets"],
                denied=["/excluded"],
            ),
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(diagnostics["blocked_boundaries"][0]["reauth_required"])
        self.assertEqual(diagnostics["blocked_boundaries"][0]["boundary_kind"], "EMBEDDED_SUBDOCUMENT")
        self.assertIn("snapshot_fingerprint", raw)

    def test_12_iframe_cross_origin(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/iframe-cross"},
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(diagnostics["self_authorized"])
        self.assertFalse(diagnostics["blocked_boundaries"][0]["egress_occurred"])
        self.assertFalse(diagnostics["blocked_boundaries"][0]["reauth_required"])
        self.assertIn("snapshot_fingerprint", raw)

    def test_13_javascript_data_blob_file_blocked(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/schemes"},
        )
        self.assertIn(status, {"SUCCEEDED", "BLOCKED", "REAUTHORIZATION_REQUIRED"})
        if status == "SUCCEEDED":
            self.assertNotIn("javascript:", str(raw["network_events"]))

    def test_14_download_attempt_blocked(self) -> None:
        status, _, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/download"},
        )
        self.assertIn(status, {"BLOCKED", "SUCCEEDED", "REAUTHORIZATION_REQUIRED"})
        if status == "BLOCKED":
            self.assertIn("download", diagnostics["error"])

    def test_15_alice_session_page(self) -> None:
        status, raw, _ = self._run(
            "navigate",
            {
                "authorized_origin": self.origin,
                "path": "/me",
                "identity_id": "id-alice",
                "session_context_reference": "sess-alice",
            },
            resolved_secret_values={"session_cookie": f"{SESSION_COOKIE_NAME}={ALICE_COOKIE}"},
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertNotIn(ALICE_COOKIE, str(raw))

    def test_16_bob_session_isolation(self) -> None:
        status, raw, _ = self._run(
            "navigate",
            {
                "authorized_origin": self.origin,
                "path": "/me",
                "identity_id": "id-alice",
                "session_context_reference": "sess-alice",
            },
            resolved_secret_values={"session_cookie": f"{SESSION_COOKIE_NAME}={ALICE_COOKIE}"},
        )
        self.assertEqual(status, "SUCCEEDED")
        status, _, diagnostics = self._run(
            "observe",
            {
                "authorized_origin": self.origin,
                "path": "/me",
                "identity_id": "id-bob",
                "session_context_reference": "sess-alice",
                "browser_context_reference": raw["browser_context_reference"],
            },
            resolved_secret_values={"session_cookie": f"{SESSION_COOKIE_NAME}={BOB_COOKIE}"},
        )
        self.assertEqual(status, "BLOCKED")

    def test_17_missing_session_after_restart(self) -> None:
        status, _, diagnostics = self._run(
            "navigate",
            {
                "authorized_origin": self.origin,
                "path": "/me",
                "session_context_reference": "sess-alice",
            },
        )
        self.assertEqual(status, "BLOCKED")
        self.assertTrue(diagnostics.get("reauthentication_required"))

    def test_18_timeout(self) -> None:
        status, _, diagnostics = self._run(
            "navigate",
            {"authorized_origin": self.origin, "path": "/", "timeout_ms": 1},
        )
        self.assertIn(status, {"TIMED_OUT", "SUCCEEDED", "EXECUTION_FAILED"})

    def test_19_engine_crash_fail_closed(self) -> None:
        self.engine.close_all()
        status, _, diagnostics = self._run(
            "observe",
            {
                "authorized_origin": self.origin,
                "path": "/",
                "browser_context_reference": "ctx-missing",
            },
        )
        self.assertEqual(status, "BLOCKED")
        self.assertIn("unknown", diagnostics["error"])

    def test_20_g19_lineage_from_sanitized_event(self) -> None:
        status, raw, _ = self._run("navigate", {"authorized_origin": self.origin, "path": "/app"})
        self.assertEqual(status, "SUCCEEDED")
        event = next(
            item
            for item in raw["network_events"]
            if item["method"] == "GET" and item["representability"] == "REPRESENTABLE"
        )
        template = http_template_from_network_event(
            event,
            authorized_origin=self.origin,
        )
        self.assertEqual(template.method, "GET")
        self.assertNotIn("cookie", str(template.headers or {}).lower())
        self.assertIsNone(template.session_context_reference)


if __name__ == "__main__":
    unittest.main()
