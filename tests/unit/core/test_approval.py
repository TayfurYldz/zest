from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    ActorType,
    ApprovalDecision,
    ApprovalView,
    ReasonCode,
    evaluate_recorded_approval,
)
from zest.core.approval import check_approval
from fixtures import human_approval


class ApprovalTests(unittest.TestCase):
    def test_correct_subject_required(self) -> None:
        approval = human_approval(subject="other-action")
        result = check_approval(approval, "action-1")
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_SUBJECT_MISMATCH)

    def test_recorded_required(self) -> None:
        approval = ApprovalView(
            approval_id="appr-1",
            subject_reference="action-1",
            decision=ApprovalDecision.APPROVE,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR,
            recorded=False,
        )
        result = check_approval(approval, "action-1")
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_NOT_RECORDED)

    def test_human_actor_required(self) -> None:
        approval = ApprovalView(
            approval_id="appr-1",
            subject_reference="action-1",
            decision=ApprovalDecision.APPROVE,
            decided_by="cp-1",
            actor_type=ActorType.CONTROL_PLANE,
            recorded=True,
        )
        result = check_approval(approval, "action-1")
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_INVALID_ACTOR)

    def test_integration_cannot_approve(self) -> None:
        approval = ApprovalView(
            approval_id="appr-1",
            subject_reference="action-1",
            decision=ApprovalDecision.APPROVE,
            decided_by="strix",
            actor_type=ActorType.INTEGRATION,
            recorded=True,
        )
        result = check_approval(approval, "action-1")
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_INVALID_ACTOR)


class RecordedApprovalEvaluationTests(unittest.TestCase):
    def test_approve_is_valid_and_authorizing(self) -> None:
        approval = human_approval(subject="finding-proposal:p1:fp")
        result = evaluate_recorded_approval(approval, "finding-proposal:p1:fp")
        self.assertTrue(result.valid_record)
        self.assertTrue(result.authorizes)

    def test_reject_is_valid_but_not_authorizing(self) -> None:
        approval = ApprovalView(
            approval_id="appr-1",
            subject_reference="finding-proposal:p1:fp",
            decision=ApprovalDecision.REJECT,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR,
            recorded=True,
        )
        result = evaluate_recorded_approval(approval, "finding-proposal:p1:fp")
        self.assertTrue(result.valid_record)
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_REJECTED)

    def test_wrong_subject_is_not_a_valid_record(self) -> None:
        approval = human_approval(subject="finding-proposal:p1:fp")
        result = evaluate_recorded_approval(approval, "finding-proposal:p2:other")
        self.assertFalse(result.valid_record)
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_SUBJECT_MISMATCH)

    def test_non_human_is_not_a_valid_record(self) -> None:
        approval = ApprovalView(
            approval_id="appr-1",
            subject_reference="finding-proposal:p1:fp",
            decision=ApprovalDecision.APPROVE,
            decided_by="model-1",
            actor_type=ActorType.CONTROL_PLANE,
            recorded=True,
        )
        result = evaluate_recorded_approval(approval, "finding-proposal:p1:fp")
        self.assertFalse(result.valid_record)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_INVALID_ACTOR)

    def test_missing_approval_is_not_a_valid_record(self) -> None:
        result = evaluate_recorded_approval(None, "finding-proposal:p1:fp")
        self.assertFalse(result.valid_record)
        self.assertFalse(result.authorizes)
        self.assertEqual(result.reason_code, ReasonCode.APPROVAL_REQUIRED)


if __name__ == "__main__":
    unittest.main()
