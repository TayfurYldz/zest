"""The persistent browser Worker must be contained before it may create Chromium.

These tests use a recording resource controller and fake Worker scripts. They
prove ordering and fail-closed behaviour in the control plane. They do not prove
kernel enforcement.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.platform.browser_resource_control import (
    BREACH_MEMORY_MAX,
    PID_CEILING_PROCESSES,
    BrowserResourceLimits,
    BrowserResourceReadiness,
)
from zest.platform.local_process_worker import LocalProcessWorkerConfig
from zest.platform.persistent_browser_worker import (
    CLEANUP_FAILED_REASON,
    CONTAINMENT_UNAVAILABLE_REASON,
    RESOURCE_BREACH_REASON,
    PersistentBrowserWorkerAdapter,
)
from zest.platform.worker import InvocationStatus
from support.browser_worker_scripts import HANDSHAKE_PREAMBLE, descendant_script
from support.worker_requests import valid_worker_request

MECHANISM = "test.recording"


class _RecordingController:
    mechanism = MECHANISM

    def __init__(
        self,
        limits: BrowserResourceLimits,
        *,
        ready: bool = True,
        prepare_error: str | None = None,
        containment_error: str | None = None,
        breach: str | None = None,
        cleanup_error: str | None = None,
        marker: Path | None = None,
    ) -> None:
        self.limits = limits
        self._ready = ready
        self._prepare_error = prepare_error
        self._containment_error = containment_error
        self._breach = breach
        self._cleanup_error = cleanup_error
        self._marker = marker
        self.confirmed_pids: list[tuple[int | None, int]] = []
        self.killed = False
        self.cleaned = False

    def readiness(self) -> BrowserResourceReadiness:
        return BrowserResourceReadiness(
            ready=self._ready,
            mechanism=self.mechanism,
            reason=None if self._ready else "test enforcement is unavailable",
        )

    def prepare(self) -> str | None:
        return self._prepare_error

    def spawn_limits(self) -> dict[str, int | None]:
        return {
            "active_process_limit": None,
            "job_memory_limit_bytes": None,
            "process_memory_limit_bytes": None,
        }

    def pid_ceiling(self) -> tuple[str, int]:
        return PID_CEILING_PROCESSES, self.limits.max_processes

    def confirm_containment(self, supervised, worker_pid: int) -> str | None:
        self.confirmed_pids.append((supervised.process.pid, worker_pid))
        if self._marker is not None:
            with self._marker.open("a", encoding="utf-8") as handle:
                handle.write("attached\n")
        return self._containment_error

    def resource_breach(self) -> str | None:
        return self._breach

    def kill_contained(self) -> str | None:
        self.killed = True
        return None

    def cleanup(self) -> str | None:
        self.cleaned = True
        return self._cleanup_error


class BrowserContainmentHandshakeTests(unittest.TestCase):
    def _temp_file(self, suffix: str) -> Path:
        handle, raw = tempfile.mkstemp(prefix="g21-handshake-", suffix=suffix)
        os.close(handle)
        path = Path(raw)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def _adapter(self, script: str, controller: _RecordingController, *, timeout_ms: int = 1500):
        import sys

        adapter = PersistentBrowserWorkerAdapter(
            LocalProcessWorkerConfig(
                worker_id="browser-handshake-test",
                argv_override=(sys.executable, "-c", script),
                default_timeout_ms=timeout_ms,
            ),
            controller_factory=lambda limits: controller,
        )
        self.addCleanup(adapter.shutdown)
        return adapter

    def test_containment_is_established_before_the_worker_may_spawn(self) -> None:
        marker = self._temp_file(".order")
        script = HANDSHAKE_PREAMBLE + (
            f"path = {str(marker)!r}\n"
            "open(path, 'a', encoding='utf-8').write('spawned\\n')\n"
            "time.sleep(30)\n"
        )
        controller = _RecordingController(BrowserResourceLimits(), marker=marker)
        adapter = self._adapter(script, controller)
        self.assertIsNone(adapter._ensure_process())
        deadline = time.time() + 3.0
        while time.time() < deadline and "spawned" not in marker.read_text(encoding="utf-8"):
            time.sleep(0.05)
        self.assertEqual(
            marker.read_text(encoding="utf-8").split(),
            ["attached", "spawned"],
            msg="the Worker continued before containment was confirmed",
        )

    def test_the_announcing_worker_pid_is_the_one_contained(self) -> None:
        pid_path = self._temp_file(".pid")
        controller = _RecordingController(BrowserResourceLimits())
        adapter = self._adapter(descendant_script(str(pid_path)), controller)
        self.assertIsNone(adapter._ensure_process())
        self.assertEqual(len(controller.confirmed_pids), 1)
        spawned_pid, worker_pid = controller.confirmed_pids[0]
        self.assertIsNotNone(spawned_pid)
        self.assertGreater(worker_pid, 0)

    def test_unavailable_enforcement_never_starts_the_browser(self) -> None:
        controller = _RecordingController(BrowserResourceLimits(), ready=False)
        adapter = self._adapter("import time; time.sleep(30)", controller)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertIn(CONTAINMENT_UNAVAILABLE_REASON, outcome.reason or "")
        self.assertEqual(controller.confirmed_pids, [])

    def test_failed_preparation_never_starts_the_browser(self) -> None:
        controller = _RecordingController(
            BrowserResourceLimits(), prepare_error="memory.max was not applied"
        )
        adapter = self._adapter("import time; time.sleep(30)", controller)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertIn("memory.max was not applied", outcome.reason or "")

    def test_a_worker_that_does_not_announce_itself_is_killed(self) -> None:
        controller = _RecordingController(BrowserResourceLimits())
        adapter = self._adapter("import time; time.sleep(30)", controller, timeout_ms=800)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertIn("did not announce itself", outcome.reason or "")
        self.assertTrue(controller.cleaned)

    def test_containment_failure_after_announcement_stops_the_worker(self) -> None:
        controller = _RecordingController(
            BrowserResourceLimits(),
            containment_error="the browser Worker is not a member of the owned cgroup",
        )
        adapter = self._adapter(HANDSHAKE_PREAMBLE + "time.sleep(30)\n", controller)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertIn("not a member of the owned cgroup", outcome.reason or "")
        self.assertTrue(controller.cleaned)

    def test_a_resource_breach_is_classified_and_never_reported_as_success(self) -> None:
        controller = _RecordingController(BrowserResourceLimits(), breach=BREACH_MEMORY_MAX)
        adapter = self._adapter(HANDSHAKE_PREAMBLE + "time.sleep(30)\n", controller, timeout_ms=900)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.TIMED_OUT)
        self.assertIn(f"{RESOURCE_BREACH_REASON}: {BREACH_MEMORY_MAX}", outcome.reason or "")
        self.assertIsNone(outcome.worker_result)

    def test_terminal_failure_returns_drained_bounded_stderr(self) -> None:
        script = HANDSHAKE_PREAMBLE + (
            "sys.stderr.write('spawned=7\\n')\n"
            "sys.stderr.flush()\n"
            "time.sleep(30)\n"
        )
        controller = _RecordingController(BrowserResourceLimits(), breach=BREACH_MEMORY_MAX)
        adapter = self._adapter(script, controller, timeout_ms=900)
        outcome = adapter.invoke(valid_worker_request())
        self.assertEqual(outcome.invocation_status, InvocationStatus.TIMED_OUT)
        self.assertIsNone(outcome.worker_result)
        self.assertIn("spawned=7", outcome.stderr_diagnostics)

    def test_a_cleanup_failure_blocks_the_next_browser_start(self) -> None:
        controller = _RecordingController(
            BrowserResourceLimits(),
            cleanup_error="refusing to remove a cgroup that still has member processes",
        )
        adapter = self._adapter(HANDSHAKE_PREAMBLE + "time.sleep(30)\n", controller, timeout_ms=900)
        first = adapter.invoke(valid_worker_request())
        self.assertEqual(first.invocation_status, InvocationStatus.TIMED_OUT)
        self.assertIsNotNone(adapter.containment_failure_reason)
        second = adapter.invoke(valid_worker_request())
        self.assertEqual(second.invocation_status, InvocationStatus.START_FAILED)
        self.assertIn(CLEANUP_FAILED_REASON, second.reason or "")


if __name__ == "__main__":
    unittest.main()
