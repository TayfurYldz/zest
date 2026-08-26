from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from e2e.lab.https_transaction_lab import HttpsTransactionLab
from e2e.lab.http_transaction_lab import EXTERNAL_REDIRECT, Gate19HttpLab
from zest.tools.registry import load_capability_registry
from zest.worker_runtime.python.capabilities import execute
from zest.worker_runtime.python.http_transaction import execute_http_transaction
from support.worker_requests import valid_worker_request


def _request(origin: str, **argument_overrides):
    arguments = {
        "authorized_origin": origin,
        "method": "GET",
        "path": "/ok",
    }
    arguments.update(argument_overrides)
    return valid_worker_request(
        worker_capability="http.transaction",
        action="read",
        arguments=arguments,
    )


class HttpTransactionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Gate19HttpLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_authorized_get_succeeds(self) -> None:
        status, raw, diagnostics = execute(_request(self.origin))
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["status_code"], 200)
        self.assertEqual(raw["json_value_kind"], "object")
        self.assertIn("ok", raw["json_top_level_keys"])
        self.assertNotIn("cookie", str(raw).lower())
        self.assertFalse(raw["self_authorized"])

    def test_required_user_agent_reaches_local_fixture(self) -> None:
        status, raw, diagnostics = execute(
            _request(self.origin, path="/ua", headers={"User-Agent": "-BugBounty-example-123"})
        )
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["json_value_kind"], "object")

    def test_authorized_https_get_succeeds(self) -> None:
        lab = HttpsTransactionLab()
        origin = lab.start()
        try:
            status, raw, diagnostics = execute(_request(origin))
        finally:
            lab.stop()
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["status_code"], 200)
        self.assertEqual(raw["json_top_level_keys"], ["ok", "transport"])
        self.assertTrue(raw["url"].startswith("https://127.0.0.1:"))
        self.assertFalse(raw["self_authorized"])

    def test_redirect_not_followed(self) -> None:
        status, raw, diagnostics = execute(_request(self.origin, path="/redirect"))
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertTrue(diagnostics["redirect"])
        self.assertFalse(diagnostics["followed"])
        self.assertTrue(diagnostics["requires_core_re_evaluation"])
        self.assertEqual(diagnostics["location"], EXTERNAL_REDIRECT)
        self.assertFalse(diagnostics["self_authorized"])
        self.assertNotEqual(raw.get("status_code"), 200)

    def test_unsupported_scheme_denied(self) -> None:
        request = _request("ftp://127.0.0.1:9")
        request["network_envelope"] = {
            "normalized_scheme": "ftp",
            "normalized_host": "127.0.0.1",
            "normalized_port": 9,
            "document_path": "/ok",
            "origin_wide": True,
            "allowed_path_prefixes": [],
            "denied_path_prefixes": [],
            "loopback_only": True,
            "source_scope_rule_ids": ["test"],
        }
        status, _, diagnostics = execute_http_transaction(request)
        self.assertEqual(status, "BLOCKED")
        self.assertIn("scheme", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_non_loopback_denied(self) -> None:
        status, _, diagnostics = execute(_request("http://example.com"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_absolute_url_in_path_denied(self) -> None:
        status, _, diagnostics = execute(_request(self.origin, path="http://127.0.0.1/ok"))
        self.assertEqual(status, "BLOCKED")
        self.assertFalse(diagnostics["contacted"])

    def test_protocol_relative_path_denied(self) -> None:
        status, _, diagnostics = execute(_request(self.origin, path="//127.0.0.1/ok"))
        self.assertEqual(status, "BLOCKED")

    def test_encoded_slash_denied(self) -> None:
        status, _, diagnostics = execute(_request(self.origin, path="/ok%2fsecret"))
        self.assertEqual(status, "BLOCKED")

    def test_dot_segments_denied(self) -> None:
        status, _, diagnostics = execute(_request(self.origin, path="/ok/../secret"))
        self.assertEqual(status, "BLOCKED")

    def test_crlf_header_denied(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, headers={"Accept": "a\r\nb"})
        )
        self.assertEqual(status, "BLOCKED")

    def test_host_override_denied(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, headers={"Host": "evil.example"})
        )
        self.assertEqual(status, "BLOCKED")

    def test_cookie_header_denied(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, headers={"Cookie": "session=secret"})
        )
        self.assertEqual(status, "BLOCKED")
        self.assertNotIn("secret", str(diagnostics))

    def test_oversized_headers_denied(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, headers={"Accept": "a" * 200})
        )
        self.assertEqual(status, "BLOCKED")

    def test_response_size_cap_enforced(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, path="/large", max_response_bytes=64)
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("byte bound", diagnostics["error"])

    def test_timeout_deterministic(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, path="/slow", timeout_ms=50)
        )
        self.assertEqual(status, "TIMED_OUT")
        self.assertEqual(diagnostics["error"], "timeout")

    def test_unknown_method_denied(self) -> None:
        status, _, diagnostics = execute(_request(self.origin, method="TRACE"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "SCHEMA_MISMATCH")

    def test_fingerprint_mismatch_denied(self) -> None:
        request = _request(self.origin)
        request["capability_definition_fingerprint"] = "b" * 64
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "DEFINITION_FINGERPRINT_MISMATCH")

    def test_session_reference_denied(self) -> None:
        status, _, diagnostics = execute(
            _request(self.origin, session_context_reference="session-1")
        )
        self.assertEqual(status, "BLOCKED")
        self.assertFalse(diagnostics["self_authorized"])

    def test_worker_cannot_self_authorize(self) -> None:
        status, raw, diagnostics = execute_http_transaction(_request("http://example.com"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertNotIn("authorization_decision_reference", raw)
        self.assertFalse(diagnostics["self_authorized"])

    def test_missing_envelope_fails_closed(self) -> None:
        request = _request(self.origin)
        request.pop("network_envelope")
        status, _, diagnostics = execute_http_transaction(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("network_envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_forged_envelope_host_mismatch_fails(self) -> None:
        request = _request(self.origin)
        request["network_envelope"]["normalized_host"] = "api.example.com"
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("outside authorized network envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_forged_envelope_port_mismatch_fails(self) -> None:
        request = _request(self.origin)
        request["network_envelope"]["normalized_port"] = 9999
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("outside authorized network envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])

    def test_forged_envelope_scheme_mismatch_fails(self) -> None:
        request = _request(self.origin)
        request["network_envelope"]["normalized_scheme"] = "https"
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("outside authorized network envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])

    def test_expanded_envelope_path_outside_prefix_fails(self) -> None:
        request = _request(self.origin, path="/public/users")
        envelope = request["network_envelope"]
        envelope["origin_wide"] = False
        envelope["document_path"] = "/private/users"
        envelope["allowed_path_prefixes"] = ["/private/"]
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("outside authorized network envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_expanded_envelope_denied_prefix_fails(self) -> None:
        request = _request(self.origin, path="/admin/users")
        envelope = request["network_envelope"]
        envelope["origin_wide"] = True
        envelope["denied_path_prefixes"] = ["/admin"]
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("outside authorized network envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_unsigned_envelope_invalid_port_fails(self) -> None:
        request = _request(self.origin)
        request["network_envelope"]["normalized_port"] = 0
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("network_envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_unsigned_envelope_wildcard_host_fails(self) -> None:
        request = _request(self.origin)
        request["network_envelope"]["normalized_host"] = "*.example.com"
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertIn("network_envelope", diagnostics["error"])
        self.assertFalse(diagnostics["contacted"])
        self.assertFalse(diagnostics["self_authorized"])

    def test_no_secret_material_in_success_payload(self) -> None:
        status, raw, diagnostics = execute(_request(self.origin))
        self.assertEqual(status, "SUCCEEDED")
        blob = str(raw).lower() + str(diagnostics).lower()
        self.assertNotIn("password", blob)
        self.assertNotIn("cookie", blob)
        self.assertNotIn("authorization", blob)
        self.assertNotIn("bearer", blob)

    def test_mutate_post_succeeds_through_action_allowlist(self) -> None:
        registry = load_capability_registry()
        capability = registry.get("http.transaction")
        assert capability is not None
        request = valid_worker_request(
            worker_capability="http.transaction",
            action="mutate",
            side_effect_level=1,
            arguments={
                "authorized_origin": self.origin,
                "method": "POST",
                "path": "/ok",
                "body": "{}",
                "content_type": "application/json",
            },
        )
        status, raw, diagnostics = execute(request)
        self.assertEqual(status, "SUCCEEDED")
        self.assertIsNone(diagnostics)
        self.assertEqual(raw["status_code"], 200)


if __name__ == "__main__":
    unittest.main()
