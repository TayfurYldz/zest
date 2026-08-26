from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.platform.secrets import SecretReference, SecretScheme
from zest.safe_data import (
    REDACTED,
    SecretMaterialError,
    SessionReference,
    redact_secret_keys,
    reject_secret_keys,
    sanitize_exception,
)


class RecursiveSecretTests(unittest.TestCase):
    def test_nested_token_rejected(self) -> None:
        with self.assertRaises(SecretMaterialError) as ctx:
            reject_secret_keys({"nested": {"token": "sk-live-must-not-echo"}}, "payload")
        message = str(ctx.exception)
        self.assertNotIn("sk-live-must-not-echo", message)
        self.assertIn("payload.nested.token", message)

    def test_nested_authorization_list_rejected(self) -> None:
        with self.assertRaises(SecretMaterialError):
            reject_secret_keys({"nested": [{"authorization": "Bearer abc"}]}, "payload")

    def test_refresh_token_rejected(self) -> None:
        with self.assertRaises(SecretMaterialError):
            reject_secret_keys({"x": {"refresh_token": "..."}}, "payload")

    def test_safe_references_allowed(self) -> None:
        payload = {
            "secret_ref": SecretReference(SecretScheme.ENV_REFERENCE, "OPENAI_API_KEY"),
            "session_ref": SessionReference("local-authenticated-cli-session"),
        }
        cleaned = reject_secret_keys(payload, "payload")
        self.assertIn("secret_ref", cleaned)
        self.assertIn("session_ref", cleaned)

    def test_redaction_does_not_echo_secret(self) -> None:
        redacted = redact_secret_keys({"nested": {"api_key": "sk-live"}}, "payload")
        self.assertEqual(redacted["nested"]["api_key"], REDACTED)
        self.assertNotIn("sk-live", str(redacted))

    def test_exception_sanitizer_omits_headers(self) -> None:
        class Boom(Exception):
            headers = {"Authorization": "Bearer secret-token"}
            status_code = 401

        safe = sanitize_exception(Boom("Authorization: Bearer secret-token"))
        self.assertEqual(safe["exception_type"], "Boom")
        self.assertNotIn("secret-token", str(safe))
        self.assertNotIn("Bearer secret-token", str(safe))


if __name__ == "__main__":
    unittest.main()
