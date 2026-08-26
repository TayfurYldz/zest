from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    build_worker_environment,
)
from zest.platform.worker import InvocationStatus
from support.worker_requests import valid_worker_request

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "workers" / "fixture_worker.py"
WORKERS_PYTHON = REPO / "workers" / "python"


def _adapter(**config_fields) -> LocalProcessWorkerAdapter:
    values = {
        "workers_python_path": WORKERS_PYTHON,
        "max_stdout_bytes": 8_192,
        "max_stderr_bytes": 4_096,
        "default_timeout_ms": 5_000,
    }
    values.update(config_fields)
    return LocalProcessWorkerAdapter(LocalProcessWorkerConfig(**values))


def _fixture_adapter(mode: str, **config_fields) -> LocalProcessWorkerAdapter:
    return _adapter(
        argv_override=(sys.executable, str(FIXTURE), mode),
        **config_fields,
    )


class LocalProcessWorkerTests(unittest.TestCase):
    def test_diagnostic_echo_round_trip(self) -> None:
        outcome = _adapter().invoke(valid_worker_request(), timeout_ms=5_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.COMPLETED)
        self.assertIsNotNone(outcome.worker_result)
        assert outcome.worker_result is not None
        self.assertEqual(outcome.worker_result["status"], "SUCCEEDED")
        self.assertEqual(outcome.worker_result["worker_id"], "local-python-diagnostic")
        self.assertEqual(outcome.worker_result["correlation"]["correlation_id"], "corr-1")
        self.assertEqual(outcome.worker_result["raw_result"]["echoed"], "ping")
        self.assertNotIn("severity", outcome.worker_result)
        self.assertIsNone(getattr(outcome, "observation", None))

    def test_unknown_capability_is_worker_result_not_transport_failure(self) -> None:
        request = valid_worker_request()
        request["worker_capability"] = "not.a.scanner"
        request["action"] = "scan"
        outcome = _adapter().invoke(request, timeout_ms=5_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.COMPLETED)
        assert outcome.worker_result is not None
        self.assertEqual(outcome.worker_result["status"], "EXECUTION_FAILED")

    def test_start_failed_does_not_fabricate_worker_result(self) -> None:
        outcome = _adapter(
            python_executable=str(REPO / "missing-python-binary-zest")
        ).invoke(valid_worker_request(), timeout_ms=2_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertIsNone(outcome.worker_result)

    def test_malformed_request_rejected_before_spawn(self) -> None:
        marker = Path(tempfile.gettempdir()) / "zest-worker-spawned"
        if marker.exists():
            marker.unlink()
        request = valid_worker_request()
        request["unexpected"] = True
        probe = REPO / "tests" / "fixtures" / "workers" / "spawn_probe.py"
        outcome = _adapter(
            argv_override=(sys.executable, str(probe), str(marker))
        ).invoke(request, timeout_ms=2_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.CONTRACT_INVALID)
        self.assertIsNone(outcome.worker_result)
        self.assertFalse(marker.exists())

    def test_unsupported_version_rejected(self) -> None:
        outcome = _adapter().invoke(
            valid_worker_request(contract_version="v2"), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.CONTRACT_INVALID)
        self.assertIsNone(outcome.worker_result)

    def test_invalid_json_is_protocol_failure(self) -> None:
        outcome = _fixture_adapter("malformed").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROTOCOL_ERROR)
        self.assertIsNone(outcome.worker_result)

    def test_schema_invalid_result_is_contract_failure(self) -> None:
        outcome = _fixture_adapter("invalid_schema").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.CONTRACT_INVALID)
        self.assertIsNone(outcome.worker_result)

    def test_correlation_mismatch_fail_closed(self) -> None:
        outcome = _fixture_adapter("correlation_mismatch").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.CONTRACT_INVALID)
        self.assertIsNone(outcome.worker_result)
        self.assertIn("correlation mismatch", outcome.reason or "")

    def test_unknown_contract_version_on_result(self) -> None:
        outcome = _fixture_adapter("unknown_version").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.CONTRACT_INVALID)
        self.assertIsNone(outcome.worker_result)

    def test_child_crash_does_not_fabricate_worker_result(self) -> None:
        outcome = _fixture_adapter("crash").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROCESS_FAILED)
        self.assertIsNone(outcome.worker_result)
        self.assertNotEqual(outcome.exit_code, 0)

    def test_timeout_does_not_fabricate_worker_result(self) -> None:
        outcome = _fixture_adapter("delay").invoke(
            valid_worker_request(), timeout_ms=50
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.TIMED_OUT)
        self.assertIsNone(outcome.worker_result)

    def test_oversize_stdout_rejected(self) -> None:
        outcome = _fixture_adapter("oversize_stdout", max_stdout_bytes=64).invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROTOCOL_ERROR)
        self.assertIsNone(outcome.worker_result)

    def test_oversize_stderr_truncated(self) -> None:
        outcome = _fixture_adapter("oversize_stderr", max_stderr_bytes=64).invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertTrue(outcome.stderr_truncated)
        self.assertLessEqual(len(outcome.stderr_diagnostics.encode("utf-8")), 64)
        self.assertEqual(outcome.invocation_status, InvocationStatus.COMPLETED)

    def test_nonzero_exit_with_result_fail_closed(self) -> None:
        outcome = _fixture_adapter("nonzero_with_result").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROTOCOL_ERROR)
        self.assertIsNone(outcome.worker_result)

    def test_extra_stdout_is_protocol_error(self) -> None:
        outcome = _fixture_adapter("extra_stdout").invoke(
            valid_worker_request(), timeout_ms=2_000
        )
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROTOCOL_ERROR)
        self.assertIsNone(outcome.worker_result)

    def test_child_env_omits_database_url(self) -> None:
        env = build_worker_environment(WORKERS_PYTHON, "local-python-diagnostic")
        self.assertNotIn("ZEST_DATABASE_URL", env)
        self.assertNotIn("ZEST_TEST_DATABASE_URL", env)
        self.assertEqual(env["ZEST_WORKER_ID"], "local-python-diagnostic")
        self.assertIn(str(WORKERS_PYTHON), env["PYTHONPATH"])

    def test_packaged_worker_health_probe_is_healthy(self) -> None:
        from zest.platform.health import ComponentHealth
        from zest.platform.worker_health import probe_local_python_worker

        check = probe_local_python_worker()
        self.assertEqual(check.health, ComponentHealth.HEALTHY)
        self.assertFalse(check.contains_secrets)


if __name__ == "__main__":
    unittest.main()
