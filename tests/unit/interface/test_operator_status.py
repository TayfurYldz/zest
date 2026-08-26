from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.operator_status import OperatorStatusSnapshot, render_operator_status
from zest.integrations.models.cli_session import CODEX_MODELS_ENV
from zest.interface.cli import build_status_snapshot
from zest.platform.argv_process import ArgvProcessResult, ArgvProcessStatus
from zest.platform.health import ComponentHealth


class StatusDatabaseSeparationTests(unittest.TestCase):
    def test_application_db_is_not_test_db(self) -> None:
        snapshot = build_status_snapshot(
            env={
                "ZEST_DATABASE_URL": "postgresql+psycopg://app:secret-pass@127.0.0.1:5432/zest",
                "ZEST_TEST_DATABASE_URL": "postgresql+psycopg://test:other-pass@127.0.0.1:55432/zest_test",
            }
        )
        self.assertNotIn("secret-pass", snapshot.application_dsn)
        self.assertNotIn("other-pass", snapshot.test_dsn)
        self.assertIn("zest_test", snapshot.test_dsn)
        self.assertNotEqual(snapshot.postgresql, snapshot.test_postgresql)
        text = render_operator_status(snapshot)
        self.assertIn("TEST_POSTGRESQL:", text)
        self.assertNotIn("secret-pass", text)
        self.assertNotIn("other-pass", text)

    def test_renderer_keeps_sections(self) -> None:
        text = render_operator_status(
            OperatorStatusSnapshot(
                postgresql=ComponentHealth.HEALTHY.value,
                test_postgresql=ComponentHealth.UNAVAILABLE.value,
                application_dsn="postgresql+psycopg://127.0.0.1/zest",
                test_dsn="postgresql+psycopg://127.0.0.1/zest_test",
                worker={"local-python": ComponentHealth.UNAVAILABLE.value},
                model_runtimes={"API": ComponentHealth.UNAVAILABLE.value},
                strix=ComponentHealth.UNAVAILABLE.value,
                auth="runtime-owned sessions only",
                orchestrator="no active run",
                budget_ledger="unknown",
                reconciliation="classifier available",
                observability="structured events",
            )
        )
        self.assertIn("POSTGRESQL:", text)
        self.assertIn("TEST_POSTGRESQL:", text)


class StatusQuotaSafetyTests(unittest.TestCase):
    def test_status_performs_zero_request_consuming_codex_calls(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv, stdin_bytes=None):
            del stdin_bytes
            calls.append(argv)
            if argv[-1] == "--version":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED, argv=argv, exit_code=0, stdout="codex 0.0.0"
                )
            if len(argv) >= 2 and argv[1] == "login":
                return ArgvProcessResult(
                    status=ArgvProcessStatus.COMPLETED, argv=argv, exit_code=0, stdout="logged in"
                )
            return ArgvProcessResult(
                status=ArgvProcessStatus.PROCESS_FAILED,
                argv=argv,
                exit_code=1,
                stderr="unexpected request-consuming call",
            )

        snapshot = build_status_snapshot(
            env={
                CODEX_MODELS_ENV: "codex-cli-terra=gpt-5.6-terra,codex-cli-gpt55=gpt-5.5",
                "ZEST_CODEX_EXECUTABLE": "codex",
            },
            argv_runner=runner,
        )
        self.assertFalse(any(len(argv) >= 2 and argv[1] == "exec" for argv in calls))
        self.assertTrue(any(argv[-1] == "--version" for argv in calls))
        self.assertEqual(snapshot.gate_04b, "PASS")
        self.assertEqual(snapshot.gate_14, "PASS")
        self.assertEqual(snapshot.gate_15, "PASS")


if __name__ == "__main__":
    unittest.main()
