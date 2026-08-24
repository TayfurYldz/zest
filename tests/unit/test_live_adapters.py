from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import pathsetup  # noqa: F401

from integrations.models.availability import UnavailableReason
from integrations.models.common import JsonSchemaModelAdapter, ProviderInvocation, parse_structured_object
from integrations.models.errors import classify_provider_exception
from integrations.models.factory import probe_live_adapter
from integrations.models.json_schemas import FALSIFIER_OUTPUT_SCHEMA, GENERATOR_OUTPUT_SCHEMA
from integrations.models.secrets import REDACTED, SecretReference, redact_secret
from research_os.research.model_port import (
    ModelCallRequest,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    StructuredOutputTransportError,
)

API_KEY_PREFIX = "sk" + "-"


class _FakeTransport:
    adapter_identity = "test.adapter"
    provider_adapter_identity = "test"

    def __init__(self, invocation: ProviderInvocation) -> None:
        self._invocation = invocation
        self.requests: list[ModelCallRequest] = []
        self.schemas: list[object] = []

    def invoke(self, request: ModelCallRequest, schema):
        self.requests.append(request)
        self.schemas.append(schema)
        return self._invocation


class LiveAdapterBoundaryTests(unittest.TestCase):
    def test_missing_sdk_or_key_is_unavailable_not_failure(self) -> None:
        availability = probe_live_adapter(
            "openai",
            model_id="gpt-test",
            env={},
        )
        self.assertFalse(availability.available)
        self.assertIn(
            availability.reason,
            {UnavailableReason.MISSING_SDK, UnavailableReason.MISSING_CREDENTIAL},
        )
        self.assertNotEqual(availability.reason.value, "BENCHMARK_FAILURE")

    def test_unknown_adapter_is_unavailable(self) -> None:
        availability = probe_live_adapter("not-a-vendor", env={})
        self.assertEqual(availability.reason, UnavailableReason.UNKNOWN_ADAPTER)

    def test_secret_is_redacted_and_not_copied_into_request(self) -> None:
        secret = "synthetic-live-secret-value"
        self.assertEqual(redact_secret(f"failed {secret}", secret), f"failed {REDACTED}")
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c1",
            context_fingerprint="fp",
            instructions="propose",
            payload={"research_context": {"note": "no key here"}},
        )
        serialized = json.dumps(dict(request.payload), ensure_ascii=True)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, request.instructions)

    def test_malformed_json_is_structured_output_failure(self) -> None:
        with self.assertRaises(StructuredOutputTransportError):
            parse_structured_object("not-json")

    def test_adapter_does_not_repair_invalid_object(self) -> None:
        transport = _FakeTransport(ProviderInvocation(text='["list-not-object"]'))
        adapter = JsonSchemaModelAdapter(transport)
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c1",
            context_fingerprint="fp",
            instructions="propose",
            payload={"task": "x"},
        )
        with self.assertRaises(StructuredOutputTransportError):
            adapter.complete(request)

    def test_api_adapter_uses_canonical_role_schemas(self) -> None:
        transport = _FakeTransport(ProviderInvocation(text='{"ok": true}'))
        adapter = JsonSchemaModelAdapter(transport)
        adapter.complete(
            ModelCallRequest(
                role=ModelRole.GENERATOR,
                correlation_id="c1",
                context_fingerprint="fp",
                instructions="propose",
                payload={"task": "x"},
            )
        )
        adapter.complete(
            ModelCallRequest(
                role=ModelRole.FALSIFIER,
                correlation_id="c2",
                context_fingerprint="fp",
                instructions="challenge",
                payload={"proposal": {"proposed_claim": "x"}},
            )
        )
        self.assertEqual(transport.schemas[0], GENERATOR_OUTPUT_SCHEMA)
        self.assertEqual(transport.schemas[1], FALSIFIER_OUTPUT_SCHEMA)

    def test_auth_and_rate_limit_are_distinct(self) -> None:
        auth = classify_provider_exception(RuntimeError("401 unauthorized"))
        rate = classify_provider_exception(RuntimeError("429 rate limit"))
        self.assertIsInstance(auth, ProviderAuthError)
        self.assertIsInstance(rate, ProviderRateLimitError)

    def test_content_filter_is_policy_blocked(self) -> None:
        from research_os.research.model_port import ContentPolicyBlockedError

        blocked = classify_provider_exception(RuntimeError("content_filter"))
        self.assertIsInstance(blocked, ContentPolicyBlockedError)

    def test_secret_reference_does_not_echo_value(self) -> None:
        ref = SecretReference("OPENAI_API_KEY")
        self.assertEqual(ref.env_name, "OPENAI_API_KEY")
        self.assertNotIn(API_KEY_PREFIX, ref.env_name)

    def test_openai_transport_maps_auth_without_leaking_key(self) -> None:
        from integrations.models.openai_adapter import OpenAIResponsesTransport

        class Boom:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            class responses:
                @staticmethod
                def create(**kwargs):
                    del kwargs
                    raise RuntimeError("invalid api_key synthetic-secret-live")

        transport = OpenAIResponsesTransport(
            model_id="gpt-test",
            secret=SecretReference("OPENAI_API_KEY"),
            env={"OPENAI_API_KEY": "synthetic-secret-live"},
        )
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c1",
            context_fingerprint="fp",
            instructions="propose",
            payload={"task": "x"},
        )
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=Boom)}):
            with self.assertRaises(ProviderAuthError) as ctx:
                transport.invoke(request, {"type": "object"})
        self.assertNotIn("synthetic-secret-live", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
