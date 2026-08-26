"""http.raw_exchange worker: catalog framing only, loopback envelope, redirect STOP."""

from __future__ import annotations

import unittest
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from e2e.lab.http_transaction_lab import EXTERNAL_REDIRECT, Gate19HttpLab
from zest.tools.registry import load_capability_registry
from zest.worker_runtime.python.http_raw_exchange import execute_http_raw_exchange


def _envelope(origin: str, path: str = "/ok") -> dict:
    parsed = urlsplit(origin)
    return {
        "normalized_scheme": parsed.scheme,
        "normalized_host": parsed.hostname,
        "normalized_port": parsed.port or 80,
        "document_path": path,
        "origin_wide": True,
        "allowed_path_prefixes": ["/"],
        "denied_path_prefixes": [],
        "loopback_only": True,
        "source_scope_rule_ids": ["rule-allow"],
        "authorization_decision_reference": "audit-1",
    }


def _request(origin: str, *, path: str = "/ok", profile: str = "http1_canonical") -> dict:
    registry = load_capability_registry()
    capability = registry.get("http.raw_exchange")
    assert capability is not None
    return {
        "worker_capability": "http.raw_exchange",
        "action": "probe",
        "capability_version": capability.version,
        "capability_definition_fingerprint": capability.definition_fingerprint,
        "arguments": {
            "authorized_origin": origin,
            "path": path,
            "framing_profile": profile,
            "control": "single_parser_control",
            "lane": "http_request_smuggling_desync",
        },
        "network_envelope": _envelope(origin, path),
    }


class HttpRawExchangeWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        load_capability_registry.cache_clear()
        self.lab = Gate19HttpLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_canonical_profile_observes_loopback_response(self) -> None:
        status, raw, diagnostics = execute_http_raw_exchange(_request(self.origin))
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["framing_profile"], "http1_canonical")
        self.assertGreaterEqual(raw["status_code"], 200)
        self.assertFalse(raw["self_authorized"])

    def test_redirect_stops_without_following(self) -> None:
        status, raw, diagnostics = execute_http_raw_exchange(
            _request(self.origin, path="/redirect")
        )
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertTrue(diagnostics["requires_core_re_evaluation"])
        self.assertFalse(diagnostics["followed"])
        self.assertEqual(diagnostics["location"], EXTERNAL_REDIRECT)
        self.assertTrue(raw["stopped"])

    def test_cl_te_profile_is_catalog_bound(self) -> None:
        status, raw, _diagnostics = execute_http_raw_exchange(
            _request(self.origin, profile="http1_cl_te")
        )
        self.assertIn(status, {"SUCCEEDED", "TIMED_OUT", "EXECUTION_FAILED"})
        if status == "SUCCEEDED":
            self.assertEqual(raw["framing_profile"], "http1_cl_te")
            self.assertEqual(raw["write_count"], 1)

    def test_outside_envelope_does_not_contact(self) -> None:
        request = _request(self.origin)
        request["arguments"]["authorized_origin"] = "http://127.0.0.1:1"
        status, _raw, diagnostics = execute_http_raw_exchange(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertFalse(diagnostics["contacted"])
