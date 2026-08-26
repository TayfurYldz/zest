from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pathsetup  # noqa: F401

import zest.benchmark.runner as runner_module
from zest.benchmark.baselines import (
    BAD_HALLUCINATOR,
    GOOD_BASELINE,
    ScriptedModelPort,
    cautious_falsifier,
    create_baseline,
    good_generator,
)
from zest.benchmark.checkpoint import (
    BenchmarkCheckpointSession,
    BenchmarkOperationalPause,
)
from zest.benchmark.errors import BenchmarkError
from zest.benchmark.experiment import compare_experiments, run_experiment
from zest.benchmark.identity import (
    BenchmarkExperimentConfig,
    ModelConfigurationIdentity,
    current_instruction_identity,
)
from zest.benchmark.runner import (
    BENCHMARK_OPERATIONAL_PAUSE_EXIT_CODE,
    identity_for_live,
    run_cli,
)
from zest.benchmark.scenarios import load_scenarios
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelCallResult,
    ModelPortError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRuntimeError,
    ProviderTimeoutError,
    RuntimeProcessError,
    StructuredOutputTransportError,
)

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


def _identity(name: str = GOOD_BASELINE) -> ModelConfigurationIdentity:
    return ModelConfigurationIdentity(
        adapter_identity=name,
        provider_adapter_identity=name,
        provider_model_id=name,
        runtime_kind="CLI_SESSION",
        runtime_class="AGENT_RUNTIME",
        runtime_id=name,
        configuration_fingerprint=f"fp-{name}",
    )


def _config(runs: int = 1) -> BenchmarkExperimentConfig:
    return BenchmarkExperimentConfig(runs_per_scenario=runs)


def _scenarios(count: int = 1):
    return load_scenarios(SCENARIO_DIR)[:count]


class NoCallModelPort:
    adapter_identity = "NO_CALL"

    def __init__(self) -> None:
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        raise AssertionError("provider should not be called during checkpoint replay")


class StagedErrorModelPort:
    adapter_identity = GOOD_BASELINE

    def __init__(self, errors: list[BaseException]) -> None:
        self.errors = list(errors)
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        if self.errors:
            raise self.errors.pop(0)
        return create_baseline(GOOD_BASELINE).complete(request)


class ContractAwareModelPort:
    def __init__(self, full_model) -> None:
        self.full_model = full_model
        self.contract_model = create_baseline(GOOD_BASELINE)
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        context = request.payload.get("research_context")
        if (
            isinstance(context, dict)
            and context.get("research_run_id") == "run-gate04b-clean-contract"
        ):
            return self.contract_model.complete(request)
        return self.full_model.complete(request)


class RecordingContractFailurePort:
    def __init__(self) -> None:
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        return create_baseline(GOOD_BASELINE).complete(request)


def _normalize_report(report) -> dict:
    payload = report.to_mapping()
    payload.pop("run_id", None)
    payload.pop("created_at", None)
    for summary in payload["summaries"]:
        for run in summary["runs"]:
            run["elapsed_ms"] = "<operational>"
    return payload


def _normalize_bundle(payload: dict) -> dict:
    normalized = dict(payload)
    for report in normalized["reports"]:
        report["run_id"] = "<operational>"
        report["created_at"] = "<operational>"
        for summary in report["summaries"]:
            for run in summary["runs"]:
                run["elapsed_ms"] = "<operational>"
    return normalized


class FakeDiscovery:
    def to_mapping(self):
        return {
            "available_model_configurations": ["openai", "anthropic"],
            "entries": [
                {
                    "configuration_id": "openai",
                    "readiness": "AVAILABLE",
                    "contains_secrets": False,
                },
                {
                    "configuration_id": "anthropic",
                    "readiness": "AVAILABLE",
                    "contains_secrets": False,
                },
            ],
            "probe_mode": "PASSIVE",
        }


def fake_discover_runtimes(*, live_probe: bool = False):
    if live_probe:
        raise AssertionError("test discovery must not perform live readiness probes")
    return FakeDiscovery()


def fake_resolve_live(adapter_id: str, model_id: str | None):
    del model_id
    baseline = GOOD_BASELINE if adapter_id == "openai" else BAD_HALLUCINATOR
    return (
        ContractAwareModelPort(create_baseline(baseline)),
        identity_for_live(
            adapter_identity=f"{adapter_id}.test",
            provider_adapter_identity=adapter_id,
            provider_model_id=f"{adapter_id}-model",
        ),
    )


