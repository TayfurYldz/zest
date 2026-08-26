from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core import (
    AuthorizationSourceState,
    AuthorizationSourceView,
    ExecutionDecisionKind,
    ReasonCode,
    evaluate_execution,
)
from fixtures import base_request


class AuthorizationTests(unittest.TestCase):
    def test_missing_source_denies(self) -> None:
        decision = evaluate_execution(base_request(authorization_source=None))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.AUTHORIZATION_MISSING)

    def test_revoked_denies(self) -> None:
        source = AuthorizationSourceView(
            "as-1", "program-1", AuthorizationSourceState.REVOKED
        )
        decision = evaluate_execution(base_request(authorization_source=source))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.AUTHORIZATION_INACTIVE)

    def test_expired_denies(self) -> None:
        source = AuthorizationSourceView(
            "as-1", "program-1", AuthorizationSourceState.EXPIRED
        )
        decision = evaluate_execution(base_request(authorization_source=source))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.AUTHORIZATION_INACTIVE)

    def test_active_continues_to_allow_when_rest_valid(self) -> None:
        decision = evaluate_execution(base_request())
        self.assertEqual(decision.decision, ExecutionDecisionKind.ALLOW)
        self.assertEqual(decision.reason_code, ReasonCode.ALLOWED)


if __name__ == "__main__":
    unittest.main()
