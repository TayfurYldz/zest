"""Research: proposals only. A7 adds a bounded reasoning cycle, not an autonomous brain.

Research must not execute, authorize, persist via PostgreSQL, or import provider SDKs.
Model output is an untrusted structured proposal until Research admission.
"""

from zest.research.admission import AdmissionDecision, AdmissionOutcome, admit_hypothesis
from zest.research.assessment import (
    AssessmentOutcome,
    ExperimentEvaluatorRegistry,
    HypothesisAssessment,
    ResearchFeedback,
    default_evaluator_registry,
)
from zest.research.context import ResearchContext, ResearchContextBuilder
from zest.research.epistemic import EpistemicClass
from zest.research.feedback import ExperimentFeedback
from zest.research.model_port import ModelCallRequest, ModelCallResult, ModelPort, ModelRole
from zest.research.proposals import HypothesisChallenge, HypothesisProposal
from zest.research.types import ExperimentPlan, HypothesisDraft

__all__ = [
    "AdmissionDecision",
    "AdmissionOutcome",
    "AssessmentOutcome",
    "EpistemicClass",
    "ExperimentEvaluatorRegistry",
    "ExperimentFeedback",
    "ExperimentPlan",
    "HypothesisAssessment",
    "HypothesisChallenge",
    "HypothesisDraft",
    "HypothesisProposal",
    "ModelCallRequest",
    "ModelCallResult",
    "ModelPort",
    "ModelRole",
    "ResearchContext",
    "ResearchContextBuilder",
    "ResearchFeedback",
    "admit_hypothesis",
    "default_evaluator_registry",
]
