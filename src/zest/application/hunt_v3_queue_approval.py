"""Approval gate for Hunt V3 queue items. Does not dispatch Workers."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.approval import ApprovalView, evaluate_recorded_approval
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.records import ApprovalRecord, AuditEventRecord, HuntV3QueueRecord

HUNT_V3_QUEUE_APPROVAL_ACTOR_ID = "control-plane:hunt-v3-queue-approval"
HUNT_V3_QUEUE_APPROVED = "HUNT_V3_QUEUE_APPROVED"
HUNT_V3_QUEUE_APPROVAL_REJECTED = "HUNT_V3_QUEUE_APPROVAL_REJECTED"


@dataclass(frozen=True)
class ApproveHuntV3QueueCommand:
    research_run_id: str
    queue_id: str


@dataclass(frozen=True)
class ApproveHuntV3QueueResult:
    research_run_id: str
    queue_id: str
    approved: bool
    state: str
    reason_code: str
    approval_id: str | None


class HuntV3QueueApprovalError(Exception):
    """Raised when queue approval inputs are invalid."""


class ApproveHuntV3Queue:
    """Move a pending V3 queue item to APPROVED only after bound human approval."""

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: ApproveHuntV3QueueCommand) -> ApproveHuntV3QueueResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            item = uow.hunt_v3_queue.get(command.queue_id)
            if item is None:
                raise HuntV3QueueApprovalError("hunt V3 queue item not found")
            if item.research_run_id != command.research_run_id:
                raise HuntV3QueueApprovalError("hunt V3 queue item does not belong to run")
            if item.state != "PENDING":
                raise HuntV3QueueApprovalError("hunt V3 queue item is not pending")

            subject = approval_subject_for_queue(item.queue_id)
            approval = uow.approvals.get_by_subject(subject)
            approval_view = _approval_view(approval)
            evaluation = evaluate_recorded_approval(approval_view, subject)

            se3_marker_valid = True
            if item.side_effect_level == 3:
                se3_marker_valid = item.arguments.get("approval_required") == "SE3"
            if not se3_marker_valid:
                reason_code = "SE3_APPROVAL_MARKER_MISSING"
                _audit(uow, now, item, approved=False, reason_code=reason_code, approval_id=None)
                uow.commit()
                return _result(item, approved=False, reason_code=reason_code, approval_id=None)

            if not evaluation.authorizes:
                reason_code = evaluation.reason_code.value
                _audit(
                    uow,
                    now,
                    item,
                    approved=False,
                    reason_code=reason_code,
                    approval_id=evaluation.approval_id,
                )
                uow.commit()
                return _result(
                    item,
                    approved=False,
                    reason_code=reason_code,
                    approval_id=evaluation.approval_id,
                )

            uow.hunt_v3_queue.set_state(item.queue_id, "APPROVED")
            approved_item = HuntV3QueueRecord(
                queue_id=item.queue_id,
                research_run_id=item.research_run_id,
                hypothesis_id=item.hypothesis_id,
                family_id=item.family_id,
                node_canonical_key=item.node_canonical_key,
                identity_id=item.identity_id,
                capability=item.capability,
                action=item.action,
                arguments=item.arguments,
                side_effect_level=item.side_effect_level,
                state="APPROVED",
                created_at=item.created_at,
            )
            _audit(
                uow,
                now,
                approved_item,
                approved=True,
                reason_code="ALLOWED",
                approval_id=evaluation.approval_id,
            )
            uow.commit()
            return _result(
                approved_item,
                approved=True,
                reason_code="ALLOWED",
                approval_id=evaluation.approval_id,
            )


def approval_subject_for_queue(queue_id: str) -> str:
    return f"hunt-v3-queue:{queue_id}"


def _approval_view(record: ApprovalRecord | None) -> ApprovalView | None:
    if record is None:
        return None
    return ApprovalView(
        approval_id=record.approval_id,
        subject_reference=record.subject_reference,
        decision=ApprovalDecision(record.decision),
        decided_by=record.decided_by,
        actor_type=ActorType(record.actor_type),
        recorded=record.recorded,
    )


def _audit(
    uow,
    now,
    item: HuntV3QueueRecord,
    *,
    approved: bool,
    reason_code: str,
    approval_id: str | None,
) -> None:
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id=HUNT_V3_QUEUE_APPROVAL_ACTOR_ID,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=HUNT_V3_QUEUE_APPROVED if approved else HUNT_V3_QUEUE_APPROVAL_REJECTED,
            subject_type="HUNT_V3_QUEUE",
            subject_id=item.queue_id,
            payload={
                "research_run_id": item.research_run_id,
                "queue_id": item.queue_id,
                "capability": item.capability,
                "action": item.action,
                "side_effect_level": item.side_effect_level,
                "reason_code": reason_code,
                "approval_id": approval_id,
            },
            correlation_id=item.research_run_id,
        )
    )


def _result(
    item: HuntV3QueueRecord,
    *,
    approved: bool,
    reason_code: str,
    approval_id: str | None,
) -> ApproveHuntV3QueueResult:
    return ApproveHuntV3QueueResult(
        research_run_id=item.research_run_id,
        queue_id=item.queue_id,
        approved=approved,
        state=item.state,
        reason_code=reason_code,
        approval_id=approval_id,
    )
