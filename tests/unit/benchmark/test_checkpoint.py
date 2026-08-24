from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.benchmark.baselines import (
    BAD_HALLUCINATOR,
    GOOD_BASELINE,
    ScriptedModelPort,
    cautious_falsifier,
    create_baseline,
    good_generator,
)
from research_os.benchmark.checkpoint import BenchmarkCheckpointSession
from research_os.benchmark.errors import BenchmarkError
from research_os.benchmark.experiment import compare_experiments, run_experiment
from research_os.benchmark.identity import (
    BenchmarkExperimentConfig,
    ModelConfigurationIdentity,
    current_instruction_identity,
)
from research_os.benchmark.runner import identity_for_live, run_cli
from research_os.benchmark.scenarios import load_scenarios
from research_os.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelRole,
    ProviderTimeoutError,
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


def _normalize_report(report) -> dict:
    payload = report.to_mapping()
    payload.pop("run_id", None)
    payload.pop("created_at", None)
    for summary in payload["summaries"]:
        for run in summary["runs"]:
            run["elapsed_ms"] = "<operational>"
    return payload


class BenchmarkCheckpointTests(unittest.TestCase):
    def test_completed_generator_replay_causes_zero_additional_provider_calls(self) -> None:
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
            with self.assertRaises(BenchmarkError) as ctx:
                run_experiment(
                    scenarios,
                    second,
                    config=config,
                    model_identity=_identity(),
                    git_commit="commit-a",
                    checkpoint_session=BenchmarkCheckpointSession(Path(tmp), resume=True),
                )
            self.assertIn("UNKNOWN_OUTCOME", str(ctx.exception))
            self.assertEqual(second.calls, [])

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
        class Discovery:
            def to_mapping(self):
                return {"available_model_configurations": ["openai", "anthropic"]}

        def discover_runtimes(*, live_probe: bool = False):
            self.assertFalse(live_probe)
            return Discovery()

        def resolve_live(adapter_id: str, model_id: str | None):
            del model_id
            baseline = GOOD_BASELINE if adapter_id == "openai" else BAD_HALLUCINATOR
            return (
                create_baseline(baseline),
                identity_for_live(
                    adapter_identity=f"{adapter_id}.test",
                    provider_adapter_identity=adapter_id,
                    provider_model_id=f"{adapter_id}-model",
                ),
            )

        def evaluate_live_status(**kwargs):
            return {
                "status": (
                    "PASS"
                    if len(kwargs["executed_live_configurations"]) == 2
                    else "PENDING"
                ),
                "reason": "test-only status",
                "available_model_configurations": list(kwargs["available_model_configurations"]),
                "executed_live_configurations": list(kwargs["executed_live_configurations"]),
                "no_automatic_winner": True,
            }

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
                    resolve_live=resolve_live,
                    discover_runtimes=discover_runtimes,
                    evaluate_live_status=evaluate_live_status,
                )
            self.assertEqual(code, 0)
            self.assertEqual(len(list(checkpoint.glob("plans/*/plan.json"))), 2)

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
                    resolve_live=resolve_live,
                    discover_runtimes=discover_runtimes,
                    evaluate_live_status=evaluate_live_status,
                )
            self.assertEqual(resumed, 0)


if __name__ == "__main__":
    unittest.main()
