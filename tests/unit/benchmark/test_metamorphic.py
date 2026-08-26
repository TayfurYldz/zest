from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.baselines import create_baseline
from zest.benchmark.evaluate import evaluate_scenario
from zest.benchmark.metamorphic import assert_variant_aligned
from zest.benchmark.scenarios import context_from_visible, load_scenario, load_scenarios
from zest.research.epistemic import EpistemicClass

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


class MetamorphicTests(unittest.TestCase):
    def test_variants_keep_hidden_family_without_exact_prose(self) -> None:
        parent_injection = load_scenario(SCENARIO_DIR / "05_prompt_injection_content.json")
        variant_injection = load_scenario(
            SCENARIO_DIR / "05b_prompt_injection_content_paraphrase.json"
        )
        parent_poison = load_scenario(SCENARIO_DIR / "06_prior_hypothesis_poisoning.json")
        variant_poison = load_scenario(
            SCENARIO_DIR / "06b_prior_hypothesis_poisoning_renamed.json"
        )
        parent_diff = load_scenario(SCENARIO_DIR / "08_differential_reasoning_seed.json")
        variant_diff = load_scenario(
            SCENARIO_DIR / "08b_differential_reasoning_seed_reordered.json"
        )
        assert_variant_aligned(parent_injection, variant_injection)
        assert_variant_aligned(parent_poison, variant_poison)
        assert_variant_aligned(parent_diff, variant_diff)
        self.assertNotIn(
            "obs:reflected-q",
            [item.observation_id for item in variant_poison.visible_input.observations],
        )
        context = context_from_visible(variant_injection.visible_input)
        untrusted = context.untrusted_external_content
        self.assertTrue(untrusted)
        self.assertTrue(all(item.epistemic_class is EpistemicClass.UNTRUSTED_EXTERNAL for item in untrusted))
        good = evaluate_scenario(
            variant_diff,
            create_baseline("GOOD_BASELINE"),
            adapter_identity="GOOD_BASELINE",
        )
        self.assertNotEqual(good.normalized_claim, parent_diff.visible_input.research_question.lower())

    def test_parent_canary_not_in_variant_visible(self) -> None:
        parent = load_scenario(SCENARIO_DIR / "05_prompt_injection_content.json")
        variant = load_scenario(SCENARIO_DIR / "05b_prompt_injection_content_paraphrase.json")
        blob = " ".join(
            item.statement for item in context_from_visible(variant.visible_input).all_items()
        )
        self.assertNotIn(parent.hidden_evaluation.leakage_canary, blob)
        self.assertNotIn(variant.hidden_evaluation.leakage_canary, blob)


class SpecificityTests(unittest.TestCase):
    def test_generic_template_is_low_specificity_and_stuffing_not_rewarded(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "08_differential_reasoning_seed.json")
        good = evaluate_scenario(
            scenario, create_baseline("GOOD_BASELINE"), adapter_identity="GOOD_BASELINE"
        )
        generic = evaluate_scenario(
            scenario,
            create_baseline("GENERIC_TEMPLATE_BASELINE"),
            adapter_identity="GENERIC_TEMPLATE_BASELINE",
        )
        stuffing = evaluate_scenario(
            scenario, create_baseline("STUFFING_BASELINE"), adapter_identity="STUFFING_BASELINE"
        )
        good_q = {item.dimension: item.passed for item in good.quality}
        generic_q = {item.dimension: item.passed for item in generic.quality}
        stuffing_q = {item.dimension: item.passed for item in stuffing.quality}
        self.assertTrue(good_q["relevant_sources_used"])
        self.assertTrue(good_q["required_source_groups"])
        self.assertTrue(good_q["scenario_specificity"])
        self.assertFalse(generic_q["relevant_sources_used"])
        self.assertFalse(generic_q["scenario_specificity"])
        injection = load_scenario(SCENARIO_DIR / "05_prompt_injection_content.json")
        stuffed_injection = evaluate_scenario(
            injection, create_baseline("STUFFING_BASELINE"), adapter_identity="STUFFING_BASELINE"
        )
        avoided = {
            item.dimension: item.passed for item in stuffed_injection.quality
        }["irrelevant_sources_avoided"]
        self.assertFalse(avoided)
        grounded = {
            item.dimension: item.passed
            for item in evaluate_scenario(
                injection, create_baseline("GOOD_BASELINE"), adapter_identity="GOOD_BASELINE"
            ).quality
        }["irrelevant_sources_avoided"]
        self.assertTrue(grounded)

    def test_policy_boundary_quality_miss_is_preserved(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        policy = next(item for item in scenarios if item.scenario_id == "policy-boundary-trap")
        result = evaluate_scenario(
            policy, create_baseline("GOOD_BASELINE"), adapter_identity="GOOD_BASELINE"
        )
        expected = {
            item.dimension: item.passed for item in result.quality
        }["admission_expectation"]
        self.assertFalse(expected)


if __name__ == "__main__":
    unittest.main()
