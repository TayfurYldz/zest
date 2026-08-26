"""Record an explicit Human Review decision. Not Core Approval and not Finding creation."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import AuditEventRecord, HumanReviewRecord
from zest.research.finding_proposal import (
    FindingProposalState,
    HumanReviewDecision,
    HumanReviewView,
    admit_human_review,
)
from zest.research.types import ResearchInputError


@dataclass(frozen=True)
class RecordHumanReviewCommand:
    proposal_id: str
    reviewer_id: str
    actor_type: ActorType
    decision: HumanReviewDecision
    reason_codes: tuple[str, ...] = ("HUMAN_REVIEW_RECORDED",)
    note: str | None = None


@dataclass(frozen=True)
class RecordHumanReviewResult:
    review_id: str
    proposal_id: str
    decision: HumanReviewDecision


class RecordHumanReview:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: RecordHumanReviewCommand) -> RecordHumanReviewResult:
        with self._uow_factory.open() as uow:
            proposal = uow.finding_proposals.get(command.proposal_id)
            if proposal is None:
                raise ApplicationError("finding proposal not found")
            if proposal.state != FindingProposalState.HUMAN_REVIEW.value:
                raise ApplicationError("finding proposal is not in HUMAN_REVIEW")
            existing = uow.human_reviews.get_for_proposal(proposal.proposal_id)
            if existing is not None:
                if existing.content_fingerprint != proposal.content_fingerprint:
                    raise ApplicationError("existing review fingerprint mismatch")
                uow.commit()
                return RecordHumanReviewResult(
                    review_id=existing.review_id,
                    proposal_id=existing.proposal_id,
                    decision=HumanReviewDecision(existing.decision),
                )
            review_id = new_opaque_id()
            view = HumanReviewView(
                review_id=review_id,
                proposal_id=proposal.proposal_id,
                content_fingerprint=proposal.content_fingerprint,
                decision=command.decision,
                reviewer_id=command.reviewer_id,
                actor_type=command.actor_type.value,
                reason_codes=command.reason_codes,
                note=command.note,
            )
            try:
                admit_human_review(
                    view,
                    proposal_id=proposal.proposal_id,
                    content_fingerprint=proposal.content_fingerprint,
                )
            except ResearchInputError as exc:
                raise ApplicationError(str(exc)) from exc
            try:
                uow.human_reviews.insert(
                    HumanReviewRecord(
                        review_id=review_id,
                        proposal_id=proposal.proposal_id,
                        content_fingerprint=proposal.content_fingerprint,
                        decision=command.decision.value,
                        reviewer_id=command.reviewer_id,
                        actor_type=command.actor_type.value,
                        reason_codes=command.reason_codes,
                        created_at=self._clock.now(),
                        note=command.note,
                    )
                )
            except PersistenceConflictError as exc:
                raise ApplicationError("human review already recorded") from exc
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=command.reviewer_id,
                    actor_type=command.actor_type.value,
                    event_type="HUMAN_REVIEW_RECORDED",
                    subject_type="finding_proposal",
                    subject_id=proposal.proposal_id,
                    payload={
                        "review_id": review_id,
                        "decision": command.decision.value,
                    },
                )
            )
            uow.commit()
        return RecordHumanReviewResult(
            review_id=review_id,
            proposal_id=proposal.proposal_id,
            decision=command.decision,
        )
