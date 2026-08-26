"""Human Approval eligibility. Core does not decide vulnerability truth."""

from dataclasses import dataclass

from zest.core.enums import ActorType, ApprovalDecision, ReasonCode
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id


@dataclass(frozen=True)
class ApprovalView:
    approval_id: str
    subject_reference: str
    decision: ApprovalDecision
    decided_by: str
    actor_type: ActorType
    recorded: bool

    def __post_init__(self) -> None:
        require_opaque_id(self.approval_id, "approval_id")
        require_opaque_id(self.subject_reference, "subject_reference")
        require_opaque_id(self.decided_by, "decided_by")
        if not isinstance(self.decision, ApprovalDecision):
            raise CoreInputError("decision must be ApprovalDecision")
        if not isinstance(self.actor_type, ActorType):
            raise CoreInputError("actor_type must be ActorType")
        if not isinstance(self.recorded, bool):
            raise CoreInputError("recorded must be bool")


@dataclass(frozen=True)
class ApprovalCheck:
    authorizes: bool
    require_human_review: bool
    reason_code: ReasonCode
    approval_id: str | None


def check_approval(
    approval: ApprovalView | None,
    requested_subject: str,
) -> ApprovalCheck:
    require_opaque_id(requested_subject, "requested_subject")
    if approval is None:
        return ApprovalCheck(False, True, ReasonCode.APPROVAL_REQUIRED, None)
    if not approval.recorded:
        return ApprovalCheck(
            False, False, ReasonCode.APPROVAL_NOT_RECORDED, approval.approval_id
        )
    if approval.actor_type is not ActorType.HUMAN_OPERATOR:
        return ApprovalCheck(
            False, False, ReasonCode.APPROVAL_INVALID_ACTOR, approval.approval_id
        )
    if approval.subject_reference != requested_subject:
        return ApprovalCheck(
            False, False, ReasonCode.APPROVAL_SUBJECT_MISMATCH, approval.approval_id
        )
    if approval.decision is ApprovalDecision.REJECT:
        return ApprovalCheck(
            False, False, ReasonCode.APPROVAL_REJECTED, approval.approval_id
        )
    return ApprovalCheck(True, False, ReasonCode.ALLOWED, approval.approval_id)


@dataclass(frozen=True)
class RecordedApprovalEvaluation:
    """Whether a recorded Approval is a valid human decision for a subject.

    authorizes is True only for APPROVE. A valid REJECT is a recorded decision
    and does not authorize. This is not Finding truth and not a vulnerability verdict.
    """

    valid_record: bool
    authorizes: bool
    reason_code: ReasonCode
    approval_id: str | None


def evaluate_recorded_approval(
    approval: ApprovalView | None,
    requested_subject: str,
) -> RecordedApprovalEvaluation:
    """Validate a durable Approval record for an explicit subject.

    Does not create Finding. Does not interpret Candidate or Evidence.
    """

    check = check_approval(approval, requested_subject)
    if approval is None:
        return RecordedApprovalEvaluation(False, False, check.reason_code, None)
    if check.reason_code is ReasonCode.APPROVAL_REJECTED:
        return RecordedApprovalEvaluation(
            True, False, check.reason_code, approval.approval_id
        )
    if check.authorizes:
        return RecordedApprovalEvaluation(
            True, True, check.reason_code, approval.approval_id
        )
    return RecordedApprovalEvaluation(
        False, False, check.reason_code, approval.approval_id
    )
