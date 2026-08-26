from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pathsetup  # noqa: F401

from zest.benchmark.runner import run_cli

REPO = Path(__file__).resolve().parents[3]


class RunnerTests(unittest.TestCase):
    def test_scripted_runner_prints_scorecard_without_magic_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            buffer = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(err):
                code = run_cli(
                    [
                        "--baseline",
                        "GOOD_BASELINE",
                        "--scenarios",
                        str(REPO / "benchmarks" / "research" / "scenarios"),
                        "--runs-per-scenario",
                        "1",
                        "--json-report",
                        str(report_path),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("no automatic winner", buffer.getvalue())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["no_aggregate_model_score"])
            self.assertTrue(payload["no_automatic_winner"])
            self.assertTrue(payload["not_evidence"])
            self.assertNotIn("model_score", payload)
            self.assertNotIn('"WINNER"', json.dumps(payload))
            self.assertGreaterEqual(len(payload["summaries"]), 10)
            self.assertFalse(payload["authoritative_real_model_comparison"])
            self.assertTrue(payload["git_commit"])
            self.assertIn("hidden_evaluation_omitted", payload["suite"])

    def test_leakage_invariant_returns_nonzero(self) -> None:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = run_cli(["--baseline", "UNKNOWN_MODEL"])
        self.assertEqual(code, 2)
        self.assertIn("unknown scripted baseline", err.getvalue())

    def test_discover_without_composition_root_is_unavailable_not_pass(self) -> None:
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run_cli(["--discover"])
        self.assertEqual(code, 0)
        self.assertIn("UNAVAILABLE", err.getvalue())
        self.assertNotIn("GATE 04B", out.getvalue())
        self.assertNotIn('"status": "PASS"', out.getvalue())

    def test_live_probe_help_states_quota_consumption(self) -> None:
        parser_help = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(parser_help):
                run_cli(["--help"])
        text = parser_help.getvalue()
        normalized = " ".join(text.split())
        self.assertIn("--live-probe", text)
        self.assertIn("consumes model quota", normalized)
        self.assertIn("PASSIVE", text)


if __name__ == "__main__":
    unittest.main()
