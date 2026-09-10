from __future__ import annotations

import json
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.preflight import _model_readiness_check
from zest.integrations.models.cli_session import (
    parse_codex_model_configurations,
    probe_codex_cli,
)
from zest.integrations.models.json_schemas import DIAGNOSTIC_OUTPUT_SCHEMA
from zest.interface.zestd import _UnavailableModel, _compose_codex_model
from zest.platform.argv_process import ArgvProcessResult, ArgvProcessStatus
from zest.platform.health import ComponentHealth
from zest.research.model_runtime import AuthMode, RuntimeClass, RuntimeKind


def _configurations():
    return parse_codex_model_configurations(
        "primary=gpt-primary,secondary=gpt-secondary",
        executable="codex",
    )


class ZestdModelCompositionTests(unittest.TestCase):
    def test_passive_auth_readiness_cannot_qualify_production(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="codex 1.0",
                )
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout="logged in",
            )

        configuration = _configurations()[0]
        passive = probe_codex_cli(
            configuration=configuration,
            runner=runner,
            live_probe=False,
        )
        requested_live: list[bool] = []

        def passive_only_probe(*, configuration, live_probe):
            self.assertEqual(configuration.configuration_id, "primary")
            requested_live.append(live_probe)
            return passive

        model, fallback_models, probe_model = _compose_codex_model(
            _configurations(),
            probe_codex=passive_only_probe,
        )
        readiness = probe_model()

        self.assertIsInstance(model, _UnavailableModel)
        self.assertEqual(requested_live, [True])
        self.assertIsNotNone(readiness.candidate)
        assert readiness.candidate is not None
        self.assertTrue(readiness.candidate.authenticated)
        self.assertFalse(readiness.candidate.available)
        self.assertFalse(readiness.candidate.structured_output_compatible)
        self.assertIs(readiness.health.health, ComponentHealth.UNAVAILABLE)
        self.assertFalse(_model_readiness_check(readiness).passed)
        self.assertFalse(any(len(argv) >= 2 and argv[1] == "exec" for argv in calls))

    def test_canonical_live_probe_qualifies_once_with_cli_identity_and_schema(self) -> None:
        calls: list[tuple[str, ...]] = []
        captured: dict[str, object] = {}

        def runner(argv, stdin_bytes=None):
            calls.append(argv)
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="codex 1.0",
                )
            if len(argv) >= 2 and argv[1] == "login":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="logged in",
                )
            captured["schema"] = json.loads(
                Path(argv[argv.index("--output-schema") + 1]).read_text(encoding="utf-8")
            )
            captured["stdin"] = stdin_bytes
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout='{"diagnostic":true}',
            )

        def live_probe(**kwargs):
            self.assertIs(kwargs["live_probe"], True)
            return probe_codex_cli(runner=runner, **kwargs)

        model, fallback_models, probe_model = _compose_codex_model(
            _configurations(),
            probe_codex=live_probe,
        )
        readiness = probe_model()
        call_count = len(calls)

        self.assertEqual(
            len(fallback_models),
            1,
        )
        self.assertEqual(
            fallback_models[0].runtime_identity.runtime_id,
            "secondary",
        )

        self.assertNotIsInstance(model, _UnavailableModel)
        self.assertEqual(model.adapter_identity, "codex.cli.session")
        self.assertIs(readiness.health.health, ComponentHealth.HEALTHY)
        self.assertTrue(_model_readiness_check(readiness).passed)
        self.assertIsNotNone(readiness.candidate)
        assert readiness.candidate is not None
        identity = readiness.candidate.identity
        self.assertIs(identity.runtime_kind, RuntimeKind.CLI_SESSION)
        self.assertIs(identity.runtime_class, RuntimeClass.AGENT_RUNTIME)
        self.assertIs(identity.auth_mode, AuthMode.AUTHENTICATED_CLI_SESSION)
        self.assertEqual(identity.adapter_id, "codex.cli.session")
        self.assertEqual(identity.runtime_id, "primary")
        self.assertEqual(captured["schema"], DIAGNOSTIC_OUTPUT_SCHEMA)
        diagnostic_stdin = captured["stdin"] or b""
        self.assertIn(b"context_fingerprint=codex-diagnostic", diagnostic_stdin)
        self.assertIn(b'payload={"diagnostic":true}', diagnostic_stdin)
        self.assertEqual(
            len([argv for argv in calls if len(argv) >= 2 and argv[1] == "exec"]),
            1,
        )

        self.assertIs(probe_model(), readiness)
        self.assertEqual(len(calls), call_count)

    def test_failed_live_probe_fails_closed_without_secondary_fallback(self) -> None:
        secret = "SENTINEL-PROMPT-SECRET"
        exec_models: list[str] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="codex 1.0",
                )
            if len(argv) >= 2 and argv[1] == "login":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="logged in",
                )
            exec_models.append(argv[argv.index("-m") + 1])
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=17,
                stdout=f"provider echoed {secret}",
                stderr=f"failure included {secret}",
                stderr_truncated=True,
                reason="non-zero exit",
            )

        model, fallback_models, probe_model = _compose_codex_model(
            _configurations(),
            probe_codex=lambda **kwargs: probe_codex_cli(runner=runner, **kwargs),
        )
        readiness = probe_model()
        check = _model_readiness_check(readiness)

        self.assertIsInstance(model, _UnavailableModel)
        self.assertFalse(check.passed)
        self.assertIn("PROCESS_EXIT_NONZERO", check.detail)
        self.assertIn('"exit_code":17', check.detail)
        self.assertNotIn(secret, check.detail)
        self.assertEqual(exec_models, ["gpt-primary"])
        self.assertIsNotNone(readiness.candidate)
        assert readiness.candidate is not None
        self.assertEqual(readiness.candidate.identity.runtime_id, "primary")
        self.assertIs(readiness.candidate.identity.runtime_kind, RuntimeKind.CLI_SESSION)
        self.assertFalse(readiness.candidate.structured_output_compatible)


if __name__ == "__main__":
    unittest.main()
