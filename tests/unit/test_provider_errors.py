from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from zest.integrations.models.cli_session import (
    CodexCliSessionAdapter,
    CodexDiagnosticEchoAdapter,
    probe_codex_cli,
)
from zest.integrations.models.errors import classify_provider_exception
from zest.platform.argv_process import ArgvProcessResult, ArgvProcessStatus
from zest.platform.readiness import ReadinessStage
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelPortError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRuntimeError,
    ProviderUsageLimitError,
)
from zest.tools.capabilities import CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY


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


class _Structured(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status
        self.code = code
        self.body = {"error": {"code": code, "type": code}}


class ProviderErrorClassificationTests(unittest.TestCase):
    def test_structured_policy_403_is_content_policy(self) -> None:
        mapped = classify_provider_exception(
            _Structured(403, "content_policy", "safety refusal")
        )
        self.assertIsInstance(mapped, ContentPolicyBlockedError)

    def test_structured_auth_403_is_auth(self) -> None:
        mapped = classify_provider_exception(
            _Structured(403, "invalid_api_key", "key rejected")
        )
        self.assertIsInstance(mapped, ProviderAuthError)

    def test_429_is_rate_limited(self) -> None:
        mapped = classify_provider_exception(_Structured(429, "rate_limit_error", "slow down"))
        self.assertIsInstance(mapped, ProviderRateLimitError)

    def test_unknown_403_is_conservative_runtime(self) -> None:
        mapped = classify_provider_exception(_Structured(403, "forbidden", "nope"))
        self.assertIsInstance(mapped, ProviderRuntimeError)
        self.assertNotIsInstance(mapped, ProviderAuthError)


class CodexRateLimitTaxonomyTests(unittest.TestCase):
    def _request(self) -> ModelCallRequest:
        return ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="rate-taxonomy",
            context_fingerprint="fp-rate-taxonomy",
            instructions="propose",
            payload={"note": "ok"},
        )

    def _adapter_for_stderr(self, stderr: str) -> CodexCliSessionAdapter:
        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr=stderr,
            )

        return CodexCliSessionAdapter(
            allowed_capabilities=(
                CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
            ),
            executable="codex",
            model="diagnostic-model",
            configuration_id="codex-cli-diagnostic",
            runner=runner,
        )

    def test_usage_quota_is_machine_distinguishable_but_still_rate_limited(
        self,
    ) -> None:
        adapter = self._adapter_for_stderr(
            "You've hit your usage limit. Try again at 18:00."
        )

        with self.assertRaises(ProviderUsageLimitError) as ctx:
            adapter.complete(self._request())

        self.assertIsInstance(
            ctx.exception,
            ProviderRateLimitError,
        )

        # Provider reset details are intentionally not propagated into
        # research/admission truth.
        self.assertNotIn(
            "18:00",
            str(ctx.exception),
        )

    def test_generic_rate_limit_is_not_misclassified_as_usage_quota(
        self,
    ) -> None:
        adapter = self._adapter_for_stderr(
            "429 rate limit: slow down"
        )

        with self.assertRaises(ProviderRateLimitError) as ctx:
            adapter.complete(self._request())

        self.assertNotIsInstance(
            ctx.exception,
            ProviderUsageLimitError,
        )


class CodexReadinessTests(unittest.TestCase):
    def test_version_only_is_not_benchmark_compatible(self) -> None:
        calls = []

        def runner(argv, stdin_bytes=None):
            calls.append(argv)
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="codex-cli 0.0.0",
                )
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr="not logged in",
            )

        availability = probe_codex_cli(runner=runner)
        self.assertIsNotNone(availability.readiness)
        assert availability.readiness is not None
        self.assertTrue(availability.readiness.installed)
        self.assertTrue(availability.readiness.version_known)
        self.assertFalse(availability.readiness.auth_ready)
        self.assertFalse(availability.readiness.benchmark_compatible)
        self.assertEqual(availability.readiness.stage, ReadinessStage.VERSION_KNOWN)

    def test_diagnostic_echo_adapter_is_not_modelport_compatible(self) -> None:
        adapter = CodexDiagnosticEchoAdapter()
        self.assertFalse(adapter.MODELPORT_COMPATIBLE)
        with self.assertRaises(ModelPortError):
            adapter.complete(
                ModelCallRequest(
                    role=ModelRole.GENERATOR,
                    correlation_id="c1",
                    context_fingerprint="fp",
                    instructions="propose",
                    payload={"note": "ok"},
                )
            )

    def test_real_adapter_consumes_request_via_stdin(self) -> None:
        captured = {}

        def runner(argv, stdin_bytes=None):
            captured["argv"] = argv
            captured["stdin"] = stdin_bytes
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=json.dumps(_generator_transport(), separators=(",", ":")),
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="diagnostic-model",
            runner=runner,
        )
        result = adapter.complete(
            ModelCallRequest(
                role=ModelRole.GENERATOR,
                correlation_id="c1",
                context_fingerprint="fp",
                instructions="unique-instruction-text",
                payload={"note": "payload-marker"},
            )
        )
        self.assertEqual(result.structured_output["proposed_claim"], "diagnostic claim")
        self.assertNotIn("source_references", result.structured_output)
        self.assertIn(b"unique-instruction-text", captured["stdin"] or b"")
        self.assertIn(b"payload-marker", captured["stdin"] or b"")
        self.assertIn(b"Do not wrap it in result_json", captured["stdin"] or b"")
        self.assertNotIn("unique-instruction-text", captured["argv"])
        self.assertTrue(adapter.MODELPORT_COMPATIBLE)


if __name__ == "__main__":
    unittest.main()
