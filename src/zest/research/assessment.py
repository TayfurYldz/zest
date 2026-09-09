"""Hypothesis assessment. Context-bound learning, not Hypothesis truth or Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from zest.research.feedback import ExperimentFeedback
from zest.research.types import ExperimentPlan, ResearchInputError


class AssessmentOutcome(Enum):
    """What this experiment taught us under this context. Not a Finding."""

    CONSISTENT_WITH_PREDICTION = "CONSISTENT_WITH_PREDICTION"
    CONTRADICTS_PREDICTION = "CONTRADICTS_PREDICTION"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXECUTION_UNUSABLE = "EXECUTION_UNUSABLE"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"


class EvaluatorKind(Enum):
    DETERMINISTIC = "DETERMINISTIC"


DIAGNOSTIC_ECHO_EVALUATION_STRATEGY = "diagnostic.echo.v1"
HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY = (
    "http.authorization.differential.v1"
)
HTTP_STATE_TRANSITION_EVALUATION_STRATEGY = "http.state_transition.v1"
HTTP_TRANSACTION_EVALUATION_STRATEGY = "http.transaction.v1"

UNUSABLE_EXECUTION_OUTCOMES = frozenset(
    {
        "INVOCATION_FAILED",
        "UNKNOWN_OUTCOME",
        "DISPATCH_DENIED",
        "HUMAN_REVIEW_REQUIRED",
        "AUTHORIZED_NOT_DISPATCHED",
    }
)
UNUSABLE_ATTEMPT_STATES = frozenset(
    {"FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN_OUTCOME"}
)
UNUSABLE_EXPERIMENT_STATES = frozenset(
    {"EXECUTION_FAILED", "BLOCKED", "CANCELLED", "BUDGET_EXHAUSTED"}
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class HypothesisAssessment:
    """One context-bound assessment. Not Evidence, Candidate, or Finding."""

    outcome: AssessmentOutcome
    evaluator_kind: EvaluatorKind
    evaluator_version: str
    rationale: Mapping[str, Any]
    hypothesis_id: str
    experiment_id: str
    research_run_id: str
    observation_ids: tuple[str, ...]
    evaluation_strategy: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AssessmentOutcome):
            raise ResearchInputError("outcome must be an AssessmentOutcome")
        if not isinstance(self.evaluator_kind, EvaluatorKind):
            raise ResearchInputError("evaluator_kind must be an EvaluatorKind")
        object.__setattr__(
            self,
            "evaluator_version",
            _require_text(self.evaluator_version, "evaluator_version"),
        )
        if not isinstance(self.rationale, Mapping):
            raise ResearchInputError("rationale must be a mapping")
        forbidden = {"severity", "evidence", "finding", "confidence", "candidate"}
        found = forbidden.intersection(self.rationale.keys())
        if found:
            raise ResearchInputError(
                f"assessment rationale must not contain {sorted(found)}"
            )
        object.__setattr__(self, "rationale", dict(self.rationale))
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self,
            "evaluation_strategy",
            _require_text(self.evaluation_strategy, "evaluation_strategy"),
        )
        if not isinstance(self.observation_ids, tuple):
            raise ResearchInputError("observation_ids must be a tuple")

    @property
    def execution_usable(self) -> bool:
        return self.outcome is not AssessmentOutcome.EXECUTION_UNUSABLE


class ExperimentEvaluator(Protocol):
    """Trusted Research evaluator. WorkerResult does not choose this."""

    strategy: str
    version: str

    def evaluate(
        self, plan: ExperimentPlan, feedback: ExperimentFeedback
    ) -> HypothesisAssessment: ...


class ExperimentEvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[str, ExperimentEvaluator] = {}

    def register(self, evaluator: ExperimentEvaluator) -> None:
        self._evaluators[evaluator.strategy] = evaluator

    def get(self, strategy: str) -> ExperimentEvaluator:
        evaluator = self._evaluators.get(strategy)
        if evaluator is None:
            raise ResearchInputError(
                f"no trusted evaluator registered for strategy {strategy!r}"
            )
        return evaluator


def default_evaluator_registry() -> ExperimentEvaluatorRegistry:
    from zest.research.evaluators.authorization_differential import (
        HttpAuthorizationDifferentialEvaluator,
    )
    from zest.research.evaluators.diagnostic_echo import DiagnosticEchoEvaluator
    from zest.research.evaluators.http_authentication import HttpAuthenticationEvaluator
    from zest.research.evaluators.http_transaction import HttpTransactionEvaluator
    from zest.research.evaluators.mutation_matrix import MutationMatrixEvaluator
    from zest.research.evaluators.oast_callback import OastCallbackEvaluator
    from zest.research.evaluators.protocol_step import ProtocolStepEvaluator
    from zest.research.evaluators.state_transition import HttpStateTransitionEvaluator

    registry = ExperimentEvaluatorRegistry()
    registry.register(DiagnosticEchoEvaluator())
    registry.register(HttpAuthorizationDifferentialEvaluator())
    registry.register(HttpStateTransitionEvaluator())
    registry.register(HttpTransactionEvaluator())
    registry.register(HttpAuthenticationEvaluator())
    registry.register(MutationMatrixEvaluator())
    registry.register(ProtocolStepEvaluator())
    registry.register(OastCallbackEvaluator())
    return registry


@dataclass(frozen=True)
class ResearchFeedback:
    """Application-facing assessment result. Not a security verdict."""

    hypothesis_id: str
    experiment_id: str
    assessment_id: str
    assessment_outcome: AssessmentOutcome
    observation_ids: tuple[str, ...]
    execution_usable: bool
    evaluation_strategy: str
    research_run_id: str
