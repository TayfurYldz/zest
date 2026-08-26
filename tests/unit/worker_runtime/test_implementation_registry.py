from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.worker_runtime.python.capabilities import execute
from zest.worker_runtime.python.implementation import IMPLEMENTATION_EXECUTORS
from zest.worker_runtime.python.packaged_registry import load_packaged_capabilities
from zest.tools.registry import load_capability_registry


def _echo_request(**overrides):
    registry = load_capability_registry()
    echo = registry.get("diagnostic.echo")
    assert echo is not None
    request = {
        "worker_capability": "diagnostic.echo",
        "action": "echo",
        "capability_version": echo.version,
        "capability_definition_fingerprint": echo.definition_fingerprint,
        "arguments": {"message": "ping"},
    }
    request.update(overrides)
    return request


class WorkerImplementationRegistryTests(unittest.TestCase):
    def test_unknown_capability_fails_closed(self) -> None:
        status, _, diagnostics = execute(_echo_request(worker_capability="nuclei.scan"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "UNKNOWN_CAPABILITY")

    def test_unknown_action_fails_closed(self) -> None:
        status, _, diagnostics = execute(_echo_request(action="scan"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "UNKNOWN_ACTION")

    def test_wrong_version_fails_closed(self) -> None:
        status, _, diagnostics = execute(_echo_request(capability_version="9"))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "UNSUPPORTED_CAPABILITY_VERSION")

    def test_wrong_fingerprint_fails_closed(self) -> None:
        status, _, diagnostics = execute(
            _echo_request(capability_definition_fingerprint="b" * 64)
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "DEFINITION_FINGERPRINT_MISMATCH")

    def test_malformed_arguments_fail_closed(self) -> None:
        status, _, diagnostics = execute(_echo_request(arguments={"message": 1}))
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "SCHEMA_MISMATCH")

    def test_worker_executors_cover_worker_definitions(self) -> None:
        catalog = load_packaged_capabilities()
        for definition in catalog.values():
            if definition.executor_class != "WORKER":
                continue
            self.assertIn(definition.implementation_reference, IMPLEMENTATION_EXECUTORS)

    def test_strix_is_not_in_worker_packaged_registry(self) -> None:
        catalog = load_packaged_capabilities()
        self.assertNotIn("strix.diagnostic.ping", catalog)
        self.assertNotIn("codex.diagnostic.structured_output", catalog)
        status, _, diagnostics = execute(
            _echo_request(worker_capability="strix.diagnostic.ping", action="ping")
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "UNKNOWN_CAPABILITY")
        status, _, diagnostics = execute(
            _echo_request(
                worker_capability="codex.diagnostic.structured_output", action="emit"
            )
        )
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(diagnostics["reason_code"], "UNKNOWN_CAPABILITY")

    def test_no_dynamic_import_in_dispatch(self) -> None:
        root = Path(__file__).resolve().parents[3] / "src" / "zest" / "worker_runtime" / "python"
        for name in ("capabilities.py", "implementation.py", "packaged_registry.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("importlib", text)
            self.assertNotIn("eval(", text)
            self.assertNotIn("__import__", text)


if __name__ == "__main__":
    unittest.main()
