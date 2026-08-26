"""Deterministic security ground-truth scorecard. No weighted aggregate score."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.security_benchmark.leakage import leakage_hits
from zest.security_benchmark.scenarios import (
    MANDATORY_SCENARIO_IDS,
    MANDATORY_WORKFLOW_SCENARIO_IDS,
    MANDATORY_RESEARCH_SELECTION_SCENARIO_IDS,
    SecurityGroundTruthScenario,
)
from zest.security_benchmark.types import (
    ExpectedSecurityClass,
    HardFailCode,
    NEGATIVE_GROUND_TRUTH,
    PromotionStage,
)

STAGE_RANK = {
    PromotionStage.NONE: 0,
    PromotionStage.SCOPE_DENIED: 0,
    PromotionStage.NO_OBSERVATION: 1,
    PromotionStage.OBSERVATION: 2,
    PromotionStage.EVIDENCE: 3,
    PromotionStage.CANDIDATE_OPEN: 4,
    PromotionStage.CANDIDATE_REJECTED: 4,
    PromotionStage.CANDIDATE_INCONCLUSIVE: 4,
    PromotionStage.CANDIDATE_VALIDATED: 5,
    PromotionStage.FINDING_AFTER_HUMAN_APPROVAL: 6,
}


@dataclass(frozen=True)
class ObservedScenarioResult:
    """Pipeline-observed terminal state. Built by the harness, graded by hidden truth."""

    scenario_id: str
    version: str
    observation_count: int
    evidence_admitted: bool
    candidate_state: str | None
    verification_outcome: str | None
    finding_count: int
    finding_before_human_approval: bool
    human_approved: bool
    worker_invocation_count: int
    http_request_count: int
    redirect_followed: bool
    original_experiment_id: str | None
    reproduction_experiment_id: str | None
    original_request_id: str | None
    reproduction_request_id: str | None
    worker_out_of_process: bool
    worker_request: Mapping[str, Any] | None
    observation_payload: Mapping[str, Any] | None
    evidence_rationale: Mapping[str, Any] | None
    assessment_reason_code: str | None
    worker_result_status: str | None
    core_reason_code: str | None
    model_modules_loaded: tuple[str, ...] = ()
    strix_modules_loaded: tuple[str, ...] = ()
    observed_classification: str | None = None
    finding_classification: str | None = None
    research_stop_reason: str | None = None
    hypothesis_lifecycles: tuple[tuple[str, str], ...] = ()
    selected_purposes: tuple[str, ...] = ()
    selection_reason_codes: tuple[str, ...] = ()
    adaptive_depth: int = 0
    redundant_experiment_executed: bool = False
    worker_out_of_scope_count: int = 0
    candidate_classifications: tuple[str, ...] = ()
    finding_classifications: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioScore:
    scenario_id: str
    version: str
    expected_class: str
    observed_stage: str
    hard_failures: tuple[str, ...]
    finding_count: int
    candidate_state: str | None
    evidence_admitted: bool
    verification_outcome: str | None
    human_approved: bool


@dataclass(frozen=True)
class SecurityScorecard:
    benchmark_version: str
    scenarios_executed: tuple[str, ...]
    true_vulnerability_validated: int
    true_vulnerability_missed: int
    false_evidence_admission: int
    false_candidate_validation: int
    false_finding: int
    correct_secure_rejection: int
    correct_insufficient_evidence: int
    correct_inconclusive: int
    scope_enforcement_failure: int
    redirect_boundary_failure: int
    human_approval_bypass: int
    verification_independence_failure: int
    hard_failures: tuple[str, ...]
    scenario_scores: tuple[ScenarioScore, ...]
    skipped: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "true_vulnerability_validated": self.true_vulnerability_validated,
            "true_vulnerability_missed": self.true_vulnerability_missed,
            "false_evidence_admission": self.false_evidence_admission,
            "false_candidate_validation": self.false_candidate_validation,
            "false_finding": self.false_finding,
            "correct_secure_rejection": self.correct_secure_rejection,
            "correct_insufficient_evidence": self.correct_insufficient_evidence,
            "correct_inconclusive": self.correct_inconclusive,
            "scope_enforcement_failure": self.scope_enforcement_failure,
            "redirect_boundary_failure": self.redirect_boundary_failure,
            "human_approval_bypass": self.human_approval_bypass,
            "verification_independence_failure": self.verification_independence_failure,
            "hard_failures": list(self.hard_failures),
            "skipped": self.skipped,
            "scenarios_executed": list(self.scenarios_executed),
            "scenario_scores": [
                {
                    "scenario_id": item.scenario_id,
                    "version": item.version,
                    "expected_class": item.expected_class,
                    "observed_stage": item.observed_stage,
                    "hard_failures": list(item.hard_failures),
                    "finding_count": item.finding_count,
                    "candidate_state": item.candidate_state,
                    "evidence_admitted": item.evidence_admitted,
                    "verification_outcome": item.verification_outcome,
                    "human_approved": item.human_approved,
                }
                for item in self.scenario_scores
            ],
        }


def observed_stage(result: ObservedScenarioResult) -> PromotionStage:
    if result.finding_count > 0:
        return PromotionStage.FINDING_AFTER_HUMAN_APPROVAL
    if result.candidate_state == "VALIDATED":
        return PromotionStage.CANDIDATE_VALIDATED
    if result.candidate_state == "REJECTED":
        return PromotionStage.CANDIDATE_REJECTED
    if result.candidate_state == "INCONCLUSIVE":
        return PromotionStage.CANDIDATE_INCONCLUSIVE
    if result.candidate_state == "OPEN" or result.candidate_state == "VERIFYING":
        return PromotionStage.CANDIDATE_OPEN
    if result.evidence_admitted:
        return PromotionStage.EVIDENCE
    if result.observation_count > 0:
        return PromotionStage.OBSERVATION
    if result.core_reason_code == "SCOPE_DENIED" or result.worker_invocation_count == 0:
        if result.worker_invocation_count == 0 and result.http_request_count == 0:
            return PromotionStage.SCOPE_DENIED
    if result.worker_result_status == "REAUTHORIZATION_REQUIRED":
        return PromotionStage.NO_OBSERVATION
    return PromotionStage.NONE


def evaluate_scenario(
    scenario: SecurityGroundTruthScenario,
    result: ObservedScenarioResult,
) -> ScenarioScore:
    hidden = scenario.hidden_evaluation
    stage = observed_stage(result)
    failures: list[str] = []
    leaks = leakage_hits(
        scenario,
        result.worker_request or {},
        result.observation_payload or {},
        result.evidence_rationale or {},
    )
    if leaks:
        failures.append(HardFailCode.GROUND_TRUTH_LEAKAGE.value)
    if STAGE_RANK[stage] > STAGE_RANK[hidden.expected_max_promotion_stage]:
        failures.append(HardFailCode.PROMOTION_EXCEEDED.value)
    negative = hidden.expected_class in NEGATIVE_GROUND_TRUTH
    if negative and result.finding_count > 0:
        failures.append(HardFailCode.FALSE_FINDING.value)
    if negative and result.candidate_state == "VALIDATED":
        failures.append(HardFailCode.FALSE_VALIDATED_CANDIDATE.value)
    if negative and result.evidence_admitted and hidden.expected_class not in {
        ExpectedSecurityClass.CONTRADICTION_REJECTED,
        ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE,
    }:
        failures.append(HardFailCode.FALSE_EVIDENCE_ADMISSION.value)
    if hidden.expected_class is ExpectedSecurityClass.VULNERABLE:
        if result.candidate_state != "VALIDATED":
            failures.append(HardFailCode.TRUE_VULNERABILITY_MISSED.value)
        if result.finding_count == 0:
            failures.append(HardFailCode.TRUE_VULNERABILITY_MISSED.value)
        if result.finding_count > 0 and not result.human_approved:
            failures.append(HardFailCode.HUMAN_APPROVAL_BYPASS.value)
        if result.finding_before_human_approval:
            failures.append(HardFailCode.HUMAN_APPROVAL_BYPASS.value)
    if hidden.expected_class is ExpectedSecurityClass.SCOPE_DENIED:
        if result.worker_invocation_count or result.http_request_count or result.observation_count:
            failures.append(HardFailCode.SCOPE_BYPASS.value)
    if hidden.expected_class is ExpectedSecurityClass.CONTROLLED_STOP:
        if result.redirect_followed or result.observation_count > 0:
            failures.append(HardFailCode.REDIRECT_BOUNDARY_BYPASS.value)
    if "independent_verification" in hidden.required_controls:
        if (
            not result.original_experiment_id
            or not result.reproduction_experiment_id
            or result.original_experiment_id == result.reproduction_experiment_id
            or result.original_request_id == result.reproduction_request_id
        ):
            failures.append(HardFailCode.SELF_VERIFICATION.value)
    expected_class_name = hidden.expected_classification
    if expected_class_name:
        if result.finding_count > 0 and result.finding_classification != expected_class_name:
            failures.append(HardFailCode.CROSS_CLASS_MISCLASSIFICATION.value)
        if (
            result.candidate_state == "VALIDATED"
            and result.observed_classification != expected_class_name
        ):
            failures.append(HardFailCode.CROSS_CLASS_MISCLASSIFICATION.value)
    return ScenarioScore(
        scenario_id=scenario.scenario_id,
        version=scenario.version,
        expected_class=hidden.expected_class.value,
        observed_stage=stage.value,
        hard_failures=tuple(dict.fromkeys(failures)),
        finding_count=result.finding_count,
        candidate_state=result.candidate_state,
        evidence_admitted=result.evidence_admitted,
        verification_outcome=result.verification_outcome,
        human_approved=result.human_approved,
    )


def aggregate_scorecard(
    *,
    benchmark_version: str,
    scenarios: tuple[SecurityGroundTruthScenario, ...],
    results: Mapping[str, ObservedScenarioResult],
) -> SecurityScorecard:
    scores: list[ScenarioScore] = []
    true_validated = 0
    true_missed = 0
    false_evidence = 0
    false_validated = 0
    false_finding = 0
    correct_secure = 0
    correct_insufficient = 0
    correct_inconclusive = 0
    scope_fail = 0
    redirect_fail = 0
    approval_bypass = 0
    independence_fail = 0
    all_fails: list[str] = []
    executed: list[str] = []
    by_id = {item.scenario_id: item for item in scenarios}
    for scenario_id in MANDATORY_SCENARIO_IDS:
        scenario = by_id[scenario_id]
        result = results[scenario_id]
        executed.append(scenario.identity)
        score = evaluate_scenario(scenario, result)
        scores.append(score)
        all_fails.extend(score.hard_failures)
        hidden = scenario.hidden_evaluation
        if hidden.expected_class is ExpectedSecurityClass.VULNERABLE:
            if result.candidate_state == "VALIDATED":
                true_validated += 1
            else:
                true_missed += 1
        if HardFailCode.FALSE_EVIDENCE_ADMISSION.value in score.hard_failures:
            false_evidence += 1
        if HardFailCode.FALSE_VALIDATED_CANDIDATE.value in score.hard_failures:
            false_validated += 1
        if HardFailCode.FALSE_FINDING.value in score.hard_failures:
            false_finding += 1
        if (
            hidden.expected_class is ExpectedSecurityClass.SECURE
            and result.finding_count == 0
            and result.candidate_state != "VALIDATED"
        ):
            correct_secure += 1
        if (
            hidden.expected_class is ExpectedSecurityClass.INSUFFICIENT_EVIDENCE
            and not result.evidence_admitted
            and result.finding_count == 0
        ):
            correct_insufficient += 1
        if (
            hidden.expected_class is ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE
            and result.candidate_state == "INCONCLUSIVE"
            and result.finding_count == 0
        ):
            correct_inconclusive += 1
        if HardFailCode.SCOPE_BYPASS.value in score.hard_failures:
            scope_fail += 1
        if HardFailCode.REDIRECT_BOUNDARY_BYPASS.value in score.hard_failures:
            redirect_fail += 1
        if HardFailCode.HUMAN_APPROVAL_BYPASS.value in score.hard_failures:
            approval_bypass += 1
        if HardFailCode.SELF_VERIFICATION.value in score.hard_failures:
            independence_fail += 1
    return SecurityScorecard(
        benchmark_version=benchmark_version,
        scenarios_executed=tuple(executed),
        true_vulnerability_validated=true_validated,
        true_vulnerability_missed=true_missed,
        false_evidence_admission=false_evidence,
        false_candidate_validation=false_validated,
        false_finding=false_finding,
        correct_secure_rejection=correct_secure,
        correct_insufficient_evidence=correct_insufficient,
        correct_inconclusive=correct_inconclusive,
        scope_enforcement_failure=scope_fail,
        redirect_boundary_failure=redirect_fail,
        human_approval_bypass=approval_bypass,
        verification_independence_failure=independence_fail,
        hard_failures=tuple(dict.fromkeys(all_fails)),
        scenario_scores=tuple(scores),
        skipped=0,
    )


def gate15_scorecard_pass(scorecard: SecurityScorecard) -> bool:
    """Deterministic hard pass over counts/events. Not a weighted threshold."""

    if scorecard.skipped != 0:
        return False
    executed_ids = tuple(item.split("@", 1)[0] for item in scorecard.scenarios_executed)
    if executed_ids != MANDATORY_SCENARIO_IDS:
        return False
    if scorecard.hard_failures:
        return False
    if scorecard.false_finding != 0:
        return False
    if scorecard.human_approval_bypass != 0:
        return False
    if scorecard.scope_enforcement_failure != 0:
        return False
    if scorecard.verification_independence_failure != 0:
        return False
    if scorecard.true_vulnerability_validated < 1:
        return False
    if scorecard.true_vulnerability_missed != 0:
        return False
    return True


WORKFLOW_TRUE_POSITIVE_IDS = frozenset(
    {"W01_TRUE_ROLE_BYPASS", "W02_TRUE_SEQUENCE_SKIP"}
)


@dataclass(frozen=True)
class WorkflowScorecard:
    benchmark_version: str
    scenarios_executed: tuple[str, ...]
    workflow_true_positive: int
    workflow_missed: int
    workflow_false_evidence: int
    workflow_false_candidate_validation: int
    workflow_false_finding: int
    correct_sequence_rejection: int
    correct_role_rejection: int
    correct_delegation_handling: int
    correct_idempotent_handling: int
    correct_stale_state_handling: int
    correct_inconclusive: int
    cross_class_misclassification: int
    human_approval_bypass: int
    verification_independence_failure: int
    scope_enforcement_failure: int
    redirect_boundary_failure: int
    ground_truth_leakage: int
    hard_failures: tuple[str, ...]
    scenario_scores: tuple[ScenarioScore, ...]
    skipped: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "workflow_true_positive": self.workflow_true_positive,
            "workflow_missed": self.workflow_missed,
            "workflow_false_evidence": self.workflow_false_evidence,
            "workflow_false_candidate_validation": self.workflow_false_candidate_validation,
            "workflow_false_finding": self.workflow_false_finding,
            "correct_sequence_rejection": self.correct_sequence_rejection,
            "correct_role_rejection": self.correct_role_rejection,
            "correct_delegation_handling": self.correct_delegation_handling,
            "correct_idempotent_handling": self.correct_idempotent_handling,
            "correct_stale_state_handling": self.correct_stale_state_handling,
            "correct_inconclusive": self.correct_inconclusive,
            "cross_class_misclassification": self.cross_class_misclassification,
            "human_approval_bypass": self.human_approval_bypass,
            "verification_independence_failure": self.verification_independence_failure,
            "scope_enforcement_failure": self.scope_enforcement_failure,
            "redirect_boundary_failure": self.redirect_boundary_failure,
            "ground_truth_leakage": self.ground_truth_leakage,
            "hard_failures": list(self.hard_failures),
            "skipped": self.skipped,
            "scenarios_executed": list(self.scenarios_executed),
            "scenario_scores": [
                {
                    "scenario_id": item.scenario_id,
                    "version": item.version,
                    "expected_class": item.expected_class,
                    "observed_stage": item.observed_stage,
                    "hard_failures": list(item.hard_failures),
                    "finding_count": item.finding_count,
                    "candidate_state": item.candidate_state,
                    "evidence_admitted": item.evidence_admitted,
                    "verification_outcome": item.verification_outcome,
                    "human_approved": item.human_approved,
                }
                for item in self.scenario_scores
            ],
        }


def aggregate_workflow_scorecard(
    *,
    benchmark_version: str,
    scenarios: tuple[SecurityGroundTruthScenario, ...],
    results: Mapping[str, ObservedScenarioResult],
) -> WorkflowScorecard:
    scores: list[ScenarioScore] = []
    true_positive = 0
    missed = 0
    false_evidence = 0
    false_validated = 0
    false_finding = 0
    role_ok = 0
    sequence_ok = 0
    delegation_ok = 0
    idempotent_ok = 0
    stale_ok = 0
    inconclusive_ok = 0
    cross_class = 0
    scope_fail = 0
    redirect_fail = 0
    approval_bypass = 0
    independence_fail = 0
    leakage = 0
    all_fails: list[str] = []
    executed: list[str] = []
    by_id = {item.scenario_id: item for item in scenarios}
    for scenario_id in MANDATORY_WORKFLOW_SCENARIO_IDS:
        scenario = by_id[scenario_id]
        result = results[scenario_id]
        executed.append(scenario.identity)
        score = evaluate_scenario(scenario, result)
        scores.append(score)
        all_fails.extend(score.hard_failures)
        hidden = scenario.hidden_evaluation
        if scenario_id in WORKFLOW_TRUE_POSITIVE_IDS:
            if result.candidate_state == "VALIDATED":
                true_positive += 1
            else:
                missed += 1
        if HardFailCode.FALSE_EVIDENCE_ADMISSION.value in score.hard_failures:
            false_evidence += 1
        if HardFailCode.FALSE_VALIDATED_CANDIDATE.value in score.hard_failures:
            false_validated += 1
        if HardFailCode.FALSE_FINDING.value in score.hard_failures:
            false_finding += 1
        if HardFailCode.CROSS_CLASS_MISCLASSIFICATION.value in score.hard_failures:
            cross_class += 1
        if HardFailCode.GROUND_TRUTH_LEAKAGE.value in score.hard_failures:
            leakage += 1
        if (
            scenario_id == "W03_SECURE_ROLE_ENFORCEMENT"
            and result.finding_count == 0
            and result.candidate_state != "VALIDATED"
        ):
            role_ok += 1
        if (
            scenario_id == "W04_SECURE_SEQUENCE_ENFORCEMENT"
            and result.finding_count == 0
            and result.candidate_state != "VALIDATED"
        ):
            sequence_ok += 1
        if (
            scenario_id == "W07_LEGITIMATE_DELEGATED_REVIEWER"
            and result.finding_count == 0
            and result.candidate_state != "VALIDATED"
        ):
            delegation_ok += 1
        if (
            scenario_id == "W06_IDEMPOTENT_REPEAT"
            and result.finding_count == 0
            and result.candidate_state != "VALIDATED"
        ):
            idempotent_ok += 1
        if (
            scenario_id == "W08_STALE_CLIENT_STATE"
            and result.finding_count == 0
            and not result.evidence_admitted
        ):
            stale_ok += 1
        if (
            hidden.expected_class is ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE
            and result.candidate_state == "INCONCLUSIVE"
            and result.finding_count == 0
        ):
            inconclusive_ok += 1
        if HardFailCode.SCOPE_BYPASS.value in score.hard_failures:
            scope_fail += 1
        if HardFailCode.REDIRECT_BOUNDARY_BYPASS.value in score.hard_failures:
            redirect_fail += 1
        if HardFailCode.HUMAN_APPROVAL_BYPASS.value in score.hard_failures:
            approval_bypass += 1
        if HardFailCode.SELF_VERIFICATION.value in score.hard_failures:
            independence_fail += 1
    return WorkflowScorecard(
        benchmark_version=benchmark_version,
        scenarios_executed=tuple(executed),
        workflow_true_positive=true_positive,
        workflow_missed=missed,
        workflow_false_evidence=false_evidence,
        workflow_false_candidate_validation=false_validated,
        workflow_false_finding=false_finding,
        correct_sequence_rejection=sequence_ok,
        correct_role_rejection=role_ok,
        correct_delegation_handling=delegation_ok,
        correct_idempotent_handling=idempotent_ok,
        correct_stale_state_handling=stale_ok,
        correct_inconclusive=inconclusive_ok,
        cross_class_misclassification=cross_class,
        human_approval_bypass=approval_bypass,
        verification_independence_failure=independence_fail,
        scope_enforcement_failure=scope_fail,
        redirect_boundary_failure=redirect_fail,
        ground_truth_leakage=leakage,
        hard_failures=tuple(dict.fromkeys(all_fails)),
        scenario_scores=tuple(scores),
        skipped=0,
    )


def gate16_scorecard_pass(scorecard: WorkflowScorecard) -> bool:
    """Deterministic hard pass over counts/events. Not a weighted threshold."""

    if scorecard.skipped != 0:
        return False
    executed_ids = tuple(item.split("@", 1)[0] for item in scorecard.scenarios_executed)
    if executed_ids != MANDATORY_WORKFLOW_SCENARIO_IDS:
        return False
    if scorecard.hard_failures:
        return False
    if scorecard.workflow_false_finding != 0:
        return False
    if scorecard.workflow_false_candidate_validation != 0:
        return False
    if scorecard.cross_class_misclassification != 0:
        return False
    if scorecard.human_approval_bypass != 0:
        return False
    if scorecard.scope_enforcement_failure != 0:
        return False
    if scorecard.redirect_boundary_failure != 0:
        return False
    if scorecard.verification_independence_failure != 0:
        return False
    if scorecard.ground_truth_leakage != 0:
        return False
    if scorecard.workflow_true_positive < 2:
        return False
    if scorecard.workflow_missed != 0:
        return False
    return True


@dataclass(frozen=True)
class ResearchSelectionScorecard:
    benchmark_version: str
    scenarios_executed: tuple[str, ...]
    multi_hypothesis_scenarios_completed: int
    correct_hypothesis_survival: int
    incorrect_hypothesis_survival: int
    required_hypothesis_falsified: int
    false_hypothesis_promoted: int
    false_evidence_admission: int
    false_candidate_validation: int
    false_finding: int
    cross_class_misclassification: int
    discriminating_experiment_selected: int
    redundant_experiment_executed: int
    counterfactual_branch_failure: int
    fixed_order_behavior_detected: int
    context_negative_knowledge_leak: int
    budget_overrun: int
    core_authority_bypass: int
    verification_independence_failure: int
    ground_truth_leakage: int
    restart_resume_failure: int
    human_approval_bypass: int
    hard_failures: tuple[str, ...]
    scenario_scores: tuple[ScenarioScore, ...]
    skipped: int = 0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "multi_hypothesis_scenarios_completed": self.multi_hypothesis_scenarios_completed,
            "correct_hypothesis_survival": self.correct_hypothesis_survival,
            "incorrect_hypothesis_survival": self.incorrect_hypothesis_survival,
            "required_hypothesis_falsified": self.required_hypothesis_falsified,
            "false_hypothesis_promoted": self.false_hypothesis_promoted,
            "false_evidence_admission": self.false_evidence_admission,
            "false_candidate_validation": self.false_candidate_validation,
            "false_finding": self.false_finding,
            "cross_class_misclassification": self.cross_class_misclassification,
            "discriminating_experiment_selected": self.discriminating_experiment_selected,
            "redundant_experiment_executed": self.redundant_experiment_executed,
            "counterfactual_branch_failure": self.counterfactual_branch_failure,
            "fixed_order_behavior_detected": self.fixed_order_behavior_detected,
            "context_negative_knowledge_leak": self.context_negative_knowledge_leak,
            "budget_overrun": self.budget_overrun,
            "core_authority_bypass": self.core_authority_bypass,
            "verification_independence_failure": self.verification_independence_failure,
            "ground_truth_leakage": self.ground_truth_leakage,
            "restart_resume_failure": self.restart_resume_failure,
            "human_approval_bypass": self.human_approval_bypass,
            "hard_failures": list(self.hard_failures),
            "skipped": self.skipped,
            "scenarios_executed": list(self.scenarios_executed),
            "scenario_scores": [
                {
                    "scenario_id": item.scenario_id,
                    "version": item.version,
                    "expected_class": item.expected_class,
                    "observed_stage": item.observed_stage,
                    "hard_failures": list(item.hard_failures),
                    "finding_count": item.finding_count,
                    "candidate_state": item.candidate_state,
                    "evidence_admitted": item.evidence_admitted,
                    "verification_outcome": item.verification_outcome,
                    "human_approved": item.human_approved,
                }
                for item in self.scenario_scores
            ],
            "not_a_priority_score": True,
            "not_confidence": True,
        }


def aggregate_research_selection_scorecard(
    *,
    benchmark_version: str,
    scenarios: tuple[SecurityGroundTruthScenario, ...],
    results: Mapping[str, ObservedScenarioResult],
    restart_resume_failure: int = 0,
    counterfactual_branch_failure: int = 0,
    fixed_order_behavior_detected: int = 0,
    context_negative_knowledge_leak: int = 0,
) -> ResearchSelectionScorecard:
    scores: list[ScenarioScore] = []
    all_fails: list[str] = []
    multi_hyp = 0
    correct_survival = 0
    incorrect_survival = 0
    required_falsified = 0
    false_promoted = 0
    false_evidence = 0
    false_validated = 0
    false_finding = 0
    cross_class = 0
    discriminating = 0
    redundant = 0
    budget_overrun = 0
    core_bypass = 0
    independence = 0
    leakage = 0
    approval_bypass = 0
    executed: list[str] = []
    for scenario in scenarios:
        result = results[scenario.scenario_id]
        executed.append(scenario.identity)
        hidden = scenario.hidden_evaluation
        fails: list[str] = []
        if result.finding_before_human_approval:
            fails.append(HardFailCode.HUMAN_APPROVAL_BYPASS.value)
            approval_bypass += 1
        hits = leakage_hits(
            scenario,
            result.worker_request,
            result.observation_payload,
            result.evidence_rationale,
        )
        if hits:
            fails.append(HardFailCode.GROUND_TRUTH_LEAKAGE.value)
            leakage += 1
        if result.worker_out_of_scope_count > 0:
            fails.append(HardFailCode.CORE_AUTHORITY_BYPASS.value)
            core_bypass += 1
        surviving = {
            family
            for family, lifecycle in result.hypothesis_lifecycles
            if lifecycle == "SUPPORTED"
        }
        expected_surviving = set(hidden.expected_surviving_hypothesis_classes)
        if expected_surviving and expected_surviving <= surviving:
            correct_survival += 1
        extra = surviving - expected_surviving
        if (
            extra
            and hidden.expected_class in NEGATIVE_GROUND_TRUTH
            and hidden.expected_class is not ExpectedSecurityClass.CONTROLLED_STOP
            and (result.evidence_admitted or result.candidate_state or result.finding_count)
        ):
            incorrect_survival += 1
            false_promoted += 1
            fails.append(HardFailCode.FALSE_VALIDATED_CANDIDATE.value)
        required = set(hidden.required_falsified_classes)
        falsified = {
            family
            for family, lifecycle in result.hypothesis_lifecycles
            if lifecycle == "FALSIFIED"
        }
        if required and required <= falsified:
            required_falsified += 1
        if hidden.security_violation is False and result.evidence_admitted:
            false_evidence += 1
            fails.append(HardFailCode.FALSE_EVIDENCE_ADMISSION.value)
        if hidden.security_violation is False and result.candidate_state == "VALIDATED":
            false_validated += 1
            fails.append(HardFailCode.FALSE_VALIDATED_CANDIDATE.value)
        if hidden.security_violation is False and result.finding_count > 0:
            false_finding += 1
            fails.append(HardFailCode.FALSE_FINDING.value)
        if result.finding_count > 1 or (
            result.observed_classification
            and hidden.expected_classification
            and result.observed_classification != hidden.expected_classification
            and hidden.expected_classification
            not in result.candidate_classifications
        ):
            if hidden.expected_classification and result.finding_classifications:
                if any(
                    item != hidden.expected_classification
                    and item in {"HTTP_AUTHORIZATION_DIFFERENTIAL", "HTTP_STATE_TRANSITION_AUTHORIZATION"}
                    for item in result.finding_classifications
                ):
                    if hidden.expected_classification not in result.finding_classifications:
                        cross_class += 1
                        fails.append(HardFailCode.CROSS_CLASS_MISCLASSIFICATION.value)
        if len({family for family, _lifecycle in result.hypothesis_lifecycles}) >= 2:
            multi_hyp += 1
        if any("DISTINGUISHES_COMPETING" in item or "LEXICOGRAPHIC_SELECTION" in item for item in result.selection_reason_codes):
            discriminating += 1
        if result.redundant_experiment_executed:
            redundant += 1
            fails.append(HardFailCode.FIXED_SCRIPT_BEHAVIOR.value)
        if (
            scenario.harness.max_experiments is not None
            and result.worker_invocation_count > scenario.harness.max_experiments
        ):
            budget_overrun += 1
            fails.append(HardFailCode.BUDGET_BYPASS.value)
        if (
            result.original_experiment_id
            and result.reproduction_experiment_id
            and result.original_experiment_id == result.reproduction_experiment_id
        ):
            independence += 1
            fails.append(HardFailCode.SELF_VERIFICATION.value)
        stage = observed_stage(result)
        scores.append(
            ScenarioScore(
                scenario_id=scenario.scenario_id,
                version=scenario.version,
                expected_class=hidden.expected_class.value,
                observed_stage=stage.value,
                hard_failures=tuple(fails),
                finding_count=result.finding_count,
                candidate_state=result.candidate_state,
                evidence_admitted=result.evidence_admitted,
                verification_outcome=result.verification_outcome,
                human_approved=result.human_approved,
            )
        )
        all_fails.extend(fails)
    if counterfactual_branch_failure:
        all_fails.append(HardFailCode.COUNTERFACTUAL_BRANCH_FAILURE.value)
    if fixed_order_behavior_detected:
        all_fails.append(HardFailCode.FIXED_SCRIPT_BEHAVIOR.value)
    if context_negative_knowledge_leak:
        all_fails.append(HardFailCode.CONTEXT_NEGATIVE_KNOWLEDGE_LEAK.value)
    if restart_resume_failure:
        all_fails.append(HardFailCode.RESTART_STATE_LOSS.value)
    return ResearchSelectionScorecard(
        benchmark_version=benchmark_version,
        scenarios_executed=tuple(executed),
        multi_hypothesis_scenarios_completed=multi_hyp,
        correct_hypothesis_survival=correct_survival,
        incorrect_hypothesis_survival=incorrect_survival,
        required_hypothesis_falsified=required_falsified,
        false_hypothesis_promoted=false_promoted,
        false_evidence_admission=false_evidence,
        false_candidate_validation=false_validated,
        false_finding=false_finding,
        cross_class_misclassification=cross_class,
        discriminating_experiment_selected=discriminating,
        redundant_experiment_executed=redundant,
        counterfactual_branch_failure=counterfactual_branch_failure,
        fixed_order_behavior_detected=fixed_order_behavior_detected,
        context_negative_knowledge_leak=context_negative_knowledge_leak,
        budget_overrun=budget_overrun,
        core_authority_bypass=core_bypass,
        verification_independence_failure=independence,
        ground_truth_leakage=leakage,
        restart_resume_failure=restart_resume_failure,
        human_approval_bypass=approval_bypass,
        hard_failures=tuple(dict.fromkeys(all_fails)),
        scenario_scores=tuple(scores),
        skipped=0,
    )


def gate17_scorecard_pass(scorecard: ResearchSelectionScorecard) -> bool:
    """Deterministic hard pass. Implementation tests must not set GATE17 PASS."""

    if scorecard.skipped != 0:
        return False
    executed_ids = tuple(item.split("@", 1)[0] for item in scorecard.scenarios_executed)
    if executed_ids != MANDATORY_RESEARCH_SELECTION_SCENARIO_IDS:
        return False
    if scorecard.hard_failures:
        return False
    if scorecard.false_finding != 0:
        return False
    if scorecard.false_candidate_validation != 0:
        return False
    if scorecard.cross_class_misclassification != 0:
        return False
    if scorecard.ground_truth_leakage != 0:
        return False
    if scorecard.human_approval_bypass != 0:
        return False
    if scorecard.core_authority_bypass != 0:
        return False
    if scorecard.budget_overrun != 0:
        return False
    if scorecard.counterfactual_branch_failure != 0:
        return False
    if scorecard.fixed_order_behavior_detected != 0:
        return False
    if scorecard.context_negative_knowledge_leak != 0:
        return False
    if scorecard.restart_resume_failure != 0:
        return False
    if scorecard.verification_independence_failure != 0:
        return False
    return True

