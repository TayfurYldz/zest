from __future__ import annotations

import os
import sys
import unittest

import pathsetup  # noqa: F401

from zest.platform.argv_process import (
    ArgvProcessStatus,
    build_cli_environment,
    run_argv,
)


class ArgvProcessTests(unittest.TestCase):
    def test_missing_executable_is_unavailable(self) -> None:
        result = run_argv(("zest-missing-cli-runtime-xyz", "--version"), timeout_ms=1_000)
        self.assertEqual(result.status, ArgvProcessStatus.UNAVAILABLE)
        self.assertIsNone(result.exit_code)

    def test_timeout_is_timed_out(self) -> None:
        result = run_argv(
            (sys.executable, "-c", "import time; time.sleep(5)"),
            timeout_ms=200,
        )
        self.assertEqual(result.status, ArgvProcessStatus.TIMED_OUT)

    def test_cli_environment_does_not_pass_secrets(self) -> None:
        env = build_cli_environment(
            (
                ("OPENAI_API_KEY", "sk-secret"),
                ("ANTHROPIC_API_KEY", "sk-other"),
                ("DATABASE_URL", "postgresql://zest@127.0.0.1/db"),
                ("ZEST_TEST_DATABASE_URL", "postgresql://x"),
                ("SAFE_FLAG", "ok"),
            )
        )
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("DATABASE_URL", env)
        self.assertNotIn("ZEST_TEST_DATABASE_URL", env)
        self.assertEqual(env.get("SAFE_FLAG"), "ok")
        inherited = os.environ.get("PATH")
        if inherited:
            self.assertEqual(env.get("PATH"), inherited)


if __name__ == "__main__":
    unittest.main()
