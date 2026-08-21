"""GATE 16 — Workflow / state-transition authorization.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Does not set SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY.
No Codex/LLM/Strix/internet target.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.gate16_harness import GATE16_HUMAN, prefix_for, run_bola_cross_class, run_scenario
from integration.harness import PostgresUnitOfWorkFactory, alembic_upgrade, truncate_spine
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.maturity import (
    GATE_04B_STATUS,
    GATE_14_STATUS,
    GATE_15_STATUS,
    GATE_16_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from research_os.research.candidate import HTTP_STATE_TRANSITION_CLASSIFICATION
from research_os.security_benchmark.leakage import leakage_hits
from research_os.security_benchmark.report import write_immutable_report
from research_os.security_benchmark.scenarios import load_workflow_scenarios
from research_os.security_benchmark.scorecard import (
    aggregate_workflow_scorecard,
    gate16_scorecard_pass,
)
from research_os.security_benchmark.types import (
    FORBIDDEN_PIPELINE_KEYS,
    WORKFLOW_BENCHMARK_VERSION,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

SCENARIO_DIR = _REPO / "benchmarks" / "security" / "workflow"
NEGATIVE_IDS = (
    "W03_SECURE_ROLE_ENFORCEMENT",
    "W04_SECURE_SEQUENCE_ENFORCEMENT",
    "W05_DECEPTIVE_200_NO_STATE_CHANGE",
    "W06_IDEMPOTENT_REPEAT",
    "W07_LEGITIMATE_DELEGATED_REVIEWER",
    "W08_STALE_CLIENT_STATE",
    "W09_CONTRADICTORY_VERIFICATION",
    "W10_OPERATIONAL_TIMEOUT",
    "W11_OUT_OF_SCOPE",
    "W12_REDIRECT_BOUNDARY",
)
BOLA_CLASSIFICATION = "HTTP_AUTHORIZATION_DIFFERENTIAL"


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; GATE 16 PostgreSQL E2E skipped "
    "(PENDING, not PASS; SQLite is not a substitute)",
)
class Gate16StateTransitionSecurityTests(unittest.TestCase):
    engine = None
    scenarios = None
    results = None
    scorecard = None
    bola = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 16 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)
        truncate_spine(cls.engine)
        factory = PostgresUnitOfWorkFactory(cls.engine)
        cls.scenarios = load_workflow_scenarios(SCENARIO_DIR)
        cls.results = {}
        for scenario in cls.scenarios:
            cls.results[scenario.scenario_id] = run_scenario(factory, scenario)
        cls.bola = run_bola_cross_class(factory)
        cls.scorecard = aggregate_workflow_scorecard(
            benchmark_version=WORKFLOW_BENCHMARK_VERSION,
            scenarios=cls.scenarios,
            results=cls.results,
        )
        results_dir = _REPO / "var" / "security-benchmark-results"
        write_immutable_report(
            results_dir,
            cls.scorecard,
            postgresql_backed=True,
            source_commit="unknown",
            report_prefix="gate16",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def _result(self, scenario_id: str):
        assert self.results is not None
        return self.results[scenario_id]

    def test_01_true_role_bypass_detected(self) -> None:
        result = self._result("W01_TRUE_ROLE_BYPASS")
        self.assertGreater(result.observation_count, 0)
        self.assertEqual(result.assessment_reason_code, "UNAUTHORIZED_STATE_TRANSITION_ESTABLISHED")

    def test_02_role_bypass_evidence_admitted(self) -> None:
        result = self._result("W01_TRUE_ROLE_BYPASS")
        self.assertTrue(result.evidence_admitted)

    def test_03_role_bypass_independently_validated(self) -> None:
        result = self._result("W01_TRUE_ROLE_BYPASS")
        self.assertEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.verification_outcome, "VALIDATED")

    def test_04_role_bypass_finding_requires_human_approval(self) -> None:
        result = self._result("W01_TRUE_ROLE_BYPASS")
        self.assertEqual(result.finding_count, 1)
        self.assertTrue(result.human_approved)
        self.assertFalse(result.finding_before_human_approval)

    def test_05_true_sequence_skip_detected(self) -> None:
        result = self._result("W02_TRUE_SEQUENCE_SKIP")
        self.assertTrue(result.evidence_admitted)
        self.assertEqual(result.assessment_reason_code, "UNAUTHORIZED_STATE_TRANSITION_ESTABLISHED")
        facts = (result.observation_payload or {})
        self.assertEqual(facts.get("pre_state"), "DRAFT")
        self.assertEqual(facts.get("post_state"), "APPROVED")

    def test_06_sequence_skip_independently_validated(self) -> None:
        result = self._result("W02_TRUE_SEQUENCE_SKIP")
        self.assertEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.finding_count, 1)
        self.assertEqual(result.finding_classification, HTTP_STATE_TRANSITION_CLASSIFICATION)
        self.assertNotEqual(result.finding_classification, BOLA_CLASSIFICATION)

    def test_07_secure_role_enforcement_no_finding(self) -> None:
        result = self._result("W03_SECURE_ROLE_ENFORCEMENT")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)

    def test_08_secure_sequence_enforcement_no_finding(self) -> None:
        result = self._result("W04_SECURE_SEQUENCE_ENFORCEMENT")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)

    def test_09_deceptive_200_unchanged_state_no_evidence_or_finding(self) -> None:
        result = self._result("W05_DECEPTIVE_200_NO_STATE_CHANGE")
        self.assertEqual(result.assessment_reason_code, "STATUS_OR_UNCHANGED_STATE_IS_NOT_PROOF")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        payload = result.observation_payload or {}
        self.assertEqual(payload.get("response_status"), 200)
        self.assertFalse(payload.get("state_changed"))

    def test_10_idempotent_repeat_no_finding(self) -> None:
        result = self._result("W06_IDEMPOTENT_REPEAT")
        self.assertEqual(result.assessment_reason_code, "IDEMPOTENT_NO_NEW_TRANSITION")
        self.assertEqual(result.finding_count, 0)

    def test_11_delegated_reviewer_no_finding(self) -> None:
        result = self._result("W07_LEGITIMATE_DELEGATED_REVIEWER")
        self.assertEqual(result.assessment_reason_code, "LEGITIMATE_DELEGATED_REVIEWER")
        self.assertEqual(result.finding_count, 0)

    def test_12_stale_state_no_finding(self) -> None:
        result = self._result("W08_STALE_CLIENT_STATE")
        self.assertEqual(result.assessment_reason_code, "STALE_OR_CONFLICT")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)

    def test_13_contradictory_verification_rejected(self) -> None:
        result = self._result("W09_CONTRADICTORY_VERIFICATION")
        self.assertTrue(result.evidence_admitted)
        self.assertEqual(result.candidate_state, "REJECTED")
        self.assertEqual(result.verification_outcome, "REJECTED")
        self.assertEqual(result.finding_count, 0)

    def test_14_timeout_inconclusive(self) -> None:
        result = self._result("W10_OPERATIONAL_TIMEOUT")
        self.assertTrue(result.evidence_admitted)
        self.assertEqual(result.candidate_state, "INCONCLUSIVE")
        self.assertEqual(result.verification_outcome, "INCONCLUSIVE")
        self.assertNotEqual(result.candidate_state, "REJECTED")
        self.assertNotEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.finding_count, 0)

    def test_15_out_of_scope_zero_worker(self) -> None:
        result = self._result("W11_OUT_OF_SCOPE")
        self.assertEqual(result.worker_invocation_count, 0)
        self.assertEqual(result.http_request_count, 0)
        self.assertEqual(result.observation_count, 0)
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)

    def test_16_redirect_not_followed(self) -> None:
        result = self._result("W12_REDIRECT_BOUNDARY")
        self.assertFalse(result.redirect_followed)
        self.assertEqual(result.observation_count, 0)
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.worker_result_status, "REAUTHORIZATION_REQUIRED")

    def test_17_fresh_verification_new_experiment(self) -> None:
        for scenario_id in ("W01_TRUE_ROLE_BYPASS", "W02_TRUE_SEQUENCE_SKIP"):
            result = self._result(scenario_id)
            self.assertIsNotNone(result.original_experiment_id)
            self.assertIsNotNone(result.reproduction_experiment_id)
            self.assertNotEqual(result.original_experiment_id, result.reproduction_experiment_id)

    def test_18_fresh_verification_new_request_id(self) -> None:
        for scenario_id in ("W01_TRUE_ROLE_BYPASS", "W02_TRUE_SEQUENCE_SKIP"):
            result = self._result(scenario_id)
            self.assertIsNotNone(result.original_request_id)
            self.assertIsNotNone(result.reproduction_request_id)
            self.assertNotEqual(result.original_request_id, result.reproduction_request_id)

    def test_19_fresh_verification_new_execution_attempt(self) -> None:
        prefix = prefix_for("W01_TRUE_ROLE_BYPASS")
        with PostgresUnitOfWork(self.engine) as uow:
            original = uow.execution_attempts.list_for_experiment(f"{prefix}-exp")
            repro = uow.execution_attempts.list_for_experiment(f"{prefix}-repro")
        self.assertEqual(len(original), 1)
        self.assertEqual(len(repro), 1)
        self.assertNotEqual(original[0].attempt_id, repro[0].attempt_id)

    def test_20_worker_remains_out_of_process(self) -> None:
        for result in self.results.values():
            if result.worker_invocation_count:
                self.assertTrue(result.worker_out_of_process)

    def test_21_ground_truth_absent_from_worker_request(self) -> None:
        assert self.scenarios is not None
        by_id = {item.scenario_id: item for item in self.scenarios}
        for scenario_id, result in self.results.items():
            if not result.worker_request:
                continue
            hits = leakage_hits(by_id[scenario_id], result.worker_request)
            self.assertEqual(hits, (), scenario_id)
            blob = str(result.worker_request)
            for key in FORBIDDEN_PIPELINE_KEYS:
                self.assertNotIn(f'"{key}"', blob)

    def test_22_ground_truth_absent_from_observation(self) -> None:
        assert self.scenarios is not None
        by_id = {item.scenario_id: item for item in self.scenarios}
        for scenario_id, result in self.results.items():
            if not result.observation_payload:
                continue
            hits = leakage_hits(by_id[scenario_id], result.observation_payload)
            self.assertEqual(hits, (), scenario_id)
            self.assertNotIn("is_vulnerable", result.observation_payload)

    def test_23_cross_class_bola_classification_preserved(self) -> None:
        assert self.bola is not None
        self.assertEqual(self.bola.result.observed_classification, BOLA_CLASSIFICATION)
        self.assertNotEqual(
            self.bola.result.observed_classification, HTTP_STATE_TRANSITION_CLASSIFICATION
        )

    def test_24_workflow_finding_cannot_be_classified_bola(self) -> None:
        for scenario_id in ("W01_TRUE_ROLE_BYPASS", "W02_TRUE_SEQUENCE_SKIP"):
            result = self._result(scenario_id)
            self.assertEqual(result.finding_classification, HTTP_STATE_TRANSITION_CLASSIFICATION)
            self.assertNotEqual(result.finding_classification, BOLA_CLASSIFICATION)
            self.assertEqual(result.observed_classification, HTTP_STATE_TRANSITION_CLASSIFICATION)

    def test_25_bola_evidence_cannot_become_workflow_candidate(self) -> None:
        assert self.bola is not None
        self.assertIsNone(self.bola.workflow_proposal_from_bola_evidence)
        self.assertEqual(self.bola.result.observed_classification, BOLA_CLASSIFICATION)

    def test_26_http_200_alone_cannot_become_workflow_evidence(self) -> None:
        result = self._result("W05_DECEPTIVE_200_NO_STATE_CHANGE")
        self.assertEqual((result.observation_payload or {}).get("response_status"), 200)
        self.assertFalse(result.evidence_admitted)

    def test_27_unchanged_state_cannot_become_vulnerability_evidence(self) -> None:
        result = self._result("W05_DECEPTIVE_200_NO_STATE_CHANGE")
        self.assertFalse((result.observation_payload or {}).get("state_changed"))
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(self._result("W06_IDEMPOTENT_REPEAT").finding_count, 0)

    def test_28_human_core_approval_mandatory(self) -> None:
        for scenario_id in ("W01_TRUE_ROLE_BYPASS", "W02_TRUE_SEQUENCE_SKIP"):
            result = self._result(scenario_id)
            self.assertTrue(result.human_approved)
            self.assertFalse(result.finding_before_human_approval)
        for scenario_id in NEGATIVE_IDS:
            self.assertEqual(self._result(scenario_id).finding_count, 0)

    def test_29_postgresql_reload_preserves_provenance(self) -> None:
        result = self._result("W01_TRUE_ROLE_BYPASS")
        prefix = prefix_for("W01_TRUE_ROLE_BYPASS")
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                run = uow.research_runs.get(f"{prefix}-run")
                experiment = uow.experiments.get(f"{prefix}-exp")
                repro = uow.experiments.get(f"{prefix}-repro")
                findings = uow.findings.list_for_research_run(f"{prefix}-run")
                candidates = uow.candidates.list_for_research_run(f"{prefix}-run")
                evidence = uow.evidence.list_for_research_run(f"{prefix}-run")
                observations = uow.observations.list_for_experiment(f"{prefix}-exp")
                verifications = uow.verifications.list_for_candidate(candidates[0].candidate_id)
                reviews = uow.human_reviews.get_for_proposal(
                    uow.finding_proposals.list_for_research_run(f"{prefix}-run")[0].proposal_id
                )
            self.assertIsNotNone(run)
            self.assertEqual(run.authorization_source_id, f"{prefix}-as")
            self.assertIsNotNone(experiment)
            self.assertIsNotNone(repro)
            self.assertNotEqual(experiment.experiment_id, repro.experiment_id)
            self.assertGreaterEqual(len(observations), 1)
            self.assertGreaterEqual(len(evidence), 2)
            self.assertEqual(candidates[0].state, "VALIDATED")
            self.assertEqual(candidates[0].classification, HTTP_STATE_TRANSITION_CLASSIFICATION)
            self.assertEqual(len(verifications), 1)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].classification, HTTP_STATE_TRANSITION_CLASSIFICATION)
            assert reviews is not None
            self.assertEqual(reviews.reviewer_id, GATE16_HUMAN)
            self.assertEqual(result.finding_count, 1)
        finally:
            reloaded.dispose()

    def test_30_no_model_or_codex_invocation(self) -> None:
        for result in self.results.values():
            self.assertEqual(result.model_modules_loaded, ())
        self.assertNotIn("research_os.integrations.models.cli_session", sys.modules)
        self.assertNotIn("openai", sys.modules)
        self.assertNotIn("anthropic", sys.modules)

    def test_31_no_strix_invocation(self) -> None:
        for result in self.results.values():
            self.assertEqual(result.strix_modules_loaded, ())
        self.assertNotIn("research_os.integrations.strix.adapter", sys.modules)

    def test_32_no_external_request(self) -> None:
        for scenario_id, result in self.results.items():
            self.assertFalse(result.redirect_followed, scenario_id)
        self.assertEqual(self._result("W11_OUT_OF_SCOPE").http_request_count, 0)

    def test_33_gate14_regression_status_unchanged(self) -> None:
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PENDING")
        self.assertFalse(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)

    def test_34_gate15_regression_status_unchanged(self) -> None:
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertEqual(GATE_16_STATUS, "PASS")
        assert self.scorecard is not None
        self.assertTrue(gate16_scorecard_pass(self.scorecard))
        self.assertEqual(self.scorecard.workflow_false_finding, 0)
        self.assertEqual(self.scorecard.cross_class_misclassification, 0)
        self.assertEqual(self.scorecard.workflow_true_positive, 2)
        self.assertEqual(self.scorecard.skipped, 0)
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a38_001_promotion_run")


if __name__ == "__main__":
    unittest.main()
