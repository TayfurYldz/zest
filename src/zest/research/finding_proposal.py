"""FindingProposal and Finding creation gate. Not Core Approval. Not a vulnerability scanner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLAIM,
    DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CANDIDATE_CLAIM,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
    CandidateState,
)
from zest.research.types import ResearchInputError

FINDING_PROPOSAL_POLICY_VERSION = "finding.proposal.v1"
DIAGNOSTIC_FINDING_PROPOSAL_TITLE = "Diagnostic echo verification proposal"
DIAGNOSTIC_FINDING_CLASSIFICATION = DIAGNOSTIC_CANDIDATE_CLASSIFICATION
HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE = (
    "Local lab missing object authorization on account read"
)
HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_CLASSIFICATION = (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION
)
HTTP_STATE_TRANSITION_FINDING_TITLE = (
    "Local lab unauthorized workflow state transition"
)
HTTP_STATE_TRANSITION_FINDING_CLASSIFICATION = HTTP_STATE_TRANSITION_CLASSIFICATION
HUMAN_OPERATOR_ACTOR = "HUMAN_OPERATOR"

FORBIDDEN_FINDING_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "bounty",
        "exploitability",
        "confidence",
        "vulnerability",
        "authorization",
    }
)


class FindingProposalState(Enum):
    """Proposal lifecycle. APPROVED is the domain view of Core Approval, not a second authority."""

    PROPOSED = "PROPOSED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FindingProposalAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_CANDIDATE_NOT_VALIDATED = "REJECTED_CANDIDATE_NOT_VALIDATED"
    REJECTED_BROKEN_PROVENANCE = "REJECTED_BROKEN_PROVENANCE"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"
    REJECTED_VALIDATION_NOT_PASSED = "REJECTED_VALIDATION_NOT_PASSED"


@dataclass(frozen=True)
class ImpactClaim:
    """One impact claim attached to a FindingProposalDraft. Must be backed by a chain."""

    claim_text: str
    impact_kind: str
    chain_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_text", _require_text(self.claim_text, "claim_text"))
        object.__setattr__(self, "impact_kind", _require_text(self.impact_kind, "impact_kind"))
        object.__setattr__(self, "chain_id", _require_text(self.chain_id, "chain_id"))


class HumanReviewDecision(Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class FindingCreationOutcome(Enum):
    CREATED = "CREATED"
    REJECTED_PROPOSAL = "REJECTED_PROPOSAL"
    REJECTED_MISSING_REVIEW = "REJECTED_MISSING_REVIEW"
    REJECTED_MISSING_APPROVAL = "REJECTED_MISSING_APPROVAL"
    REJECTED_ACTOR = "REJECTED_ACTOR"
    REJECTED_SUBJECT_MISMATCH = "REJECTED_SUBJECT_MISMATCH"
    REJECTED_INCONSISTENT_DECISIONS = "REJECTED_INCONSISTENT_DECISIONS"
    REJECTED_CANDIDATE_NOT_VALIDATED = "REJECTED_CANDIDATE_NOT_VALIDATED"
    REJECTED_ILLEGAL_STATE = "REJECTED_ILLEGAL_STATE"


LEGAL_FINDING_PROPOSAL_TRANSITIONS = frozenset(
    {
        (FindingProposalState.PROPOSED, FindingProposalState.HUMAN_REVIEW),
        (FindingProposalState.HUMAN_REVIEW, FindingProposalState.APPROVED),
        (FindingProposalState.HUMAN_REVIEW, FindingProposalState.REJECTED),
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_FINDING_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


def finding_proposal_content_fingerprint(
    *,
    candidate_id: str,
    title: str,
    claim: str,
    evidence_ids: tuple[str, ...],
    verification_ids: tuple[str, ...],
) -> str:
    payload = {
        "candidate_id": candidate_id,
        "title": title,
        "claim": claim,
        "evidence_ids": list(evidence_ids),
        "verification_ids": list(verification_ids),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def approval_subject_for(proposal_id: str, content_fingerprint: str) -> str:
    return f"finding-proposal:{proposal_id}:{content_fingerprint}"


@dataclass(frozen=True)
class FindingProposalDraft:
    """Research proposal asking Human Review to accept a VALIDATED Candidate. Not a Finding."""

    proposal_id: str
    candidate_id: str
    research_run_id: str
    title: str
    claim: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    rationale: Mapping[str, Any]
    provenance: Mapping[str, Any]
    impact_claims: tuple[ImpactClaim, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proposal_id", _require_text(self.proposal_id, "proposal_id")
        )
        object.__setattr__(
            self, "candidate_id", _require_text(self.candidate_id, "candidate_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))
        object.__setattr__(
            self, "evidence_ids", _require_ids(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(
            self,
            "verification_ids",
            _require_ids(self.verification_ids, "verification_ids"),
        )
        object.__setattr__(self, "rationale", _reject_forbidden(self.rationale, "rationale"))
        object.__setattr__(
            self, "provenance", _reject_forbidden(self.provenance, "provenance")
        )
        if not isinstance(self.impact_claims, tuple):
            raise ResearchInputError("impact_claims must be a tuple")
        object.__setattr__(
            self,
            "impact_claims",
            tuple(
                item if isinstance(item, ImpactClaim) else ImpactClaim(**item)
                for item in self.impact_claims
            ),
        )

    @property
    def content_fingerprint(self) -> str:
        return finding_proposal_content_fingerprint(
            candidate_id=self.candidate_id,
            title=self.title,
            claim=self.claim,
            evidence_ids=self.evidence_ids,
            verification_ids=self.verification_ids,
        )

    @property
    def approval_subject(self) -> str:
        return approval_subject_for(self.proposal_id, self.content_fingerprint)


@dataclass(frozen=True)
class FindingProposalAdmissionContext:
    candidate_id: str
    candidate_state: CandidateState
    research_run_id: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    classification: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_text(self.candidate_id, "candidate_id")
        )
        if not isinstance(self.candidate_state, CandidateState):
            raise ResearchInputError("candidate_state must be a CandidateState")
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "evidence_ids", _require_ids(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(
            self,
            "verification_ids",
            _require_ids(self.verification_ids, "verification_ids"),
        )
        object.__setattr__(
            self, "classification", _require_text(self.classification, "classification")
        )


@dataclass(frozen=True)
class FindingProposalAdmissionDecision:
    outcome: FindingProposalAdmissionOutcome
    reason_codes: tuple[str, ...]
    draft: FindingProposalDraft
    admitted: bool
    initial_state: FindingProposalState | None

    @property
    def creates_proposal(self) -> bool:
        return (
            self.outcome is FindingProposalAdmissionOutcome.ADMITTED and self.admitted
        )


@dataclass(frozen=True)
class HumanReviewView:
    """Recorded human decision. Not Core Approval and not a Finding."""

    review_id: str
    proposal_id: str
    content_fingerprint: str
    decision: HumanReviewDecision
    reviewer_id: str
    actor_type: str
    reason_codes: tuple[str, ...]
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_id", _require_text(self.review_id, "review_id"))
        object.__setattr__(
            self, "proposal_id", _require_text(self.proposal_id, "proposal_id")
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_text(self.content_fingerprint, "content_fingerprint"),
        )
        if not isinstance(self.decision, HumanReviewDecision):
            raise ResearchInputError("decision must be a HumanReviewDecision")
        object.__setattr__(
            self, "reviewer_id", _require_text(self.reviewer_id, "reviewer_id")
        )
        object.__setattr__(
            self, "actor_type", _require_text(self.actor_type, "actor_type")
        )
        object.__setattr__(
            self, "reason_codes", _require_ids(self.reason_codes, "reason_codes")
        )
        if self.note is not None:
            object.__setattr__(self, "note", _require_text(self.note, "note"))


@dataclass(frozen=True)
class FindingCreationContext:
    candidate_id: str
    candidate_state: CandidateState
    research_run_id: str
    proposal_id: str
    proposal_state: FindingProposalState
    title: str
    claim: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    content_fingerprint: str
    approval_subject: str
    human_review: HumanReviewView | None
    approval_valid_record: bool
    approval_authorizes: bool
    approval_subject_matches: bool
    approval_decision: HumanReviewDecision | None
    approval_actor_type: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _require_text(self.candidate_id, "candidate_id")
        )
        if not isinstance(self.candidate_state, CandidateState):
            raise ResearchInputError("candidate_state must be a CandidateState")
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self, "proposal_id", _require_text(self.proposal_id, "proposal_id")
        )
        if not isinstance(self.proposal_state, FindingProposalState):
            raise ResearchInputError("proposal_state must be a FindingProposalState")
        object.__setattr__(self, "title", _require_text(self.title, "title"))
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))
        object.__setattr__(
            self, "evidence_ids", _require_ids(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(
            self,
            "verification_ids",
            _require_ids(self.verification_ids, "verification_ids"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _require_text(self.content_fingerprint, "content_fingerprint"),
        )
        object.__setattr__(
            self,
            "approval_subject",
            _require_text(self.approval_subject, "approval_subject"),
        )
        if not isinstance(self.approval_valid_record, bool):
            raise ResearchInputError("approval_valid_record must be bool")
        if not isinstance(self.approval_authorizes, bool):
            raise ResearchInputError("approval_authorizes must be bool")
        if not isinstance(self.approval_subject_matches, bool):
            raise ResearchInputError("approval_subject_matches must be bool")


@dataclass(frozen=True)
class FindingCreationDecision:
    outcome: FindingCreationOutcome
    reason_codes: tuple[str, ...]
    proposal_state: FindingProposalState
    creates_finding: bool


def propose_diagnostic_finding_proposal(
    context: FindingProposalAdmissionContext,
    *,
    proposal_id: str,
) -> FindingProposalDraft | None:
    if context.candidate_state is not CandidateState.VALIDATED:
        return None
    if context.classification != DIAGNOSTIC_FINDING_CLASSIFICATION:
        return None
    if not context.evidence_ids or not context.verification_ids:
        return None
    return FindingProposalDraft(
        proposal_id=proposal_id,
        candidate_id=context.candidate_id,
        research_run_id=context.research_run_id,
        title=DIAGNOSTIC_FINDING_PROPOSAL_TITLE,
        claim=DIAGNOSTIC_CANDIDATE_CLAIM,
        evidence_ids=context.evidence_ids,
        verification_ids=context.verification_ids,
        rationale={
            "reason_code": "DIAGNOSTIC_PLUMBING_PROPOSAL",
            "not_a_vulnerability": True,
            "not_a_finding": True,
        },
        provenance={"source": "diagnostic.echo.finding_proposal"},
    )


def propose_authorization_differential_finding_proposal(
    context: FindingProposalAdmissionContext,
    *,
    proposal_id: str,
) -> FindingProposalDraft | None:
    if context.candidate_state is not CandidateState.VALIDATED:
        return None
    if context.classification != HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_CLASSIFICATION:
        return None
    if not context.evidence_ids or not context.verification_ids:
        return None
    return FindingProposalDraft(
        proposal_id=proposal_id,
        candidate_id=context.candidate_id,
        research_run_id=context.research_run_id,
        title=HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
        claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
        evidence_ids=context.evidence_ids,
        verification_ids=context.verification_ids,
        rationale={
            "reason_code": "AUTHORIZED_LOCAL_LAB_PROPOSAL",
            "not_a_finding": True,
        },
        provenance={"source": "http.authorization.differential.finding_proposal"},
    )


def propose_state_transition_finding_proposal(
    context: FindingProposalAdmissionContext,
    *,
    proposal_id: str,
) -> FindingProposalDraft | None:
    if context.candidate_state is not CandidateState.VALIDATED:
        return None
    if context.classification != HTTP_STATE_TRANSITION_FINDING_CLASSIFICATION:
        return None
    if not context.evidence_ids or not context.verification_ids:
        return None
    return FindingProposalDraft(
        proposal_id=proposal_id,
        candidate_id=context.candidate_id,
        research_run_id=context.research_run_id,
        title=HTTP_STATE_TRANSITION_FINDING_TITLE,
        claim=HTTP_STATE_TRANSITION_CANDIDATE_CLAIM,
        evidence_ids=context.evidence_ids,
        verification_ids=context.verification_ids,
        rationale={
            "reason_code": "AUTHORIZED_LOCAL_LAB_PROPOSAL",
            "not_a_finding": True,
        },
        provenance={"source": "http.state_transition.finding_proposal"},
    )


def admit_finding_proposal(
    draft: FindingProposalDraft,
    context: FindingProposalAdmissionContext,
) -> FindingProposalAdmissionDecision:
    """Research-owned FindingProposal admission. Application persists; Core does not judge truth."""

    codes: list[str] = []
    if draft.candidate_id != context.candidate_id:
        codes.append("WRONG_CANDIDATE")
    if draft.research_run_id != context.research_run_id:
        codes.append("WRONG_RESEARCH_RUN")
    if set(draft.evidence_ids) != set(context.evidence_ids):
        codes.append("EVIDENCE_SET_MISMATCH")
    if set(draft.verification_ids) != set(context.verification_ids):
        codes.append("VERIFICATION_SET_MISMATCH")
    if context.candidate_state is not CandidateState.VALIDATED:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_CANDIDATE_NOT_VALIDATED,
            reason_codes=("CANDIDATE_NOT_VALIDATED", *codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    if draft.impact_claims:
        missing = [claim.claim_text for claim in draft.impact_claims if not claim.chain_id.strip()]
        if missing:
            return FindingProposalAdmissionDecision(
                outcome=FindingProposalAdmissionOutcome.REJECTED_POLICY_CONFLICT,
                reason_codes=("IMPACT_CHAIN_MISSING", *codes),
                draft=draft,
                admitted=False,
                initial_state=None,
            )
    if "WRONG_CANDIDATE" in codes or "WRONG_RESEARCH_RUN" in codes:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    if "EVIDENCE_SET_MISMATCH" in codes or "VERIFICATION_SET_MISMATCH" in codes:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    expected_title = DIAGNOSTIC_FINDING_PROPOSAL_TITLE
    expected_claim = DIAGNOSTIC_CANDIDATE_CLAIM
    expected_classification = DIAGNOSTIC_FINDING_CLASSIFICATION
    admitted_reason = "DIAGNOSTIC_FINDING_PROPOSAL_FROM_VALIDATED_CANDIDATE"
    title_code = "TITLE_NOT_DIAGNOSTIC"
    claim_code = "CLAIM_NOT_DIAGNOSTIC"
    if context.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_CLASSIFICATION:
        expected_title = HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE
        expected_claim = HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM
        expected_classification = HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_CLASSIFICATION
        admitted_reason = "HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_PROPOSAL_FROM_VALIDATED_CANDIDATE"
        title_code = "TITLE_NOT_HTTP_AUTHORIZATION_DIFFERENTIAL"
        claim_code = "CLAIM_NOT_HTTP_AUTHORIZATION_DIFFERENTIAL"
    elif context.classification == HTTP_STATE_TRANSITION_FINDING_CLASSIFICATION:
        expected_title = HTTP_STATE_TRANSITION_FINDING_TITLE
        expected_claim = HTTP_STATE_TRANSITION_CANDIDATE_CLAIM
        expected_classification = HTTP_STATE_TRANSITION_FINDING_CLASSIFICATION
        admitted_reason = "HTTP_STATE_TRANSITION_AUTHORIZATION_FINDING_PROPOSAL_FROM_VALIDATED_CANDIDATE"
        title_code = "TITLE_NOT_HTTP_STATE_TRANSITION_AUTHORIZATION"
        claim_code = "CLAIM_NOT_HTTP_STATE_TRANSITION_AUTHORIZATION"
    if draft.title != expected_title:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=(title_code, *codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    if draft.claim != expected_claim:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=(claim_code, *codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    if context.classification != expected_classification:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_POLICY_CONFLICT,
            reason_codes=("UNSUPPORTED_CLASSIFICATION", *codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    if codes:
        return FindingProposalAdmissionDecision(
            outcome=FindingProposalAdmissionOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=tuple(codes),
            draft=draft,
            admitted=False,
            initial_state=None,
        )
    return FindingProposalAdmissionDecision(
        outcome=FindingProposalAdmissionOutcome.ADMITTED,
        reason_codes=(admitted_reason,),
        draft=draft,
        admitted=True,
        initial_state=FindingProposalState.PROPOSED,
    )


def transition_finding_proposal(
    current: FindingProposalState,
    target: FindingProposalState,
) -> FindingProposalState:
    if not isinstance(current, FindingProposalState) or not isinstance(
        target, FindingProposalState
    ):
        raise ResearchInputError("FindingProposal states must be FindingProposalState")
    if (current, target) not in LEGAL_FINDING_PROPOSAL_TRANSITIONS:
        raise ResearchInputError(
            f"illegal FindingProposal transition {current.value} → {target.value}"
        )
    return target


def start_finding_proposal_review(current: FindingProposalState) -> FindingProposalState:
    return transition_finding_proposal(current, FindingProposalState.HUMAN_REVIEW)


def admit_human_review(review: HumanReviewView, *, proposal_id: str, content_fingerprint: str) -> None:
    if review.proposal_id != proposal_id:
        raise ResearchInputError("human review proposal_id mismatch")
    if review.content_fingerprint != content_fingerprint:
        raise ResearchInputError("human review content_fingerprint mismatch")
    if review.actor_type != HUMAN_OPERATOR_ACTOR:
        raise ResearchInputError("human review requires HUMAN_OPERATOR")
    if review.decision not in {HumanReviewDecision.APPROVE, HumanReviewDecision.REJECT}:
        raise ResearchInputError("human review decision must be APPROVE or REJECT")


def evaluate_finding_creation(context: FindingCreationContext) -> FindingCreationDecision:
    """Research creation gate. Core Approval is an input. This does not persist."""

    if context.candidate_state is not CandidateState.VALIDATED:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_CANDIDATE_NOT_VALIDATED,
            reason_codes=("CANDIDATE_NOT_VALIDATED",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if context.proposal_state is not FindingProposalState.HUMAN_REVIEW:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_ILLEGAL_STATE,
            reason_codes=("PROPOSAL_NOT_IN_HUMAN_REVIEW",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    review = context.human_review
    if review is None:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_MISSING_REVIEW,
            reason_codes=("HUMAN_REVIEW_REQUIRED",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if review.actor_type != HUMAN_OPERATOR_ACTOR or (
        context.approval_actor_type is not None
        and context.approval_actor_type != HUMAN_OPERATOR_ACTOR
    ):
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_ACTOR,
            reason_codes=("NON_HUMAN_ACTOR",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if (
        review.proposal_id != context.proposal_id
        or review.content_fingerprint != context.content_fingerprint
    ):
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_SUBJECT_MISMATCH,
            reason_codes=("REVIEW_CONTENT_MISMATCH",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if not context.approval_subject_matches or not context.approval_valid_record:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_MISSING_APPROVAL
            if not context.approval_valid_record
            else FindingCreationOutcome.REJECTED_SUBJECT_MISMATCH,
            reason_codes=(
                ("APPROVAL_REQUIRED",)
                if not context.approval_valid_record
                else ("APPROVAL_SUBJECT_MISMATCH",)
            ),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if context.approval_decision is None or context.approval_decision is not review.decision:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_INCONSISTENT_DECISIONS,
            reason_codes=("REVIEW_APPROVAL_DECISION_MISMATCH",),
            proposal_state=context.proposal_state,
            creates_finding=False,
        )
    if review.decision is HumanReviewDecision.REJECT:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_PROPOSAL,
            reason_codes=("HUMAN_REJECTED_PROPOSAL", "CANDIDATE_REMAINS_VALIDATED"),
            proposal_state=FindingProposalState.REJECTED,
            creates_finding=False,
        )
    if not context.approval_authorizes:
        return FindingCreationDecision(
            outcome=FindingCreationOutcome.REJECTED_PROPOSAL,
            reason_codes=("CORE_APPROVAL_REJECTED", "CANDIDATE_REMAINS_VALIDATED"),
            proposal_state=FindingProposalState.REJECTED,
            creates_finding=False,
        )
    if (
        context.title == HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE
        and context.claim == HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM
    ) or (
        context.title == HTTP_STATE_TRANSITION_FINDING_TITLE
        and context.claim == HTTP_STATE_TRANSITION_CANDIDATE_CLAIM
    ):
        created_codes = ("AUTHORIZED_LOCAL_LAB_FINDING",)
    else:
        created_codes = ("DIAGNOSTIC_PLUMBING_FINDING", "NOT_A_VULNERABILITY")
    return FindingCreationDecision(
        outcome=FindingCreationOutcome.CREATED,
        reason_codes=created_codes,
        proposal_state=FindingProposalState.APPROVED,
        creates_finding=True,
    )
