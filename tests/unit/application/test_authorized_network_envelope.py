from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.authorized_network_envelope import (
    AuthorizedNetworkEnvelope,
    derive_authorized_network_envelope,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules, evaluate_scope_candidate
from zest.platform.url_normalize import normalize_url


class AuthorizedNetworkEnvelopeTests(unittest.TestCase):
    def test_wildcard_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AuthorizedNetworkEnvelope(
                normalized_scheme="http",
                normalized_host="*.example.com",
                normalized_port=80,
                document_path="/",
                origin_wide=True,
                allowed_path_prefixes=(),
                denied_path_prefixes=(),
                loopback_only=True,
                source_scope_rule_ids=("rule-allow",),
            )

    def test_deny_prefixes_remain_on_an_allowed_document(self) -> None:
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
                ScopeRuleDefinition(
                    rule_id="rule-deny",
                    effect=ScopeRuleEffect.DENY,
                    scheme="https",
                    host="example.com",
                    port=None,
                    path_prefix="/admin",
                    source_reference="scope-src",
                ),
            )
        )
        candidate = normalize_url("https://example.com/app")
        check = evaluate_scope_candidate(candidate, compiled)
        envelope = derive_authorized_network_envelope(
            candidate,
            compiled,
            check,
            loopback_only=False,
        )
        self.assertIsNotNone(envelope)
        assert envelope is not None
        self.assertTrue(envelope.origin_wide)
        self.assertEqual(envelope.document_path, "/app")
        self.assertEqual(envelope.denied_path_prefixes, ("/admin",))
        self.assertEqual(envelope.source_scope_rule_ids, ("rule-allow",))

    def test_denied_candidate_does_not_produce_an_envelope(self) -> None:
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
                ScopeRuleDefinition(
                    rule_id="rule-deny",
                    effect=ScopeRuleEffect.DENY,
                    scheme="https",
                    host="example.com",
                    port=None,
                    path_prefix="/admin",
                    source_reference="scope-src",
                ),
            )
        )
        candidate = normalize_url("https://example.com/admin")
        check = evaluate_scope_candidate(candidate, compiled)
        self.assertIsNone(
            derive_authorized_network_envelope(
                candidate,
                compiled,
                check,
                loopback_only=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
