from __future__ import annotations

import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - environment-dependent
    sync_playwright = None

from research_os.application.autonomous_research_controller import (
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.interface import dashboard
from research_os.interface.dashboard import DashboardHandler, DashboardRunControlRuntime
from research_os.research.orchestration import OrchestrationBounds


def _command(run_id: str) -> StartAutonomousResearchCommand:
    return StartAutonomousResearchCommand(
        research_run_id=run_id,
        budget_id="budget-1",
        target_reference="http://127.0.0.1:1",
        scope=ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-1", ScopeRuleEffect.ALLOW, True, "auth"),),
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


class _FakeApplicationControl:
    def __init__(self) -> None:
        self.state = "CREATED"
        self.calls: list[tuple[str, str]] = []

    def start(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        self.calls.append(("start", command.research_run_id))
        self.state = "READY"
        return self._result("CONTINUE")

    def pause(self, research_run_id: str) -> OrchestrationTickResult:
        self.calls.append(("pause", research_run_id))
        self.state = "PAUSED"
        return self._result("PAUSE")

    def resume(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        self.calls.append(("resume", command.research_run_id))
        self.state = "READY"
        return self._result("CONTINUE")

    def cancel(self, research_run_id: str) -> OrchestrationTickResult:
        self.calls.append(("cancel", research_run_id))
        self.state = "COMPLETED"
        return self._result("COMPLETE")

    def _result(self, outcome: str) -> OrchestrationTickResult:
        return OrchestrationTickResult(
            research_run_id="run/1",
            state=self.state,
            cycle_number=0,
            outcome=outcome,
            stop_reason=None,
            last_phase="operator",
        )


@unittest.skipUnless(sync_playwright is not None, "Playwright is not installed")
class DashboardOperatorControlsBrowserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.control = _FakeApplicationControl()
        dashboard.configure_dashboard_run_control(
            DashboardRunControlRuntime(
                control=self.control,
                command_factory=lambda run_id, payload: _command(run_id),
            )
        )

        def payload() -> dict[str, object]:
            state = None if self.control.state == "CREATED" else self.control.state
            return {
                "generated_at": "2026-08-20T00:00:00+00:00",
                "status": {},
                "database": {
                    "state": "HEALTHY",
                    "summary": {"research_runs": 1},
                    "runs": [
                        {
                            "research_run_id": "run/1",
                            "program_id": "program-1",
                            "state": state,
                            "current_phase": "CYCLE_READY",
                            "started_at": "2026-08-20T00:00:00+00:00",
                        }
                    ],
                    "programs": [],
                    "run_details": [],
                    "audit_events": [],
                    "coverage": [],
                    "queue": {},
                },
                "git": {},
                "oast": {},
            }

        self.bootstrap = lambda payload: {
            "program_id": "program-1",
            "research_run_id": "run/1",
            "state": "STARTABLE",
        }
        self._payload_patcher = mock.patch.object(
            dashboard, "collect_dashboard_payload", side_effect=payload
        )
        self._bootstrap_patcher = mock.patch.object(
            dashboard, "bootstrap_program", side_effect=self.bootstrap
        )
        self._payload_patcher.start()
        self._bootstrap_patcher.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self._bootstrap_patcher.stop()
        self._payload_patcher.stop()
        dashboard.configure_dashboard_run_control(None)

    def test_bootstrap_and_full_run_lifecycle_controls(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(self.url, wait_until="domcontentloaded")
                page.get_by_role("button", name="Program Setup", exact=True).click()
                page.locator("#programName").fill("Local Program")
                page.locator("#targetReference").fill("http://127.0.0.1:1")

                # Dashboard projection refresh must not destroy an
                # operator's in-progress Program Setup draft.
                page.locator("#refreshButton").click()
                page.locator("#programName").wait_for(state="visible")
                self.assertEqual(
                    page.locator("#programName").input_value(),
                    "Local Program",
                )
                self.assertEqual(
                    page.locator("#targetReference").input_value(),
                    "http://127.0.0.1:1",
                )

                page.locator("#authorizationReference").fill("local-auth")
                page.locator("#inScope").fill(
                    "http://127.0.0.1:1\\n"
                    "http://127.0.0.1:1"
                )
                page.locator("#outOfScope").fill("http://127.0.0.1:2/private/*")
                page.locator("#maxRequests").fill("25")
                page.locator("#sideEffectCeiling").select_option("0")

                page.get_by_role("button", name="Review bounded run").click()
                page.locator("#bootstrapReview").wait_for(state="visible")
                page.get_by_text("IN SCOPE · 1").wait_for(state="visible")

                # Review state is also operator state and must survive
                # projection refresh before explicit confirmation.
                page.locator("#refreshButton").click()
                page.locator("#bootstrapReview").wait_for(state="visible")
                page.get_by_text("IN SCOPE · 1").wait_for(state="visible")

                self.assertEqual(
                    page.locator("#programName").input_value(),
                    "Local Program",
                )
                self.assertEqual(
                    page.locator("#maxRequests").input_value(),
                    "25",
                )

                page.get_by_role(
                    "button",
                    name="Confirm & Create Ready Run",
                ).click()
                page.get_by_text("ready: run/1").wait_for(state="visible")

                page.get_by_role("button", name="Experiment Control", exact=True).click()

                start = page.locator('#runs button[data-run-action="start"]')
                start.wait_for(state="visible")
                start.dblclick()
                page.locator('#runs button[data-run-action="pause"]').wait_for(state="visible")
                self.assertEqual(self.control.calls[-1], ("start", "run/1"))

                page.locator('#runs button[data-run-action="pause"]').click()
                page.locator('#runs button[data-run-action="resume"]').wait_for(state="visible")
                self.assertEqual(self.control.calls[-1], ("pause", "run/1"))

                page.locator('#runs button[data-run-action="resume"]').click()
                page.locator('#runs button[data-run-action="cancel"]').wait_for(state="visible")
                self.assertEqual(self.control.calls[-1], ("resume", "run/1"))

                page.locator('#runs button[data-run-action="cancel"]').click()
                page.get_by_text("Terminal: COMPLETED").wait_for(state="visible")
                self.assertEqual(self.control.calls, [
                    ("start", "run/1"),
                    ("pause", "run/1"),
                    ("resume", "run/1"),
                    ("cancel", "run/1"),
                ])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
