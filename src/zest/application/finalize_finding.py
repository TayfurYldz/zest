"""Finalize FindingProposal via Core Approval. Application coordinates; cannot self-approve.

Approval + FindingProposal state + optional Finding commit together.
Human review interaction is a prior short transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.approval import ApprovalView, evaluate_recorded_approval
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.records import ApprovalRecord, AuditEventRecord, FindingRecord
from zest.research.candidate import CandidateState
from zest.research.finding_proposal import (
    FindingCreationContext,
    FindingCreationOutcome,
    FindingProposalState,
    HumanReviewDecision,
    HumanReviewView,
    approval_subject_for,
    evaluate_finding_creation,
    transition_finding_proposal,
)
from zest.research.types import ResearchInputError


@dataclass(frozen=True)
class FinalizeFindingCommand:
    proposal_id: str
    decided_by: str
    actor_type: ActorType


@dataclass(frozen=True)
class FinalizeFindingResult:
    outcome: FindingCreationOutcome
    proposal_id: str
    proposal_state: FindingProposalState
    finding_id: str | None
    approval_id: str | None
    reason_codes: tuple[str, ...]


class FinalizeFinding:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: FinalizeFindingCommand) -> FinalizeFindingResult:
        with self._uow_factory.open() as uow:
            proposal = uow.finding_proposals.get(command.proposal_id)
            if proposal is None:
                raise ApplicationError("finding proposal not found")
            existing = uow.findings.get_by_proposal(proposal.proposal_id)
            if existing is not None:
                uow.commit()
                return FinalizeFindingResult(
                    outcome=FindingCreationOutcome.CREATED,
                    proposal_id=proposal.proposal_id,
                    proposal_state=FindingProposalState.APPROVED,
                    finding_id=existing.finding_id,
                    approval_id=existing.approval_id,
                    reason_codes=("IDEMPOTENT_EXISTING_FINDING",),
                )
            try:
                proposal_state = FindingProposalState(proposal.state)
            except ValueError as exc:
                raise ApplicationError("finding proposal state is invalid") from exc
            if proposal_state is FindingProposalState.REJECTED:
                approval = uow.approvals.get_by_subject(
                    approval_subject_for(proposal.proposal_id, proposal.content_fingerprint)
                )
                uow.commit()
                return FinalizeFindingResult(
                    outcome=FindingCreationOutcome.REJECTED_PROPOSAL,
                    proposal_id=proposal.proposal_id,
                    proposal_state=FindingProposalState.REJECTED,
                    finding_id=None,
                    approval_id=None if approval is None else approval.approval_id,
                    reason_codes=("IDEMPOTENT_REJECTED_PROPOSAL",),
                )
            if proposal_state is FindingProposalState.APPROVED:
                raise ApplicationError("approved proposal is missing Finding provenance")
            candidate = uow.candidates.get(proposal.candidate_id)
            if candidate is None:
                raise ApplicationError("candidate not found")
            try:
                candidate_state = CandidateState(candidate.state)
            except ValueError as exc:
                raise ApplicationError("candidate state is invalid") from exc
            review_record = uow.human_reviews.get_for_proposal(proposal.proposal_id)
            review = None
            if review_record is not None:
                review = HumanReviewView(
                    review_id=review_record.review_id,
                    proposal_id=review_record.proposal_id,
                    content_fingerprint=review_record.content_fingerprint,
                    decision=HumanReviewDecision(review_record.decision),
                    reviewer_id=review_record.reviewer_id,
                    actor_type=review_record.actor_type,
                    reason_codes=review_record.reason_codes,
                    note=review_record.note,
                )
            subject = approval_subject_for(
                proposal.proposal_id, proposal.content_fingerprint
            )
            approval_id = new_opaque_id()
            approval_decision = None
            if review is not None:
                approval_decision = ApprovalDecision(review.decision.value)
            approval_view = None
            if review is not None:
                approval_view = ApprovalView(
                    approval_id=approval_id,
                    subject_reference=subject,
                    decision=approval_decision or ApprovalDecision.REJECT,
                    decided_by=command.decided_by,
                    actor_type=command.actor_type,
                    recorded=True,
                )
            evaluation = evaluate_recorded_approval(approval_view, subject)
            creation = evaluate_finding_creation(
                FindingCreationContext(
                    candidate_id=candidate.candidate_id,
                    candidate_state=candidate_state,
                    research_run_id=candidate.research_run_id,
                    proposal_id=proposal.proposal_id,
                    proposal_state=proposal_state,
                    title=proposal.title,
                    claim=proposal.claim,
                    evidence_ids=proposal.evidence_ids,
                    verification_ids=proposal.verification_ids,
                    content_fingerprint=proposal.content_fingerprint,
                    approval_subject=subject,
                    human_review=review,
                    approval_valid_record=evaluation.valid_record,
                    approval_authorizes=evaluation.authorizes,
                    approval_subject_matches=(
                        approval_view is not None
                        and approval_view.subject_reference == subject
                    ),
                    approval_decision=(
                        None
                        if approval_decision is None
                        else HumanReviewDecision(approval_decision.value)
                    ),
                    approval_actor_type=command.actor_type.value,
                )
            )
            if creation.outcome in {
                FindingCreationOutcome.REJECTED_MISSING_REVIEW,
                FindingCreationOutcome.REJECTED_MISSING_APPROVAL,
                FindingCreationOutcome.REJECTED_ACTOR,
                FindingCreationOutcome.REJECTED_SUBJECT_MISMATCH,
                FindingCreationOutcome.REJECTED_INCONSISTENT_DECISIONS,
                FindingCreationOutcome.REJECTED_CANDIDATE_NOT_VALIDATED,
                FindingCreationOutcome.REJECTED_ILLEGAL_STATE,
            }:
                raise ApplicationError(
                    f"finding finalization rejected: {creation.outcome.value}"
                )
            if review is None or approval_view is None:
                raise ApplicationError("finding finalization rejected: missing review")
            try:
                next_state = transition_finding_proposal(
                    proposal_state, creation.proposal_state
                )
            except ResearchInputError as exc:
                raise ApplicationError(str(exc)) from exc
            uow.approvals.insert(
                ApprovalRecord(
                    approval_id=approval_id,
                    subject_reference=subject,
                    decision=approval_view.decision.value,
                    decided_by=command.decided_by,
                    actor_type=command.actor_type.value,
                    recorded=True,
                    created_at=self._clock.now(),
                    research_run_id=proposal.research_run_id,
                    proposal_id=proposal.proposal_id,
                    human_review_id=review.review_id,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=command.decided_by,
                    actor_type=command.actor_type.value,
                    event_type="CORE_APPROVAL_RECORDED",
                    subject_type="finding_proposal",
                    subject_id=proposal.proposal_id,
                    payload={
                        "approval_id": approval_id,
                        "decision": approval_view.decision.value,
                    },
                )
            )
            uow.finding_proposals.set_state(proposal.proposal_id, next_state.value)
            finding_id = None
            if creation.creates_finding:
                finding_id = new_opaque_id()
                uow.findings.insert(
                    FindingRecord(
                        finding_id=finding_id,
                        finding_proposal_id=proposal.proposal_id,
                        candidate_id=candidate.candidate_id,
                        research_run_id=proposal.research_run_id,
                        approval_id=approval_id,
                        human_review_id=review.review_id,
                        title=proposal.title,
                        claim=proposal.claim,
                        classification=proposal.classification,
                        evidence_ids=proposal.evidence_ids,
                        verification_ids=proposal.verification_ids,
                        created_at=self._clock.now(),
                    )
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id="control-plane",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="FINDING_CREATED",
                        subject_type="finding",
                        subject_id=finding_id,
                        payload={
                            "proposal_id": proposal.proposal_id,
                            "candidate_id": candidate.candidate_id,
                            "not_a_finding_until_approval": True,
                            "classification": proposal.classification,
                        },
                    )
                )
            uow.commit()
        return FinalizeFindingResult(
            outcome=creation.outcome,
            proposal_id=proposal.proposal_id,
            proposal_state=next_state,
            finding_id=finding_id,
            approval_id=approval_id,
            reason_codes=creation.reason_codes,
        )
