from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from integrations.models.discovery import Readiness, discover_configured_runtimes, gate_04b_status

API_KEY_PREFIX = "sk" + "-"


class RuntimeDiscoveryTests(unittest.TestCase):
    def test_empty_env_does_not_fabricate_availability(self) -> None:
        report = discover_configured_runtimes(
            env={"ZEST_CODEX_EXECUTABLE": "__zest_missing_codex__"}
        )
        mapping = report.to_mapping()
        self.assertIn(
            mapping["kind_matrix"]["API"],
            {Readiness.UNAVAILABLE.value, Readiness.CONFIGURED_NOT_READY.value},
        )
        self.assertNotEqual(mapping["kind_matrix"]["API"], Readiness.AVAILABLE.value)
        self.assertEqual(mapping["kind_matrix"]["SUBSCRIPTION_OAUTH"], Readiness.UNAVAILABLE.value)
        self.assertIn(mapping["kind_matrix"]["CLI_SESSION"], {Readiness.UNAVAILABLE.value, Readiness.CONFIGURED_NOT_READY.value, Readiness.AVAILABLE.value})
        self.assertEqual(mapping["kind_matrix"]["LOCAL_MODEL"], Readiness.UNAVAILABLE.value)
        self.assertEqual(mapping["kind_matrix"]["EXTERNAL_AGENT"], Readiness.UNAVAILABLE.value)
        self.assertTrue(mapping["strix_is_not_model_runtime"])
        self.assertTrue(mapping["scripted_does_not_count"])
        self.assertFalse(
            any(
                item in mapping["available_model_configurations"]
                for item in ("openai", "anthropic", "gemini")
            )
        )
        serialized = json.dumps(mapping)
        self.assertNotIn(API_KEY_PREFIX, serialized)
        self.assertNotIn("WINNER", serialized)
        strix = next(item for item in report.entries if item.runtime_kind == "STRIX")
        self.assertFalse(strix.counts_as_model_runtime)

    def test_configured_local_endpoint_is_not_ready_without_product(self) -> None:
        report = discover_configured_runtimes(
            env={
                "ZEST_LOCAL_MODEL_ENDPOINT": "http://explicit-local",
                "ZEST_CODEX_EXECUTABLE": "__zest_missing_codex__",
            }
        )
        local = next(item for item in report.entries if item.runtime_kind == "LOCAL_MODEL")
        self.assertEqual(local.readiness, Readiness.CONFIGURED_NOT_READY)
        self.assertNotIn("local-model", report.available_model_configurations)

    def test_gate_04b_requires_two_executed_comparable_runtimes(self) -> None:
        pending = gate_04b_status(
            available_model_configurations=("openai",),
            executed_live_configurations=(),
            comparable=False,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
        )
        self.assertEqual(pending["status"], "PENDING")
        self.assertFalse(pending["strix_counted_as_model_runtime"])
        self.assertFalse(pending["scripted_counted"])
        eligible = gate_04b_status(
            available_model_configurations=("openai", "codex-cli"),
            executed_live_configurations=("openai", "codex-cli"),
            comparable=True,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
            operationally_comparable=True,
            contract_qualified=True,
            full_comparison_completed=True,
        )
        self.assertEqual(eligible["status"], "PASS")
        operational_only = gate_04b_status(
            available_model_configurations=("openai", "codex-cli"),
            executed_live_configurations=("openai", "codex-cli"),
            comparable=True,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
            operationally_comparable=True,
            contract_qualified=False,
            full_comparison_completed=True,
        )
        self.assertEqual(operational_only["status"], "PENDING")
        self.assertFalse(eligible["sealed_holdout_is_unseen_generalization"])
        leaky = gate_04b_status(
            available_model_configurations=("openai", "anthropic"),
            executed_live_configurations=("openai", "anthropic"),
            comparable=True,
            harness_invariant_failed=True,
            runs_per_scenario=3,
            development_suite=True,
        )
        self.assertEqual(leaky["status"], "NEEDS_REVIEW")
        single_run = gate_04b_status(
            available_model_configurations=("openai", "anthropic"),
            executed_live_configurations=("openai", "anthropic"),
            comparable=True,
            harness_invariant_failed=False,
            runs_per_scenario=1,
            development_suite=True,
        )
        self.assertEqual(single_run["status"], "PENDING")

    def test_gate_04b_does_not_pass_systemic_contract_failure(self) -> None:
        status = gate_04b_status(
            available_model_configurations=("openai", "anthropic"),
            executed_live_configurations=("openai", "anthropic"),
            comparable=True,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
            operationally_comparable=True,
            contract_qualified=False,
            full_comparison_completed=True,
            contract_status={
                "contract_qualified": False,
                "per_runtime": [
                    {
                        "configuration_id": "openai",
                        "generator_calls": 3,
                        "falsifier_calls": 0,
                        "structured_output_failures": 3,
                    },
                    {
                        "configuration_id": "anthropic",
                        "generator_calls": 3,
                        "falsifier_calls": 0,
                        "structured_output_failures": 3,
                    },
                ],
            },
        )
        self.assertEqual(status["status"], "PENDING")
        self.assertTrue(status["gate_04b_state"]["OPERATIONALLY_COMPARABLE"])
        self.assertFalse(status["gate_04b_state"]["CONTRACT_QUALIFIED"])


if __name__ == "__main__":
    unittest.main()
