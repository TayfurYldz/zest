from __future__ import annotations

import unittest
from types import SimpleNamespace

from zest.application.discovery.reauthorization_followup import (
    compile_allowed_same_origin_redirect_frontier,
)
from zest.core.enums import ReasonCode, ScopeClassification, ScopeDecision
from zest.core.scope import ScopeCheck


class RedirectReauthorizationFollowupTests(unittest.TestCase):
    def _record(self):
        return SimpleNamespace(
            research_run_id="run-1",
            identity_id="anonymous",
            session_context_id=None,
            strategy_version="surface.discovery.v1",
        )

    def _allow(self) -> ScopeCheck:
        return ScopeCheck(
            ScopeDecision.ALLOW,
            ReasonCode.ALLOWED,
            ("rule-1",),
            ScopeClassification.IN_SCOPE,
        )

    def _request(self, target: str, method: str = "GET") -> dict[str, object]:
        return {
            "proposed_target_reference": target,
            "discovery_context": {"proposed_method": method},
        }

    def test_dyson_same_origin_get_compiles_fresh_read_only_frontier(self) -> None:
        item = compile_allowed_same_origin_redirect_frontier(
            self._request(
                "https://www.dyson.tw/cdn-cgi/challenge-platform/h/g/scripts/jsd/330e41bb475c/main.js"
            ),
            self._allow(),
            record=self._record(),
            normalized_origin="https://www.dyson.tw",
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.candidate_origin, "https://www.dyson.tw")
        self.assertEqual(
            item.candidate_path,
            "/cdn-cgi/challenge-platform/h/g/scripts/jsd/330e41bb475c/main.js",
        )
        self.assertEqual(item.proposed_capability, "http.transaction")
        self.assertEqual(item.proposed_action, "read")
        self.assertEqual(item.expected_side_effect, 0)
        self.assertEqual(item.budget_class, 0)
        self.assertEqual(item.scope_hint, "core_reauthorized_redirect")
        self.assertEqual(item.attributes["method"], "GET")
        self.assertFalse(item.attributes["auto_replay"])

    def test_cross_origin_remains_human_gated_even_when_core_check_is_allow(self) -> None:
        item = compile_allowed_same_origin_redirect_frontier(
            self._request("https://shop.dyson.tw/redirected"),
            self._allow(),
            record=self._record(),
            normalized_origin="https://www.dyson.tw",
        )
        self.assertIsNone(item)

    def test_mutating_redirect_remains_human_gated(self) -> None:
        item = compile_allowed_same_origin_redirect_frontier(
            self._request("https://www.dyson.tw/redirected", method="POST"),
            self._allow(),
            record=self._record(),
            normalized_origin="https://www.dyson.tw",
        )
        self.assertIsNone(item)

    def test_query_bearing_redirect_remains_human_gated(self) -> None:
        item = compile_allowed_same_origin_redirect_frontier(
            self._request("https://www.dyson.tw/redirected?state=1"),
            self._allow(),
            record=self._record(),
            normalized_origin="https://www.dyson.tw",
        )
        self.assertIsNone(item)

    def test_non_allow_scope_decision_never_compiles_followup(self) -> None:
        review = ScopeCheck(
            ScopeDecision.REQUIRE_HUMAN_REVIEW,
            ReasonCode.SCOPE_AMBIGUOUS,
            ("rule-1",),
            ScopeClassification.OUT_OF_SCOPE,
        )
        item = compile_allowed_same_origin_redirect_frontier(
            self._request("https://www.dyson.tw/redirected"),
            review,
            record=self._record(),
            normalized_origin="https://www.dyson.tw",
        )
        self.assertIsNone(item)


if __name__ == "__main__":
    unittest.main()
