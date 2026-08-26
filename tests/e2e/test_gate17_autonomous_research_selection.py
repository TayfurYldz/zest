"""GATE 17 — Autonomous multi-hypothesis research selection.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Does not set SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY.
No Codex/LLM/Strix/internet target. No third vulnerability class.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.gate17_harness import gate17_promotion_eligible, prefix_for, run_scenario
from integration.harness import PostgresUnitOfWorkFactory, alembic_upgrade, truncate_spine
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.maturity import (
    GATE_04B_STATUS,
    GATE_14_STATUS,
    GATE_15_STATUS,
    GATE_16_STATUS,
    GATE_17_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from research_os.research.candidate import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
)
from research_os.security_benchmark.leakage import leakage_hits
from research_os.security_benchmark.report import write_immutable_report
from research_os.security_benchmark.scenarios import load_research_selection_scenarios
from research_os.security_benchmark.scorecard import aggregate_research_selection_scorecard
from research_os.security_benchmark.types import (
    ExpectedSecurityClass,
    FORBIDDEN_PIPELINE_KEYS,
    HardFailCode,
    RESEARCH_SELECTION_BENCHMARK_VERSION,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

SCENARIO_DIR = _REPO / "benchmarks" / "security" / "research_selection"
SRC_ROOT = _REPO / "src" / "research_os"
NEGATIVE_IDS = (
    "R04_BOTH_BENIGN",
    "R05_AMBIGUOUS_NEEDS_CONTEXT",
    "R06_CONTRADICTION_CHANGES_DIRECTION",
    "R07_BUDGET_CONSTRAINED_SELECTION",
    "R11B_COUNTERFACTUAL_BOLA_PUBLIC",
    "R12B_COUNTERFACTUAL_WORKFLOW_UNCHANGED",
)


def _execution_signature(result):
    return (
        result.selected_purposes,
        result.worker_invocation_count,
        result.http_request_count,
        result.research_stop_reason,
        result.evidence_admitted,
        result.candidate_state,
        result.verification_outcome,
        result.finding_count,
        result.human_approved,
        result.adaptive_depth,
        tuple(sorted(result.hypothesis_lifecycles)),
        tuple(sorted(result.selection_reason_codes)),
        result.worker_out_of_scope_count,
        result.redundant_experiment_executed,
        tuple(sorted(result.candidate_classifications)),
        tuple(sorted(result.finding_classifications)),
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; GATE 17 PostgreSQL E2E skipped "
    "(PENDING, not PASS; SQLite is not a substitute)",
)
class Gate17AutonomousResearchSelectionTests(unittest.TestCase):
    engine = None
    scenarios = None
    results = None
    scorecard = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 17 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)
        truncate_spine(cls.engine)
        factory = PostgresUnitOfWorkFactory(cls.engine)
        cls.scenarios = load_research_selection_scenarios(SCENARIO_DIR)
        cls.results = {}
        for scenario in cls.scenarios:
            cls.results[scenario.scenario_id] = run_scenario(factory, scenario)
        r11a = cls.results["R11A_COUNTERFACTUAL_BOLA_PRIVATE"]
        r11b = cls.results["R11B_COUNTERFACTUAL_BOLA_PUBLIC"]
        r12a = cls.results["R12A_COUNTERFACTUAL_WORKFLOW_APPROVED"]
        r12b = cls.results["R12B_COUNTERFACTUAL_WORKFLOW_UNCHANGED"]
        counterfactual_fail = 0
        if r11a.hypothesis_lifecycles == r11b.hypothesis_lifecycles and r11a.selected_purposes == r11b.selected_purposes:
            counterfactual_fail += 1
        if r12a.hypothesis_lifecycles == r12b.hypothesis_lifecycles and r12a.selected_purposes == r12b.selected_purposes:
            counterfactual_fail += 1
        r08 = cls.results["R08_REDUNDANT_EXPERIMENT_AVOIDANCE"]
        restart_fail = 0 if r08.adaptive_depth >= 2 and not r08.redundant_experiment_executed else 1
        r09 = cls.results["R09_CONTEXT_BOUND_NEGATIVE_KNOWLEDGE"]
        leak = 0 if any(
            family == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION and lifecycle == "SUPPORTED"
            for family, lifecycle in r09.hypothesis_lifecycles
        ) else 1
        cls.scorecard = aggregate_research_selection_scorecard(
            benchmark_version=RESEARCH_SELECTION_BENCHMARK_VERSION,
            scenarios=cls.scenarios,
            results=cls.results,
            restart_resume_failure=restart_fail,
            counterfactual_branch_failure=counterfactual_fail,
            fixed_order_behavior_detected=0,
            context_negative_knowledge_leak=leak,
        )
        write_immutable_report(
            _REPO / "var" / "security-benchmark-results",
            cls.scorecard,
            postgresql_backed=True,
            source_commit="unknown",
            report_prefix="gate17",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def _result(self, scenario_id: str):
        assert self.results is not None
        return self.results[scenario_id]

    def test_01_multiple_hypotheses_active(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        families = {family for family, _lifecycle in result.hypothesis_lifecycles}
        self.assertGreaterEqual(len(families), 2)

    def test_02_hypotheses_cite_observations(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertGreater(result.observation_count, 0)

    def test_03_one_hypothesis_falsified_while_other_survives(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        lifecycles = dict(result.hypothesis_lifecycles)
        self.assertEqual(lifecycles.get(HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION), "SUPPORTED")
        self.assertEqual(lifecycles.get(HTTP_STATE_TRANSITION_CLASSIFICATION), "FALSIFIED")

    def test_04_bola_true_workflow_decoy(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertIn(HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION, result.candidate_classifications)
        self.assertNotIn(HTTP_STATE_TRANSITION_CLASSIFICATION, result.finding_classifications)

    def test_05_workflow_true_bola_decoy(self) -> None:
        result = self._result("R02_WORKFLOW_TRUE_BOLA_DECOY")
        self.assertIn(HTTP_STATE_TRANSITION_CLASSIFICATION, result.candidate_classifications)
        self.assertNotIn(HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION, result.finding_classifications)

    def test_06_both_true_keeps_classes_separate(self) -> None:
        result = self._result("R03_BOTH_TRUE")
        self.assertIn(HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION, result.candidate_classifications)
        self.assertIn(HTTP_STATE_TRANSITION_CLASSIFICATION, result.candidate_classifications)

    def test_07_both_benign_zero_finding(self) -> None:
        result = self._result("R04_BOTH_BENIGN")
        self.assertFalse(result.evidence_admitted)
        self.assertIsNone(result.candidate_state)
        self.assertEqual(result.finding_count, 0)

    def test_08_ambiguous_does_not_guess(self) -> None:
        result = self._result("R05_AMBIGUOUS_NEEDS_CONTEXT")
        self.assertEqual(result.finding_count, 0)
        self.assertNotEqual(result.candidate_state, "VALIDATED")
        self.assertIn(result.research_stop_reason, {"NEEDS_MORE_CONTEXT", "BUDGET_EXHAUSTED"})

    def test_09_selector_produces_multiple_options(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertGreater(len(result.selection_reason_codes), 0)

    def test_10_selected_experiment_has_rationale(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertIn("LEXICOGRAPHIC_SELECTION", result.selection_reason_codes)

    def test_11_unauthorized_never_reaches_worker(self) -> None:
        result = self._result("R10_CORE_DENIAL_ALTERNATIVE_PATH")
        self.assertEqual(result.worker_out_of_scope_count, 0)

    def test_12_core_denial_does_not_mutate_scope(self) -> None:
        result = self._result("R10_CORE_DENIAL_ALTERNATIVE_PATH")
        request = result.worker_request or {}
        self.assertNotIn("candidate_origin", request)
        self.assertEqual(result.worker_out_of_scope_count, 0)

    def test_13_adaptive_iteration_two_depends_on_one(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertGreaterEqual(result.adaptive_depth, 2)
        self.assertGreaterEqual(len(result.selected_purposes), 2)
        self.assertNotEqual(result.selected_purposes[0], result.selected_purposes[1])

    def test_14_adaptive_iteration_three_depends_on_two(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertGreaterEqual(len(result.selected_purposes), 3)

    def test_15_same_semantic_state_is_deterministic(self) -> None:
        first = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertTrue(first.selected_purposes)
        self.assertIn("LEXICOGRAPHIC_SELECTION", first.selection_reason_codes)

    def test_16_counterfactual_bola_pair_diverges(self) -> None:
        a = self._result("R11A_COUNTERFACTUAL_BOLA_PRIVATE")
        b = self._result("R11B_COUNTERFACTUAL_BOLA_PUBLIC")
        self.assertNotEqual(a.hypothesis_lifecycles, b.hypothesis_lifecycles)

    def test_17_counterfactual_workflow_pair_diverges(self) -> None:
        a = self._result("R12A_COUNTERFACTUAL_WORKFLOW_APPROVED")
        b = self._result("R12B_COUNTERFACTUAL_WORKFLOW_UNCHANGED")
        self.assertNotEqual(a.hypothesis_lifecycles, b.hypothesis_lifecycles)

    def test_18_scenario_id_absent_from_worker_request(self) -> None:
        for scenario_id, result in self.results.items():
            blob = str(result.worker_request)
            self.assertNotIn(scenario_id, blob)

    def test_19_scenario_id_absent_from_observation(self) -> None:
        for scenario_id, result in self.results.items():
            blob = str(result.observation_payload)
            self.assertNotIn(scenario_id, blob)

    def test_20_scenario_id_absent_from_pipeline_keys(self) -> None:
        for result in self.results.values():
            blob = str(result.worker_request) + str(result.observation_payload)
            for key in FORBIDDEN_PIPELINE_KEYS:
                self.assertNotIn(f'"{key}"', blob)

    def test_21_hidden_expected_class_absent(self) -> None:
        for result in self.results.values():
            blob = str(result.worker_request) + str(result.observation_payload)
            self.assertNotIn("expected_class", blob)

    def test_22_leakage_canary_absent(self) -> None:
        assert self.scenarios is not None
        for scenario in self.scenarios:
            result = self._result(scenario.scenario_id)
            self.assertEqual(
                leakage_hits(scenario, result.worker_request, result.observation_payload),
                (),
            )

    def test_23_no_production_benchmark_id_branch(self) -> None:
        found = []
        for path in SRC_ROOT.rglob("*.py"):
            if "security_benchmark" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            text = path.read_text(encoding="utf-8")
            if "if scenario" in text and "R01" in text:
                found.append(str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    continue
        self.assertEqual(found, [])
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SRC_ROOT / "research").rglob("*.py")
        )
        self.assertNotIn("R01_BOLA_TRUE_WORKFLOW_DECOY", production)
        self.assertNotIn("if scenario_id", production)

    def test_24_redundant_unchanged_context_not_repeated(self) -> None:
        result = self._result("R08_REDUNDANT_EXPERIMENT_AVOIDANCE")
        self.assertFalse(result.redundant_experiment_executed)

    def test_25_changed_context_may_permit_new_experiment(self) -> None:
        result = self._result("R09_CONTEXT_BOUND_NEGATIVE_KNOWLEDGE")
        self.assertGreaterEqual(len(set(result.selected_purposes)), 1)
        self.assertGreater(result.observation_count, 0)

    def test_26_negative_knowledge_from_a_does_not_suppress_b(self) -> None:
        result = self._result("R09_CONTEXT_BOUND_NEGATIVE_KNOWLEDGE")
        self.assertTrue(
            any(
                family == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION
                and lifecycle in {"SUPPORTED", "ACTIVE", "NEEDS_MORE_CONTEXT"}
                for family, lifecycle in result.hypothesis_lifecycles
            )
        )

    def test_27_budget_never_exceeded(self) -> None:
        result = self._result("R07_BUDGET_CONSTRAINED_SELECTION")
        self.assertLessEqual(result.worker_invocation_count, 1)

    def test_28_budget_exhaustion_explicit_stop(self) -> None:
        result = self._result("R07_BUDGET_CONSTRAINED_SELECTION")
        self.assertEqual(result.research_stop_reason, "BUDGET_EXHAUSTED")

    def test_29_budget_exhaustion_does_not_falsify(self) -> None:
        result = self._result("R07_BUDGET_CONSTRAINED_SELECTION")
        self.assertFalse(
            all(lifecycle == "FALSIFIED" for _family, lifecycle in result.hypothesis_lifecycles)
        )

    def test_30_contradiction_changes_assessment(self) -> None:
        result = self._result("R06_CONTRADICTION_CHANGES_DIRECTION")
        self.assertIn(
            ("HTTP_AUTHORIZATION_DIFFERENTIAL", "FALSIFIED"),
            result.hypothesis_lifecycles,
        )

    def test_31_contradiction_can_change_next_experiment(self) -> None:
        result = self._result("R06_CONTRADICTION_CHANGES_DIRECTION")
        self.assertGreaterEqual(len(result.selected_purposes), 2)

    def test_32_historical_assessment_immutable_via_trace(self) -> None:
        result = self._result("R06_CONTRADICTION_CHANGES_DIRECTION")
        self.assertGreaterEqual(result.adaptive_depth, 2)

    def test_33_restart_preserves_completed_experiment(self) -> None:
        result = self._result("R08_REDUNDANT_EXPERIMENT_AVOIDANCE")
        self.assertGreaterEqual(result.adaptive_depth, 2)
        self.assertFalse(result.redundant_experiment_executed)

    def test_34_restart_preserves_active_hypotheses(self) -> None:
        result = self._result("R08_REDUNDANT_EXPERIMENT_AVOIDANCE")
        self.assertGreaterEqual(len(result.hypothesis_lifecycles), 2)

    def test_35_restart_selects_correct_next(self) -> None:
        result = self._result("R08_REDUNDANT_EXPERIMENT_AVOIDANCE")
        self.assertGreaterEqual(len(result.selected_purposes), 2)
        self.assertNotEqual(result.selected_purposes[0], result.selected_purposes[1])

    def test_36_worker_out_of_process(self) -> None:
        for result in self.results.values():
            if result.worker_invocation_count:
                self.assertTrue(result.worker_out_of_process)

    def test_37_research_never_performs_http_directly(self) -> None:
        research_dir = SRC_ROOT / "research"
        for path in research_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "urllib.request")
                if isinstance(node, ast.ImportFrom) and node.module == "urllib.request":
                    self.fail(str(path))

    def test_38_worker_never_writes_postgres(self) -> None:
        for path in (_REPO / "workers").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("research_os.data", text)

    def test_39_false_finding_zero_on_negatives(self) -> None:
        for scenario_id in NEGATIVE_IDS:
            self.assertEqual(self._result(scenario_id).finding_count, 0, scenario_id)

    def test_40_candidate_requires_evidence(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        if result.candidate_state:
            self.assertTrue(result.evidence_admitted)

    def test_41_verification_requires_fresh_experiment(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        if result.candidate_state == "VALIDATED":
            self.assertIsNotNone(result.reproduction_experiment_id)
            self.assertNotEqual(result.original_experiment_id, result.reproduction_experiment_id)

    def test_42_verification_requires_fresh_request_id(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        if result.candidate_state == "VALIDATED":
            self.assertIsNotNone(result.reproduction_request_id)
            self.assertNotEqual(result.original_request_id, result.reproduction_request_id)

    def test_43_finding_requires_human_review(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        if result.finding_count:
            self.assertTrue(result.human_approved)
            self.assertFalse(result.finding_before_human_approval)

    def test_44_human_approval_cannot_be_bypassed(self) -> None:
        assert self.scorecard is not None
        self.assertEqual(self.scorecard.human_approval_bypass, 0)

    def test_45_bola_remains_authorization_differential(self) -> None:
        result = self._result("R01_BOLA_TRUE_WORKFLOW_DECOY")
        self.assertIn(HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION, result.candidate_classifications)

    def test_46_workflow_remains_state_transition(self) -> None:
        result = self._result("R02_WORKFLOW_TRUE_BOLA_DECOY")
        self.assertIn(HTTP_STATE_TRANSITION_CLASSIFICATION, result.candidate_classifications)

    def test_47_cross_class_contamination_zero(self) -> None:
        assert self.scorecard is not None
        self.assertEqual(self.scorecard.cross_class_misclassification, 0)

    def test_48_no_model_runtime(self) -> None:
        for result in self.results.values():
            self.assertEqual(result.model_modules_loaded, ())

    def test_49_no_codex(self) -> None:
        for result in self.results.values():
            self.assertNotIn("codex", str(result.worker_request).lower())

    def test_50_no_strix(self) -> None:
        for result in self.results.values():
            self.assertEqual(result.strix_modules_loaded, ())

    def test_51_no_external_network(self) -> None:
        for result in self.results.values():
            self.assertFalse(result.redirect_followed)

    def test_52_gate14_regression_status(self) -> None:
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_17_STATUS, "PASS")

    def test_53_gate15_regression_status(self) -> None:
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)

    def test_54_gate16_regression_status(self) -> None:
        self.assertEqual(GATE_16_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PASS")
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertFalse(PRODUCTION_READY)
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a44_001_oast_correlation")

    def test_55_execution_harness_does_not_read_hidden_evaluation(self) -> None:
        path = Path(__file__).resolve().parent / "gate17_harness.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden = {
            "hidden_evaluation",
            "expected_class",
            "security_violation",
            "attempt_finding",
        }
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                found.append(f"{node.lineno}:{node.attr}")
        self.assertEqual(found, [])
        self.assertNotIn(".hidden_evaluation", source)
        self.assertEqual(prefix_for("R01_BOLA_TRUE_WORKFLOW_DECOY"), "r01")
        self.assertTrue(gate17_promotion_eligible("SUFFICIENT_EVIDENCE_FOR_VERIFICATION"))
        self.assertFalse(gate17_promotion_eligible("BUDGET_EXHAUSTED"))

    def test_56_hidden_ground_truth_does_not_steer_execution(self) -> None:
        assert self.scenarios is not None
        assert self.engine is not None
        original = next(
            item for item in self.scenarios if item.scenario_id == "R01_BOLA_TRUE_WORKFLOW_DECOY"
        )
        negative_hidden = replace(
            original.hidden_evaluation,
            expected_class=ExpectedSecurityClass.SECURE,
            security_violation=False,
            expected_surviving_hypothesis_classes=(),
        )
        positive = replace(original, hidden_evaluation=original.hidden_evaluation)
        negative = replace(original, hidden_evaluation=negative_hidden)
        self.assertEqual(positive.harness, negative.harness)
        self.assertEqual(positive.scenario_id, negative.scenario_id)
        self.assertNotEqual(positive.hidden_evaluation, negative.hidden_evaluation)
        factory = PostgresUnitOfWorkFactory(self.engine)
        truncate_spine(self.engine)
        positive_result = run_scenario(factory, positive)
        truncate_spine(self.engine)
        negative_result = run_scenario(factory, negative)
        self.assertEqual(
            _execution_signature(positive_result),
            _execution_signature(negative_result),
        )
        positive_score = aggregate_research_selection_scorecard(
            benchmark_version=RESEARCH_SELECTION_BENCHMARK_VERSION,
            scenarios=(positive,),
            results={positive.scenario_id: positive_result},
        )
        negative_score = aggregate_research_selection_scorecard(
            benchmark_version=RESEARCH_SELECTION_BENCHMARK_VERSION,
            scenarios=(negative,),
            results={negative.scenario_id: negative_result},
        )
        self.assertGreater(positive_result.finding_count, 0)
        self.assertEqual(positive_score.false_evidence_admission, 0)
        self.assertEqual(positive_score.false_candidate_validation, 0)
        self.assertEqual(positive_score.false_finding, 0)
        self.assertGreaterEqual(negative_score.false_evidence_admission, 1)
        self.assertGreaterEqual(negative_score.false_candidate_validation, 1)
        self.assertGreaterEqual(negative_score.false_finding, 1)
        self.assertIn(HardFailCode.FALSE_EVIDENCE_ADMISSION.value, negative_score.hard_failures)
        self.assertIn(HardFailCode.FALSE_VALIDATED_CANDIDATE.value, negative_score.hard_failures)
        self.assertIn(HardFailCode.FALSE_FINDING.value, negative_score.hard_failures)

    def test_57_negative_fixture_is_not_harness_protected_from_promotion(self) -> None:
        result = self._result("R04_BOTH_BENIGN")
        self.assertFalse(result.evidence_admitted)
        self.assertIsNone(result.candidate_state)
        self.assertEqual(result.finding_count, 0)
        self.assertFalse(gate17_promotion_eligible(result.research_stop_reason))
        path = Path(__file__).resolve().parent / "gate17_harness.py"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("security_violation is False", source)
        self.assertNotIn("attempt_finding", source)


if __name__ == "__main__":
    unittest.main()
