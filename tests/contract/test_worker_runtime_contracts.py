from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO / "tests") not in sys.path:
    sys.path.insert(0, str(REPO / "tests"))

from zest.platform.contract_validation import ContractValidator
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from zest.platform.worker import InvocationStatus

EXAMPLES = Path(__file__).resolve().parent / "examples"


class CanonicalContractRuntimeTests(unittest.TestCase):
    def test_canonical_examples_validate(self) -> None:
        validator = ContractValidator()
        request = json.loads(
            (EXAMPLES / "worker-request.valid.json").read_text(encoding="utf-8")
        )
        result = json.loads(
            (EXAMPLES / "worker-result.valid.json").read_text(encoding="utf-8")
        )
        validator.validate_worker_request(request)
        validator.validate_worker_result(result)
        self.assertTrue(validator.correlation_matches(request, result))

    def test_canonical_request_executes_through_local_worker(self) -> None:
        request = json.loads(
            (EXAMPLES / "worker-request.valid.json").read_text(encoding="utf-8")
        )
        adapter = LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=REPO / "workers" / "python",
                default_timeout_ms=5_000,
            )
        )
        outcome = adapter.invoke(request, timeout_ms=5_000)
        self.assertEqual(outcome.invocation_status, InvocationStatus.COMPLETED)
        assert outcome.worker_result is not None
        self.assertEqual(
            outcome.worker_result["raw_result"]["echoed"], "canonical-example"
        )
        self.assertEqual(
            outcome.worker_result["correlation"], request["correlation"]
        )


if __name__ == "__main__":
    unittest.main()
