from __future__ import annotations

import json
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.baselines import create_baseline
from zest.benchmark.cycle import RecordingModelPort, run_bounded_cycle
from zest.benchmark.evaluate import evaluate_scenario
from zest.benchmark.leakage import leakage_hits, model_visible_blob
from zest.benchmark.scenarios import context_from_visible, load_scenario, load_scenarios
from zest.research.cycle import context_model_payload
from zest.research.model_port import ModelCallRequest, ModelRole

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


class LeakageTests(unittest.TestCase):
    def test_hidden_evaluation_never_in_context_or_model_request(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        model = create_baseline("GOOD_BASELINE")
        for scenario in scenarios:
            result = evaluate_scenario(
                scenario, model, adapter_identity=model.adapter_identity
            )
            self.assertEqual(result.leakage, ())
            self.assertNotIn("HIDDEN_BENCHMARK_DATA_LEAKAGE", result.hard_failures)
            context = context_from_visible(scenario.visible_input)
            payload = json.dumps(context_model_payload(context), sort_keys=True)
            self.assertNotIn("hidden_evaluation", payload)
            self.assertNotIn(scenario.hidden_evaluation.leakage_canary, payload)
            self.assertNotIn("leakage_canary", payload)
            self.assertNotIn("forbidden_fabricated_source_ids", payload)
            blob = model_visible_blob(context)
            self.assertNotIn(scenario.hidden_evaluation.leakage_canary, blob)

    def test_accidental_hidden_merge_is_detected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "04_source_hallucination_trap.json")
        context = context_from_visible(scenario.visible_input)
        poisoned = dict(context_model_payload(context))
        poisoned["hidden_evaluation"] = {
            "leakage_canary": scenario.hidden_evaluation.leakage_canary,
            "forbidden_fabricated_source_ids": list(
                scenario.hidden_evaluation.forbidden_fabricated_source_ids
            ),
        }
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="leak-test",
            context_fingerprint=context.fingerprint,
            instructions="Propose one testable research hypothesis as structured fields only.",
            payload={"research_context": poisoned, "instructions_channel": {"role": "GENERATOR"}},
        )
        hits = leakage_hits(scenario, context, [request])
        self.assertTrue(hits)
        self.assertTrue(any("hidden_keys" in item or "leakage_canary" in item for item in hits))

    def test_canary_in_visible_blob_is_leakage(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        context = context_from_visible(scenario.visible_input)
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="canary-test",
            context_fingerprint=context.fingerprint,
            instructions="x " + scenario.hidden_evaluation.leakage_canary,
            payload={"research_context": context_model_payload(context)},
        )
        hits = leakage_hits(scenario, context, [request])
        self.assertIn("leakage_canary", hits)

    def test_recording_port_does_not_put_hidden_into_generator_or_falsifier(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "05_prompt_injection_content.json")
        context = context_from_visible(scenario.visible_input)
        inner = create_baseline("GOOD_BASELINE")
        trace = run_bounded_cycle(context, RecordingModelPort(inner), correlation_id="rec")
        for request in trace.requests:
            dumped = json.dumps(request.payload, sort_keys=True)
            self.assertNotIn(scenario.hidden_evaluation.leakage_canary, dumped)
            self.assertNotIn("hidden_evaluation", dumped)
            self.assertNotIn(scenario.hidden_evaluation.leakage_canary, request.instructions)


if __name__ == "__main__":
    unittest.main()
