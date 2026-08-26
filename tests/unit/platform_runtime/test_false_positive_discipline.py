from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.data.records import ObservationRecord
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from support.worker_requests import valid_worker_request

REPO = Path(__file__).resolve().parents[3]
WORKERS_PYTHON = REPO / "workers" / "python"
FIXTURE = REPO / "tests" / "fixtures" / "workers" / "fixture_worker.py"


class FalsePositiveDisciplineTests(unittest.TestCase):
    def test_process_success_does_not_create_observation(self) -> None:
        adapter = LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(workers_python_path=WORKERS_PYTHON)
        )
        outcome = adapter.invoke(valid_worker_request(), timeout_ms=5_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.COMPLETED)
        self.assertIsInstance(outcome, WorkerInvocationOutcome)
        self.assertNotIsInstance(outcome, ObservationRecord)
        self.assertFalse(hasattr(outcome, "evidence"))
        self.assertFalse(hasattr(outcome, "finding"))
        assert outcome.worker_result is not None
        self.assertNotIn("observation", outcome.worker_result)
        self.assertNotIn("evidence", outcome.worker_result)
        self.assertNotIn("finding", outcome.worker_result)
        self.assertNotIn("candidate", outcome.worker_result)

    def test_malformed_output_cannot_enter_downstream_pipeline(self) -> None:
        adapter = LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=WORKERS_PYTHON,
                argv_override=(sys.executable, str(FIXTURE), "malformed"),
            )
        )
        outcome = adapter.invoke(valid_worker_request(), timeout_ms=2_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.PROTOCOL_ERROR)
        self.assertIsNone(outcome.worker_result)


if __name__ == "__main__":
    unittest.main()
