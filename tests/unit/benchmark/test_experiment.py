from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.baselines import create_baseline
from zest.benchmark.errors import BenchmarkError
from zest.benchmark.experiment import (
    compare_experiments,
    run_experiment,
    write_immutable_report,
)
from zest.benchmark.identity import (
    BenchmarkExperimentConfig,
    ModelConfigurationIdentity,
    current_instruction_identity,
)
from zest.benchmark.scenarios import load_scenario, load_scenarios, parse_scenario
from zest.benchmark.suite import build_suite_manifest, scenario_integrity_hash
from zest.research.model_port import ModelPortError

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


def _identity(name: str) -> ModelConfigurationIdentity:
    return ModelConfigurationIdentity(adapter_identity=name, generator_configuration=name)


class RepeatedRunTests(unittest.TestCase):
    def test_configured_run_count_is_honored_and_independent(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)[:1]
        report = run_experiment(
            scenarios,
            create_baseline("UNSTABLE_BASELINE"),
            config=BenchmarkExperimentConfig(runs_per_scenario=4),
            model_identity=_identity("UNSTABLE_BASELINE"),
            git_commit="unknown",
        )
        summary = report.summaries[0]
        self.assertEqual(summary.attempted, 4)
        self.assertEqual(len(summary.runs), 4)
        self.assertEqual([item.run_index for item in summary.runs], [1, 2, 3, 4])
        self.assertIn("HALLUCINATED_SOURCE_REFERENCE", summary.hard_fail_occurrence)
        self.assertEqual(summary.hard_fail_occurrence["HALLUCINATED_SOURCE_REFERENCE"], "2/4")
        self.assertNotIn("80%", json.dumps(summary.to_mapping()))
        self.assertGreaterEqual(summary.research_quality_failures, 1)

    def test_provider_auth_is_not_research_quality_failure(self) -> None:
        from zest.research.model_port import ProviderAuthError

        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        port = create_baseline("GOOD_BASELINE")
        port._error = ProviderAuthError("401")
        report = run_experiment(
            (scenario,),
            port,
            config=BenchmarkExperimentConfig(runs_per_scenario=2),
            model_identity=_identity("GOOD_BASELINE"),
        )
        summary = report.summaries[0]
        self.assertEqual(summary.provider_auth_failures, 2)
        self.assertEqual(summary.research_quality_failures, 0)
        self.assertEqual(summary.runs[0].failure_class, "PROVIDER_AUTH")

    def test_provider_outage_is_not_research_quality_failure(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        port = create_baseline("GOOD_BASELINE")
        port._error = ModelPortError("provider unavailable")
        report = run_experiment(
            (scenario,),
            port,
            config=BenchmarkExperimentConfig(runs_per_scenario=2),
            model_identity=_identity("GOOD_BASELINE"),
        )
        summary = report.summaries[0]
        self.assertEqual(summary.provider_runtime_failures, 2)
        self.assertEqual(summary.research_quality_failures, 0)
        self.assertEqual(summary.runs[0].failure_class, "PROVIDER_RUNTIME")


class PairedComparisonTests(unittest.TestCase):
    def test_paired_same_suite_and_rejects_mismatch(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        config = BenchmarkExperimentConfig(runs_per_scenario=1)
        left = run_experiment(
            scenarios,
            create_baseline("GOOD_BASELINE"),
            config=config,
            model_identity=_identity("GOOD_BASELINE"),
        )
        right = run_experiment(
            scenarios,
            create_baseline("BAD_HALLUCINATOR"),
            config=config,
            model_identity=_identity("BAD_HALLUCINATOR"),
        )
        paired = compare_experiments(left, right)
        self.assertTrue(paired.comparable)
        self.assertGreaterEqual(len(paired.scenarios), 10)
        self.assertNotIn('"WINNER"', json.dumps(paired.to_mapping()))
        mismatched = run_experiment(
            scenarios[:2],
            create_baseline("GOOD_BASELINE"),
            config=config,
            model_identity=_identity("GOOD_BASELINE"),
        )
        incomparable = compare_experiments(left, mismatched)
        self.assertFalse(incomparable.comparable)
        other_config = BenchmarkExperimentConfig(runs_per_scenario=2)
        different_runs = run_experiment(
            scenarios,
            create_baseline("GOOD_BASELINE"),
            config=other_config,
            model_identity=_identity("GOOD_BASELINE"),
        )
        self.assertFalse(compare_experiments(left, different_runs).comparable)


class FingerprintAndReportTests(unittest.TestCase):
    def test_fingerprint_changes_with_hidden_semantics(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        original = scenario_integrity_hash(scenario)
        raw = json.loads(
            (SCENARIO_DIR / "01_clean_diagnostic.json").read_text(encoding="utf-8")
        )
        raw["hidden_evaluation"]["expected_admission_outcomes"] = ["NEEDS_MORE_CONTEXT"]
        mutated = parse_scenario(raw)
        self.assertNotEqual(original, scenario_integrity_hash(mutated))
        suite = build_suite_manifest((scenario,), suite_id="t")
        self.assertTrue(suite.to_mapping()["hidden_evaluation_omitted"])
        self.assertNotIn("ROS_HIDDEN_CANARY", json.dumps(suite.to_mapping()))

    def test_immutable_report_and_unknown_git(self) -> None:
        scenarios = (load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json"),)
        report = run_experiment(
            scenarios,
            create_baseline("GOOD_BASELINE"),
            config=BenchmarkExperimentConfig(runs_per_scenario=1),
            model_identity=_identity("GOOD_BASELINE"),
            git_commit="unknown",
        )
        mapping = report.to_mapping()
        self.assertEqual(mapping["git_commit"], "unknown")
        self.assertNotIn("WINNER:", json.dumps(mapping).upper())
        with tempfile.TemporaryDirectory() as tmp:
            first = write_immutable_report(Path(tmp), report)
            self.assertTrue(first.exists())
            with self.assertRaises(BenchmarkError):
                write_immutable_report(Path(tmp), report)


class RuntimeIdentityTests(unittest.TestCase):
    def test_api_and_cli_benchmark_identities_differ(self) -> None:
        from zest.benchmark.runner import identity_for_cli_session, identity_for_live

        api = identity_for_live(
            adapter_identity="openai.responses",
            provider_adapter_identity="openai",
            provider_model_id="gpt-test",
        )
        cli = identity_for_cli_session(
            adapter_identity="codex.cli.session",
            runtime_id="codex-cli",
            runtime_version="unknown",
        )
        self.assertEqual(api.runtime_kind, "API")
        self.assertEqual(cli.runtime_kind, "CLI_SESSION")
        self.assertEqual(api.runtime_class, "INFERENCE_RUNTIME")
        self.assertEqual(cli.runtime_class, "AGENT_RUNTIME")
        self.assertNotEqual(api.to_mapping(), cli.to_mapping())
        self.assertFalse(api.to_mapping()["contains_secrets"])
        self.assertNotIn("sk-", json.dumps(api.to_mapping()))


class InstructionIdentityTests(unittest.TestCase):
    def test_instruction_identity_is_stable_for_current_templates(self) -> None:
        first = current_instruction_identity()
        second = current_instruction_identity()
        self.assertEqual(first.generator_instruction_fingerprint, second.generator_instruction_fingerprint)
        self.assertTrue(first.generator_instruction_version)
        self.assertNotIn("sk-", first.generator_instruction_fingerprint)


if __name__ == "__main__":
    unittest.main()
