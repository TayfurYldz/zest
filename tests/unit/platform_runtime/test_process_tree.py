from __future__ import annotations

import os
import sys
import time
import unittest

import pathsetup  # noqa: F401

from zest.platform.browser_resource_control import BrowserResourceReadiness
from zest.platform.process_tree import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_SUSPENDED,
    spawn_kwargs,
)


class _NoopResourceController:
    """Isolate process-tree cleanup from host kernel containment.

    This double acknowledges containment so the adapter may spawn a real Worker
    and descendant. It does not apply cgroup or Job Object resource limits.
    """

    mechanism = "test.noop"

    def readiness(self) -> BrowserResourceReadiness:
        return BrowserResourceReadiness(ready=True, mechanism=self.mechanism)

    def prepare(self) -> str | None:
        return None

    def spawn_limits(self) -> dict[str, int | None]:
        return {
            "active_process_limit": None,
            "job_memory_limit_bytes": None,
            "process_memory_limit_bytes": None,
        }

    def pid_ceiling(self) -> tuple[str, int]:
        return "processes", 8

    def confirm_containment(self, supervised, worker_pid: int) -> str | None:
        return None

    def resource_breach(self) -> str | None:
        return None

    def kill_contained(self) -> str | None:
        return None

    def cleanup(self) -> str | None:
        return None


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ProcessTreeTests(unittest.TestCase):
    def test_posix_branch_uses_new_session(self) -> None:
        kwargs = spawn_kwargs.__wrapped__ if hasattr(spawn_kwargs, "__wrapped__") else spawn_kwargs()
        if os.name == "posix":
            self.assertTrue(kwargs.get("start_new_session"))
        else:
            self.assertEqual(
                kwargs.get("creationflags"),
                CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
            )
            self.assertFalse(kwargs.get("start_new_session"))

    def test_windows_and_posix_kwargs_are_explicit(self) -> None:
        posix = {"start_new_session": True}
        windows = {
            "start_new_session": False,
            "creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
        }
        self.assertTrue(posix["start_new_session"])
        self.assertEqual(windows["creationflags"], CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED)

    def test_timeout_cleans_grandchild_when_environment_allows(self) -> None:
        from zest.platform.process_tree import run_supervised

        script = (
            "import os, sys, time, subprocess\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(child.pid)\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        result = run_supervised(
            [sys.executable, "-c", script],
            timeout_seconds=0.8,
        )
        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.exit_code)
        self.assertFalse(result.cleanup_failed, result.cleanup_reason)
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        self.assertTrue(stdout, msg="grandchild pid was not printed")
        grandchild_pid = int(stdout.splitlines()[0])
        time.sleep(0.3)
        self.assertFalse(_pid_alive(grandchild_pid), f"grandchild {grandchild_pid} still alive")

    def test_setsid_descendant_is_killed(self) -> None:
        from zest.platform.process_tree import run_supervised

        script = (
            "import os, sys, time, subprocess\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import os, time; os.setsid(); time.sleep(30)'],\n"
            "    start_new_session=True,\n"
            ")\n"
            "print(child.pid)\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        result = run_supervised(
            [sys.executable, "-c", script],
            timeout_seconds=0.8,
        )
        self.assertTrue(result.timed_out)
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        self.assertTrue(stdout, msg="setsid descendant pid was not printed")
        descendant_pid = int(stdout.splitlines()[0])
        time.sleep(0.3)
        self.assertFalse(_pid_alive(descendant_pid), f"setsid descendant {descendant_pid} still alive")

    def test_persistent_browser_worker_kills_descendants(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        from zest.platform.local_process_worker import LocalProcessWorkerConfig
        from zest.platform.persistent_browser_worker import PersistentBrowserWorkerAdapter
        from support.browser_worker_scripts import descendant_script

        handle, raw_path = tempfile.mkstemp(prefix="g21-child-", suffix=".pid")
        os.close(handle)
        pid_path = Path(raw_path)
        script = descendant_script(str(pid_path))
        adapter = PersistentBrowserWorkerAdapter(
            LocalProcessWorkerConfig(
                worker_id="browser-test",
                argv_override=(sys.executable, "-c", script),
                default_timeout_ms=800,
            ),
            controller_factory=lambda limits: _NoopResourceController(),
        )
        try:
            error = adapter._ensure_process()
            self.assertIsNone(error)
            deadline = time.time() + 2.0
            child_pid = None
            while time.time() < deadline:
                if pid_path.exists() and pid_path.stat().st_size > 0:
                    child_pid = int(pid_path.read_text(encoding="utf-8").strip())
                    break
                time.sleep(0.05)
            self.assertIsNotNone(child_pid, "browser descendant pid was not written")
            self.assertTrue(_pid_alive(child_pid))
            adapter.shutdown()
            deadline = time.time() + 1.0
            while time.time() < deadline and _pid_alive(child_pid):
                time.sleep(0.05)
            self.assertFalse(_pid_alive(child_pid), f"browser descendant {child_pid} still alive")
        finally:
            adapter.shutdown()
            try:
                pid_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
