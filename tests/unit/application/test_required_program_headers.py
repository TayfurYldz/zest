from __future__ import annotations

from types import SimpleNamespace
import unittest

import pathsetup  # noqa: F401

from zest.application.execute_planned_experiment import (
    _build_worker_request,
)
from zest.interface.dashboard import _bootstrap_payload
from zest.worker_runtime.python.browser_page import (
    _binding_from_request,
)


class RequiredProgramHeaderTests(unittest.TestCase):
    def _builder(self, *, plan_headers, required_headers):
        experiment = SimpleNamespace(
            research_run_id="run-1",
            experiment_id="experiment-1",
        )
        plan = SimpleNamespace(
            arguments={"headers": dict(plan_headers)},
            required_capability="http.transaction",
            action="read",
            target_reference="https://example.test",
            side_effect_level=0,
        )
        capability = SimpleNamespace(
            capability_version="1",
            definition_fingerprint="fingerprint",
        )
        issued = SimpleNamespace(
            budget_id="budget-1",
            max_requests=10,
            max_tool_calls=10,
            max_runtime_ms=1000,
            max_concurrency=1,
        )
        return _build_worker_request(
            experiment=experiment,
            plan=plan,
            capability_view=capability,
            issued=issued,
            request_id="request-1",
            correlation_id="correlation-1",
            authorization_decision_reference="decision-1",
            required_user_agent="Zest-Research",
            required_headers=required_headers,
        )

    def test_dashboard_parses_generic_identification_header(self):
        payload = _bootstrap_payload(
            {
                "program_name": "Authorized Test",
                "target_reference": "https://example.test",
                "authorization_reference": "program-policy",
                "in_scope": "https://example.test",
                "required_headers": (
                    "X-PP-BB: HackerOne-researcher\n"
                    "X-Research-Program: field"
                ),
            }
        )

        self.assertEqual(
            payload["required_headers"],
            {
                "X-PP-BB": "HackerOne-researcher",
                "X-Research-Program": "field",
            },
        )

    def test_dashboard_rejects_sensitive_required_header(self):
        with self.assertRaisesRegex(ValueError, "not permitted"):
            _bootstrap_payload(
                {
                    "program_name": "Authorized Test",
                    "target_reference": "https://example.test",
                    "authorization_reference": "program-policy",
                    "in_scope": "https://example.test",
                    "required_headers": "Authorization: Bearer nope",
                }
            )

    def test_policy_header_wins_case_insensitively(self):
        request = self._builder(
            plan_headers={
                "x-pp-bb": "MODEL_MUST_NOT_WIN",
                "X-Other": "allowed",
            },
            required_headers={
                "X-PP-BB": "HackerOne-researcher",
            },
        )

        headers = request["arguments"]["headers"]

        pp = [
            (name, value)
            for name, value in headers.items()
            if name.lower() == "x-pp-bb"
        ]

        self.assertEqual(
            pp,
            [("X-PP-BB", "HackerOne-researcher")],
        )

        ua = [
            (name, value)
            for name, value in headers.items()
            if name.lower() == "user-agent"
        ]

        self.assertEqual(
            ua,
            [("User-Agent", "Zest-Research")],
        )

    def test_request_builder_rejects_sensitive_policy_header(self):
        with self.assertRaisesRegex(
            ValueError,
            "required_headers policy is invalid",
        ):
            self._builder(
                plan_headers={},
                required_headers={"Cookie": "session=bad"},
            )

    def test_browser_binding_carries_non_user_agent_headers(self):
        request = self._builder(
            plan_headers={},
            required_headers={
                "X-PP-BB": "HackerOne-researcher",
            },
        )

        binding = _binding_from_request(
            request,
            "https://example.test",
        )

        self.assertEqual(
            binding["required_headers"],
            {"X-PP-BB": "HackerOne-researcher"},
        )
        self.assertEqual(
            binding["required_user_agent"],
            "Zest-Research",
        )


if __name__ == "__main__":
    unittest.main()
