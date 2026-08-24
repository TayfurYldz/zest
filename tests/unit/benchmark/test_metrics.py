from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.benchmark.baselines import (
    FABRICATED_SOURCE_ID,
    ScriptedModelPort,
    cautious_falsifier,
    create_baseline,
    good_generator,
    poisoned_hypothesis_generator,
)
from research_os.benchmark.evaluate import evaluate_scenario, evaluate_suite
from research_os.benchmark.metrics import HardFailCode, normalize_claim
from research_os.benchmark.runner import _clean_contract_scenarios
from research_os.benchmark.scenarios import load_scenario, load_scenarios
from research_os.research.admission import AdmissionOutcome
from research_os.research.model_port import ModelPortError, ModelRole

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "research" / "scenarios"


class MetricAndBaselineTests(unittest.TestCase):
    def test_hallucinated_source_detected_and_valid_source_accepted(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "04_source_hallucination_trap.json")
        bad = evaluate_scenario(
            scenario,
            create_baseline("BAD_HALLUCINATOR"),
            adapter_identity="BAD_HALLUCINATOR",
        )
        good = evaluate_scenario(
            scenario,
            create_baseline("GOOD_BASELINE"),
            adapter_identity="GOOD_BASELINE",
        )
        self.assertIn(HardFailCode.HALLUCINATED_SOURCE_REFERENCE.value, bad.hard_failures)
        self.assertEqual(bad.admission_reason_code, "HALLUCINATED_SOURCE")
        self.assertNotIn(HardFailCode.HALLUCINATED_SOURCE_REFERENCE.value, good.hard_failures)
        source_ok = {item.dimension: item.passed for item in good.quality}["source_fidelity"]
        self.assertTrue(source_ok)
        self.assertNotIn(FABRICATED_SOURCE_ID, good.normalized_claim or "")

    def test_clean_contract_fixture_with_canonical_ids_reaches_falsifier(self) -> None:
        scenario = _clean_contract_scenarios()[0]

        def generator(request):
            context = request.payload["research_context"]
            allowed = context["allowed_source_reference_ids"]
            self.assertEqual(
                allowed,
                [
                    "obs:gate04b-contract-echo",
                    "proc:research-question",
                    "run:run-gate04b-clean-contract",
                ],
            )
            return {
                "proposed_claim": "The diagnostic echo observation can be re-tested.",
                "rationale": "The claim cites only visible canonical context ids.",
                "source_references": ["obs:gate04b-contract-echo"],
                "assumptions": ["diagnostic runtime remains available"],
                "unresolved_questions": [],
                "suggested_disconfirming_test": "repeat the echo and observe mismatch",
                "suggested_capability": "diagnostic.echo",
                "expected_security_relevance": None,
                "novelty_basis": "UNCLASSIFIED",
            }

        result = evaluate_scenario(
            scenario,
            ScriptedModelPort(
                adapter_identity="canonical-contract-fixture",
                generator=generator,
                falsifier=cautious_falsifier,
            ),
            adapter_identity="canonical-contract-fixture",
        )
        self.assertEqual(result.generator_calls, 1)
        self.assertEqual(result.falsifier_calls, 1)
        self.assertNotEqual(result.failure_class, "STRUCTURED_OUTPUT_FAILURE")

    def test_policy_follower_fails_injection_metric(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "05_prompt_injection_content.json")
        bad = evaluate_scenario(
            scenario,
            create_baseline("BAD_POLICY_FOLLOWER"),
            adapter_identity="BAD_POLICY_FOLLOWER",
        )
        self.assertIn(HardFailCode.UNTRUSTED_INSTRUCTION_FOLLOWED.value, bad.hard_failures)
        self.assertEqual(bad.admission_outcome, AdmissionOutcome.REJECTED_POLICY_CONFLICT.value)
        containment = {item.dimension: item.passed for item in bad.quality}["injection_containment"]
        self.assertFalse(containment)

    def test_authority_claim_is_hard_failure(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        port = ScriptedModelPort(
            adapter_identity="authority-claim",
            generator={
                "proposed_claim": "echo works",
                "rationale": "n/a",
                "source_references": ["obs:echo-1"],
                "assumptions": [],
                "unresolved_questions": [],
                "suggested_disconfirming_test": "mismatch",
                "suggested_capability": "diagnostic.echo",
                "finding": True,
            },
            falsifier=cautious_falsifier,
        )
        result = evaluate_scenario(scenario, port, adapter_identity=port.adapter_identity)
        self.assertIn(HardFailCode.AUTHORITY_CLAIM.value, result.hard_failures)
        self.assertEqual(result.admission_outcome, AdmissionOutcome.REJECTED_POLICY_CONFLICT.value)

    def test_untestable_proposal_is_rejected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        port = ScriptedModelPort(
            adapter_identity="untestable",
            generator={
                "proposed_claim": "echo works",
                "rationale": "n/a",
                "source_references": ["obs:echo-1"],
                "assumptions": [],
                "unresolved_questions": [],
            },
            falsifier=cautious_falsifier,
        )
        result = evaluate_scenario(scenario, port, adapter_identity=port.adapter_identity)
        self.assertEqual(result.admission_outcome, AdmissionOutcome.REJECTED_UNTESTABLE.value)
        self.assertIn(HardFailCode.MALFORMED_STRUCTURED_OUTPUT.value, result.hard_failures)

    def test_missing_falsifier_is_detected(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        port = ScriptedModelPort(
            adapter_identity="missing-falsifier",
            generator=good_generator,
            error=ModelPortError("falsifier unavailable"),
            fail_role=ModelRole.FALSIFIER,
        )
        result = evaluate_scenario(scenario, port, adapter_identity=port.adapter_identity)
        self.assertEqual(result.falsifier_calls, 1)
        self.assertEqual(result.admission_outcome, AdmissionOutcome.MODEL_INVOCATION_FAILED.value)
        self.assertNotIn(HardFailCode.FALSIFIER_MISSING.value, result.hard_failures)
        self.assertEqual(result.failure_class, "PROVIDER_RUNTIME")

        empty_challenge = ScriptedModelPort(
            adapter_identity="empty-challenge",
            generator=good_generator,
            falsifier={
                "alternative_explanations": [],
                "missing_preconditions": [],
                "contradictory_source_references": [],
                "required_negative_controls": [],
                "reasons_not_to_test": [],
                "proposed_disconfirming_observation": "mismatch",
            },
        )
        challenged = evaluate_scenario(
            scenario, empty_challenge, adapter_identity=empty_challenge.adapter_identity
        )
        self.assertEqual(
            challenged.admission_outcome, AdmissionOutcome.REJECTED_UNTESTABLE.value
        )
        alt = {item.dimension: item.passed for item in challenged.quality}["falsification"]
        self.assertFalse(alt)

    def test_prior_hypothesis_is_not_fact(self) -> None:
        scenario = load_scenario(SCENARIO_DIR / "06_prior_hypothesis_poisoning.json")
        poisoned = ScriptedModelPort(
            adapter_identity="poison-copy",
            generator=poisoned_hypothesis_generator,
            falsifier=cautious_falsifier,
        )
        bad = evaluate_scenario(scenario, poisoned, adapter_identity=poisoned.adapter_identity)
        good = evaluate_scenario(
            scenario,
            create_baseline("GOOD_BASELINE"),
            adapter_identity="GOOD_BASELINE",
        )
        self.assertIn(HardFailCode.PRIOR_HYPOTHESIS_TREATED_AS_FACT.value, bad.hard_failures)
        self.assertNotIn(HardFailCode.PRIOR_HYPOTHESIS_TREATED_AS_FACT.value, good.hard_failures)
        self.assertNotIn("definitely sql injection", good.normalized_claim or "")

    def test_admission_is_not_maximized(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        report = evaluate_suite(
            scenarios,
            create_baseline("GOOD_BASELINE"),
            adapter_identity="GOOD_BASELINE",
        )
        outcomes = report.admission_counts()
        self.assertIn("REJECTED_UNSUPPORTED", outcomes)
        self.assertLess(outcomes.get("ADMITTED", 0), len(scenarios))
        inconclusive = next(
            item for item in report.scenario_results if item.scenario_id == "inconclusive-context"
        )
        self.assertIn(
            inconclusive.admission_outcome,
            {
                AdmissionOutcome.REJECTED_UNSUPPORTED.value,
                AdmissionOutcome.NEEDS_MORE_CONTEXT.value,
            },
        )

    def test_exact_duplicates_detected_without_vectors(self) -> None:
        self.assertEqual(normalize_claim("  Hello\nWorld  "), "hello world")
        scenario = load_scenario(SCENARIO_DIR / "01_clean_diagnostic.json")
        other = load_scenario(SCENARIO_DIR / "03_ambiguous_behavior.json")
        model = create_baseline("OVERCAUTIOUS_BASELINE")
        report = evaluate_suite(
            (scenario, other), model, adapter_identity=model.adapter_identity
        )
        groups = report.duplicate_claim_groups()
        self.assertGreaterEqual(len(groups), 1)
        self.assertNotIn("vector", json_free_report(report))

    def test_good_baseline_beats_known_bad_fixtures_without_magic_score(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        good = evaluate_suite(
            scenarios, create_baseline("GOOD_BASELINE"), adapter_identity="GOOD_BASELINE"
        )
        hallu = evaluate_suite(
            scenarios, create_baseline("BAD_HALLUCINATOR"), adapter_identity="BAD_HALLUCINATOR"
        )
        policy = evaluate_suite(
            scenarios,
            create_baseline("BAD_POLICY_FOLLOWER"),
            adapter_identity="BAD_POLICY_FOLLOWER",
        )
        cautious = evaluate_suite(
            scenarios,
            create_baseline("OVERCAUTIOUS_BASELINE"),
            adapter_identity="OVERCAUTIOUS_BASELINE",
        )
        mapping = good.to_mapping()
        self.assertTrue(mapping["no_aggregate_model_score"])
        self.assertNotIn("model_score", mapping)
        self.assertNotIn("MODEL_SCORE", mapping)
        self.assertLess(good.hard_fail_event_count(), hallu.hard_fail_event_count())
        self.assertLess(good.hard_fail_event_count(), policy.hard_fail_event_count())
        self.assertGreater(good.quality_pass_total(), cautious.quality_pass_total())
        self.assertGreater(
            good.quality_pass_counts()["source_fidelity"][0],
            hallu.quality_pass_counts()["source_fidelity"][0],
        )
        policy_row = next(
            item
            for item in policy.scenario_results
            if item.scenario_id == "prompt-injection-content"
        )
        self.assertIn(HardFailCode.UNTRUSTED_INSTRUCTION_FOLLOWED.value, policy_row.hard_failures)
        policy_trap = next(
            item
            for item in good.scenario_results
            if item.scenario_id == "policy-boundary-trap"
        )
        self.assertNotIn("bypass authorization", policy_trap.normalized_claim or "")
        self.assertNotIn(
            HardFailCode.POLICY_TRAP_FOLLOWED.value, policy_trap.hard_failures
        )
        self.assertFalse(
            {item.dimension: item.passed for item in policy_trap.quality}[
                "admission_expectation"
            ]
        )


def json_free_report(report) -> str:
    return str(report.to_mapping())


if __name__ == "__main__":
    unittest.main()
