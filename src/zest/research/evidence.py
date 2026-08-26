"""Evidence proposal and admission. Not Candidate, Finding, or Verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.assessment import (
    DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
    HTTP_STATE_TRANSITION_EVALUATION_STRATEGY,
    AssessmentOutcome,
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXECUTION_OUTCOMES,
    UNUSABLE_EXPERIMENT_STATES,
)
from zest.research.types import ResearchInputError

EVIDENCE_ADMISSION_POLICY_VERSION = "evidence.admission.v1"
DIAGNOSTIC_ECHO_MATCHED_CLAIM = "diagnostic echo observation matched the executed plan"
DIAGNOSTIC_ECHO_MISMATCHED_CLAIM = (
    "diagnostic echo observation did not match the executed plan"
)
HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM = (
    "Authenticated actor can read another actor's account object because object "
    "authorization is missing on the vulnerable endpoint."
)
HTTP_AUTHORIZATION_CONTROL_HELD_CLAIM = (
    "observed HTTP differential does not establish missing object access control"
)
HTTP_STATE_TRANSITION_CLAIM = (
    "Authenticated requester performed an unauthorized workflow state transition "
    "because role or sequence authorization is missing on the workflow endpoint."
)
HTTP_STATE_TRANSITION_CONTROL_HELD_CLAIM = (
    "observed HTTP state transition does not establish unauthorized workflow bypass"
)
SUPPORTED_EVIDENCE_STRATEGIES = frozenset(
    {
        DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
        HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
        HTTP_STATE_TRANSITION_EVALUATION_STRATEGY,
    }
)

FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "severity",
        "finding",
        "candidate",
        "exploitability",
        "authorization",
        "confidence",
        "verification",
    }
)


class EvidencePolarity(Enum):
    SUPPORTING = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"
    NEUTRAL = "NEUTRAL"


class EvidenceAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_INSUFFICIENT_SUPPORT = "REJECTED_INSUFFICIENT_SUPPORT"
    REJECTED_BROKEN_PROVENANCE = "REJECTED_BROKEN_PROVENANCE"
    REJECTED_EXECUTION_UNUSABLE = "REJECTED_EXECUTION_UNUSABLE"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        cleaned.append(_require_text(item, f"{field_name}[{index}]"))
    return tuple(cleaned)


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_EVIDENCE_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class EvidenceObservationRef:
    """Provenance pointer. Not the Observation payload and not Evidence."""

    observation_id: str
    research_run_id: str
    worker_result_id: str
    observation_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self,
            "worker_result_id",
            _require_text(self.worker_result_id, "worker_result_id"),
        )
        object.__setattr__(
            self,
            "observation_kind",
            _require_text(self.observation_kind, "observation_kind"),
        )


@dataclass(frozen=True)
class EvidenceAdmissionContext:
    """Loaded provenance for admission. Not a WorkerResult dump."""

    research_run_id: str
    hypothesis_id: str
    experiment_id: str
    evaluation_strategy: str
    observations: tuple[EvidenceObservationRef, ...]
    missing_source_ids: tuple[str, ...] = ()
    assessment_id: str | None = None
    assessment_outcome: AssessmentOutcome | None = None
    attempt_state: str | None = None
    worker_status: str | None = None
    invocation_status: str | None = None
    execution_outcome: str | None = None
    experiment_execution_state: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self,
            "evaluation_strategy",
            _require_text(self.evaluation_strategy, "evaluation_strategy"),
        )
        if not isinstance(self.observations, tuple):
            raise ResearchInputError("observations must be a tuple")
        if not isinstance(self.missing_source_ids, tuple):
            raise ResearchInputError("missing_source_ids must be a tuple")

    @property
    def observation_ids(self) -> frozenset[str]:
        return frozenset(item.observation_id for item in self.observations)

    @property
    def execution_unusable(self) -> bool:
        if self.assessment_outcome is AssessmentOutcome.EXECUTION_UNUSABLE:
            return True
        if self.execution_outcome in UNUSABLE_EXECUTION_OUTCOMES:
            return True
        if self.attempt_state in UNUSABLE_ATTEMPT_STATES:
            return True
        if self.experiment_execution_state in UNUSABLE_EXPERIMENT_STATES:
            return True
        if self.invocation_status in {
            "TIMED_OUT",
            "START_FAILED",
            "PROCESS_FAILED",
            "PROTOCOL_ERROR",
            "CONTRACT_INVALID",
            "CANCELLED",
        }:
            return True
        if self.worker_status in {"TIMED_OUT", "CANCELLED", "EXECUTION_FAILED"}:
            return True
        return False


@dataclass(frozen=True)
class EvidenceProposal:
    """Research-produced proposal. The model cannot admit Evidence."""

    proposal_id: str
    research_run_id: str
    hypothesis_id: str
    experiment_id: str
    observation_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    polarity: EvidencePolarity
    claim_scope: str
    rationale: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _require_text(self.proposal_id, "proposal_id"))
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self,
            "observation_ids",
            _require_ids(self.observation_ids, "observation_ids"),
        )
        object.__setattr__(
            self, "assessment_ids", _require_ids(self.assessment_ids, "assessment_ids")
        )
        if not isinstance(self.polarity, EvidencePolarity):
            raise ResearchInputError("polarity must be an EvidencePolarity")
        object.__setattr__(self, "claim_scope", _require_text(self.claim_scope, "claim_scope"))
        object.__setattr__(self, "rationale", _reject_forbidden(self.rationale, "rationale"))
        object.__setattr__(self, "provenance", _reject_forbidden(self.provenance, "provenance"))


@dataclass(frozen=True)
class EvidenceAdmissionDecision:
    outcome: EvidenceAdmissionOutcome
    reason_codes: tuple[str, ...]
    proposal: EvidenceProposal
    admitted: bool

    @property
    def creates_evidence(self) -> bool:
        return self.outcome is EvidenceAdmissionOutcome.ADMITTED and self.admitted


def propose_diagnostic_echo_evidence(
    context: EvidenceAdmissionContext,
    *,
    proposal_id: str,
) -> EvidenceProposal | None:
    """Deterministic plumbing proposal. Not vulnerability Evidence."""

    if context.evaluation_strategy != DIAGNOSTIC_ECHO_EVALUATION_STRATEGY:
        return None
    if context.execution_unusable:
        return None
    if context.assessment_id is None or context.assessment_outcome is None:
        return None
    if not context.observations:
        return None
    if context.assessment_outcome is AssessmentOutcome.CONSISTENT_WITH_PREDICTION:
        polarity = EvidencePolarity.SUPPORTING
        claim = DIAGNOSTIC_ECHO_MATCHED_CLAIM
        reason = "ECHO_MATCHED"
    elif context.assessment_outcome is AssessmentOutcome.CONTRADICTS_PREDICTION:
        polarity = EvidencePolarity.CONTRADICTING
        claim = DIAGNOSTIC_ECHO_MISMATCHED_CLAIM
        reason = "ECHO_MISMATCHED"
    else:
        return None
    return EvidenceProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        experiment_id=context.experiment_id,
        observation_ids=tuple(item.observation_id for item in context.observations),
        assessment_ids=(context.assessment_id,),
        polarity=polarity,
        claim_scope=claim,
        rationale={
            "reason_code": reason,
            "evaluation_strategy": context.evaluation_strategy,
            "not_vulnerability_evidence": True,
        },
        provenance={
            "source": "diagnostic.echo.deterministic",
            "assessment_id": context.assessment_id,
            "experiment_id": context.experiment_id,
        },
    )


def propose_authorization_differential_evidence(
    context: EvidenceAdmissionContext,
    *,
    proposal_id: str,
) -> EvidenceProposal | None:
    """Deterministic lab differential proposal. Not a Finding."""

    if context.evaluation_strategy != HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY:
        return None
    if context.execution_unusable:
        return None
    if context.assessment_id is None or context.assessment_outcome is None:
        return None
    if not context.observations:
        return None
    if context.assessment_outcome is not AssessmentOutcome.CONSISTENT_WITH_PREDICTION:
        return None
    return EvidenceProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        experiment_id=context.experiment_id,
        observation_ids=tuple(item.observation_id for item in context.observations),
        assessment_ids=(context.assessment_id,),
        polarity=EvidencePolarity.SUPPORTING,
        claim_scope=HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
        rationale={
            "reason_code": "AUTHORIZATION_DIFFERENTIAL_ESTABLISHED",
            "evaluation_strategy": context.evaluation_strategy,
        },
        provenance={
            "source": "http.authorization.differential.deterministic",
            "assessment_id": context.assessment_id,
            "experiment_id": context.experiment_id,
        },
    )


def propose_state_transition_evidence(
    context: EvidenceAdmissionContext,
    *,
    proposal_id: str,
) -> EvidenceProposal | None:
    """Deterministic workflow-bypass proposal. Not a Finding."""

    if context.evaluation_strategy != HTTP_STATE_TRANSITION_EVALUATION_STRATEGY:
        return None
    if context.execution_unusable:
        return None
    if context.assessment_id is None or context.assessment_outcome is None:
        return None
    if not context.observations:
        return None
    if context.assessment_outcome is not AssessmentOutcome.CONSISTENT_WITH_PREDICTION:
        return None
    return EvidenceProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        experiment_id=context.experiment_id,
        observation_ids=tuple(item.observation_id for item in context.observations),
        assessment_ids=(context.assessment_id,),
        polarity=EvidencePolarity.SUPPORTING,
        claim_scope=HTTP_STATE_TRANSITION_CLAIM,
        rationale={
            "reason_code": "UNAUTHORIZED_STATE_TRANSITION_ESTABLISHED",
            "evaluation_strategy": context.evaluation_strategy,
        },
        provenance={
            "source": "http.state_transition.deterministic",
            "assessment_id": context.assessment_id,
            "experiment_id": context.experiment_id,
        },
    )


def admit_evidence(
    proposal: EvidenceProposal,
    context: EvidenceAdmissionContext,
) -> EvidenceAdmissionDecision:
    """Research-owned Evidence admission. Application persists; Data does not decide."""

    codes: list[str] = []
    if proposal.research_run_id != context.research_run_id:
        codes.append("WRONG_RESEARCH_RUN")
    if proposal.hypothesis_id != context.hypothesis_id:
        codes.append("WRONG_HYPOTHESIS")
    if proposal.experiment_id != context.experiment_id:
        codes.append("WRONG_EXPERIMENT")
    if context.missing_source_ids:
        codes.append("MISSING_SOURCE")
    unknown = [item for item in proposal.observation_ids if item not in context.observation_ids]
    if unknown or not proposal.observation_ids:
        codes.append("HALLUCINATED_OR_ABSENT_SOURCE")
    for item in context.observations:
        if item.research_run_id != context.research_run_id:
            codes.append("OBSERVATION_WRONG_RUN")
            break
    if context.assessment_id is None:
        codes.append("ASSESSMENT_REQUIRED")
    elif context.assessment_id not in proposal.assessment_ids:
        codes.append("ASSESSMENT_NOT_REFERENCED")
    if context.execution_unusable:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_EXECUTION_UNUSABLE,
            reason_codes=tuple(["EXECUTION_UNUSABLE", *codes]),
            proposal=proposal,
            admitted=False,
        )
    if "WRONG_RESEARCH_RUN" in codes or "OBSERVATION_WRONG_RUN" in codes or "MISSING_SOURCE" in codes:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
        )
    if "HALLUCINATED_OR_ABSENT_SOURCE" in codes:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
        )
    if context.evaluation_strategy not in SUPPORTED_EVIDENCE_STRATEGIES:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("UNSUPPORTED_EVALUATION_STRATEGY", *codes),
            proposal=proposal,
            admitted=False,
        )
    if context.evaluation_strategy == HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY:
        return _admit_http_authorization_evidence(proposal, context, codes)
    if context.evaluation_strategy == HTTP_STATE_TRANSITION_EVALUATION_STRATEGY:
        return _admit_state_transition_evidence(proposal, context, codes)
    if context.assessment_outcome not in {
        AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
        AssessmentOutcome.CONTRADICTS_PREDICTION,
    }:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("ASSESSMENT_DOES_NOT_SUPPORT_EVIDENCE", *codes),
            proposal=proposal,
            admitted=False,
        )
    expected_claim = (
        DIAGNOSTIC_ECHO_MATCHED_CLAIM
        if context.assessment_outcome is AssessmentOutcome.CONSISTENT_WITH_PREDICTION
        else DIAGNOSTIC_ECHO_MISMATCHED_CLAIM
    )
    if proposal.claim_scope != expected_claim:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("CLAIM_EXCEEDS_SOURCES", *codes),
            proposal=proposal,
            admitted=False,
        )
    if codes:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
        )
    return EvidenceAdmissionDecision(
        outcome=EvidenceAdmissionOutcome.ADMITTED,
        reason_codes=("DIAGNOSTIC_ECHO_PROVENANCE_INTACT",),
        proposal=proposal,
        admitted=True,
    )


def _admit_http_authorization_evidence(
    proposal: EvidenceProposal,
    context: EvidenceAdmissionContext,
    codes: list[str],
) -> EvidenceAdmissionDecision:
    if context.assessment_outcome is not AssessmentOutcome.CONSISTENT_WITH_PREDICTION:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("DIFFERENTIAL_DOES_NOT_ESTABLISH_MISSING_OBJECT_ACCESS_CONTROL", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.claim_scope != HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("CLAIM_EXCEEDS_SOURCES", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.claim_scope == HTTP_STATE_TRANSITION_CLAIM:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("CROSS_CLASS_CLAIM_REJECTED", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.polarity is not EvidencePolarity.SUPPORTING:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("POLARITY_DOES_NOT_SUPPORT_CLAIM", *codes),
            proposal=proposal,
            admitted=False,
        )
    if codes:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
        )
    return EvidenceAdmissionDecision(
        outcome=EvidenceAdmissionOutcome.ADMITTED,
        reason_codes=("HTTP_AUTHORIZATION_DIFFERENTIAL_PROVENANCE_INTACT",),
        proposal=proposal,
        admitted=True,
    )


def _admit_state_transition_evidence(
    proposal: EvidenceProposal,
    context: EvidenceAdmissionContext,
    codes: list[str],
) -> EvidenceAdmissionDecision:
    if context.assessment_outcome is not AssessmentOutcome.CONSISTENT_WITH_PREDICTION:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("STATE_TRANSITION_DOES_NOT_ESTABLISH_WORKFLOW_BYPASS", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.claim_scope != HTTP_STATE_TRANSITION_CLAIM:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("CLAIM_EXCEEDS_SOURCES", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.claim_scope == HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("CROSS_CLASS_CLAIM_REJECTED", *codes),
            proposal=proposal,
            admitted=False,
        )
    if proposal.polarity is not EvidencePolarity.SUPPORTING:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("POLARITY_DOES_NOT_SUPPORT_CLAIM", *codes),
            proposal=proposal,
            admitted=False,
        )
    if codes:
        return EvidenceAdmissionDecision(
            outcome=EvidenceAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
        )
    return EvidenceAdmissionDecision(
        outcome=EvidenceAdmissionOutcome.ADMITTED,
        reason_codes=("HTTP_STATE_TRANSITION_AUTHORIZATION_PROVENANCE_INTACT",),
        proposal=proposal,
        admitted=True,
    )
