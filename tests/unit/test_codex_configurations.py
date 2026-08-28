from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

import zest.integrations.models.cli_session as cli_session
from zest.integrations.models.cli_session import (
    CODEX_MODELS_ENV,
    CODEX_DIAGNOSTIC_TIMEOUT_MS,
    CODEX_SKIP_GIT_REPO_CHECK_FLAG,
    NON_GIT_WORKING_DIRECTORY_DETAIL,
    STRUCTURED_OUTPUT_SCHEMA,
    CodexCliConfigurationError,
    CodexCliSessionAdapter,
    derive_codex_configuration_id,
    load_codex_model_configurations,
    parse_codex_model_configurations,
    probe_codex_cli,
    probe_codex_configurations,
)
from zest.integrations.models.discovery import (
    ProbeMode,
    Readiness,
    discover_configured_runtimes,
    gate_04b_status,
)
from zest.integrations.models.json_schemas import (
    DIAGNOSTIC_OUTPUT_SCHEMA,
    FALSIFIER_OUTPUT_SCHEMA,
    GENERATOR_APPLICATION_SCHEMA,
    GENERATOR_OUTPUT_SCHEMA,
    decode_transport_output,
    decode_transport_output_for_request,
    schema_for_role,
    schema_for_request,
    validate_strict_transport_schema,
)
from zest.interface.cli import build_status_snapshot
from zest.maturity import GATE_04B_STATUS
from zest.platform.argv_process import ArgvProcessResult, ArgvProcessStatus
from zest.platform.readiness import ReadinessStage
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RuntimeProcessError,
    StructuredOutputTransportError,
)
from zest.research.model_runtime import RuntimeOutcome, cli_session_runtime_identity
from zest.research.output_contracts import (
    ACCEPTED_NOVELTY_BASIS,
    DIAGNOSTIC_CONTRACT,
    FALSIFIER_CONTRACT,
    GENERATOR_CONTRACT,
)
from zest.research.proposals import parse_novelty_basis
from zest.research.types import ResearchInputError
from zest.tools.capabilities import CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY

API_KEY_PREFIX = "sk" + "-"


def _transport_stdout(inner: dict) -> str:
    return json.dumps(inner, separators=(",", ":"))


def _request() -> ModelCallRequest:
    return ModelCallRequest(
        role=ModelRole.GENERATOR,
        correlation_id="c1",
        context_fingerprint="fp",
        instructions="propose",
        payload={"note": "ok"},
    )


def _generator_transport(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
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
    payload.update(overrides)
    return payload


def _falsifier_transport(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "alternative_explanations": None,
        "missing_preconditions": None,
        "contradictory_source_references": None,
        "required_negative_controls": None,
        "ambiguity": None,
        "reasons_not_to_test": None,
        "proposed_disconfirming_observation": "echo mismatch",
    }
    payload.update(overrides)
    return payload


def _argv_runner(allowed_models: frozenset[str]):
    def runner(argv, stdin_bytes=None):
        del stdin_bytes
        if argv[-1] == "--version":
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout="codex 0.0.0",
            )
        if len(argv) >= 2 and argv[1] == "login":
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout="logged in",
            )
        if "danger-full-access" in argv or "--yolo" in argv:
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr="forbidden flag",
            )
        model = None
        if "-m" in argv:
            model = argv[argv.index("-m") + 1]
        if model in allowed_models:
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=_transport_stdout({"diagnostic": True}),
            )
        return ArgvProcessResult(
            status=ArgvProcessStatus.PROCESS_FAILED,
            argv=argv,
            exit_code=1,
            stderr=f"unknown model {model}",
        )

    return runner


