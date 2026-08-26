from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core.enums import ReasonCode, ScopeDecision, ScopeRuleEffect
from zest.core.errors import CoreInputError
from zest.core.scope_compiler import (
    ScopeRuleDefinition,
    compile_scope_rules,
    evaluate_scope_candidate,
)
from zest.platform.url_normalize import normalize_url


def _allow(host: str = "example.com", scheme: str = "https", port: int | None = None, path: str | None = None):
    return ScopeRuleDefinition(
        rule_id="rule-allow",
        effect=ScopeRuleEffect.ALLOW,
        scheme=scheme,
        host=host,
        port=port,
        path_prefix=path,
        source_reference="scope-src",
    )


def _deny(host: str = "example.com", scheme: str = "https", port: int | None = None, path: str | None = None):
    return ScopeRuleDefinition(
        rule_id="rule-deny",
        effect=ScopeRuleEffect.DENY,
        scheme=scheme,
        host=host,
        port=port,
        path_prefix=path,
        source_reference="scope-src",
    )


class ScopeCompilerTests(unittest.TestCase):
    def test_exact_allowed_origin(self) -> None:
        compiled = compile_scope_rules((_allow(),))
        decision = evaluate_scope_candidate(normalize_url("https://example.com/app"), compiled)
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_wrong_scheme_denies(self) -> None:
        compiled = compile_scope_rules((_allow(),))
        decision = evaluate_scope_candidate(normalize_url("http://example.com/app"), compiled)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_wrong_non_default_port_denies(self) -> None:
        compiled = compile_scope_rules((_allow(port=8443),))
        decision = evaluate_scope_candidate(normalize_url("https://example.com/app"), compiled)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_default_port_equivalence(self) -> None:
        compiled = compile_scope_rules((_allow(),))
        decision = evaluate_scope_candidate(normalize_url("https://example.com:443/app"), compiled)
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_userinfo_denies(self) -> None:
        compiled = compile_scope_rules((_allow(),))
        decision = evaluate_scope_candidate(normalize_url("https://user@example.com/app"), compiled)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_USERINFO_DENIED)

    def test_allow_plus_exclusion_denies(self) -> None:
        compiled = compile_scope_rules((_allow(), _deny(path="/admin")))
        decision = evaluate_scope_candidate(normalize_url("https://example.com/admin"), compiled)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_no_matching_rule_denies(self) -> None:
        compiled = compile_scope_rules((_allow(host="allowed.example"),))
        decision = evaluate_scope_candidate(normalize_url("https://example.com/"), compiled)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_wildcard_rule_rejected(self) -> None:
        with self.assertRaises(CoreInputError):
            compile_scope_rules((_allow(host="*.example.com"),))

    def test_query_cannot_create_host_confusion(self) -> None:
        compiled = compile_scope_rules((_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://evil.example/?host=example.com"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_fragment_cannot_grant_scope(self) -> None:
        compiled = compile_scope_rules((_allow(host="example.com"),))
        decision = evaluate_scope_candidate(
            normalize_url("https://evil.example/#https://example.com"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)


if __name__ == "__main__":
    unittest.main()
