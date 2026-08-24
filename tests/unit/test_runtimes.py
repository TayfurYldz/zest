from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from integrations.models.cli_session import CodexCliSessionAdapter
from integrations.models.common import JsonSchemaModelAdapter, ProviderInvocation
from integrations.models.external_agent import ExternalAgentRuntimeAdapter
from integrations.models.local_runtime import LocalModelRuntimeAdapter, probe_local_model
from research_os.platform.argv_process import ArgvProcessResult, ArgvProcessStatus
from research_os.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelPortError,
    ModelRole,
    ProviderTimeoutError,
    RuntimeUnavailableError,
    StructuredOutputTransportError,
)
from research_os.research.model_runtime import RuntimeClass, RuntimeKind, cli_session_runtime_identity
from research_os.tools.capabilities import CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY


class _FakeTransport:
    adapter_identity = "test.adapter"
    provider_adapter_identity = "test"

    def __init__(self, invocation: ProviderInvocation) -> None:
        self._invocation = invocation

    def invoke(self, request: ModelCallRequest, schema):
        del request, schema
        return self._invocation


def _request() -> ModelCallRequest:
    return ModelCallRequest(
        role=ModelRole.GENERATOR,
        correlation_id="c1",
        context_fingerprint="fp",
        instructions="propose",
        payload={"note": "ok"},
    )


def _generator_transport() -> dict[str, object]:
    return {
        "proposed_claim": "diagnostic claim",
        "rationale": "diagnostic rationale",
        "source_references": None,
        "assumptions": None,
        "expected_security_relevance": None,
        "unresolved_questions": None,
        "suggested_disconfirming_test": "echo mismatch",
        "suggested_capability": "diagnostic.echo",
        "novelty_basis": None,
    }


class RuntimeAdapterTests(unittest.TestCase):
    def test_api_adapter_attaches_api_identity_distinct_from_cli(self) -> None:
        adapter = JsonSchemaModelAdapter(
            _FakeTransport(
                ProviderInvocation(
                    text=json.dumps(_generator_transport(), separators=(",", ":"))
                )
            )
        )
        result = adapter.complete(_request())
        self.assertIsNotNone(result.runtime_identity)
        assert result.runtime_identity is not None
        self.assertEqual(result.runtime_identity.runtime_kind, RuntimeKind.API)
        self.assertEqual(result.runtime_identity.runtime_class, RuntimeClass.INFERENCE_RUNTIME)
        cli = cli_session_runtime_identity(adapter_id="codex.cli.session", runtime_id="codex-cli")
        self.assertNotEqual(result.runtime_identity.configuration_fingerprint, cli.configuration_fingerprint)
        serialized = json.dumps(result.runtime_identity.to_mapping(), ensure_ascii=True)
        self.assertNotIn("sk-", serialized)
        self.assertNotIn("api_key", serialized)

    def test_unavailable_cli_is_unavailable(self) -> None:
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="diagnostic-model",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.UNAVAILABLE, argv=argv, reason="missing"
            ),
        )
        with self.assertRaises(RuntimeUnavailableError):
            adapter.complete(_request())

    def test_malformed_cli_output_is_structured_output_invalid(self) -> None:
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="diagnostic-model",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED, argv=argv, exit_code=0, stdout="not-json"
            ),
        )
        with self.assertRaises(StructuredOutputTransportError):
            adapter.complete(_request())

    def test_cli_timeout_is_timed_out(self) -> None:
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="diagnostic-model",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.TIMED_OUT, argv=argv, reason="timeout"
            ),
        )
        with self.assertRaises(ProviderTimeoutError):
            adapter.complete(_request())

    def test_cli_content_policy_is_blocked(self) -> None:
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="diagnostic-model",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr="content policy blocked",
            ),
        )
        with self.assertRaises(ContentPolicyBlockedError):
            adapter.complete(_request())

    def test_unrestricted_cli_capability_is_rejected(self) -> None:
        with self.assertRaises(ModelPortError):
            CodexCliSessionAdapter(allowed_capabilities=("*",))
        with self.assertRaises(ModelPortError):
            CodexCliSessionAdapter(allowed_capabilities=("danger-full-access",))

    def test_local_and_external_contracts_are_unavailable(self) -> None:
        availability = probe_local_model()
        self.assertFalse(availability.available)
        with self.assertRaises(RuntimeUnavailableError):
            LocalModelRuntimeAdapter().complete(_request())
        with self.assertRaises(ModelPortError):
            ExternalAgentRuntimeAdapter(allowed_capabilities=())
        with self.assertRaises(ModelPortError):
            ExternalAgentRuntimeAdapter(allowed_capabilities=("unrestricted",))
        adapter = ExternalAgentRuntimeAdapter(allowed_capabilities=("external.diagnostic.ping",))
        with self.assertRaises(RuntimeUnavailableError):
            adapter.complete(_request())
        envelope = adapter.untrusted_result_envelope({"echo": "pong"})
        self.assertTrue(envelope["untrusted"])
        self.assertTrue(envelope["not_authorization"])
        self.assertTrue(envelope["not_evidence"])
        self.assertTrue(envelope["not_finding"])


if __name__ == "__main__":
    unittest.main()
