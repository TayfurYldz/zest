"""The Worker refuses to create Chromium until containment is acknowledged."""

from __future__ import annotations

import io
import json
import unittest

import pathsetup  # noqa: F401

from zest.worker_runtime.python.browser_containment import (
    BROWSER_WORKER_PROTOCOL,
    CONTAINMENT_MESSAGE_TYPE,
    CONTAINMENT_NOT_ESTABLISHED,
    PID_CEILING_PROCESSES,
    PID_CEILING_TASKS,
    ContainmentAck,
    accept_containment,
    containment,
    hello_document,
    reset_containment,
    set_containment,
)
from zest.worker_runtime.python.browser_engine import BrowserRuntimeLimits
from zest.worker_runtime.python.browser_page import execute_browser_page
from zest.worker_runtime.python.persistent_runtime import run_persistent
from support.worker_requests import valid_worker_request

LIMITS = BrowserRuntimeLimits()


def _ack(**overrides) -> dict[str, object]:
    message: dict[str, object] = {
        "message_type": CONTAINMENT_MESSAGE_TYPE,
        "protocol": BROWSER_WORKER_PROTOCOL,
        "mechanism": "linux.cgroup2",
        "max_memory_bytes": LIMITS.max_memory_bytes,
        "pid_ceiling_kind": PID_CEILING_TASKS,
        "pid_ceiling_value": LIMITS.max_descendant_tasks,
    }
    message.update(overrides)
    return message


def _accept(message):
    return accept_containment(
        message,
        max_memory_bytes=LIMITS.max_memory_bytes,
        max_descendant_processes=LIMITS.max_descendant_processes,
        max_descendant_tasks=LIMITS.max_descendant_tasks,
    )


class ContainmentAcknowledgementTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_containment()
        self.addCleanup(reset_containment)

    def test_hello_announces_the_worker_pid_and_protocol(self) -> None:
        document = hello_document(4242)
        self.assertEqual(document["pid"], 4242)
        self.assertEqual(document["protocol"], BROWSER_WORKER_PROTOCOL)
        self.assertEqual(document["protocol"], "browser.worker.v2")

    def test_a_complete_linux_acknowledgement_is_accepted(self) -> None:
        ack, error = _accept(_ack())
        self.assertIsNone(error)
        assert ack is not None
        self.assertEqual(ack.mechanism, "linux.cgroup2")
        self.assertEqual(ack.max_memory_bytes, LIMITS.max_memory_bytes)
        self.assertEqual(ack.pid_ceiling_kind, PID_CEILING_TASKS)
        self.assertEqual(ack.pid_ceiling_value, LIMITS.max_descendant_tasks)

    def test_a_complete_windows_acknowledgement_is_accepted(self) -> None:
        ack, error = _accept(
            _ack(
                mechanism="windows.jobobject",
                pid_ceiling_kind=PID_CEILING_PROCESSES,
                pid_ceiling_value=LIMITS.max_descendant_processes,
            )
        )
        self.assertIsNone(error)
        assert ack is not None
        self.assertEqual(ack.pid_ceiling_kind, PID_CEILING_PROCESSES)
        self.assertEqual(ack.pid_ceiling_value, LIMITS.max_descendant_processes)

    def test_a_tighter_ceiling_is_accepted(self) -> None:
        ack, error = _accept(_ack(max_memory_bytes=64 * 1024 * 1024, pid_ceiling_value=4))
        self.assertIsNone(error)
        assert ack is not None
        self.assertEqual(ack.pid_ceiling_value, 4)

    def test_a_wider_memory_ceiling_is_rejected(self) -> None:
        ack, error = _accept(_ack(max_memory_bytes=LIMITS.max_memory_bytes * 2))
        self.assertIsNone(ack)
        self.assertIn("wider than the declared limit", error or "")

    def test_a_wider_task_ceiling_is_rejected(self) -> None:
        ack, error = _accept(_ack(pid_ceiling_value=LIMITS.max_descendant_tasks + 1))
        self.assertIsNone(ack)
        self.assertIn("wider than the declared limit", error or "")

    def test_a_wider_process_ceiling_is_rejected(self) -> None:
        ack, error = _accept(
            _ack(
                mechanism="windows.jobobject",
                pid_ceiling_kind=PID_CEILING_PROCESSES,
                pid_ceiling_value=LIMITS.max_descendant_processes + 1,
            )
        )
        self.assertIsNone(ack)
        self.assertIn("wider than the declared limit", error or "")

    def test_linux_process_kind_is_rejected(self) -> None:
        ack, error = _accept(
            _ack(
                mechanism="linux.cgroup2",
                pid_ceiling_kind=PID_CEILING_PROCESSES,
                pid_ceiling_value=LIMITS.max_descendant_processes,
            )
        )
        self.assertIsNone(ack)
        self.assertIn("do not match", error or "")

    def test_windows_task_kind_is_rejected(self) -> None:
        ack, error = _accept(
            _ack(
                mechanism="windows.jobobject",
                pid_ceiling_kind=PID_CEILING_TASKS,
                pid_ceiling_value=LIMITS.max_descendant_tasks,
            )
        )
        self.assertIsNone(ack)
        self.assertIn("do not match", error or "")

    def test_a_missing_mechanism_is_rejected(self) -> None:
        ack, error = _accept(_ack(mechanism=""))
        self.assertIsNone(ack)
        self.assertIn("mechanism", error or "")

    def test_an_unknown_mechanism_is_rejected(self) -> None:
        ack, error = _accept(_ack(mechanism="test.recording"))
        self.assertIsNone(ack)
        self.assertIn("unknown enforcement mechanism", error or "")

    def test_an_absent_memory_ceiling_is_rejected(self) -> None:
        message = _ack()
        del message["max_memory_bytes"]
        ack, error = _accept(message)
        self.assertIsNone(ack)
        self.assertIn("memory ceiling", error or "")

    def test_an_absent_pid_ceiling_is_rejected(self) -> None:
        message = _ack()
        del message["pid_ceiling_value"]
        ack, error = _accept(message)
        self.assertIsNone(ack)
        self.assertIn("pid ceiling", error or "")

    def test_an_unknown_pid_kind_is_rejected(self) -> None:
        ack, error = _accept(_ack(pid_ceiling_kind="threads"))
        self.assertIsNone(ack)
        self.assertIn("unknown pid ceiling kind", error or "")

    def test_a_boolean_ceiling_is_rejected(self) -> None:
        ack, error = _accept(_ack(pid_ceiling_value=True))
        self.assertIsNone(ack)
        self.assertIn("pid ceiling", error or "")

    def test_a_nonpositive_ceiling_is_rejected(self) -> None:
        ack, error = _accept(_ack(pid_ceiling_value=0))
        self.assertIsNone(ack)
        self.assertIn("pid ceiling", error or "")

    def test_another_message_type_is_rejected(self) -> None:
        ack, error = _accept(_ack(message_type="shutdown"))
        self.assertIsNone(ack)
        self.assertIn("first message", error or "")

    def test_a_protocol_mismatch_is_rejected(self) -> None:
        ack, error = _accept(_ack(protocol="browser.worker.v1"))
        self.assertIsNone(ack)
        self.assertIn("protocol", error or "")


class EngineCreationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_containment()
        self.addCleanup(reset_containment)

    def _request(self) -> dict[str, object]:
        return valid_worker_request(
            worker_capability="browser.page",
            action="navigate",
            arguments={"authorized_origin": "http://127.0.0.1:9", "path": "/"},
            network_envelope={
                "normalized_scheme": "http",
                "normalized_host": "127.0.0.1",
                "normalized_port": 9,
                "document_path": "/",
                "origin_wide": True,
                "allowed_path_prefixes": ["/"],
                "denied_path_prefixes": [],
                "loopback_only": True,
                "source_scope_rule_ids": ["rule-allow"],
                "authorization_decision_reference": "authz-1",
            },
            max_attempted_requests=16,
        )

    def test_no_engine_is_created_before_containment(self) -> None:
        status, raw, diagnostics = execute_browser_page(self._request())
        self.assertEqual(status, "EXECUTION_FAILED")
        self.assertEqual(raw, {})
        assert diagnostics is not None
        self.assertEqual(diagnostics["reason_code"], CONTAINMENT_NOT_ESTABLISHED)

    def test_the_gate_reports_containment_state(self) -> None:
        self.assertIsNone(containment())
        set_containment(
            ContainmentAck(
                mechanism="windows.jobobject",
                max_memory_bytes=LIMITS.max_memory_bytes,
                pid_ceiling_kind=PID_CEILING_PROCESSES,
                pid_ceiling_value=LIMITS.max_descendant_processes,
            )
        )
        ack = containment()
        assert ack is not None
        self.assertEqual(ack.mechanism, "windows.jobobject")


class PersistentRuntimeHandshakeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_containment()
        self.addCleanup(reset_containment)

    def test_the_loop_announces_itself_and_stores_the_acknowledgement(self) -> None:
        stdin = io.StringIO(json.dumps(_ack()) + "\n" + '{"message_type":"shutdown"}\n')
        stdout = io.StringIO()
        stderr = io.StringIO()
        self.assertEqual(run_persistent(stdin, stdout, stderr), 0)
        hello = json.loads(stdout.getvalue().splitlines()[0])
        self.assertEqual(hello["message_type"], "hello")
        ack = containment()
        assert ack is not None
        self.assertEqual(ack.pid_ceiling_kind, PID_CEILING_TASKS)
        self.assertEqual(ack.pid_ceiling_value, LIMITS.max_descendant_tasks)

    def test_the_loop_refuses_to_run_without_an_acknowledgement(self) -> None:
        request = json.dumps(valid_worker_request())
        stdin = io.StringIO(request + "\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        self.assertEqual(run_persistent(stdin, stdout, stderr), 1)
        self.assertIn("containment handshake failed", stderr.getvalue())
        self.assertIsNone(containment())
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)

    def test_the_loop_refuses_a_widened_task_ceiling(self) -> None:
        stdin = io.StringIO(json.dumps(_ack(pid_ceiling_value=1024)) + "\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        self.assertEqual(run_persistent(stdin, stdout, stderr), 1)
        self.assertIn("wider than the declared limit", stderr.getvalue())
        self.assertIsNone(containment())

    def test_a_closed_stdin_before_the_acknowledgement_fails_closed(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        self.assertEqual(run_persistent(io.StringIO(""), stdout, stderr), 1)
        self.assertIn("closed stdin", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