class CodexConfigurationParseTests(unittest.TestCase):
    def test_defaults_are_operational_not_architecture(self) -> None:
        configs = parse_codex_model_configurations(None)
        self.assertEqual(
            [(item.configuration_id, item.model) for item in configs],
            [("codex-cli-terra", "gpt-5.6-terra"), ("codex-cli-gpt55", "gpt-5.5")],
        )
        for item in configs:
            self.assertEqual(item.runtime_kind, "CLI_SESSION")
            self.assertEqual(item.runtime_class, "AGENT_RUNTIME")
            self.assertTrue(item.ephemeral)
            self.assertEqual(item.sandbox, "read-only")
            self.assertTrue(item.runtime_configuration()["isolated_non_repository_cwd"])
            self.assertTrue(item.runtime_configuration()["skip_git_repo_check"])

    def test_model_override_and_derived_ids(self) -> None:
        configs = parse_codex_model_configurations("gpt-5.6-terra,gpt-5.5")
        self.assertEqual(configs[0].configuration_id, derive_codex_configuration_id("gpt-5.6-terra"))
        self.assertEqual(configs[1].configuration_id, derive_codex_configuration_id("gpt-5.5"))
        self.assertEqual(configs[0].model, "gpt-5.6-terra")
        self.assertEqual(configs[1].model, "gpt-5.5")

    def test_explicit_ids(self) -> None:
        configs = load_codex_model_configurations(
            {CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5"}
        )
        self.assertEqual(configs[0].configuration_id, "codex-cli-terra")
        self.assertEqual(configs[1].configuration_id, "codex-cli-gpt55")

    def test_duplicate_configuration_fails_closed(self) -> None:
        with self.assertRaises(CodexCliConfigurationError):
            parse_codex_model_configurations("gpt-5.5,gpt-5.5")
        with self.assertRaises(CodexCliConfigurationError):
            parse_codex_model_configurations("alpha=gpt-5.5,alpha=gpt-5.4")

    def test_empty_entries_fail_closed(self) -> None:
        with self.assertRaises(CodexCliConfigurationError):
            parse_codex_model_configurations("gpt-5.5,")
        with self.assertRaises(CodexCliConfigurationError):
            parse_codex_model_configurations(" ,gpt-5.5")
        with self.assertRaises(CodexCliConfigurationError):
            parse_codex_model_configurations("codex-cli-terra=")


class CodexIndependentReadinessTests(unittest.TestCase):
    def test_two_valid_models_are_independently_benchmark_compatible(self) -> None:
        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        runner = _argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"}))
        results = probe_codex_configurations(env=env, runner=runner, live_probe=True)
        self.assertEqual(len(results), 2)
        for item in results:
            assert item.readiness is not None
            self.assertTrue(item.readiness.installed)
            self.assertTrue(item.readiness.version_known)
            self.assertTrue(item.readiness.auth_ready)
            self.assertTrue(item.readiness.diagnostic_ready)
            self.assertTrue(item.readiness.modelport_compatible)
            self.assertTrue(item.readiness.benchmark_compatible)
            self.assertEqual(item.readiness.stage, ReadinessStage.BENCHMARK_COMPATIBLE)
            self.assertTrue(item.available)
        self.assertNotEqual(results[0].configuration_fingerprint, results[1].configuration_fingerprint)

    def test_one_valid_one_unavailable_does_not_infer_compatibility(self) -> None:
        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        results = probe_codex_configurations(
            env=env,
            runner=_argv_runner(frozenset({"gpt-5.6-terra"})),
            live_probe=True,
        )
        by_id = {item.configuration_id: item for item in results}
        terra = by_id["codex-cli-terra"]
        gpt55 = by_id["codex-cli-gpt55"]
        assert terra.readiness is not None
        assert gpt55.readiness is not None
        self.assertTrue(terra.readiness.benchmark_compatible)
        self.assertFalse(gpt55.readiness.benchmark_compatible)
        self.assertTrue(gpt55.readiness.auth_ready)
        self.assertFalse(gpt55.available)

    def test_exec_argv_is_documented_and_model_specific(self) -> None:
        captured = {}

        def runner(argv, stdin_bytes=None):
            captured["argv"] = argv
            captured["stdin"] = stdin_bytes
            schema_path = argv[argv.index("--output-schema") + 1]
            captured["schema"] = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=_transport_stdout(_generator_transport()),
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=runner,
        )
        result = adapter.complete(_request())
        argv = captured["argv"]
        self.assertEqual(result.structured_output["proposed_claim"], "diagnostic claim")
        self.assertNotIn("source_references", result.structured_output)
        self.assertEqual(argv[1], "exec")
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ephemeral", argv)
        self.assertIn(CODEX_SKIP_GIT_REPO_CHECK_FLAG, argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.5")
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("danger-full-access", argv)
        self.assertNotIn("dangerously-bypass-approvals-and-sandbox", argv)
        self.assertNotIn("--full-auto", argv)
        self.assertIn(b"propose", captured["stdin"] or b"")
        self.assertIn(b"Do not wrap it in result_json", captured["stdin"] or b"")
        self.assertEqual(captured["schema"]["additionalProperties"], False)
        self.assertNotIn("result_json", captured["schema"]["properties"])
        self.assertEqual(captured["schema"], GENERATOR_OUTPUT_SCHEMA)

    def test_caller_supplied_working_directory_does_not_skip_git_check(self) -> None:
        captured = {}

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            captured["argv"] = argv
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=_transport_stdout(_generator_transport()),
            )

        with tempfile.TemporaryDirectory(prefix="zest-codex-test-") as tmp:
            adapter = CodexCliSessionAdapter(
                allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
                executable="codex",
                model="gpt-5.5",
                configuration_id="codex-cli-gpt55",
                runner=runner,
                working_directory=Path(tmp),
            )
            result = adapter.complete(_request())
        self.assertEqual(result.structured_output["proposed_claim"], "diagnostic claim")
        self.assertNotIn(CODEX_SKIP_GIT_REPO_CHECK_FLAG, captured["argv"])

    def test_runtime_fingerprint_records_isolated_skip_git_mode(self) -> None:
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=_argv_runner(frozenset({"gpt-5.5"})),
        )
        expected = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-gpt55",
            session_reference="local-authenticated-cli-session",
            model_id="gpt-5.5",
            runtime_configuration={
                "sandbox": "read-only",
                "ephemeral": True,
                "ignore_user_config": True,
                "isolated_non_repository_cwd": True,
                "skip_git_repo_check": True,
                "executable": "codex",
            },
        )
        legacy = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-gpt55",
            session_reference="local-authenticated-cli-session",
            model_id="gpt-5.5",
            runtime_configuration={
                "sandbox": "read-only",
                "ephemeral": True,
                "ignore_user_config": True,
                "executable": "codex",
            },
        )
        self.assertEqual(
            adapter.runtime_identity.configuration_fingerprint,
            expected.configuration_fingerprint,
        )
        self.assertNotEqual(
            adapter.runtime_identity.configuration_fingerprint,
            legacy.configuration_fingerprint,
        )

    def test_live_probe_uses_named_bounded_diagnostic_timeout(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_argv(argv, *, config=None, stdin_bytes=None, timeout_ms=None):
            del stdin_bytes
            calls.append({"argv": argv, "timeout_ms": timeout_ms, "cwd": getattr(config, "working_directory", None)})
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="codex 0.149.1",
                )
            if len(argv) >= 2 and argv[1] == "login":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout="Logged in using ChatGPT",
                )
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=_transport_stdout({"diagnostic": True}),
            )

        original_run_argv = cli_session.run_argv
        original_resolve = cli_session.resolve_executable
        cli_session.run_argv = fake_run_argv
        cli_session.resolve_executable = lambda _name: "codex"
        try:
            probe = probe_codex_cli(
                configuration=parse_codex_model_configurations(
                    "codex-cli-gpt55=gpt-5.5", executable="codex"
                )[0],
                live_probe=True,
            )
        finally:
            cli_session.run_argv = original_run_argv
            cli_session.resolve_executable = original_resolve

        self.assertTrue(probe.available)
        exec_calls = [item for item in calls if _is_codex_exec(item["argv"])]
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(exec_calls[0]["timeout_ms"], CODEX_DIAGNOSTIC_TIMEOUT_MS)
        self.assertIsNotNone(exec_calls[0]["cwd"])

    def test_independent_fingerprints_include_model(self) -> None:
        first = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-terra",
            model_id="gpt-5.6-terra",
            runtime_configuration={"sandbox": "read-only", "ephemeral": True, "executable": "codex"},
        )
        second = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id="codex-cli-gpt55",
            model_id="gpt-5.5",
            runtime_configuration={"sandbox": "read-only", "ephemeral": True, "executable": "codex"},
        )
        self.assertNotEqual(first.configuration_fingerprint, second.configuration_fingerprint)
        self.assertEqual(first.runtime_kind.value, "CLI_SESSION")
        serialized = json.dumps(first.to_mapping())
        self.assertNotIn(API_KEY_PREFIX, serialized)
        self.assertNotIn("token=", serialized)

    def test_no_credential_leakage_in_probe_payload(self) -> None:
        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra",
            "ZEST_CODEX_EXECUTABLE": "codex",
            "OPENAI_API_KEY": "synthetic-secret-value",
        }
        result = probe_codex_cli(
            configuration=parse_codex_model_configurations(
                env[CODEX_MODELS_ENV], executable="codex"
            )[0],
            runner=_argv_runner(frozenset({"gpt-5.6-terra"})),
            live_probe=True,
        )
        serialized = json.dumps(result.to_mapping())
        self.assertNotIn("synthetic-secret-value", serialized)
        self.assertNotIn(API_KEY_PREFIX, serialized)

    def test_host_probe_without_model_is_not_benchmark_compatible(self) -> None:
        availability = probe_codex_cli(runner=_argv_runner(frozenset({"gpt-5.5"})))
        assert availability.readiness is not None
        self.assertTrue(availability.readiness.auth_ready)
        self.assertFalse(availability.readiness.benchmark_compatible)
        self.assertFalse(availability.readiness.diagnostic_ready)


