"""Candidate proposal, admission, and lifecycle. Not Finding. Not Verification authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
    EvidencePolarity,
)
from zest.research.types import ResearchInputError

CANDIDATE_ADMISSION_POLICY_VERSION = "candidate.admission.v1"
DIAGNOSTIC_CANDIDATE_CLASSIFICATION = "DIAGNOSTIC_PLUMBING"
DIAGNOSTIC_CANDIDATE_CLAIM = DIAGNOSTIC_ECHO_MATCHED_CLAIM
HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION = "HTTP_AUTHORIZATION_DIFFERENTIAL"
HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM = HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM
HTTP_STATE_TRANSITION_CLASSIFICATION = "HTTP_STATE_TRANSITION_AUTHORIZATION"
HTTP_STATE_TRANSITION_CANDIDATE_CLAIM = HTTP_STATE_TRANSITION_CLAIM

FORBIDDEN_CANDIDATE_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "finding",
        "finding_proposal",
        "exploitability",
        "authorization",
        "confidence",
        "verification",
    }
)

ALLOWED_CANDIDATE_CLASSIFICATIONS = frozenset(
    {
        DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
        HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
        HTTP_STATE_TRANSITION_CLASSIFICATION,
    }
)


class CandidateState(Enum):
    """Lifecycle authority for a Candidate. VALIDATED is not a Finding."""

    OPEN = "OPEN"
    VERIFYING = "VERIFYING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    DUPLICATE = "DUPLICATE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class CandidateAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_INSUFFICIENT_SUPPORT = "REJECTED_INSUFFICIENT_SUPPORT"
    REJECTED_BROKEN_PROVENANCE = "REJECTED_BROKEN_PROVENANCE"
    REJECTED_CLAIM_EXCEEDS_EVIDENCE = "REJECTED_CLAIM_EXCEEDS_EVIDENCE"
    REJECTED_NOT_TESTABLE = "REJECTED_NOT_TESTABLE"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"


LEGAL_CANDIDATE_TRANSITIONS = frozenset(
    {
        (CandidateState.OPEN, CandidateState.VERIFYING),
        (CandidateState.VERIFYING, CandidateState.VALIDATED),
        (CandidateState.VERIFYING, CandidateState.REJECTED),
        (CandidateState.VERIFYING, CandidateState.INCONCLUSIVE),
        (CandidateState.OPEN, CandidateState.OUT_OF_SCOPE),
        (CandidateState.VERIFYING, CandidateState.OUT_OF_SCOPE),
        (CandidateState.OPEN, CandidateState.DUPLICATE),
        (CandidateState.VERIFYING, CandidateState.DUPLICATE),
    }
)

TERMINAL_CANDIDATE_STATES = frozenset(
    {
        CandidateState.VALIDATED,
        CandidateState.REJECTED,
        CandidateState.INCONCLUSIVE,
        CandidateState.DUPLICATE,
        CandidateState.OUT_OF_SCOPE,
    }
)


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
    found = FORBIDDEN_CANDIDATE_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class CandidateEvidenceRef:
    """Admitted Evidence pointer. Not Observation and not a Finding."""

    evidence_id: str
    research_run_id: str
    hypothesis_id: str
    experiment_id: str
    polarity: str
    claim_scope: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_id", _require_text(self.evidence_id, "evidence_id")
        )
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
        object.__setattr__(self, "polarity", _require_text(self.polarity, "polarity"))
        object.__setattr__(
            self, "claim_scope", _require_text(self.claim_scope, "claim_scope")
        )


@dataclass(frozen=True)
class CandidateProposal:
    """Research proposal to create a Candidate. Not a Candidate and not a Finding."""

    proposal_id: str
    research_run_id: str
    hypothesis_id: str
    evidence_ids: tuple[str, ...]
    claim: str
    classification: str
    rationale: Mapping[str, Any]
    provenance: Mapping[str, Any]
    duplicate_of_candidate_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proposal_id", _require_text(self.proposal_id, "proposal_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "evidence_ids", _require_ids(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))
        object.__setattr__(
            self,
            "classification",
            _require_text(self.classification, "classification"),
        )
        object.__setattr__(self, "rationale", _reject_forbidden(self.rationale, "rationale"))
        object.__setattr__(
            self, "provenance", _reject_forbidden(self.provenance, "provenance")
        )
        if self.duplicate_of_candidate_id is not None:
            object.__setattr__(
                self,
                "duplicate_of_candidate_id",
                _require_text(
                    self.duplicate_of_candidate_id, "duplicate_of_candidate_id"
                ),
            )


@dataclass(frozen=True)
class CandidateAdmissionContext:
    """Loaded provenance for Candidate admission. Not a WorkerResult dump."""

    research_run_id: str
    hypothesis_id: str
    evidence: tuple[CandidateEvidenceRef, ...]
    missing_evidence_ids: tuple[str, ...] = ()
    authoritative_out_of_scope: bool = False
    known_duplicate_candidate_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        if not isinstance(self.evidence, tuple):
            raise ResearchInputError("evidence must be a tuple")
        if not isinstance(self.missing_evidence_ids, tuple):
            raise ResearchInputError("missing_evidence_ids must be a tuple")
        if not isinstance(self.authoritative_out_of_scope, bool):
            raise ResearchInputError("authoritative_out_of_scope must be bool")

    @property
    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.evidence)


@dataclass(frozen=True)
class CandidateAdmissionDecision:
    outcome: CandidateAdmissionOutcome
    reason_codes: tuple[str, ...]
    proposal: CandidateProposal
    admitted: bool
    initial_state: CandidateState | None

    @property
    def creates_candidate(self) -> bool:
        return self.outcome is CandidateAdmissionOutcome.ADMITTED and self.admitted


def propose_diagnostic_candidate(
    context: CandidateAdmissionContext,
    *,
    proposal_id: str,
) -> CandidateProposal | None:
    """Deterministic plumbing proposal. Not a vulnerability Candidate."""

    if len(context.evidence) != 1:
        return None
    evidence = context.evidence[0]
    if evidence.polarity != EvidencePolarity.SUPPORTING.value:
        return None
    if evidence.claim_scope != DIAGNOSTIC_ECHO_MATCHED_CLAIM:
        return None
    if evidence.research_run_id != context.research_run_id:
        return None
    return CandidateProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        evidence_ids=(evidence.evidence_id,),
        claim=DIAGNOSTIC_CANDIDATE_CLAIM,
        classification=DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
        rationale={
            "reason_code": "DIAGNOSTIC_ECHO_EVIDENCE_SEEDED",
            "not_a_vulnerability": True,
            "not_a_finding": True,
        },
        provenance={
            "source": "diagnostic.echo.candidate",
            "evidence_id": evidence.evidence_id,
        },
    )


def propose_authorization_differential_candidate(
    context: CandidateAdmissionContext,
    *,
    proposal_id: str,
) -> CandidateProposal | None:
    """Bounded object-access Candidate. Not a Finding and not a CVSS score."""

    if len(context.evidence) != 1:
        return None
    evidence = context.evidence[0]
    if evidence.polarity != EvidencePolarity.SUPPORTING.value:
        return None
    if evidence.claim_scope != HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM:
        return None
    if evidence.research_run_id != context.research_run_id:
        return None
    return CandidateProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        evidence_ids=(evidence.evidence_id,),
        claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
        classification=HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
        rationale={
            "reason_code": "HTTP_AUTHORIZATION_DIFFERENTIAL_EVIDENCE_SEEDED",
            "not_a_finding": True,
        },
        provenance={
            "source": "http.authorization.differential.candidate",
            "evidence_id": evidence.evidence_id,
        },
    )


def propose_state_transition_candidate(
    context: CandidateAdmissionContext,
    *,
    proposal_id: str,
) -> CandidateProposal | None:
    """Bounded workflow-bypass Candidate. Not a Finding and not a BOLA class."""

    if len(context.evidence) != 1:
        return None
    evidence = context.evidence[0]
    if evidence.polarity != EvidencePolarity.SUPPORTING.value:
        return None
    if evidence.claim_scope != HTTP_STATE_TRANSITION_CLAIM:
        return None
    if evidence.claim_scope == HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM:
        return None
    if evidence.research_run_id != context.research_run_id:
        return None
    return CandidateProposal(
        proposal_id=proposal_id,
        research_run_id=context.research_run_id,
        hypothesis_id=context.hypothesis_id,
        evidence_ids=(evidence.evidence_id,),
        claim=HTTP_STATE_TRANSITION_CANDIDATE_CLAIM,
        classification=HTTP_STATE_TRANSITION_CLASSIFICATION,
        rationale={
            "reason_code": "HTTP_STATE_TRANSITION_AUTHORIZATION_EVIDENCE_SEEDED",
            "not_a_finding": True,
        },
        provenance={
            "source": "http.state_transition.candidate",
            "evidence_id": evidence.evidence_id,
        },
    )


def admit_candidate(
    proposal: CandidateProposal,
    context: CandidateAdmissionContext,
) -> CandidateAdmissionDecision:
    """Research-owned Candidate admission. Application persists; Data does not decide."""

    codes: list[str] = []
    if proposal.research_run_id != context.research_run_id:
        codes.append("WRONG_RESEARCH_RUN")
    if proposal.hypothesis_id != context.hypothesis_id:
        codes.append("WRONG_HYPOTHESIS")
    if context.missing_evidence_ids:
        codes.append("MISSING_EVIDENCE")
    unknown = [item for item in proposal.evidence_ids if item not in context.evidence_ids]
    if unknown or not proposal.evidence_ids:
        codes.append("HALLUCINATED_OR_ABSENT_EVIDENCE")
    for item in context.evidence:
        if item.research_run_id != context.research_run_id:
            codes.append("EVIDENCE_WRONG_RUN")
            break
        if item.hypothesis_id != context.hypothesis_id:
            codes.append("EVIDENCE_WRONG_HYPOTHESIS")
            break
    if context.authoritative_out_of_scope:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("AUTHORITATIVE_OUT_OF_SCOPE", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if "WRONG_RESEARCH_RUN" in codes or "EVIDENCE_WRONG_RUN" in codes or "MISSING_EVIDENCE" in codes:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if "HALLUCINATED_OR_ABSENT_EVIDENCE" in codes:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if proposal.classification not in ALLOWED_CANDIDATE_CLASSIFICATIONS:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_NOT_TESTABLE,
            reason_codes=("UNSUPPORTED_CANDIDATE_CLASSIFICATION", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    evidence_claims = {
        item.claim_scope
        for item in context.evidence
        if item.evidence_id in proposal.evidence_ids
    }
    if (
        proposal.classification == HTTP_STATE_TRANSITION_CLASSIFICATION
        and HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM in evidence_claims
    ) or (
        proposal.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION
        and HTTP_STATE_TRANSITION_CLAIM in evidence_claims
    ):
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_NOT_TESTABLE,
            reason_codes=("CROSS_CLASS_EVIDENCE_REJECTED", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    expected_claim = DIAGNOSTIC_CANDIDATE_CLAIM
    expected_evidence_claim = DIAGNOSTIC_ECHO_MATCHED_CLAIM
    if proposal.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION:
        expected_claim = HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM
        expected_evidence_claim = HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM
    elif proposal.classification == HTTP_STATE_TRANSITION_CLASSIFICATION:
        expected_claim = HTTP_STATE_TRANSITION_CANDIDATE_CLAIM
        expected_evidence_claim = HTTP_STATE_TRANSITION_CLAIM
    if proposal.claim != expected_claim:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_NOT_TESTABLE,
            reason_codes=("CLAIM_NOT_TESTABLE_FOR_CLASSIFICATION", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    supporting = [
        item
        for item in context.evidence
        if item.evidence_id in proposal.evidence_ids
        and item.polarity == EvidencePolarity.SUPPORTING.value
        and item.claim_scope == expected_evidence_claim
    ]
    if not supporting:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=("EVIDENCE_DOES_NOT_SUPPORT_CLAIM", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if proposal.claim != supporting[0].claim_scope:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_CLAIM_EXCEEDS_EVIDENCE,
            reason_codes=("CLAIM_EXCEEDS_EVIDENCE_SCOPE", *codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if proposal.duplicate_of_candidate_id is not None:
        if proposal.duplicate_of_candidate_id != context.known_duplicate_candidate_id:
            return CandidateAdmissionDecision(
                outcome=CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
                reason_codes=("UNKNOWN_DUPLICATE_REFERENCE", *codes),
                proposal=proposal,
                admitted=False,
                initial_state=None,
            )
    if codes:
        return CandidateAdmissionDecision(
            outcome=CandidateAdmissionOutcome.REJECTED_INSUFFICIENT_SUPPORT,
            reason_codes=tuple(codes),
            proposal=proposal,
            admitted=False,
            initial_state=None,
        )
    if proposal.classification == HTTP_STATE_TRANSITION_CLASSIFICATION:
        reason = "HTTP_STATE_TRANSITION_AUTHORIZATION_CANDIDATE_SEEDED_FROM_EVIDENCE"
    elif proposal.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION:
        reason = "HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_SEEDED_FROM_EVIDENCE"
    else:
        reason = "DIAGNOSTIC_CANDIDATE_SEEDED_FROM_EVIDENCE"
    return CandidateAdmissionDecision(
        outcome=CandidateAdmissionOutcome.ADMITTED,
        reason_codes=(reason,),
        proposal=proposal,
        admitted=True,
        initial_state=CandidateState.OPEN,
    )


def transition_candidate(
    current: CandidateState,
    target: CandidateState,
) -> CandidateState:
    """Central legal-transition enforcement. Does not persist. Does not create Finding."""

    if not isinstance(current, CandidateState) or not isinstance(target, CandidateState):
        raise ResearchInputError("candidate states must be CandidateState values")
    if (current, target) not in LEGAL_CANDIDATE_TRANSITIONS:
        raise ResearchInputError(
            f"illegal Candidate transition {current.value} → {target.value}"
        )
    return target


def start_candidate_verification(current: CandidateState) -> CandidateState:
    return transition_candidate(current, CandidateState.VERIFYING)
