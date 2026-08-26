from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.core.enums import (
    ReasonCode,
    ScopeClassification,
    ScopeDecision,
    ScopeRuleEffect,
)
from zest.core.errors import CoreInputError
from zest.core.scope_compiler import (
    ScopeRuleDefinition,
    compile_scope_rules,
    evaluate_scope_candidate,
)
from zest.platform.url_normalize import normalize_url


class ScopeCompilerV2Tests(unittest.TestCase):
    def _wildcard_allow(self, pattern: str = "*.example.com"):
        return ScopeRuleDefinition(
            rule_id="rule-wildcard-allow",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host_pattern=pattern,
            source_reference="scope-src",
        )

    def _exact_allow(self, host: str = "api.example.com"):
        return ScopeRuleDefinition(
            rule_id="rule-exact-allow",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host=host,
            source_reference="scope-src",
        )

    def _wildcard_unknown(self, pattern: str = "*.example.com"):
        return ScopeRuleDefinition(
            rule_id="rule-wildcard-unknown",
            effect=ScopeRuleEffect.UNKNOWN,
            scheme="https",
            host_pattern=pattern,
            source_reference="scope-src",
        )

    def _deny_admin(self, host: str = "*.example.com"):
        return ScopeRuleDefinition(
            rule_id="rule-deny-admin",
            effect=ScopeRuleEffect.DENY,
            scheme="https",
            host_pattern=host,
            path_prefix="/admin",
            source_reference="scope-src",
        )

    def test_wildcard_matches_multi_level_subdomain(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        for host in ("api.example.com", "a.b.example.com", "x.y.z.example.com"):
            with self.subTest(host=host):
                decision = evaluate_scope_candidate(
                    normalize_url(f"https://{host}/app"), compiled
                )
                self.assertEqual(decision.decision, ScopeDecision.ALLOW)
                self.assertEqual(decision.classification, ScopeClassification.IN_SCOPE)

    def test_wildcard_apex_does_not_match(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://example.com/app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_wildcard_rejects_suffix_attacks(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        for host in ("evil-example.com", "example.com.evil.com", "notexample.com"):
            with self.subTest(host=host):
                decision = evaluate_scope_candidate(
                    normalize_url(f"https://{host}/app"), compiled
                )
                self.assertEqual(
                    decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED
                )
                self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_wildcard_plus_exclusion_denies(self) -> None:
        compiled = compile_scope_rules(
            (self._wildcard_allow("*.example.com"), self._deny_admin("*.example.com"))
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/admin"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)
        self.assertIn("rule-deny-admin", decision.matched_rule_ids)

    def test_exact_host_still_works(self) -> None:
        compiled = compile_scope_rules((self._exact_allow("api.example.com"),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_unknown_rule_denies_active_probing(self) -> None:
        compiled = compile_scope_rules((self._wildcard_unknown(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_allow_overrides_unknown(self) -> None:
        compiled = compile_scope_rules(
            (
                self._wildcard_unknown("*.example.com"),
                self._exact_allow("api.example.com"),
            )
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)
        self.assertEqual(decision.classification, ScopeClassification.IN_SCOPE)

    def test_expired_rule_requires_human_review(self) -> None:
        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        expired = ScopeRuleDefinition(
            rule_id="rule-expired",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            expires_at=fixed_now - timedelta(hours=1),
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((expired,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"),
            compiled,
            now=fixed_now,
        )
        self.assertEqual(decision.decision, ScopeDecision.REQUIRE_HUMAN_REVIEW)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_EXPIRED)

    def test_not_yet_expired_rule_allows(self) -> None:
        fixed_now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        active = ScopeRuleDefinition(
            rule_id="rule-active",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            expires_at=fixed_now + timedelta(hours=1),
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((active,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"),
            compiled,
            now=fixed_now,
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_wildcard_rejects_userinfo(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://user@api.example.com/app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_USERINFO_DENIED)

    def test_wildcard_wrong_scheme_denies(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("http://api.example.com/app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_invalid_wildcard_pattern_rejected(self) -> None:
        with self.assertRaisesRegex(CoreInputError, "host_pattern must start with"):
            ScopeRuleDefinition(
                rule_id="rule-bad",
                effect=ScopeRuleEffect.ALLOW,
                scheme="https",
                host_pattern="example.*.com",
                source_reference="scope-src",
            )

    def test_host_and_pattern_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(CoreInputError, "mutually exclusive"):
            ScopeRuleDefinition(
                rule_id="rule-bad",
                effect=ScopeRuleEffect.ALLOW,
                scheme="https",
                host="api.example.com",
                host_pattern="*.example.com",
                source_reference="scope-src",
            )

    def test_port_mismatch_denies(self) -> None:
        compiled = compile_scope_rules((self._exact_allow("api.example.com"),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com:8443/app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_explicit_port_match_allows(self) -> None:
        rule = ScopeRuleDefinition(
            rule_id="rule-port-8443",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            port=8443,
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((rule,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com:8443/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_path_traversal_encoded_dot_dot_rejected(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/%2e%2e%2fadmin"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_PATH_AMBIGUOUS)

    def test_path_traversal_literal_dot_dot_rejected(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/../admin"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_PATH_AMBIGUOUS)

    def test_path_prefix_match_allows(self) -> None:
        rule = ScopeRuleDefinition(
            rule_id="rule-api-prefix",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            path_prefix="/api/v1/",
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((rule,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/api/v1/users"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_path_prefix_mismatch_denies(self) -> None:
        rule = ScopeRuleDefinition(
            rule_id="rule-api-prefix",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            path_prefix="/api/v1/",
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((rule,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/admin"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_idn_punycode_normalized_and_matched(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://xn--bcher-kva.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)
        self.assertEqual(decision.classification, ScopeClassification.IN_SCOPE)

    def test_idn_unicode_normalized_and_matched(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://bücher.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)
        self.assertEqual(decision.classification, ScopeClassification.IN_SCOPE)

    def test_trailing_dot_apex_does_not_match_wildcard(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        decision = evaluate_scope_candidate(
            normalize_url("https://example.com./app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_casing_host_normalized(self) -> None:
        compiled = compile_scope_rules((self._exact_allow("API.Example.COM"),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_casing_path_preserved(self) -> None:
        rule = ScopeRuleDefinition(
            rule_id="rule-case-path",
            effect=ScopeRuleEffect.ALLOW,
            scheme="https",
            host="api.example.com",
            path_prefix="/API/",
            source_reference="scope-src",
        )
        compiled = compile_scope_rules((rule,))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/api/users"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_wildcard_dotless_suffix_rejected(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        for host in ("examplecom", "apiexamplecom", "example.co"):
            with self.subTest(host=host):
                decision = evaluate_scope_candidate(
                    normalize_url(f"https://{host}/app"), compiled
                )
                self.assertEqual(
                    decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED
                )

    def test_wildcard_subdomain_plus_exact_exclusion(self) -> None:
        compiled = compile_scope_rules(
            (
                self._wildcard_allow("*.example.com"),
                ScopeRuleDefinition(
                    rule_id="rule-exact-deny",
                    effect=ScopeRuleEffect.DENY,
                    scheme="https",
                    host="evil.example.com",
                    source_reference="scope-src",
                ),
            )
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://evil.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_unknown_with_exclusion_out_of_scope(self) -> None:
        compiled = compile_scope_rules(
            (
                self._wildcard_unknown("*.example.com"),
                self._deny_admin("*.example.com"),
            )
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/admin"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)

    def test_allow_overrides_unknown_for_host(self) -> None:
        compiled = compile_scope_rules(
            (
                self._wildcard_unknown("*.example.com"),
                self._exact_allow("api.example.com"),
            )
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/admin"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.ALLOW)

    def test_no_rule_empty_scope_denies(self) -> None:
        compiled = compile_scope_rules(())
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_wildcard_dotless_suffix_is_unknown(self) -> None:
        compiled = compile_scope_rules((self._wildcard_allow(),))
        for host in ("examplecom", "apiexamplecom", "example.co"):
            with self.subTest(host=host):
                decision = evaluate_scope_candidate(
                    normalize_url(f"https://{host}/app"), compiled
                )
                self.assertEqual(
                    decision.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED
                )
                self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_unknown_classification_allows_census_but_denies_active_probe(self) -> None:
        compiled = compile_scope_rules((self._wildcard_unknown("*.example.com"),))
        decision = evaluate_scope_candidate(
            normalize_url("https://api.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_UNKNOWN_CLASSIFICATION)
        self.assertEqual(decision.classification, ScopeClassification.UNKNOWN)

    def test_explicit_out_of_scope_denies_census(self) -> None:
        compiled = compile_scope_rules(
            (
                self._wildcard_allow("*.example.com"),
                ScopeRuleDefinition(
                    rule_id="rule-exclude-evil",
                    effect=ScopeRuleEffect.OUT_OF_SCOPE,
                    scheme="https",
                    host="evil.example.com",
                    source_reference="scope-src",
                ),
            )
        )
        decision = evaluate_scope_candidate(
            normalize_url("https://evil.example.com/app"), compiled
        )
        self.assertEqual(decision.decision, ScopeDecision.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SCOPE_DENIED)
        self.assertEqual(decision.classification, ScopeClassification.OUT_OF_SCOPE)


if __name__ == "__main__":
    unittest.main()