def fake_resolve_no_call(adapter_id: str, model_id: str | None):
    del model_id
    return (
        NoCallModelPort(),
        identity_for_live(
            adapter_identity=f"{adapter_id}.test",
            provider_adapter_identity=adapter_id,
            provider_model_id=f"{adapter_id}-model",
        ),
    )


def fake_gate_status(**kwargs):
    return {
        "status": "PASS"
        if (
            len(kwargs["executed_live_configurations"]) == 2
            and kwargs.get("contract_qualified") is True
            and kwargs.get("full_comparison_completed") is True
        )
        else "PENDING",
        "reason": "test-only status",
        "available_model_configurations": list(kwargs["available_model_configurations"]),
        "executed_live_configurations": list(kwargs["executed_live_configurations"]),
        "operationally_comparable": kwargs.get("operationally_comparable", False),
        "contract_qualified": kwargs.get("contract_qualified", False),
        "full_comparison_completed": kwargs.get("full_comparison_completed", False),
        "no_automatic_winner": True,
    }


class BenchmarkCheckpointTests(unittest.TestCase):
    def test_synchronous_allowlisted_error_becomes_completed_error(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_experiment(
                scenarios,
                ScriptedModelPort(
                    adapter_identity=GOOD_BASELINE,
                    generator=good_generator,
                    falsifier=cautious_falsifier,
                    error=ProviderTimeoutError("raw timeout detail should not persist"),
                    fail_role=ModelRole.GENERATOR,
                ),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            self.assertEqual(report.summaries[0].provider_timeout_failures, 1)
            record = json.loads(
                sorted(root.glob("plans/*/calls/*.json"))[0].read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "COMPLETED_ERROR")
            self.assertEqual(record["error"]["code"], "PROVIDER_TIMEOUT")
            self.assertEqual(record["error"]["message"], "provider timeout")
            self.assertNotIn("raw timeout", json.dumps(record))
            self.assertNotIn("research_context", json.dumps(record))

    def test_resume_replays_completed_error_with_zero_provider_calls(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run_experiment(
                scenarios,
                ScriptedModelPort(
                    adapter_identity=GOOD_BASELINE,
                    generator=good_generator,
                    falsifier=cautious_falsifier,
                    error=ProviderTimeoutError("provider timeout"),
                    fail_role=ModelRole.GENERATOR,
                ),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            no_call = NoCallModelPort()
            resumed = run_experiment(
                scenarios,
                no_call,
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
            )
            self.assertEqual(no_call.calls, [])
            self.assertEqual(_normalize_report(first), _normalize_report(resumed))

    def test_provider_error_mappings_replay_existing_semantic_classes(self) -> None:
        cases = [
            (ProviderAuthError("provider authentication failed"), "PROVIDER_AUTH", True),
            (ProviderRateLimitError("provider rate limit"), "PROVIDER_RATE_LIMIT", True),
            (ProviderTimeoutError("provider timeout"), "PROVIDER_TIMEOUT", True),
            (ProviderRuntimeError("provider runtime error"), "PROVIDER_RUNTIME", True),
            (RuntimeProcessError("runtime process error"), "PROVIDER_RUNTIME", True),
            (
                StructuredOutputTransportError("structured output failure"),
                "STRUCTURED_OUTPUT_FAILURE",
                False,
            ),
            (
                ContentPolicyBlockedError("content policy blocked"),
                "CONTENT_POLICY_BLOCKED",
                True,
            ),
        ]
        scenarios = _scenarios()
        for exc, failure_class, runtime_error in cases:
            with self.subTest(exc=exc.__class__.__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    run_experiment(
                        scenarios,
                        ScriptedModelPort(
                            adapter_identity=GOOD_BASELINE,
                            generator=good_generator,
                            falsifier=cautious_falsifier,
                            error=exc,
                            fail_role=ModelRole.GENERATOR,
                        ),
                        config=_config(),
                        model_identity=_identity(),
                        git_commit="commit-a",
                        checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
                    )
                    resumed = run_experiment(
                        scenarios,
                        NoCallModelPort(),
                        config=_config(),
                        model_identity=_identity(),
                        git_commit="commit-a",
                        checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                    )
                    run = resumed.summaries[0].runs[0]
                    self.assertEqual(run.failure_class, failure_class)
                    self.assertEqual(run.provider_runtime_error, runtime_error)

    def test_crash_after_intent_remains_unknown_outcome(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(KeyboardInterrupt):
                run_experiment(
                    scenarios,
                    StagedErrorModelPort([KeyboardInterrupt()]),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
                )
            record = json.loads(
                sorted(root.glob("plans/*/calls/*.json"))[0].read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "INTENT")
            with self.assertRaises(BenchmarkError) as ctx:
                run_experiment(
                    scenarios,
                    NoCallModelPort(),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                )
            self.assertIn("UNKNOWN_OUTCOME", str(ctx.exception))

    def test_unexpected_model_port_error_remains_fail_closed(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                StagedErrorModelPort([ModelPortError("unexpected base model error")]),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            record = json.loads(
                sorted(root.glob("plans/*/calls/*.json"))[0].read_text(encoding="utf-8")
            )
            self.assertEqual(record["status"], "INTENT")
            with self.assertRaises(BenchmarkError) as ctx:
                run_experiment(
                    scenarios,
                    NoCallModelPort(),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                )
            self.assertIn("UNKNOWN_OUTCOME", str(ctx.exception))

    def test_corrupt_completed_error_record_fails_closed(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                ScriptedModelPort(
                    adapter_identity=GOOD_BASELINE,
                    generator=good_generator,
                    falsifier=cautious_falsifier,
                    error=ProviderTimeoutError("provider timeout"),
                    fail_role=ModelRole.GENERATOR,
                ),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            record = sorted(root.glob("plans/*/calls/*.json"))[0]
            payload = json.loads(record.read_text(encoding="utf-8"))
            payload["error"]["raw_stderr"] = "must not replay"
            record.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(BenchmarkError) as ctx:
                run_experiment(
                    scenarios,
                    NoCallModelPort(),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                )
            self.assertIn("completed error record is invalid", str(ctx.exception))

    def test_known_provider_failures_do_not_poison_later_boundary_resume(self) -> None:
        scenarios = _scenarios(3)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(BenchmarkOperationalPause):
                run_experiment(
                    scenarios,
                    StagedErrorModelPort(
                        [
                            ProviderTimeoutError("provider timeout"),
                            ProviderRateLimitError("provider rate limit"),
                        ]
                    ),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(
                        root,
                        resume=False,
                        max_new_model_calls=2,
                    ),
                )
            statuses = [
                json.loads(path.read_text(encoding="utf-8"))["status"]
                for path in sorted(root.glob("plans/*/calls/*.json"))
            ]
            self.assertEqual(statuses, ["COMPLETED_ERROR", "COMPLETED_ERROR"])

            resumed = run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(
                    root,
                    resume=True,
                    max_new_model_calls=2,
                ),
            )
            self.assertEqual(resumed.summaries[0].provider_timeout_failures, 1)
            self.assertEqual(resumed.summaries[1].provider_rate_limit_failures, 1)
            self.assertEqual(resumed.summaries[2].completed, 1)

    def test_error_invocations_count_toward_boundary_limit(self) -> None:
        scenarios = _scenarios(2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(BenchmarkOperationalPause):
                run_experiment(
                    scenarios,
                    StagedErrorModelPort([ProviderTimeoutError("provider timeout")]),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(
                        root,
                        resume=False,
                        max_new_model_calls=1,
                    ),
                )
            records = sorted(root.glob("plans/*/calls/*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                json.loads(records[0].read_text(encoding="utf-8"))["status"],
                "COMPLETED_ERROR",
            )
            pause = json.loads((root / "pause.json").read_text(encoding="utf-8"))
            self.assertEqual(pause["new_model_calls"], 1)
            self.assertEqual(pause["next_call"]["slot"]["role"], "GENERATOR")

    def test_max_new_model_calls_pauses_before_next_intent(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = ScriptedModelPort(
                adapter_identity=GOOD_BASELINE,
                generator=good_generator,
                falsifier=cautious_falsifier,
            )
            with self.assertRaises(BenchmarkOperationalPause) as ctx:
                run_experiment(
                    scenarios,
                    model,
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(
                        root,
                        resume=False,
                        max_new_model_calls=1,
                    ),
                )
            self.assertEqual([call.role for call in model.calls], [ModelRole.GENERATOR])
            records = sorted(root.glob("plans/*/calls/*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                json.loads(records[0].read_text(encoding="utf-8"))["status"],
                "COMPLETED_SUCCESS",
            )
            self.assertFalse(
                any(
                    json.loads(path.read_text(encoding="utf-8"))["status"] == "INTENT"
                    for path in records
                )
            )
            pause = json.loads((root / "pause.json").read_text(encoding="utf-8"))
            self.assertEqual(pause["status"], "PAUSED_AT_BOUNDARY")
            self.assertEqual(pause["new_model_calls"], 1)
            self.assertEqual(pause["next_call"]["slot"]["role"], "FALSIFIER")
            self.assertTrue(pause["not_gate_pass"])
            self.assertTrue(pause["not_benchmark_failure"])
            self.assertEqual(ctx.exception.payload["status"], "PAUSED_AT_BOUNDARY")

    def test_resume_replays_completed_generator_then_calls_pending_falsifier(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(BenchmarkOperationalPause):
                run_experiment(
                    scenarios,
                    ScriptedModelPort(
                        adapter_identity=GOOD_BASELINE,
                        generator=good_generator,
                        falsifier=cautious_falsifier,
                    ),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(
                        root,
                        resume=False,
                        max_new_model_calls=1,
                    ),
                )

            resumed_model = ScriptedModelPort(
                adapter_identity=GOOD_BASELINE,
                generator=good_generator,
                falsifier=cautious_falsifier,
            )
            report = run_experiment(
                scenarios,
                resumed_model,
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(
                    root,
                    resume=True,
                    max_new_model_calls=1,
                ),
            )
            self.assertEqual([call.role for call in resumed_model.calls], [ModelRole.FALSIFIER])
            self.assertEqual(report.summaries[0].completed, 1)
            records = sorted(root.glob("plans/*/calls/*.json"))
            self.assertEqual(len(records), 2)
            self.assertEqual(
                {
                    json.loads(path.read_text(encoding="utf-8"))["status"]
                    for path in records
                },
                {"COMPLETED_SUCCESS"},
            )

    def test_completed_generator_and_falsifier_error_replay_causes_zero_calls(self) -> None:
        scenarios = _scenarios()
        config = _config()
        with tempfile.TemporaryDirectory() as tmp:
            create = BenchmarkCheckpointSession(Path(tmp), resume=False)
            first = ScriptedModelPort(
                adapter_identity=GOOD_BASELINE,
                generator=good_generator,
                falsifier=cautious_falsifier,
                error=ProviderTimeoutError("timeout"),
                fail_role=ModelRole.FALSIFIER,
            )
            report = run_experiment(
                scenarios,
                first,
                config=config,
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=create,
            )
            self.assertEqual(report.summaries[0].provider_timeout_failures, 1)

            second = ScriptedModelPort(
                adapter_identity=GOOD_BASELINE,
                generator=good_generator,
                falsifier=cautious_falsifier,
            )
            resumed = run_experiment(
                scenarios,
                second,
                config=config,
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(Path(tmp), resume=True),
            )
            self.assertEqual(second.calls, [])
            self.assertEqual(_normalize_report(report), _normalize_report(resumed))

    def test_completed_falsifier_replay_causes_zero_additional_provider_calls(self) -> None:
        scenarios = _scenarios()
        config = _config()
        with tempfile.TemporaryDirectory() as tmp:
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=config,
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(Path(tmp), resume=False),
            )
            no_call = NoCallModelPort()
            resumed = run_experiment(
                scenarios,
                no_call,
                config=config,
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(Path(tmp), resume=True),
            )
            self.assertEqual(no_call.calls, [])
            self.assertEqual(resumed.summaries[0].attempted, 1)

    def test_completed_records_are_immutable_on_replay(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            records = sorted(root.glob("plans/*/calls/*.json"))
            before = {path: path.read_bytes() for path in records}
            run_experiment(
                scenarios,
                NoCallModelPort(),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
            )
            after = {path: path.read_bytes() for path in records}
            self.assertEqual(before, after)

    def test_fingerprint_mismatches_refuse_resume(self) -> None:
        scenarios = _scenarios(2)
        config = _config()
        identity = _identity()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=config,
                model_identity=identity,
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            instr = current_instruction_identity()
            cases = {
                "commit": (scenarios, config, identity, "commit-b"),
                "suite_order": (tuple(reversed(scenarios)), config, identity, "commit-a"),
                "run_count": (scenarios, _config(2), identity, "commit-a"),
                "generator_instruction": (
                    scenarios,
                    replace(
                        config,
                        instruction_identity=replace(
                            instr, generator_instruction_fingerprint="different-generator"
                        ),
                    ),
                    identity,
                    "commit-a",
                ),
                "schema": (
                    scenarios,
                    replace(
                        config,
                        instruction_identity=replace(
                            instr,
                            structured_output_spec_fingerprint="different-schema",
                        ),
                    ),
                    identity,
                    "commit-a",
                ),
                "v2_schema_version": (
                    scenarios,
                    replace(
                        config,
                        instruction_identity=replace(
                            instr,
                            structured_output_spec_version="research.structured-output.v2",
                        ),
                    ),
                    identity,
                    "commit-a",
                ),
                "v2_harness_version": (
                    scenarios,
                    replace(config, harness_version="gate-04b.2"),
                    identity,
                    "commit-a",
                ),
                "v3_harness_version": (
                    scenarios,
                    replace(config, harness_version="gate-04b.3"),
                    identity,
                    "commit-a",
                ),
                "adapter": (
                    scenarios,
                    config,
                    replace(identity, adapter_identity="other-adapter"),
                    "commit-a",
                ),
                "runtime": (
                    scenarios,
                    config,
                    replace(identity, runtime_id="other-runtime"),
                    "commit-a",
                ),
                "model": (
                    scenarios,
                    config,
                    replace(identity, provider_model_id="other-model"),
                    "commit-a",
                ),
            }
            for name, (case_scenarios, case_config, case_identity, commit) in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(BenchmarkError):
                        run_experiment(
                            case_scenarios,
                            create_baseline(GOOD_BASELINE),
                            config=case_config,
                            model_identity=case_identity,
                            git_commit=commit,
                            checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                        )

    def test_request_fingerprint_mismatch_refuses_resume(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            record = sorted(root.glob("plans/*/calls/*.json"))[0]
            payload = json.loads(record.read_text(encoding="utf-8"))
            payload["request_fingerprint"] = "different-request"
            record.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(BenchmarkError):
                run_experiment(
                    scenarios,
                    create_baseline(GOOD_BASELINE),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                )

    def test_corrupt_checkpoint_fails_closed(self) -> None:
        scenarios = _scenarios()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=_config(),
                model_identity=_identity(),
                git_commit="commit-a",
                checkpoint_session=BenchmarkCheckpointSession(root, resume=False),
            )
            record = sorted(root.glob("plans/*/calls/*.json"))[0]
            record.write_text('{"status": "COMPLETED"', encoding="utf-8")
            with self.assertRaises(BenchmarkError):
                run_experiment(
                    scenarios,
                    create_baseline(GOOD_BASELINE),
                    config=_config(),
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(root, resume=True),
                )

    def test_resumed_and_uninterrupted_reports_and_pairing_are_equivalent(self) -> None:
        scenarios = _scenarios(2)
        config = _config(2)
        left_identity = _identity(GOOD_BASELINE)
        right_identity = _identity(BAD_HALLUCINATOR)
        uninterrupted_left = run_experiment(
            scenarios,
            create_baseline(GOOD_BASELINE),
            config=config,
            model_identity=left_identity,
            git_commit="commit-a",
        )
        uninterrupted_right = run_experiment(
            scenarios,
            create_baseline(BAD_HALLUCINATOR),
            config=config,
            model_identity=right_identity,
            git_commit="commit-a",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create = BenchmarkCheckpointSession(root, resume=False)
            run_experiment(
                scenarios,
                create_baseline(GOOD_BASELINE),
                config=config,
                model_identity=left_identity,
                git_commit="commit-a",
                checkpoint_session=create,
            )
            run_experiment(
                scenarios,
                create_baseline(BAD_HALLUCINATOR),
                config=config,
                model_identity=right_identity,
                git_commit="commit-a",
                checkpoint_session=create,
            )
            resume = BenchmarkCheckpointSession(root, resume=True)
            resumed_left = run_experiment(
                scenarios,
                NoCallModelPort(),
                config=config,
                model_identity=left_identity,
                git_commit="commit-a",
                checkpoint_session=resume,
            )
            resumed_right = run_experiment(
                scenarios,
                NoCallModelPort(),
                config=config,
                model_identity=right_identity,
                git_commit="commit-a",
                checkpoint_session=resume,
            )
        self.assertEqual(_normalize_report(uninterrupted_left), _normalize_report(resumed_left))
        self.assertEqual(_normalize_report(uninterrupted_right), _normalize_report(resumed_right))
        self.assertEqual(
            compare_experiments(uninterrupted_left, uninterrupted_right).to_mapping(),
            compare_experiments(resumed_left, resumed_right).to_mapping(),
        )

    def test_development_gate04b_shape_remains_thirteen_scenarios_three_runs(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        config = BenchmarkExperimentConfig()
        self.assertEqual(len(scenarios), 13)
        self.assertEqual(config.runs_per_scenario, 3)


class BenchmarkCheckpointCliTests(unittest.TestCase):
    def test_max_new_model_calls_cli_pause_is_not_gate_pass_or_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint"
            report = Path(tmp) / "paired.json"
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                        "--checkpoint-dir",
                        str(checkpoint),
                        "--max-new-model-calls",
                        "1",
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(code, BENCHMARK_OPERATIONAL_PAUSE_EXIT_CODE)
            self.assertFalse(report.exists())
            pause = json.loads((checkpoint / "pause.json").read_text(encoding="utf-8"))
            self.assertEqual(pause["status"], "PAUSED_AT_BOUNDARY")
            self.assertTrue(pause["not_gate_pass"])
            self.assertTrue(pause["not_benchmark_failure"])
            self.assertIn("PAUSED_AT_BOUNDARY", err.getvalue())

    def test_invalid_max_new_model_calls_fails_closed(self) -> None:
        cases = [
            [
                "--baseline",
                GOOD_BASELINE,
                "--max-new-model-calls",
                "0",
            ],
            [
                "--baseline",
                GOOD_BASELINE,
                "--max-new-model-calls",
                "-1",
            ],
            [
                "--baseline",
                GOOD_BASELINE,
                "--max-new-model-calls",
                "1",
            ],
        ]
        for args in cases:
            with self.subTest(args=args):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(run_cli(args), 2)

    def test_checkpoint_and_resume_cli_options_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint"
            report = Path(tmp) / "report.json"
            args = [
                "--baseline",
                GOOD_BASELINE,
                "--scenarios",
                str(SCENARIO_DIR),
                "--runs-per-scenario",
                "1",
                "--json-report",
                str(report),
                "--checkpoint-dir",
                str(checkpoint),
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(run_cli(args), 0)
            self.assertTrue(list(checkpoint.glob("plans/*/calls/*.json")))

            resumed_report = Path(tmp) / "resumed.json"
            resumed_args = [
                "--baseline",
                GOOD_BASELINE,
                "--scenarios",
                str(SCENARIO_DIR),
                "--runs-per-scenario",
                "1",
                "--json-report",
                str(resumed_report),
                "--resume-checkpoint",
                str(checkpoint),
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(run_cli(resumed_args), 0)

    def test_checkpoint_dir_refuses_unrelated_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint"
            checkpoint.mkdir()
            (checkpoint / "unrelated.txt").write_text("state", encoding="utf-8")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = run_cli(
                    [
                        "--baseline",
                        GOOD_BASELINE,
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--checkpoint-dir",
                        str(checkpoint),
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", err.getvalue())

    def test_discover_and_compare_checkpoint_path_uses_scripted_test_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--checkpoint-dir",
                        str(checkpoint),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(list(checkpoint.glob("paired-plans/*/paired-plan.json"))), 2)
            self.assertEqual(len(list(checkpoint.glob("paired-plans/*/models/*/*/plan.json"))), 4)

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                resumed = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--resume-checkpoint",
                        str(checkpoint),
                    ],
                    resolve_live=fake_resolve_no_call,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(resumed, 0)

    def test_discover_and_compare_json_report_persists_paired_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "paired.json"
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(code, 0)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "Gate04BPairedComparisonBundle")
            self.assertTrue(payload["not_evidence"])
            self.assertTrue(payload["not_finding"])
            self.assertTrue(payload["not_candidate"])
            self.assertTrue(payload["not_sor_truth"])
            self.assertTrue(payload["no_automatic_winner"])
            self.assertEqual(len(payload["reports"]), 2)
            self.assertEqual(payload["paired_comparison"]["kind"], "PairedComparison")
            self.assertEqual(payload["gate_04b_status"]["status"], "PASS")
            self.assertEqual(len(payload["executed_model_configurations"]), 2)
            self.assertEqual(payload["paired_comparison"]["left_configuration"], "openai")
            self.assertEqual(payload["paired_comparison"]["right_configuration"], "anthropic")
            self.assertIn("paired comparison: openai (openai-model) vs anthropic (anthropic-model)", out.getvalue())
            self.assertTrue(payload["reports"][0]["no_aggregate_model_score"])
            self.assertTrue(payload["reports"][1]["no_aggregate_model_score"])
            self.assertNotIn('"WINNER"', json.dumps(payload))
            self.assertNotIn("checkpoint", json.dumps(payload).lower())
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)

    def test_discover_and_compare_json_report_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "paired.json"
            report.write_text("existing", encoding="utf-8")
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", err.getvalue())

    def test_discover_and_compare_write_results_produces_immutable_paired_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = runner_module.DEFAULT_RESULTS_DIR
            runner_module.DEFAULT_RESULTS_DIR = Path(tmp) / "results"
            report = Path(tmp) / "paired.json"
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = run_cli(
                        [
                            "--discover-and-compare",
                            "--scenarios",
                            str(SCENARIO_DIR),
                            "--runs-per-scenario",
                            "1",
                            "--json-report",
                            str(report),
                            "--write-results",
                        ],
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    )
            finally:
                runner_module.DEFAULT_RESULTS_DIR = original
            self.assertEqual(code, 0)
            written = list((Path(tmp) / "results").glob("*_paired.json"))
            self.assertEqual(len(written), 1)
            payload = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], "Gate04BPairedComparisonBundle")
            self.assertEqual(payload, json.loads(report.read_text(encoding="utf-8")))
            self.assertEqual(written[0].stat().st_mode & 0o777, 0o600)

    def test_completed_bundle_is_written_before_needs_review_exit(self) -> None:
        def needs_review_status(**kwargs):
            status = fake_gate_status(**kwargs)
            status["status"] = "NEEDS_REVIEW"
            status["reason"] = "test review"
            return status

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "paired.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=needs_review_status,
                )
            self.assertEqual(code, 2)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["gate_04b_status"]["status"], "NEEDS_REVIEW")

    def test_completed_bundle_is_written_before_hard_failure_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "paired.json"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                        "--fail-on-hard-fail",
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(code, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["gate_04b_status"]["status"], "PASS")
            self.assertGreater(
                payload["paired_comparison"]["scenarios"][0]["right"][
                    "research_quality_failures"
                ],
                0,
            )

    def test_contract_failure_prevents_full_suite_execution_and_final_bundle(self) -> None:
        failing = ScriptedModelPort(
            adapter_identity="anthropic.test",
            generator={"unsupported": True},
            falsifier=cautious_falsifier,
        )
        good = RecordingContractFailurePort()

        def resolve_contract_failure(adapter_id: str, model_id: str | None):
            del model_id
            port = good if adapter_id == "openai" else failing
            return (
                port,
                identity_for_live(
                    adapter_identity=f"{adapter_id}.test",
                    provider_adapter_identity=adapter_id,
                    provider_model_id=f"{adapter_id}-model",
                ),
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "paired.json"
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report),
                    ],
                    resolve_live=resolve_contract_failure,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            self.assertIn("GATE 04B CONTRACT", out.getvalue())
            self.assertNotIn("suite: zest.development.v1", out.getvalue())
            self.assertTrue(good.calls)
            self.assertTrue(
                all(
                    request.payload.get("research_context", {}).get("research_run_id")
                    == "run-gate04b-clean-contract"
                    for request in good.calls
                )
            )
            self.assertTrue(failing.calls)
            self.assertTrue(
                all(
                    request.payload.get("research_context", {}).get("research_run_id")
                    == "run-gate04b-clean-contract"
                    for request in failing.calls
                )
            )

    def test_ordered_paired_checkpoint_refuses_reversed_or_replaced_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                first = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--checkpoint-dir",
                        str(checkpoint),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(first, 0)

            class ReversedDiscovery:
                def to_mapping(self):
                    return {"available_model_configurations": ["anthropic", "openai"]}

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                reversed_code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--resume-checkpoint",
                        str(checkpoint),
                    ],
                    resolve_live=fake_resolve_live,
                    discover_runtimes=lambda live_probe=False: ReversedDiscovery(),
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(reversed_code, 2)

            def replaced_resolve(adapter_id: str, model_id: str | None):
                port, identity = fake_resolve_live(adapter_id, model_id)
                if adapter_id == "anthropic":
                    identity = replace(identity, provider_model_id="replacement-model")
                return port, identity

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                replaced_code = run_cli(
                    [
                        "--discover-and-compare",
                        "--scenarios",
                        str(SCENARIO_DIR),
                        "--runs-per-scenario",
                        "1",
                        "--resume-checkpoint",
                        str(checkpoint),
                    ],
                    resolve_live=replaced_resolve,
                    discover_runtimes=fake_discover_runtimes,
                    evaluate_live_status=fake_gate_status,
                )
            self.assertEqual(replaced_code, 2)

    def test_resumed_and_uninterrupted_paired_bundles_are_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uninterrupted = Path(tmp) / "uninterrupted.json"
            checkpointed = Path(tmp) / "checkpointed.json"
            resumed = Path(tmp) / "resumed.json"
            checkpoint = Path(tmp) / "checkpoint"
            args = [
                "--discover-and-compare",
                "--scenarios",
                str(SCENARIO_DIR),
                "--runs-per-scenario",
                "1",
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    run_cli(
                        args + ["--json-report", str(uninterrupted)],
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    ),
                    0,
                )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    run_cli(
                        args
                        + [
                            "--json-report",
                            str(checkpointed),
                            "--checkpoint-dir",
                            str(checkpoint),
                        ],
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    ),
                    0,
                )
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    run_cli(
                        args
                        + [
                            "--json-report",
                            str(resumed),
                            "--resume-checkpoint",
                            str(checkpoint),
                        ],
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    ),
                    0,
                )
            self.assertEqual(
                _normalize_bundle(json.loads(uninterrupted.read_text(encoding="utf-8"))),
                _normalize_bundle(json.loads(resumed.read_text(encoding="utf-8"))),
            )

    def test_repeated_bounded_resumes_match_uninterrupted_paired_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            uninterrupted = Path(tmp) / "uninterrupted.json"
            bounded = Path(tmp) / "bounded.json"
            checkpoint = Path(tmp) / "checkpoint"
            args = [
                "--discover-and-compare",
                "--scenarios",
                str(SCENARIO_DIR),
                "--runs-per-scenario",
                "1",
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    run_cli(
                        args + ["--json-report", str(uninterrupted)],
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    ),
                    0,
                )

            first = True
            exits: list[int] = []
            for _attempt in range(20):
                checkpoint_args = (
                    ["--checkpoint-dir", str(checkpoint)]
                    if first
                    else ["--resume-checkpoint", str(checkpoint)]
                )
                first = False
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = run_cli(
                        args
                        + [
                            "--json-report",
                            str(bounded),
                            "--max-new-model-calls",
                            "7",
                        ]
                        + checkpoint_args,
                        resolve_live=fake_resolve_live,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    )
                exits.append(code)
                if code == 0:
                    break
                self.assertEqual(code, BENCHMARK_OPERATIONAL_PAUSE_EXIT_CODE)
                self.assertFalse(bounded.exists())
            self.assertEqual(exits[-1], 0)
            self.assertIn(BENCHMARK_OPERATIONAL_PAUSE_EXIT_CODE, exits)
            self.assertEqual(
                _normalize_bundle(json.loads(uninterrupted.read_text(encoding="utf-8"))),
                _normalize_bundle(json.loads(bounded.read_text(encoding="utf-8"))),
            )

    def test_bounded_resumed_paired_bundle_matches_provider_error_bundle(self) -> None:
        def resolve_with_provider_errors(adapter_id: str, model_id: str | None):
            del model_id
            if adapter_id == "openai":
                return (
                    ContractAwareModelPort(
                        ScriptedModelPort(
                            adapter_identity="openai.test",
                            generator=good_generator,
                            falsifier=cautious_falsifier,
                            error=ProviderTimeoutError("provider timeout"),
                            fail_role=ModelRole.GENERATOR,
                        )
                    ),
                    identity_for_live(
                        adapter_identity="openai.test",
                        provider_adapter_identity="openai",
                        provider_model_id="openai-model",
                    ),
                )
            return fake_resolve_live(adapter_id, None)

        with tempfile.TemporaryDirectory() as tmp:
            uninterrupted = Path(tmp) / "uninterrupted.json"
            bounded = Path(tmp) / "bounded.json"
            checkpoint = Path(tmp) / "checkpoint"
            args = [
                "--discover-and-compare",
                "--scenarios",
                str(SCENARIO_DIR),
                "--runs-per-scenario",
                "1",
            ]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(
                    run_cli(
                        args + ["--json-report", str(uninterrupted)],
                        resolve_live=resolve_with_provider_errors,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    ),
                    0,
                )

            first = True
            exits: list[int] = []
            for _attempt in range(20):
                checkpoint_args = (
                    ["--checkpoint-dir", str(checkpoint)]
                    if first
                    else ["--resume-checkpoint", str(checkpoint)]
                )
                first = False
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = run_cli(
                        args
                        + [
                            "--json-report",
                            str(bounded),
                            "--max-new-model-calls",
                            "5",
                        ]
                        + checkpoint_args,
                        resolve_live=resolve_with_provider_errors,
                        discover_runtimes=fake_discover_runtimes,
                        evaluate_live_status=fake_gate_status,
                    )
                exits.append(code)
                if code == 0:
                    break
                self.assertEqual(code, BENCHMARK_OPERATIONAL_PAUSE_EXIT_CODE)
                self.assertFalse(bounded.exists())
            self.assertEqual(exits[-1], 0)
            self.assertEqual(
                _normalize_bundle(json.loads(uninterrupted.read_text(encoding="utf-8"))),
                _normalize_bundle(json.loads(bounded.read_text(encoding="utf-8"))),
            )
            payload = json.loads(bounded.read_text(encoding="utf-8"))
            self.assertGreater(payload["reports"][0]["summaries"][0]["provider_timeout_failures"], 0)


if __name__ == "__main__":
    unittest.main()
