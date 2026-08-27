from __future__ import annotations

import inspect
import unittest

import pathsetup  # noqa: F401

from datetime import datetime, timezone

from zest.application.identity import new_opaque_id
from zest.interface.dashboard import (
    DashboardRunControlRuntime,
    HTML,
    _bootstrap_payload,
    _operator_run_action,
    _operator_run_analysis,
    _scope_records,
    bootstrap_program,
    configure_dashboard_run_control,
    collect_dashboard_payload,
)
from zest.application.autonomous_research_controller import OrchestrationTickResult
from zest.application.autonomous_research_controller import StartAutonomousResearchCommand
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.research.orchestration import OrchestrationBounds


class DashboardTests(unittest.TestCase):
    def tearDown(self) -> None:
        configure_dashboard_run_control(None)

    def test_collect_payload_degrades_without_database(self) -> None:
        payload = collect_dashboard_payload(
            env={
                "PATH": "",
                "ZEST_CODEX_MODELS": "",
            }
        )

        self.assertIn("status", payload)
        self.assertIn("database", payload)
        self.assertEqual(payload["database"]["state"], "UNAVAILABLE")
        self.assertEqual(payload["database"]["programs"], [])
        self.assertEqual(payload["database"]["audit_events"], [])
        rendered = str(payload).lower()
        self.assertNotIn("sk-", rendered)
        self.assertNotIn("password", rendered)
        self.assertNotIn("api_key", rendered)

    def test_html_contains_dashboard_api_and_no_external_assets(self) -> None:
        self.assertIn("/api/dashboard", HTML)
        self.assertNotIn("https://", HTML)
        self.assertNotIn("http://", HTML)
        self.assertIn("Research operations cockpit", HTML)
        self.assertIn("/api/programs/bootstrap", HTML)

    def test_bootstrap_success_path_keeps_form_reference_across_await(self) -> None:
        form_reference = "const form = event.currentTarget;"
        self.assertIn(form_reference, HTML)
        self.assertIn("new FormData(form)", HTML)
        self.assertIn("form.reset();", HTML)
        self.assertNotIn("event.currentTarget.reset();", HTML)

    def test_bootstrap_submit_guard_prevents_double_submit(self) -> None:
        self.assertIn("if (submit.disabled) return;", HTML)
        self.assertIn("submit.disabled = true;", HTML)
        self.assertIn("submit.disabled = false;", HTML)

    def test_bootstrap_error_path_keeps_error_status(self) -> None:
        self.assertIn("status.textContent = `error: ${err.message}`;", HTML)

    def test_bootstrap_does_not_create_controller_orchestration(self) -> None:
        source = inspect.getsource(bootstrap_program)
        self.assertNotIn("research_orchestrations.insert", source)
        self.assertIn('"orchestration"', source)

    def test_operator_actions_delegate_to_application_control(self) -> None:
        calls: list[tuple[str, object]] = []

        class FakeControl:
            def start(self, command):
                calls.append(("start", command))
                return OrchestrationTickResult("run-1", "READY", 0, "CONTINUE", None, "start")

            def pause(self, research_run_id):
                calls.append(("pause", research_run_id))
                return OrchestrationTickResult("run-1", "PAUSED", 0, "PAUSE", "OPERATOR_PAUSED", "operator")

            def resume(self, command):
                calls.append(("resume", command))
                return OrchestrationTickResult("run-1", "READY", 0, "CONTINUE", None, "resume")

            def cancel(self, research_run_id):
                calls.append(("cancel", research_run_id))
                return OrchestrationTickResult("run-1", "COMPLETED", 0, "COMPLETE", "OPERATOR_CANCELLED", "operator")

        command = StartAutonomousResearchCommand(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            scope=ScopeEvaluationInput(
                matches=(ScopeRuleMatch("rule-1", ScopeRuleEffect.ALLOW, True, "src"),),
                ambiguous=False,
            ),
            bounds=OrchestrationBounds(
                max_cycles=1,
                max_experiments=1,
                max_model_calls=1,
                max_worker_invocations=1,
                max_elapsed_ms=1000,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
            ),
        )
        configure_dashboard_run_control(
            DashboardRunControlRuntime(FakeControl(), lambda run_id, payload: command)
        )

        self.assertEqual(_operator_run_action("start", "run-1", {})["state"], "READY")
        self.assertEqual(_operator_run_action("pause", "run-1", {})["state"], "PAUSED")
        self.assertEqual(_operator_run_action("resume", "run-1", {})["state"], "READY")
        self.assertEqual(_operator_run_action("cancel", "run-1", {})["state"], "COMPLETED")
        self.assertEqual([item[0] for item in calls], ["start", "pause", "resume", "cancel"])

    def test_hq_analysis_delegates_to_operator_client(self) -> None:
        class FakeControl:
            def get_run_analysis(self, research_run_id):
                return {
                    "schema": "hq.run.analysis.v1",
                    "research_run_id": research_run_id,
                    "projection_only": True,
                }

        configure_dashboard_run_control(
            DashboardRunControlRuntime(
                FakeControl(),
                lambda run_id, payload: None,
            )
        )

        result = _operator_run_analysis("run-1")
        self.assertEqual(result["schema"], "hq.run.analysis.v1")
        self.assertEqual(result["research_run_id"], "run-1")
        self.assertTrue(result["projection_only"])

    def test_bootstrap_payload_requires_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "in_scope"):
            _bootstrap_payload(
                {
                    "program_name": "Authorized Test",
                    "target_reference": "local-target",
                    "authorization_reference": "auth-reference",
                    "in_scope": "",
                }
            )

    def test_bootstrap_payload_deduplicates_scope_entries(self) -> None:
        payload = _bootstrap_payload(
            {
                "program_name": "Authorized Test",
                "target_reference": "https://www.example.test",
                "authorization_reference": "auth-reference",
                "in_scope": (
                    "https://www.example.test\n"
                    "https://www.example.test\n"
                    "https://*.example.test\n"
                    "https://*.example.test\n"
                ),
            }
        )

        self.assertEqual(
            payload["in_scope"],
            [
                "https://www.example.test",
                "https://*.example.test",
            ],
        )

    def test_bootstrap_payload_rejects_exact_scope_overlap(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "both in_scope and out_of_scope",
        ):
            _bootstrap_payload(
                {
                    "program_name": "Authorized Test",
                    "target_reference": "https://www.example.test",
                    "authorization_reference": "auth-reference",
                    "in_scope": "https://www.example.test",
                    "out_of_scope": "https://www.example.test",
                }
            )

    def test_bootstrap_payload_accepts_yeswehack_platform(self) -> None:
        payload = _bootstrap_payload(
            {
                "program_name": "Authorized Test",
                "platform": "yeswehack",
                "target_reference": "local-target",
                "authorization_reference": "auth-reference",
                "in_scope": "example.test",
            }
        )

        self.assertEqual(payload["platform"], "yeswehack")

    def test_bootstrap_payload_preserves_generic_required_user_agent(self) -> None:
        payload = _bootstrap_payload(
            {
                "program_name": "Authorized Test",
                "target_reference": "local-target",
                "authorization_reference": "auth-reference",
                "in_scope": "example.test",
                "required_user_agent": "-BugBounty-example-123",
            }
        )
        self.assertEqual(payload["required_user_agent"], "-BugBounty-example-123")

    def test_scope_records_parse_exact_and_wildcard_hosts(self) -> None:
        now = datetime.now(timezone.utc)
        records = _scope_records(
            {
                "in_scope": ["https://app.example.test/api", "*.example.test"],
                "out_of_scope": ["admin.example.test"],
            },
            program_id=new_opaque_id(),
            now=now,
        )

        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].host, "app.example.test")
        self.assertEqual(records[0].path_prefix, "/api")
        self.assertEqual(records[1].host_pattern, "*.example.test")
        self.assertEqual(records[2].effect, "OUT_OF_SCOPE")

    def test_scope_records_normalize_terminal_path_wildcard_to_prefix(self) -> None:
        now = datetime.now(timezone.utc)
        records = _scope_records(
            {
                "in_scope": ["https://api.example.test/broker/api/*"],
                "out_of_scope": ["https://api.example.test/private/*"],
            },
            program_id=new_opaque_id(),
            now=now,
        )

        self.assertEqual(records[0].path_prefix, "/broker/api/")
        self.assertEqual(records[1].path_prefix, "/private/")
        self.assertEqual(records[1].effect, ScopeRuleEffect.OUT_OF_SCOPE.value)

    def test_bootstrap_program_requires_database_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "ZEST_DATABASE_URL"):
            bootstrap_program(
                {
                    "program_name": "Authorized Test",
                    "target_reference": "local-target",
                    "authorization_reference": "auth-reference",
                    "in_scope": "example.test",
                },
                env={},
            )


if __name__ == "__main__":
    unittest.main()
