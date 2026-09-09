"""Playwright isolation of passive OOS resources vs real authority transitions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.lab.full_lifecycle_lab import OriginServer, _html
from zest.worker_runtime.python.browser_engine import BrowserEngineUnavailable
from zest.worker_runtime.python.browser_page import execute_browser_page
from support.worker_requests import valid_worker_request

CHROMIUM_REASON = "Chromium/Playwright is not installed for passive OOS isolation tests"


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


def _envelope(origin: str, path: str = "/"):
    parsed = urlsplit(origin)
    return {
        "normalized_scheme": parsed.scheme,
        "normalized_host": parsed.hostname,
        "normalized_port": parsed.port,
        "document_path": path,
        "origin_wide": True,
        "allowed_path_prefixes": ["/"],
        "denied_path_prefixes": [],
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


class PassiveOosLab:
    def __init__(self) -> None:
        self.main = OriginServer(name="passive-main")
        self.third = OriginServer(name="passive-third")

    def start(self) -> str:
        third = self.third.start()
        main = self.main.start()
        self.third.pages = {
            "/beacon.js": ("application/javascript", b"window.__third=true;"),
            "/pixel.gif": ("image/gif", b"GIF89a"),
            "/font.css": ("text/css", b"body{color:red}"),
            "/x.woff": ("font/woff", b"font"),
            "/arrive": ("text/html; charset=utf-8", _html("third", "<p>third</p>").encode("utf-8")),
        }
        self.main.pages = {
            "/image": (
                "text/html; charset=utf-8",
                _html(
                    "image",
                    f"<p>home</p><a name='home' href='/'>home</a><img src='{third}/pixel.gif' alt=''>",
                ).encode("utf-8"),
            ),
            "/style": (
                "text/html; charset=utf-8",
                _html(
                    "style",
                    f"<p>home</p><a name='home' href='/'>home</a><link rel='stylesheet' href='{third}/font.css'>",
                ).encode("utf-8"),
            ),
            "/script": (
                "text/html; charset=utf-8",
                _html(
                    "script",
                    f"<p>home</p><a name='home' href='/'>home</a><script src='{third}/beacon.js'></script>",
                ).encode("utf-8"),
            ),
            "/same": (
                "text/html; charset=utf-8",
                _html(
                    "same",
                    "<p>home</p><a name='home' href='/'>home</a><link rel='stylesheet' href='/assets/app.css'>",
                ).encode("utf-8"),
            ),
            "/assets/app.css": ("text/css", b"body{color:#111}"),
            "/leave-link": (
                "text/html; charset=utf-8",
                _html(
                    "leave-link",
                    f"<a href='{third}/arrive' name='leave'>leave</a>",
                ).encode("utf-8"),
            ),
            "/js-leave": (
                "text/html; charset=utf-8",
                _html(
                    "js-leave",
                    f"<script>window.location.href='{third}/arrive';</script>",
                ).encode("utf-8"),
            ),
        }
        self.main.redirects = {"/leave-redirect": f"{third}/arrive"}
        return main

    def stop(self) -> None:
        self.main.stop()
        self.third.stop()


@unittest.skipUnless(_playwright_installed(), CHROMIUM_REASON)
class PassiveOosPlaywrightTests(unittest.TestCase):
    engine = None
    lab = None
    origin = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _chromium_engine()
        if cls.engine is None:
            raise unittest.SkipTest(CHROMIUM_REASON)
        cls.lab = PassiveOosLab()
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
        if self.lab is not None:
            self.lab.third.hits.clear()
            self.lab.main.hits.clear()

    def _run(self, action: str, arguments: dict, **overrides):
        return execute_browser_page(
            _request(self.origin, action, arguments, **overrides),
            engine=self.engine,
        )

    def _assert_passive(self, status, raw, diagnostics, *, resource_type: str) -> None:
        self.assertEqual(status, "SUCCEEDED")
        self.assertEqual(self.lab.third.hits, [])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["authority_granted"])
        match = next(
            item
            for item in diagnostics["blocked_boundaries"]
            if item["resource_type"] == resource_type
        )
        self.assertFalse(match["egress_occurred"])
        self.assertFalse(match["reauth_required"])
        self.assertTrue(match["main_observation_preserved"])
        self.assertEqual(match["route_decision"], "BLOCK_PASSIVE_BOUNDARY")
        self.assertIn("snapshot_fingerprint", raw)
        names = {item["name"] for item in raw["controls"]}
        self.assertIn("home", names)

    def test_a1_oos_image(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/image"}
        )
        self._assert_passive(status, raw, diagnostics, resource_type="image")

    def test_a2_oos_stylesheet(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/style"}
        )
        self._assert_passive(status, raw, diagnostics, resource_type="stylesheet")

    def test_a3_oos_script(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/script"}
        )
        self._assert_passive(status, raw, diagnostics, resource_type="script")

    def test_a4_main_frame_oos_redirect(self) -> None:
        status, _, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/leave-redirect"}
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(self.lab.third.hits, [])

    def test_a5_explicit_oos_link_navigation(self) -> None:
        status, raw, _ = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/leave-link"}
        )
        self.assertEqual(status, "SUCCEEDED")
        link = next(item for item in raw["controls"] if item["name"] == "leave")
        status, _, diagnostics = self._run(
            "interact",
            {
                "authorized_origin": self.origin,
                "path": "/leave-link",
                "browser_context_reference": raw["browser_context_reference"],
                "page_reference": raw["page_reference"],
                "element_reference": link["element_reference"],
                "snapshot_fingerprint": raw["snapshot_fingerprint"],
                "kind": "click",
            },
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(self.lab.third.hits, [])

    def test_a6_same_origin_resource(self) -> None:
        status, raw, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/same"}
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertNotIn("blocked_boundaries", raw)
        self.assertEqual(self.lab.third.hits, [])
        self.assertIn("GET /assets/app.css", self.lab.main.hits)

    def test_js_location_main_frame_requires_reauth(self) -> None:
        status, _, diagnostics = self._run(
            "navigate", {"authorized_origin": self.origin, "path": "/js-leave"}
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(self.lab.third.hits, [])


if __name__ == "__main__":
    unittest.main()