class CodexDiscoveryTests(unittest.TestCase):
    def test_available_model_configurations_requires_independent_readiness(self) -> None:
        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        both = discover_configured_runtimes(
            env=env,
            argv_runner=_argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"})),
            probe_mode=ProbeMode.LIVE,
        )
        self.assertEqual(
            both.available_model_configurations,
            ("codex-cli-terra", "codex-cli-gpt55"),
        )
        strix = next(item for item in both.entries if item.runtime_kind == "STRIX")
        self.assertFalse(strix.counts_as_model_runtime)
        mixed = discover_configured_runtimes(
            env=env,
            argv_runner=_argv_runner(frozenset({"gpt-5.6-terra"})),
            probe_mode=ProbeMode.LIVE,
        )
        self.assertEqual(mixed.available_model_configurations, ("codex-cli-terra",))
        pending = gate_04b_status(
            available_model_configurations=both.available_model_configurations,
            executed_live_configurations=(),
            comparable=False,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertFalse(pending["strix_counted_as_model_runtime"])
        serialized = json.dumps(both.to_mapping())
        self.assertNotIn(API_KEY_PREFIX, serialized)
        terra = next(item for item in both.entries if item.configuration_id == "codex-cli-terra")
        gpt55 = next(item for item in both.entries if item.configuration_id == "codex-cli-gpt55")
        self.assertEqual(terra.readiness, Readiness.AVAILABLE)
        self.assertEqual(gpt55.readiness, Readiness.AVAILABLE)
        self.assertNotEqual(terra.configuration_fingerprint, gpt55.configuration_fingerprint)

    def test_invalid_configuration_is_not_available(self) -> None:
        report = discover_configured_runtimes(
            env={CODEX_MODELS_ENV: "gpt-5.5,gpt-5.5", "ZEST_CODEX_EXECUTABLE": "codex"}
        )
        self.assertNotIn("gpt-5.5", report.available_model_configurations)
        cli = [item for item in report.entries if item.runtime_kind == "CLI_SESSION"]
        self.assertTrue(cli)
        self.assertTrue(all(item.readiness is not Readiness.AVAILABLE for item in cli))


class CodexTransportEnvelopeTests(unittest.TestCase):
    def _complete(self, stdout: str):
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=stdout,
            ),
        )
        return adapter.complete(_request())

    def test_strict_schema_requires_additional_properties_false(self) -> None:
        self.assertEqual(STRUCTURED_OUTPUT_SCHEMA["additionalProperties"], False)
        self.assertEqual(STRUCTURED_OUTPUT_SCHEMA["type"], "object")
        self.assertEqual(STRUCTURED_OUTPUT_SCHEMA, DIAGNOSTIC_OUTPUT_SCHEMA)
        self.assertEqual(STRUCTURED_OUTPUT_SCHEMA["required"], ["diagnostic"])
        self.assertNotEqual(STRUCTURED_OUTPUT_SCHEMA.get("additionalProperties"), True)

    def test_generator_and_falsifier_strict_transport_schemas_validate(self) -> None:
        validate_strict_transport_schema(GENERATOR_CONTRACT, GENERATOR_OUTPUT_SCHEMA)
        validate_strict_transport_schema(FALSIFIER_CONTRACT, FALSIFIER_OUTPUT_SCHEMA)
        validate_strict_transport_schema(DIAGNOSTIC_CONTRACT, DIAGNOSTIC_OUTPUT_SCHEMA)

    def test_transport_schema_requires_every_property(self) -> None:
        for schema in (GENERATOR_OUTPUT_SCHEMA, FALSIFIER_OUTPUT_SCHEMA, DIAGNOSTIC_OUTPUT_SCHEMA):
            self.assertEqual(set(schema["properties"]), set(schema["required"]))

    def test_transport_optional_fields_are_nullable_and_required_fields_are_not(self) -> None:
        for contract, schema in (
            (GENERATOR_CONTRACT, GENERATOR_OUTPUT_SCHEMA),
            (FALSIFIER_CONTRACT, FALSIFIER_OUTPUT_SCHEMA),
        ):
            for field in contract.fields:
                field_type = schema["properties"][field.name]["type"]
                types = field_type if isinstance(field_type, list) else [field_type]
                if field.required:
                    self.assertNotIn("null", types, field.name)
                else:
                    self.assertIn("null", types, field.name)

    def test_transport_schema_rejects_unsupported_keywords(self) -> None:
        for schema in (GENERATOR_OUTPUT_SCHEMA, FALSIFIER_OUTPUT_SCHEMA, DIAGNOSTIC_OUTPUT_SCHEMA):
            self.assertEqual(_unsupported_schema_keys(schema), set())
        schema = json.loads(json.dumps(GENERATOR_OUTPUT_SCHEMA))
        schema["properties"]["proposed_claim"]["minLength"] = 1
        with self.assertRaises(StructuredOutputTransportError):
            validate_strict_transport_schema(GENERATOR_CONTRACT, schema)

    def test_application_schema_keeps_canonical_required_semantics(self) -> None:
        self.assertEqual(GENERATOR_APPLICATION_SCHEMA, GENERATOR_CONTRACT.json_schema())
        self.assertLess(
            len(GENERATOR_APPLICATION_SCHEMA["required"]),
            len(GENERATOR_APPLICATION_SCHEMA["properties"]),
        )
        self.assertEqual(
            set(GENERATOR_OUTPUT_SCHEMA["required"]),
            set(GENERATOR_APPLICATION_SCHEMA["properties"]),
        )

    def test_transport_decoder_null_optional_means_canonical_omission(self) -> None:
        decoded = decode_transport_output(GENERATOR_CONTRACT, _generator_transport())
        self.assertEqual(decoded["proposed_claim"], "diagnostic claim")
        self.assertNotIn("source_references", decoded)
        self.assertNotIn("novelty_basis", decoded)

    def test_transport_decoder_preserves_non_null_optional_values(self) -> None:
        decoded = decode_transport_output(
            GENERATOR_CONTRACT,
            _generator_transport(
                source_references=["obs:1"],
                assumptions=["assumption"],
                expected_security_relevance="bounded note",
                novelty_basis="TARGET_SPECIFIC_BEHAVIOR",
            ),
        )
        self.assertEqual(decoded["source_references"], ["obs:1"])
        self.assertEqual(decoded["assumptions"], ["assumption"])
        self.assertEqual(decoded["expected_security_relevance"], "bounded note")
        self.assertEqual(decoded["novelty_basis"], "TARGET_SPECIFIC_BEHAVIOR")

    def test_transport_decoder_required_null_unknown_and_wrong_types_fail_closed(self) -> None:
        with self.assertRaises(StructuredOutputTransportError):
            decode_transport_output(GENERATOR_CONTRACT, _generator_transport(proposed_claim=None))
        with self.assertRaises(StructuredOutputTransportError):
            decode_transport_output(GENERATOR_CONTRACT, _generator_transport(extra="nope"))
        with self.assertRaises(StructuredOutputTransportError):
            decode_transport_output(
                GENERATOR_CONTRACT,
                _generator_transport(source_references="not-an-array"),
            )
        with self.assertRaises(StructuredOutputTransportError):
            decode_transport_output(
                GENERATOR_CONTRACT,
                _generator_transport(novelty_basis="N4_ZERO_DAY"),
            )

    def test_novelty_vocabulary_is_shared_by_parser_schema_and_instructions(self) -> None:
        enum_values = tuple(GENERATOR_OUTPUT_SCHEMA["properties"]["novelty_basis"]["enum"])
        schema_values = tuple(item for item in enum_values if item is not None)
        self.assertEqual(schema_values, ACCEPTED_NOVELTY_BASIS)
        for value in ACCEPTED_NOVELTY_BASIS:
            parsed, claimed = parse_novelty_basis(value)
            self.assertEqual(parsed.value, value)
            self.assertEqual(claimed, value)
            self.assertIn(value, GENERATOR_CONTRACT.instructions())
        with self.assertRaises(ResearchInputError):
            parse_novelty_basis("N4_ZERO_DAY")

    def test_request_schema_constrains_visible_source_references(self) -> None:
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c-source",
            context_fingerprint="fp",
            instructions="propose",
            payload={
                "research_context": {
                    "allowed_source_reference_ids": [
                        "run:run-gate04b-clean-contract",
                        "proc:research-question",
                        "obs:gate04b-contract-echo",
                    ],
                },
            },
        )
        schema = schema_for_request(request)
        items = schema["properties"]["source_references"]["items"]
        self.assertEqual(
            items["enum"],
            [
                "run:run-gate04b-clean-contract",
                "proc:research-question",
                "obs:gate04b-contract-echo",
            ],
        )
        decoded = decode_transport_output_for_request(
            request,
            _generator_transport(source_references=["obs:gate04b-contract-echo"]),
        )
        self.assertEqual(decoded["source_references"], ["obs:gate04b-contract-echo"])
        with self.assertRaises(StructuredOutputTransportError):
            decode_transport_output_for_request(
                request,
                _generator_transport(source_references=["run-gate04b-clean-contract"]),
            )

    def test_direct_application_object_decoding(self) -> None:
        result = self._complete(_transport_stdout(_generator_transport()))
        self.assertEqual(result.structured_output["proposed_claim"], "diagnostic claim")
        self.assertNotIn("source_references", result.structured_output)
        self.assertNotIn("result_json", result.structured_output)

    def test_diagnostic_object_round_trip(self) -> None:
        captured = {}

        def runner(argv, stdin_bytes=None):
            del argv
            captured["stdin"] = stdin_bytes
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=("codex", "exec"),
                exit_code=0,
                stdout=_transport_stdout({"diagnostic": True}),
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.6-terra",
            configuration_id="codex-cli-terra",
            runner=runner,
        )
        result = adapter.complete(
            ModelCallRequest(
                role=ModelRole.GENERATOR,
                correlation_id="zest.codex.diagnostic",
                context_fingerprint="codex-diagnostic",
                instructions='Return a JSON object {"diagnostic": true} only. Do not call tools.',
                payload={"diagnostic": True},
            )
        )
        self.assertEqual(result.structured_output, {"diagnostic": True})
        stdin = captured["stdin"] or b""
        self.assertIn(b"Do not wrap it in result_json", stdin)
        self.assertIn(b"application_json_schema", stdin)
        probe = probe_codex_cli(
            configuration=parse_codex_model_configurations(
                "codex-cli-terra=gpt-5.6-terra", executable="codex"
            )[0],
            runner=_argv_runner(frozenset({"gpt-5.6-terra"})),
            live_probe=True,
        )
        assert probe.readiness is not None
        self.assertTrue(probe.readiness.benchmark_compatible)
        self.assertIn("gpt-5.6-terra", probe.detail)

    def test_unknown_direct_object_is_rejected_before_core_parse(self) -> None:
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"diagnostic": true}')
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"result_json": "{}", "extra": true}')

    def test_empty_direct_object_omits_required_field(self) -> None:
        with self.assertRaises(StructuredOutputTransportError):
            self._complete("{}")
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"result_json": null}')

    def test_legacy_result_json_envelope_is_transport_error(self) -> None:
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"result_json": "{not-json}"}')
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"result_json": "[1]"}')
        with self.assertRaises(StructuredOutputTransportError):
            self._complete('{"result_json": "\\"text\\""}')

    def test_falsifier_schema_is_role_specific(self) -> None:
        captured = {}

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            schema_path = argv[argv.index("--output-schema") + 1]
            captured["schema"] = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            return ArgvProcessResult(
                status=ArgvProcessStatus.COMPLETED,
                argv=argv,
                exit_code=0,
                stdout=_transport_stdout(_falsifier_transport()),
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=runner,
        )
        adapter.complete(
            ModelCallRequest(
                role=ModelRole.FALSIFIER,
                correlation_id="c1",
                context_fingerprint="fp",
                instructions="challenge",
                payload={"proposal": {"proposed_claim": "x"}},
            )
        )
        self.assertEqual(captured["schema"], FALSIFIER_OUTPUT_SCHEMA)

    def test_direct_v2_schemas_are_role_specific(self) -> None:
        self.assertEqual(schema_for_role(ModelRole.GENERATOR.value), GENERATOR_OUTPUT_SCHEMA)
        self.assertEqual(schema_for_role(ModelRole.FALSIFIER.value), FALSIFIER_OUTPUT_SCHEMA)

    def test_both_configured_models_remain_independent_and_gate04b_pending(self) -> None:
        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        results = probe_codex_configurations(
            env=env,
            runner=_argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"})),
            live_probe=True,
        )
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.available for item in results))
        self.assertNotEqual(results[0].configuration_fingerprint, results[1].configuration_fingerprint)
        pending = gate_04b_status(
            available_model_configurations=("codex-cli-terra", "codex-cli-gpt55"),
            executed_live_configurations=(),
            comparable=False,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertEqual(GATE_04B_STATUS, "PASS")


def _is_codex_exec(argv) -> bool:
    return len(argv) >= 2 and argv[1] == "exec"


def _unsupported_schema_keys(value: object) -> set[str]:
    supported = {"type", "properties", "required", "additionalProperties", "items", "enum"}
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "properties":
                if isinstance(item, dict):
                    for field_schema in item.values():
                        found.update(_unsupported_schema_keys(field_schema))
                continue
            if key not in supported:
                found.add(key)
            found.update(_unsupported_schema_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_unsupported_schema_keys(item))
    return found


class CodexPassiveLiveProbeTests(unittest.TestCase):
    def test_passive_discovery_executes_zero_codex_exec(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            return _argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"}))(argv)

        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        report = discover_configured_runtimes(env=env, argv_runner=runner)
        self.assertEqual(report.probe_mode, ProbeMode.PASSIVE.value)
        self.assertFalse(any(_is_codex_exec(argv) for argv in calls))
        self.assertEqual(report.available_model_configurations, ())
        for item in report.entries:
            if item.runtime_kind != "CLI_SESSION":
                continue
            self.assertIsNot(item.readiness, Readiness.AVAILABLE)
            self.assertFalse(item.counts_as_model_runtime)
            assert item.structured_readiness is not None
            self.assertTrue(item.structured_readiness.auth_ready)
            self.assertFalse(item.structured_readiness.diagnostic_ready)
            self.assertFalse(item.structured_readiness.modelport_compatible)
            self.assertFalse(item.structured_readiness.benchmark_compatible)
        strix = next(item for item in report.entries if item.runtime_kind == "STRIX")
        self.assertFalse(strix.counts_as_model_runtime)
        pending = gate_04b_status(
            available_model_configurations=report.available_model_configurations,
            executed_live_configurations=(),
            comparable=False,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertEqual(GATE_04B_STATUS, "PASS")

    def test_operator_status_executes_zero_codex_exec(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            return _argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"}))(argv)

        snapshot = build_status_snapshot(
            env={
                CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
                "ZEST_CODEX_EXECUTABLE": "codex",
            },
            argv_runner=runner,
        )
        self.assertFalse(any(_is_codex_exec(argv) for argv in calls))
        self.assertEqual(snapshot.gate_04b, "PASS")

    def test_live_probe_executes_model_specific_diagnostic_and_can_become_compatible(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            return _argv_runner(frozenset({"gpt-5.6-terra", "gpt-5.5"}))(argv)

        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        report = discover_configured_runtimes(
            env=env,
            argv_runner=runner,
            probe_mode=ProbeMode.LIVE,
        )
        execs = [argv for argv in calls if _is_codex_exec(argv)]
        self.assertEqual(len(execs), 2)
        models = {argv[argv.index("-m") + 1] for argv in execs}
        self.assertEqual(models, {"gpt-5.6-terra", "gpt-5.5"})
        self.assertEqual(report.available_model_configurations, ("codex-cli-terra", "codex-cli-gpt55"))
        for item in report.entries:
            if item.configuration_id in {"codex-cli-terra", "codex-cli-gpt55"}:
                assert item.structured_readiness is not None
                self.assertTrue(item.structured_readiness.benchmark_compatible)
                self.assertTrue(item.counts_as_model_runtime)

    def test_usage_limit_maps_to_rate_limited_without_invalidating_other_config(self) -> None:
        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            if argv[-1] == "--version" or (len(argv) >= 2 and argv[1] == "login"):
                return _argv_runner(frozenset({"gpt-5.6-terra"}))(argv)
            if _is_codex_exec(argv):
                model = argv[argv.index("-m") + 1]
                if model == "gpt-5.5":
                    return ArgvProcessResult(
                        status=ArgvProcessStatus.PROCESS_FAILED,
                        argv=argv,
                        exit_code=1,
                        stderr="You've hit your usage limit. Try again at 18:00.",
                        reason="non-zero exit",
                    )
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED,
                    argv=argv,
                    exit_code=0,
                    stdout=_transport_stdout({"diagnostic": True}),
                )
            return ArgvProcessResult(status=ArgvProcessStatus.PROCESS_FAILED, argv=argv, exit_code=1)

        env = {
            CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
            "ZEST_CODEX_EXECUTABLE": "codex",
        }
        results = probe_codex_configurations(env=env, runner=runner, live_probe=True)
        by_id = {item.configuration_id: item for item in results}
        terra = by_id["codex-cli-terra"]
        gpt55 = by_id["codex-cli-gpt55"]
        assert terra.readiness is not None
        assert gpt55.readiness is not None
        self.assertTrue(terra.readiness.benchmark_compatible)
        self.assertEqual(gpt55.outcome, RuntimeOutcome.RATE_LIMITED)
        self.assertFalse(gpt55.readiness.benchmark_compatible)
        self.assertEqual(gpt55.detail, "Codex CLI usage/rate limit reached")
        self.assertNotIn("18:00", gpt55.detail)
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=runner,
        )
        with self.assertRaises(ProviderRateLimitError) as ctx:
            adapter.complete(_request())
        self.assertEqual(str(ctx.exception), "Codex CLI usage/rate limit reached")

    def test_process_failures_are_single_attempt_and_sanitized(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr="fatal: not a git repository (or any of the parent directories): .git",
                reason="non-zero exit",
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=runner,
        )
        with self.assertRaises(RuntimeProcessError) as ctx:
            adapter.complete(_request())
        self.assertEqual(str(ctx.exception), NON_GIT_WORKING_DIRECTORY_DETAIL)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(".git", str(ctx.exception))

    def test_timeout_is_single_attempt_and_remains_timed_out(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            return ArgvProcessResult(
                status=ArgvProcessStatus.TIMED_OUT,
                argv=argv,
                reason="process exceeded timeout",
            )

        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=runner,
        )
        with self.assertRaises(ProviderTimeoutError):
            adapter.complete(_request())
        self.assertEqual(len(calls), 1)

    def test_auth_rate_limit_and_policy_classification_take_precedence(self) -> None:
        cases = (
            ("not authenticated; not a git repository", ProviderAuthError),
            ("rate limit: not a git repository", ProviderRateLimitError),
            ("content policy blocked; not a git repository", ContentPolicyBlockedError),
        )
        for stderr, expected in cases:
            with self.subTest(stderr=stderr):
                adapter = CodexCliSessionAdapter(
                    allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
                    executable="codex",
                    model="gpt-5.5",
                    configuration_id="codex-cli-gpt55",
                    runner=lambda argv, stdin_bytes=None, text=stderr: ArgvProcessResult(
                        status=ArgvProcessStatus.PROCESS_FAILED,
                        argv=argv,
                        exit_code=1,
                        stderr=text,
                        reason="non-zero exit",
                    ),
                )
                with self.assertRaises(expected):
                    adapter.complete(_request())

    def test_generic_policy_words_are_not_content_policy_refusals(self) -> None:
        cases = (
            "schema content did not match expected object",
            "policy file could not be loaded",
            "safety metadata was unavailable",
        )
        for stderr in cases:
            with self.subTest(stderr=stderr):
                adapter = CodexCliSessionAdapter(
                    allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
                    executable="codex",
                    model="gpt-5.5",
                    configuration_id="codex-cli-gpt55",
                    runner=lambda argv, stdin_bytes=None, text=stderr: ArgvProcessResult(
                        status=ArgvProcessStatus.PROCESS_FAILED,
                        argv=argv,
                        exit_code=1,
                        stderr=text,
                        reason="non-zero exit",
                    ),
                )
                with self.assertRaises(RuntimeProcessError) as ctx:
                    adapter.complete(_request())
                diagnostic = str(ctx.exception)
                self.assertIn('"failure_code":"PROCESS_EXIT_NONZERO"', diagnostic)
                self.assertNotIn(stderr, diagnostic)

    def test_explicit_policy_refusal_markers_are_content_policy_blocked(self) -> None:
        cases = (
            "content_policy",
            "content_policy_violation",
            "content_filter",
            "safety_refusal",
            "blocked by content policy",
            "refused by moderation",
            "model refused for safety",
        )
        for stderr in cases:
            with self.subTest(stderr=stderr):
                adapter = CodexCliSessionAdapter(
                    allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
                    executable="codex",
                    model="gpt-5.5",
                    configuration_id="codex-cli-gpt55",
                    runner=lambda argv, stdin_bytes=None, text=stderr: ArgvProcessResult(
                        status=ArgvProcessStatus.PROCESS_FAILED,
                        argv=argv,
                        exit_code=1,
                        stderr=text,
                        reason="non-zero exit",
                    ),
                )
                with self.assertRaises(ContentPolicyBlockedError) as ctx:
                    adapter.complete(_request())
                self.assertNotIn(stderr, str(ctx.exception))

    def test_unmatched_process_error_remains_runtime_process_error(self) -> None:
        prompt_secret = "SENTINEL-PROMPT-SECRET"
        stdout = f"provider echoed prompt {prompt_secret}"
        stderr = f"schema validation failed: {prompt_secret}"
        adapter = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable="codex",
            model="gpt-5.5",
            configuration_id="codex-cli-gpt55",
            runner=lambda argv, stdin_bytes=None: ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=17,
                stdout=stdout,
                stderr=stderr,
                stderr_truncated=True,
                reason="non-zero exit",
                cleanup_failed=True,
                cleanup_reason=f"unsafe cleanup output {prompt_secret}",
            ),
        )
        with self.assertRaises(RuntimeProcessError) as ctx:
            adapter.complete(_request())
        diagnostic = str(ctx.exception)
        self.assertIn('"failure_code":"PROCESS_EXIT_NONZERO"', diagnostic)
        self.assertIn('"exit_code":17', diagnostic)
        self.assertIn('"reason":"non-zero exit"', diagnostic)
        self.assertIn('"stderr_truncated":true', diagnostic)
        self.assertIn(f'"stdout_byte_length":{len(stdout.encode("utf-8"))}', diagnostic)
        self.assertIn(f'"stderr_byte_length":{len(stderr.encode("utf-8"))}', diagnostic)
        self.assertIn(
            f'"stdout_sha256":"{hashlib.sha256(stdout.encode("utf-8")).hexdigest()}"',
            diagnostic,
        )
        self.assertIn(
            f'"stderr_sha256":"{hashlib.sha256(stderr.encode("utf-8")).hexdigest()}"',
            diagnostic,
        )
        self.assertIn('"cleanup_failed":true', diagnostic)
        self.assertIn('"cleanup_status":"FAILED"', diagnostic)
        self.assertNotIn(stdout, diagnostic)
        self.assertNotIn(stderr, diagnostic)
        self.assertNotIn(prompt_secret, diagnostic)
        self.assertNotIn("unsafe cleanup output", diagnostic)


if __name__ == "__main__":
    unittest.main()
