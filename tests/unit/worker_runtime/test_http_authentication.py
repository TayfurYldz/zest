from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from e2e.lab.http_auth_lab import ALICE_PASSWORD, ALICE_USERNAME, SESSION_COOKIE_NAME, Gate20AuthLab
from zest.worker_runtime.python.capabilities import execute
from zest.worker_runtime.python.http_authentication import execute_http_authentication
from support.worker_requests import valid_worker_request


def _login_request(origin: str, **argument_overrides):
    arguments = {
        "authorized_origin": origin,
        "path": "/login",
        "username": ALICE_USERNAME,
        "username_field": "username",
        "password_secret_name": "login_password",
        "session_cookie_name": SESSION_COOKIE_NAME,
        "session_context_id": "session-alice",
        "identity_id": "id-alice",
    }
    arguments.update(argument_overrides)
    request = valid_worker_request(
        worker_capability="http.authentication",
        action="login",
        arguments=arguments,
        side_effect_level=1,
    )
    request["resolved_secret_values"] = {"login_password": ALICE_PASSWORD}
    return request


class HttpAuthenticationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Gate20AuthLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_login_establishes_ephemeral_cookie_without_echoing_it(self) -> None:
        status, raw, diagnostics = execute_http_authentication(_login_request(self.origin))
        self.assertEqual(status, "SUCCEEDED")
        self.assertTrue(raw["session_established"])
        self.assertIn("_ephemeral_session_cookie", raw)
        self.assertNotIn(ALICE_PASSWORD, str(raw))
        self.assertNotIn(ALICE_PASSWORD, str(diagnostics))

    def test_missing_password_secret_blocked(self) -> None:
        request = _login_request(self.origin)
        request.pop("resolved_secret_values")
        status, _, diagnostics = execute_http_authentication(request)
        self.assertEqual(status, "BLOCKED")
        self.assertFalse(diagnostics["self_authorized"])
        self.assertNotIn(ALICE_PASSWORD, str(diagnostics))

    def test_redirect_requires_core_reevaluation(self) -> None:
        status, _, diagnostics = execute(_login_request(self.origin, path="/login-redirect"))
        self.assertEqual(status, "REAUTHORIZATION_REQUIRED")
        self.assertTrue(diagnostics["requires_core_re_evaluation"])
        self.assertFalse(diagnostics["followed"])
        self.assertFalse(diagnostics["self_authorized"])
        self.assertEqual(diagnostics["raw_location"], "/login")
        self.assertTrue(str(diagnostics["response_url"]).endswith("/login-redirect"))

    def test_fingerprint_mismatch_denied(self) -> None:
        request = _login_request(self.origin)
        request["capability_definition_fingerprint"] = "b" * 64
        status, _, diagnostics = execute(request)
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "DEFINITION_FINGERPRINT_MISMATCH")

    def test_invalid_password_does_not_establish_session(self) -> None:
        request = _login_request(self.origin)
        request["resolved_secret_values"] = {"login_password": "wrong"}
        status, raw, diagnostics = execute(request)
        self.assertEqual(status, "SUCCEEDED")
        self.assertFalse(raw["session_established"])
        self.assertNotIn("_ephemeral_session_cookie", raw)
        self.assertNotIn("wrong", str(raw))
        self.assertNotIn("wrong", str(diagnostics))


if __name__ == "__main__":
    unittest.main()
