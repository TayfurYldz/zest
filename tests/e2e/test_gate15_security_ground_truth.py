"""GATE 15 — Security ground-truth / false-positive benchmark.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Does not set SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY.
GATE 15 may be PASS while GATE 04B remains PENDING. No Codex/LLM/Strix/internet target.
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

from e2e.gate15_harness import GATE15_HUMAN, prefix_for, run_scenario
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
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from research_os.security_benchmark.leakage import leakage_hits
from research_os.security_benchmark.report import write_immutable_report
from research_os.security_benchmark.scenarios import load_scenarios
from research_os.security_benchmark.scorecard import (
    aggregate_scorecard,
    gate15_scorecard_pass,
)
from research_os.security_benchmark.types import BENCHMARK_VERSION, FORBIDDEN_PIPELINE_KEYS

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

SCENARIO_DIR = _REPO / "benchmarks" / "security" / "scenarios"
NEGATIVE_IDS = (
    "S02_SECURE_OBJECT_AUTHORIZATION",
    "S03_PUBLIC_OBJECT_LEGITIMATE_200",
    "S04_EXPLICIT_DELEGATED_ACCESS",
    "S05_DECEPTIVE_200_NO_OWNERSHIP_PROOF",
    "S06_SHARED_RESOURCE",
    "S07_CONTRADICTORY_VERIFICATION",
    "S08_OPERATIONAL_TIMEOUT",
    "S09_REDIRECT_BOUNDARY",
    "S10_OUT_OF_SCOPE",
)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; GATE 15 PostgreSQL E2E skipped "
    "(PENDING, not PASS; SQLite is not a substitute)",
)
class Gate15SecurityGroundTruthTests(unittest.TestCase):
    engine = None
    scenarios = None
    results = None
    scorecard = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 15 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)
        truncate_spine(cls.engine)
        factory = PostgresUnitOfWorkFactory(cls.engine)
        cls.scenarios = load_scenarios(SCENARIO_DIR)
        cls.results = {}
        for scenario in cls.scenarios:
            cls.results[scenario.scenario_id] = run_scenario(factory, scenario)
        cls.scorecard = aggregate_scorecard(
            benchmark_version=BENCHMARK_VERSION,
            scenarios=cls.scenarios,
            results=cls.results,
        )
        results_dir = _REPO / "var" / "security-benchmark-results"
        write_immutable_report(
            results_dir,
            cls.scorecard,
            postgresql_backed=True,
            source_commit="unknown",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def _result(self, scenario_id: str):
        assert self.results is not None
        return self.results[scenario_id]

    def test_01_true_bola_is_detected_and_evidence_admitted(self) -> None:
        result = self._result("S01_TRUE_BOLA")
        self.assertTrue(result.evidence_admitted)
        self.assertGreater(result.observation_count, 0)
        self.assertEqual(result.assessment_reason_code, "AUTHORIZATION_DIFFERENTIAL_ESTABLISHED")

    def test_02_true_bola_candidate_independently_validated(self) -> None:
        result = self._result("S01_TRUE_BOLA")
        self.assertEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.verification_outcome, "VALIDATED")
        self.assertNotEqual(result.original_experiment_id, result.reproduction_experiment_id)
        self.assertNotEqual(result.original_request_id, result.reproduction_request_id)

    def test_03_true_bola_finding_requires_human_approval(self) -> None:
        result = self._result("S01_TRUE_BOLA")
        self.assertEqual(result.finding_count, 1)
        self.assertTrue(result.human_approved)
        self.assertFalse(result.finding_before_human_approval)

    def test_04_secure_endpoint_produces_no_finding(self) -> None:
        result = self._result("S02_SECURE_OBJECT_AUTHORIZATION")
        self.assertGreater(result.observation_count, 0)
        self.assertFalse(result.evidence_admitted)
        self.assertIsNone(result.candidate_state)
        self.assertEqual(result.finding_count, 0)

    def test_05_public_cross_object_200_produces_no_finding(self) -> None:
        result = self._result("S03_PUBLIC_OBJECT_LEGITIMATE_200")
        self.assertEqual(result.observation_payload["cross_object_request_status"], 200)
        self.assertEqual(result.observation_payload["cross_object_request_visibility"], "PUBLIC")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.assessment_reason_code, "LEGITIMATE_PUBLIC_ACCESS")

    def test_06_delegated_cross_object_200_produces_no_finding(self) -> None:
        result = self._result("S04_EXPLICIT_DELEGATED_ACCESS")
        self.assertEqual(result.observation_payload["cross_object_request_status"], 200)
        self.assertIn("alice", result.observation_payload["cross_object_request_authorized_readers"])
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.assessment_reason_code, "LEGITIMATE_DELEGATED_ACCESS")

    def test_07_shared_resource_200_produces_no_finding(self) -> None:
        result = self._result("S06_SHARED_RESOURCE")
        self.assertEqual(result.observation_payload["cross_object_request_status"], 200)
        self.assertEqual(result.observation_payload["cross_object_request_resource_kind"], "SHARED")
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.assessment_reason_code, "LEGITIMATE_SHARED_ACCESS")

    def test_08_deceptive_200_without_ownership_produces_no_evidence_or_finding(self) -> None:
        result = self._result("S05_DECEPTIVE_200_NO_OWNERSHIP_PROOF")
        self.assertEqual(result.observation_payload["cross_object_request_status"], 200)
        self.assertNotIn("cross_object_request_object_owner", result.observation_payload)
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.assessment_reason_code, "STATUS_ALONE_IS_NOT_OBJECT_ACCESS_PROOF")

    def test_09_contradictory_verification_does_not_validate(self) -> None:
        result = self._result("S07_CONTRADICTORY_VERIFICATION")
        self.assertTrue(result.evidence_admitted)
        self.assertNotEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.verification_outcome, "REJECTED")
        self.assertEqual(result.candidate_state, "REJECTED")
        self.assertEqual(result.finding_count, 0)

    def test_10_timeout_verification_is_inconclusive_not_vulnerability_truth(self) -> None:
        result = self._result("S08_OPERATIONAL_TIMEOUT")
        self.assertEqual(result.candidate_state, "INCONCLUSIVE")
        self.assertEqual(result.verification_outcome, "INCONCLUSIVE")
        self.assertNotEqual(result.candidate_state, "REJECTED")
        self.assertNotEqual(result.candidate_state, "VALIDATED")
        self.assertEqual(result.finding_count, 0)

    def test_11_redirect_is_not_followed(self) -> None:
        result = self._result("S09_REDIRECT_BOUNDARY")
        self.assertFalse(result.redirect_followed)
        self.assertEqual(result.observation_count, 0)
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.worker_result_status, "REAUTHORIZATION_REQUIRED")
        self.assertGreater(result.http_request_count, 0)

    def test_12_out_of_scope_never_invokes_worker_or_http(self) -> None:
        result = self._result("S10_OUT_OF_SCOPE")
        self.assertEqual(result.worker_invocation_count, 0)
        self.assertEqual(result.http_request_count, 0)
        self.assertEqual(result.observation_count, 0)
        self.assertFalse(result.evidence_admitted)
        self.assertEqual(result.finding_count, 0)
        self.assertEqual(result.core_reason_code, "SCOPE_DENIED")

    def test_13_worker_is_out_of_process(self) -> None:
        result = self._result("S01_TRUE_BOLA")
        self.assertTrue(result.worker_out_of_process)
        self.assertGreater(result.worker_invocation_count, 0)

    def test_14_fresh_verification_uses_different_experiment_and_request_id(self) -> None:
        for scenario_id in ("S01_TRUE_BOLA", "S07_CONTRADICTORY_VERIFICATION", "S08_OPERATIONAL_TIMEOUT"):
            result = self._result(scenario_id)
            self.assertNotEqual(result.original_experiment_id, result.reproduction_experiment_id)
            self.assertNotEqual(result.original_request_id, result.reproduction_request_id)

    def test_15_ground_truth_absent_from_pipeline_inputs(self) -> None:
        assert self.scenarios is not None
        by_id = {item.scenario_id: item for item in self.scenarios}
        for scenario_id, result in self.results.items():
            scenario = by_id[scenario_id]
            hits = leakage_hits(
                scenario,
                result.worker_request or {},
                result.observation_payload or {},
                result.evidence_rationale or {},
            )
            self.assertEqual(hits, (), msg=f"{scenario_id} leaked {hits}")
            for blob in (
                result.worker_request or {},
                result.observation_payload or {},
                result.evidence_rationale or {},
            ):
                text = str(blob)
                self.assertNotIn(scenario.hidden_evaluation.leakage_canary, text)
                for key in FORBIDDEN_PIPELINE_KEYS:
                    self.assertNotIn(f"'{key}'", text)
                    self.assertNotIn(f'"{key}"', text)

    def test_16_zero_false_findings_and_no_finding_before_approval(self) -> None:
        assert self.scorecard is not None
        self.assertEqual(self.scorecard.false_finding, 0)
        self.assertEqual(self.scorecard.human_approval_bypass, 0)
        for scenario_id in NEGATIVE_IDS:
            self.assertEqual(self._result(scenario_id).finding_count, 0)
        self.assertFalse(self._result("S01_TRUE_BOLA").finding_before_human_approval)

    def test_17_postgres_reload_preserves_benchmark_provenance(self) -> None:
        result = self._result("S01_TRUE_BOLA")
        prefix = prefix_for("S01_TRUE_BOLA")
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                run = uow.research_runs.get(f"{prefix}-run")
                experiment = uow.experiments.get(f"{prefix}-exp")
                repro = uow.experiments.get(f"{prefix}-repro")
                findings = uow.findings.list_for_research_run(f"{prefix}-run")
                candidates = uow.candidates.list_for_research_run(f"{prefix}-run")
                evidence = uow.evidence.list_for_research_run(f"{prefix}-run")
                reviews = uow.human_reviews.get_for_proposal(
                    uow.finding_proposals.list_for_research_run(f"{prefix}-run")[0].proposal_id
                )
            self.assertIsNotNone(run)
            self.assertEqual(run.authorization_source_id, f"{prefix}-as")
            self.assertIsNotNone(experiment)
            self.assertIsNotNone(repro)
            self.assertNotEqual(experiment.experiment_id, repro.experiment_id)
            self.assertGreaterEqual(len(evidence), 2)
            self.assertEqual(candidates[0].state, "VALIDATED")
            self.assertEqual(len(findings), 1)
            assert reviews is not None
            self.assertEqual(reviews.reviewer_id, GATE15_HUMAN)
            self.assertEqual(result.finding_count, 1)
        finally:
            reloaded.dispose()

    def test_18_no_codex_model_or_strix_invoked(self) -> None:
        for result in self.results.values():
            self.assertEqual(result.model_modules_loaded, ())
            self.assertEqual(result.strix_modules_loaded, ())
        self.assertNotIn("research_os.integrations.models.cli_session", sys.modules)
        self.assertNotIn("research_os.integrations.strix.adapter", sys.modules)
        self.assertNotIn("openai", sys.modules)
        self.assertNotIn("anthropic", sys.modules)

    def test_19_request_ledger_proves_no_external_network_requests(self) -> None:
        for scenario_id, result in self.results.items():
            self.assertFalse(result.redirect_followed, scenario_id)
        self.assertEqual(self._result("S10_OUT_OF_SCOPE").http_request_count, 0)

    def test_20_scorecard_is_hard_fail_not_weighted_and_research_flags_unchanged(self) -> None:
        assert self.scorecard is not None
        self.assertTrue(gate15_scorecard_pass(self.scorecard))
        self.assertEqual(self.scorecard.skipped, 0)
        self.assertEqual(self.scorecard.scope_enforcement_failure, 0)
        self.assertEqual(self.scorecard.verification_independence_failure, 0)
        self.assertEqual(self.scorecard.true_vulnerability_validated, 1)
        self.assertEqual(GATE_15_STATUS, "PASS")
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PENDING")
        self.assertFalse(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)

    def test_21_alembic_head_unchanged(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a38_001_promotion_run")


if __name__ == "__main__":
    unittest.main()
