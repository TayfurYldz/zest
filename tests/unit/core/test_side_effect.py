from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    ActorType,
    ApprovalDecision,
    ApprovalView,
    ExecutionDecisionKind,
    ReasonCode,
    SideEffectLevel,
    evaluate_execution,
)
from core.fixtures import base_request, human_approval


class SideEffectTests(unittest.TestCase):
    def test_level_0_can_allow(self) -> None:
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_0)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.ALLOW)

    def test_level_1_can_allow(self) -> None:
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_1)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.ALLOW)

    def test_level_2_without_approval_requires_review(self) -> None:
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_2, approval=None)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_level_2_with_valid_human_approval_still_denied_for_echo(self) -> None:
        decision = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_2,
                approval=human_approval(),
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_level_2_approval_by_worker_does_not_allow(self) -> None:
        approval = ApprovalView(
            approval_id="appr-w",
            subject_reference="action-1",
            decision=ApprovalDecision.APPROVE,
            decided_by="worker-1",
            actor_type=ActorType.WORKER,
            recorded=True,
        )
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_2, approval=approval)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_level_2_rejected_approval_denies(self) -> None:
        approval = ApprovalView(
            approval_id="appr-r",
            subject_reference="action-1",
            decision=ApprovalDecision.REJECT,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR,
            recorded=True,
        )
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_2, approval=approval)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_level_3_denies_even_with_approval(self) -> None:
        decision = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_3,
                approval=human_approval(),
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)


if __name__ == "__main__":
    unittest.main()
