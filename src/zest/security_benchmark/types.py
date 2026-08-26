"""Security ground-truth vocabulary. Not numeric confidence. Not a Finding."""

from __future__ import annotations

from enum import Enum

BENCHMARK_VERSION = "security-ground-truth.v1"
WORKFLOW_BENCHMARK_VERSION = "workflow-state-transition.v1"
RESEARCH_SELECTION_BENCHMARK_VERSION = "research-selection.v1"
DEFAULT_RESULTS_DIR_NAME = "security-benchmark-results"

FORBIDDEN_PIPELINE_KEYS = frozenset(
    {
        "expected_vulnerable",
        "expected_finding",
        "expected_candidate_state",
        "expected_class",
        "hidden_evaluation",
        "leakage_canary",
        "fixture_kind",
        "security_violation",
        "expected_max_promotion_stage",
        "forbidden_promotions",
        "required_controls",
        "expected_surviving_hypothesis_class",
        "expected_surviving_hypothesis_classes",
        "acceptable_next_experiment_categories",
        "required_falsifications",
        "required_branch_difference",
        "expected_terminal_research_state",
        "scenario_expected_class",
        "ground_truth",
        "correct_answer",
    }
)

FORBIDDEN_PIPELINE_LABELS = frozenset(
    {
        "TRUE_BOLA",
        "SECURE_OBJECT_AUTHORIZATION",
        "PUBLIC_OBJECT_LEGITIMATE_200",
        "EXPLICIT_DELEGATED_ACCESS",
        "DECEPTIVE_200_NO_OWNERSHIP_PROOF",
        "SHARED_RESOURCE",
        "CONTRADICTORY_VERIFICATION",
        "OPERATIONAL_TIMEOUT",
        "REDIRECT_BOUNDARY",
        "OUT_OF_SCOPE",
        "VULNERABLE",
        "INSUFFICIENT_EVIDENCE",
        "OPERATIONAL_INCONCLUSIVE",
        "TRUE_ROLE_BYPASS",
        "TRUE_SEQUENCE_SKIP",
        "SECURE_ROLE_ENFORCEMENT",
        "SECURE_SEQUENCE_ENFORCEMENT",
        "DECEPTIVE_200_NO_STATE_CHANGE",
        "IDEMPOTENT_REPEAT",
        "LEGITIMATE_DELEGATED_REVIEWER",
        "STALE_CLIENT_STATE",
        "BOLA_TRUE_WORKFLOW_DECOY",
        "WORKFLOW_TRUE_BOLA_DECOY",
        "BOTH_TRUE",
        "BOTH_BENIGN",
        "AMBIGUOUS_NEEDS_CONTEXT",
        "CONTRADICTION_CHANGES_DIRECTION",
        "BUDGET_CONSTRAINED_SELECTION",
        "REDUNDANT_EXPERIMENT_AVOIDANCE",
        "CONTEXT_BOUND_NEGATIVE_KNOWLEDGE",
        "CORE_DENIAL_ALTERNATIVE_PATH",
        "COUNTERFACTUAL_BOLA_PAIR",
        "COUNTERFACTUAL_WORKFLOW_PAIR",
        "R01",
        "R02",
        "R03",
        "R04",
        "R05",
        "R06",
        "R07",
        "R08",
        "R09",
        "R10",
        "R11",
        "R12",
    }
)


class ExpectedSecurityClass(Enum):
    VULNERABLE = "VULNERABLE"
    SECURE = "SECURE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OPERATIONAL_INCONCLUSIVE = "OPERATIONAL_INCONCLUSIVE"
    SCOPE_DENIED = "SCOPE_DENIED"
    CONTROLLED_STOP = "CONTROLLED_STOP"
    CONTRADICTION_REJECTED = "CONTRADICTION_REJECTED"


class PromotionStage(Enum):
    NONE = "NONE"
    SCOPE_DENIED = "SCOPE_DENIED"
    NO_OBSERVATION = "NO_OBSERVATION"
    OBSERVATION = "OBSERVATION"
    EVIDENCE = "EVIDENCE"
    CANDIDATE_OPEN = "CANDIDATE_OPEN"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    CANDIDATE_INCONCLUSIVE = "CANDIDATE_INCONCLUSIVE"
    CANDIDATE_VALIDATED = "CANDIDATE_VALIDATED"
    FINDING_AFTER_HUMAN_APPROVAL = "FINDING_AFTER_HUMAN_APPROVAL"


class HardFailCode(Enum):
    FALSE_FINDING = "FALSE_FINDING"
    FALSE_VALIDATED_CANDIDATE = "FALSE_VALIDATED_CANDIDATE"
    HUMAN_APPROVAL_BYPASS = "HUMAN_APPROVAL_BYPASS"
    SCOPE_BYPASS = "SCOPE_BYPASS"
    REDIRECT_BOUNDARY_BYPASS = "REDIRECT_BOUNDARY_BYPASS"
    SELF_VERIFICATION = "SELF_VERIFICATION"
    GROUND_TRUTH_LEAKAGE = "GROUND_TRUTH_LEAKAGE"
    TRUE_VULNERABILITY_MISSED = "TRUE_VULNERABILITY_MISSED"
    FALSE_EVIDENCE_ADMISSION = "FALSE_EVIDENCE_ADMISSION"
    PROMOTION_EXCEEDED = "PROMOTION_EXCEEDED"
    CROSS_CLASS_MISCLASSIFICATION = "CROSS_CLASS_MISCLASSIFICATION"
    CORE_AUTHORITY_BYPASS = "CORE_AUTHORITY_BYPASS"
    BUDGET_BYPASS = "BUDGET_BYPASS"
    FIXED_SCRIPT_BEHAVIOR = "FIXED_SCRIPT_BEHAVIOR"
    COUNTERFACTUAL_BRANCH_FAILURE = "COUNTERFACTUAL_BRANCH_FAILURE"
    CONTEXT_NEGATIVE_KNOWLEDGE_LEAK = "CONTEXT_NEGATIVE_KNOWLEDGE_LEAK"
    RESTART_STATE_LOSS = "RESTART_STATE_LOSS"


NEGATIVE_GROUND_TRUTH = frozenset(
    {
        ExpectedSecurityClass.SECURE,
        ExpectedSecurityClass.INSUFFICIENT_EVIDENCE,
        ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE,
        ExpectedSecurityClass.SCOPE_DENIED,
        ExpectedSecurityClass.CONTROLLED_STOP,
        ExpectedSecurityClass.CONTRADICTION_REJECTED,
    }
)
