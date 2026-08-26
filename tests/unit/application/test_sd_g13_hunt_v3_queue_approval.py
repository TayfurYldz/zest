from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.hunt_v3_queue_approval import (
    ApproveHuntV3Queue,
    ApproveHuntV3QueueCommand,
    approval_subject_for_queue,
)
from zest.core.enums import ActorType, ApprovalDecision
from zest.data.records import ApprovalRecord, HuntV3QueueRecord
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT


def _queue(*, queue_id: str = "queue-1", approval_required: str | None = "SE3") -> HuntV3QueueRecord:
    arguments = {
        "claim": "protocol parser plan",
        "node_id": "op-1",
        "family_name": "HTTP_REQUEST_SMUGGLING_DESYNC",
        "protocol_plan_hash": "a" * 64,
        "plan_version": "protocol.parser.v1",
        "protocol_lane": "http_request_smuggling_desync",
        "step_count": 8,
        "worker_dispatch": "forbidden_until_se3_approval",
    }
    if approval_required is not None:
        arguments["approval_required"] = approval_required
    return HuntV3QueueRecord(
        queue_id=queue_id,
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        family_id="hf-http-smuggling-desync",
        node_canonical_key="op:https://example.test/edge",
        identity_id=None,
        capability="protocol.parser",
        action="plan",
        arguments=arguments,
        side_effect_level=3,
        state="PENDING",
        created_at=CREATED_AT,
    )


def _approval(subject: str, *, decision: ApprovalDecision = ApprovalDecision.APPROVE) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id="approval-1",
        subject_reference=subject,
        decision=decision.value,
        decided_by="operator-1",
        actor_type=ActorType.HUMAN_OPERATOR.value,
        recorded=True,
        created_at=CREATED_AT,
        research_run_id="run-1",
        proposal_id="proposal-placeholder",
        human_review_id="human-review-placeholder",
    )


class SDG13HuntV3QueueApprovalTests(unittest.TestCase):
    def test_se3_queue_without_human_approval_remains_pending(self) -> None:
        store = _Store()
        store.hunt_v3_queue["queue-1"] = _queue()
        result = ApproveHuntV3Queue(FakeUnitOfWorkFactory(store)).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertFalse(result.approved)
        self.assertEqual(result.reason_code, "APPROVAL_REQUIRED")
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "PENDING")
        self.assertEqual(len(store.audit_events), 1)

    def test_bound_human_approval_moves_se3_queue_to_approved(self) -> None:
        store = _Store()
        store.hunt_v3_queue["queue-1"] = _queue()
        store.approvals["approval-1"] = _approval(approval_subject_for_queue("queue-1"))

        result = ApproveHuntV3Queue(FakeUnitOfWorkFactory(store)).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertTrue(result.approved)
        self.assertEqual(result.reason_code, "ALLOWED")
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")
        audit = next(iter(store.audit_events.values()))
        self.assertEqual(audit.event_type, "HUNT_V3_QUEUE_APPROVED")

    def test_se3_queue_missing_marker_is_not_approved_even_with_human_approval(self) -> None:
        store = _Store()
        store.hunt_v3_queue["queue-1"] = _queue(approval_required=None)
        store.approvals["approval-1"] = _approval(approval_subject_for_queue("queue-1"))

        result = ApproveHuntV3Queue(FakeUnitOfWorkFactory(store)).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertFalse(result.approved)
        self.assertEqual(result.reason_code, "SE3_APPROVAL_MARKER_MISSING")
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "PENDING")

    def test_rejected_human_approval_does_not_approve_queue(self) -> None:
        store = _Store()
        store.hunt_v3_queue["queue-1"] = _queue()
        store.approvals["approval-1"] = _approval(
            approval_subject_for_queue("queue-1"),
            decision=ApprovalDecision.REJECT,
        )

        result = ApproveHuntV3Queue(FakeUnitOfWorkFactory(store)).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )

        self.assertFalse(result.approved)
        self.assertEqual(result.reason_code, "APPROVAL_REJECTED")
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "PENDING")


if __name__ == "__main__":
    unittest.main()
