from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.model_port import ContentPolicyBlockedError, ModelCallRequest, ModelPortError, ModelRole
from zest.research.model_runtime import (
    AuthMode,
    RuntimeClass,
    RuntimeKind,
    api_runtime_identity,
    cli_session_runtime_identity,
    reject_secret_keys,
)
from zest.research.types import ResearchInputError


def _request(**overrides) -> ModelCallRequest:
    values = dict(
        role=ModelRole.GENERATOR,
        correlation_id="c1",
        context_fingerprint="fp",
        instructions="propose",
        payload={"note": "ok"},
    )
    values.update(overrides)
    return ModelCallRequest(**values)


class ModelRuntimeTests(unittest.TestCase):
    def test_api_and_cli_identities_differ(self) -> None:
        api = api_runtime_identity(adapter_id="openai.responses", runtime_id="openai")
        cli = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli",
            session_reference="local-authenticated-cli-session",
        )
        self.assertEqual(api.runtime_kind, RuntimeKind.API)
        self.assertEqual(api.runtime_class, RuntimeClass.INFERENCE_RUNTIME)
        self.assertEqual(api.auth_mode, AuthMode.API_KEY)
        self.assertEqual(cli.runtime_kind, RuntimeKind.CLI_SESSION)
        self.assertEqual(cli.runtime_class, RuntimeClass.AGENT_RUNTIME)
        self.assertEqual(cli.auth_mode, AuthMode.AUTHENTICATED_CLI_SESSION)
        self.assertNotEqual(api.configuration_fingerprint, cli.configuration_fingerprint)
        self.assertNotEqual(api.to_mapping(), cli.to_mapping())
        self.assertFalse(api.to_mapping()["contains_secrets"])
        terra = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-terra",
            model_id="gpt-5.6-terra",
        )
        gpt55 = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-gpt55",
            model_id="gpt-5.5",
        )
        self.assertNotEqual(terra.configuration_fingerprint, gpt55.configuration_fingerprint)

    def test_model_request_rejects_secret_keys(self) -> None:
        with self.assertRaises(ModelPortError):
            _request(payload={"api_key": "sk-secret"})
        with self.assertRaises(ResearchInputError):
            reject_secret_keys({"token": "nope"}, "payload")

    def test_session_reference_rejects_secret_material(self) -> None:
        with self.assertRaises(ResearchInputError):
            cli_session_runtime_identity(
                adapter_id="codex.cli.session",
                runtime_id="codex-cli",
                session_reference="sk-secret-session",
            )

    def test_content_policy_block_is_runtime_outcome_not_hypothesis_rejection(self) -> None:
        error = ContentPolicyBlockedError("provider safety refusal")
        self.assertIsInstance(error, ModelPortError)
        self.assertNotIn("REJECTED", type(error).__name__)
        self.assertNotIn("hypothesis", str(error).lower())


if __name__ == "__main__":
    unittest.main()
