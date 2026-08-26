from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.scope_reauthorization import (
    evaluate_reauthorization_request,
    proposed_redirect_method,
    reauthorization_request_from_worker_result,
    reevaluate_redirect_location,
)
from zest.core.enums import ReasonCode, ScopeDecision, ScopeRuleEffect
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.platform.contract_validation import ContractValidator


class RedirectReauthorizationTests(unittest.TestCase):
    def test_redirect_location_requires_fresh_core_evaluation(self) -> None:
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="example.com",
                    port=None,
                    path_prefix=None,
                    source_reference="scope-src",
                ),
            )
        )
        allowed = reevaluate_redirect_location("https://example.com/next", compiled)
        self.assertEqual(allowed.decision, ScopeDecision.ALLOW)
        denied = reevaluate_redirect_location("https://evil.example/next", compiled)
        self.assertEqual(denied.decision, ScopeDecision.DENY)
        self.assertEqual(denied.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_relative_location_is_resolved_against_response_url(self) -> None:
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="http",
                    host="127.0.0.1",
                    port=8080,
                    path_prefix="/next",
                    source_reference="scope-src",
                ),
            )
        )
        allowed = reevaluate_redirect_location(
            "../next",
            compiled,
            response_url="http://127.0.0.1:8080/a/b",
        )
        self.assertEqual(allowed.decision, ScopeDecision.ALLOW)
        denied = reevaluate_redirect_location(
            "../secret",
            compiled,
            response_url="http://127.0.0.1:8080/a/b",
        )
        self.assertEqual(denied.decision, ScopeDecision.DENY)

    def test_same_origin_redirect_still_requires_core_evaluation(self) -> None:
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="example.com",
                    port=None,
                    path_prefix="/app",
                    source_reference="scope-src",
                ),
            )
        )
        same_origin = reevaluate_redirect_location(
            "/admin",
            compiled,
            response_url="https://example.com/app",
        )
        self.assertEqual(same_origin.decision, ScopeDecision.DENY)

    def test_reauthorization_request_is_typed_and_not_a_grant(self) -> None:
        request = {
            "contract_version": "v1",
            "correlation": {
                "correlation_id": "c1",
                "research_run_id": "run-1",
                "experiment_id": "exp-1",
                "request_id": "req-1",
            },
            "worker_id": "local-python-diagnostic",
            "arguments": {
                "authorized_origin": "http://127.0.0.1:8080",
                "method": "POST",
                "path": "/a/b",
            },
        }
        result = {
            "contract_version": "v1",
            "correlation": request["correlation"],
            "worker_id": "local-python-diagnostic",
            "status": "REAUTHORIZATION_REQUIRED",
            "raw_result": {"method": "POST", "status": 302, "path": "/a/b"},
            "diagnostics": {
                "raw_location": "/next",
                "response_url": "http://127.0.0.1:8080/a/b",
                "followed": False,
            },
        }
        built = reauthorization_request_from_worker_result(request, result)
        ContractValidator().validate_reauthorization_request(built)
        self.assertEqual(built["reason"], "redirect")
        self.assertEqual(built["proposed_target_reference"], "http://127.0.0.1:8080/next")
        self.assertEqual(built["discovery_context"]["proposed_method"], "GET")
        self.assertFalse(built["discovery_context"]["followed"])
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="http",
                    host="127.0.0.1",
                    port=8080,
                    path_prefix=None,
                    source_reference="scope-src",
                ),
            )
        )
        check = evaluate_reauthorization_request(built, compiled)
        self.assertEqual(check.decision, ScopeDecision.ALLOW)

    def test_proposed_redirect_method_preserves_307(self) -> None:
        self.assertEqual(proposed_redirect_method("POST", 307), "POST")
        self.assertEqual(proposed_redirect_method("POST", 308), "POST")
        self.assertEqual(proposed_redirect_method("POST", 303), "GET")
        self.assertEqual(proposed_redirect_method("POST", 302), "GET")


if __name__ == "__main__":
    unittest.main()
