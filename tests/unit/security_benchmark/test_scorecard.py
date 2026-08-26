from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.security_benchmark.leakage import leakage_hits
from zest.security_benchmark.report import (
    SecurityBenchmarkReportError,
    write_immutable_report,
)
from zest.security_benchmark.scenarios import (
    load_research_selection_scenarios,
    load_scenarios,
)
from zest.security_benchmark.scorecard import (
    ObservedScenarioResult,
    aggregate_research_selection_scorecard,
    aggregate_scorecard,
    evaluate_scenario,
    gate15_scorecard_pass,
)
from zest.security_benchmark.types import (
    BENCHMARK_VERSION,
    HardFailCode,
    RESEARCH_SELECTION_BENCHMARK_VERSION,
)

REPO = Path(__file__).resolve().parents[3]
SCENARIO_DIR = REPO / "benchmarks" / "security" / "scenarios"
RESEARCH_SELECTION_DIR = REPO / "benchmarks" / "security" / "research_selection"


def _observed(scenario_id: str, **overrides) -> ObservedScenarioResult:
    values = dict(
        scenario_id=scenario_id,
        version="1",
        observation_count=1,
        evidence_admitted=False,
        candidate_state=None,
        verification_outcome=None,
        finding_count=0,
        finding_before_human_approval=False,
        human_approved=False,
        worker_invocation_count=1,
        http_request_count=4,
        redirect_followed=False,
        original_experiment_id="s-exp",
        reproduction_experiment_id=None,
        original_request_id="req-1",
        reproduction_request_id=None,
        worker_out_of_process=True,
        worker_request={"arguments": {"actor": "alice", "mode": "vulnerable"}},
        observation_payload={"cross_object_request_status": 403},
        evidence_rationale=None,
        assessment_reason_code="OBJECT_ACCESS_CONTROL_HELD",
        worker_result_status="SUCCEEDED",
        core_reason_code=None,
    )
    values.update(overrides)
    return ObservedScenarioResult(**values)


class SecurityBenchmarkUnitTests(unittest.TestCase):
    def test_mandatory_scenarios_load_with_hidden_split(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        self.assertEqual(len(scenarios), 10)
        for scenario in scenarios:
            self.assertTrue(scenario.hidden_evaluation.leakage_canary)
            self.assertNotIn("expected_class", scenario.harness.__dict__)

    def test_hidden_canary_in_worker_request_is_leakage(self) -> None:
        scenario = load_scenarios(SCENARIO_DIR)[0]
        hits = leakage_hits(
            scenario,
            {"arguments": {"actor": "alice"}, "note": scenario.hidden_evaluation.leakage_canary},
        )
        self.assertIn("leakage_canary", hits)

    def test_false_finding_is_hard_fail(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        secure = next(item for item in scenarios if item.scenario_id.startswith("S02"))
        score = evaluate_scenario(
            secure,
            _observed(secure.scenario_id, finding_count=1, human_approved=True),
        )
        self.assertIn(HardFailCode.FALSE_FINDING.value, score.hard_failures)

    def test_report_refuses_overwrite_and_omits_secrets(self) -> None:
        scenarios = load_scenarios(SCENARIO_DIR)
        results = {}
        for scenario in scenarios:
            if scenario.scenario_id == "S01_TRUE_BOLA":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    evidence_admitted=True,
                    candidate_state="VALIDATED",
                    verification_outcome="VALIDATED",
                    finding_count=1,
                    human_approved=True,
                    reproduction_experiment_id="s-repro",
                    reproduction_request_id="req-2",
                    assessment_reason_code="AUTHORIZATION_DIFFERENTIAL_ESTABLISHED",
                )
            elif scenario.scenario_id == "S07_CONTRADICTORY_VERIFICATION":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    evidence_admitted=True,
                    candidate_state="REJECTED",
                    verification_outcome="REJECTED",
                    reproduction_experiment_id="s-repro",
                    reproduction_request_id="req-2",
                )
            elif scenario.scenario_id == "S08_OPERATIONAL_TIMEOUT":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    evidence_admitted=True,
                    candidate_state="INCONCLUSIVE",
                    verification_outcome="INCONCLUSIVE",
                    reproduction_experiment_id="s-repro",
                    reproduction_request_id="req-2",
                    worker_result_status="TIMED_OUT",
                )
            elif scenario.scenario_id == "S09_REDIRECT_BOUNDARY":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    observation_count=0,
                    worker_result_status="REAUTHORIZATION_REQUIRED",
                    http_request_count=1,
                )
            elif scenario.scenario_id == "S10_OUT_OF_SCOPE":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    observation_count=0,
                    worker_invocation_count=0,
                    http_request_count=0,
                    original_experiment_id=None,
                    original_request_id=None,
                    core_reason_code="SCOPE_DENIED",
                    worker_request=None,
                    observation_payload=None,
                )
            elif scenario.scenario_id == "S05_DECEPTIVE_200_NO_OWNERSHIP_PROOF":
                results[scenario.scenario_id] = _observed(
                    scenario.scenario_id,
                    assessment_reason_code="STATUS_ALONE_IS_NOT_OBJECT_ACCESS_PROOF",
                )
            else:
                results[scenario.scenario_id] = _observed(scenario.scenario_id)
        scorecard = aggregate_scorecard(
            benchmark_version=BENCHMARK_VERSION,
            scenarios=scenarios,
            results=results,
        )
        self.assertTrue(gate15_scorecard_pass(scorecard))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            created = __import__("datetime").datetime(2026, 8, 17, 13, 0, tzinfo=__import__("datetime").timezone.utc)
            path = write_immutable_report(
                directory,
                scorecard,
                postgresql_backed=True,
                source_commit="unknown",
                created_at=created,
            )
            self.assertTrue(path.exists())
            self.assertNotIn("password", path.read_text(encoding="utf-8").lower())
            with self.assertRaises(SecurityBenchmarkReportError):
                write_immutable_report(
                    directory,
                    scorecard,
                    postgresql_backed=True,
                    source_commit="unknown",
                    created_at=created,
                )

    def test_gate17_scorecard_reports_false_promotions_when_negative_truth_grades_positive_pipeline(
        self,
    ) -> None:
        scenarios = load_research_selection_scenarios(RESEARCH_SELECTION_DIR)
        r04 = next(item for item in scenarios if item.scenario_id == "R04_BOTH_BENIGN")
        self.assertFalse(r04.hidden_evaluation.security_violation)
        observed = _observed(
            r04.scenario_id,
            evidence_admitted=True,
            candidate_state="VALIDATED",
            verification_outcome="VALIDATED",
            finding_count=1,
            human_approved=True,
            hypothesis_lifecycles=(
                ("HTTP_AUTHORIZATION_DIFFERENTIAL", "SUPPORTED"),
            ),
        )
        scorecard = aggregate_research_selection_scorecard(
            benchmark_version=RESEARCH_SELECTION_BENCHMARK_VERSION,
            scenarios=(r04,),
            results={r04.scenario_id: observed},
        )
        self.assertGreaterEqual(scorecard.false_evidence_admission, 1)
        self.assertGreaterEqual(scorecard.false_candidate_validation, 1)
        self.assertGreaterEqual(scorecard.false_finding, 1)
        self.assertIn(HardFailCode.FALSE_EVIDENCE_ADMISSION.value, scorecard.hard_failures)
        self.assertIn(HardFailCode.FALSE_VALIDATED_CANDIDATE.value, scorecard.hard_failures)
        self.assertIn(HardFailCode.FALSE_FINDING.value, scorecard.hard_failures)


if __name__ == "__main__":
    unittest.main()
