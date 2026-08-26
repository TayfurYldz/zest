"""Browser resource containment unit tests.

These tests cover detection, control-file writes, classification, and cleanup
logic against a controlled cgroup filesystem double. They do NOT prove kernel
enforcement. Kernel enforcement is proved only by the real Linux integration
test in tests/e2e/test_gate21_linux_cgroup.py.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.platform.browser_resource_control import (
    BREACH_MEMORY_MAX,
    BREACH_PIDS_MAX,
    CGROUP_ROOT_ENV,
    MECHANISM_LINUX_CGROUP_V2,
    MECHANISM_WINDOWS_JOB_OBJECT,
    PID_CEILING_PROCESSES,
    PID_CEILING_TASKS,
    BrowserResourceLimits,
    LinuxCgroupV2ResourceController,
    UnsupportedPlatformResourceController,
    WindowsJobObjectResourceController,
    browser_resource_controller,
)

CHILD_CONTROL_FILES = (
    "cgroup.procs",
    "cgroup.kill",
    "memory.max",
    "pids.max",
    "memory.events",
    "pids.events",
    "memory.oom.group",
    "memory.swap.max",
)
LIMITS = BrowserResourceLimits(max_memory_bytes=134_217_728, max_processes=8, max_tasks=8)
CHILD_NAME = "zest-browser-4242-abcd1234"


def _kernel_child(path: Path) -> None:
    """Stand in for the kernel populating a new cgroup v2 directory."""

    path.mkdir()
    for name in CHILD_CONTROL_FILES:
        (path / name).write_text("" if name.endswith(".procs") else "max\n", encoding="utf-8")
    (path / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n", encoding="utf-8")
    (path / "pids.events").write_text("max 0\n", encoding="utf-8")


def _kernel_remove(path: Path) -> None:
    """The kernel drops the virtual control files when a cgroup is destroyed."""

    shutil.rmtree(path)


class _CgroupTreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="g21-cgroup-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "cgroup.controllers").write_text("cpu memory pids\n", encoding="utf-8")
        self.base = self.root / "delegated.scope"
        self.base.mkdir()
        self._write_base("cgroup.controllers", "cpu memory pids\n")
        self._write_base("cgroup.subtree_control", "memory pids\n")
        self._write_base("cgroup.procs", "4242\n")

    def _write_base(self, name: str, value: str) -> None:
        (self.base / name).write_text(value, encoding="utf-8")

    def _controller(self, **overrides) -> LinuxCgroupV2ResourceController:
        options: dict[str, object] = {
            "cgroup_root": self.root,
            "self_cgroup_reader": lambda: "/delegated.scope",
            "child_factory": _kernel_child,
            "platform_is_linux": True,
            "child_name": CHILD_NAME,
            "root_override": "",
            "empty_wait_seconds": 0.05,
            "tree_reader": lambda root: {root, 9191},
            "child_remover": _kernel_remove,
        }
        options.update(overrides)
        return LinuxCgroupV2ResourceController(LIMITS, **options)  # type: ignore[arg-type]


class CgroupReadinessTests(_CgroupTreeCase):
    def test_ready_when_v2_controllers_and_delegation_are_present(self) -> None:
        state = self._controller().readiness()
        self.assertTrue(state.ready, state.reason)
        self.assertEqual(state.mechanism, MECHANISM_LINUX_CGROUP_V2)
        self.assertIn("memory.max=134217728", state.detail or "")
        self.assertIn("pids.max=8", state.detail or "")

    def test_not_ready_when_cgroup_v2_is_inactive(self) -> None:
        (self.root / "cgroup.controllers").unlink()
        state = self._controller().readiness()
        self.assertFalse(state.ready)
        self.assertEqual(state.reason, "cgroup v2 is not active")

    def test_not_ready_when_host_is_not_linux(self) -> None:
        state = self._controller(platform_is_linux=False).readiness()
        self.assertFalse(state.ready)
        self.assertEqual(state.reason, "host is not Linux")

    def test_not_ready_without_memory_controller(self) -> None:
        self._write_base("cgroup.controllers", "cpu pids\n")
        state = self._controller().readiness()
        self.assertFalse(state.ready)
        self.assertIn("memory controller is not available", state.reason or "")

    def test_not_ready_without_pids_controller(self) -> None:
        self._write_base("cgroup.controllers", "cpu memory\n")
        state = self._controller().readiness()
        self.assertFalse(state.ready)
        self.assertIn("pids controller is not available", state.reason or "")

    def test_not_ready_when_subtree_is_not_writable(self) -> None:
        state = self._controller(access=lambda path, mode: False).readiness()
        self.assertFalse(state.ready)
        self.assertIn("not writable", state.reason or "")

    def test_not_ready_when_controllers_cannot_be_enabled_due_to_member_processes(self) -> None:
        self._write_base("cgroup.subtree_control", "cpu\n")
        state = self._controller().readiness()
        self.assertFalse(state.ready)
        self.assertIn("delegated cgroup subtree", state.reason or "")

    def test_root_override_must_stay_inside_the_cgroup_root(self) -> None:
        outside = Path(self._tmp.name).parent / "elsewhere"
        controller = self._controller(root_override=str(outside))
        base, reason = controller.base_cgroup()
        self.assertIsNone(base)
        self.assertIn(CGROUP_ROOT_ENV, reason or "")

    def test_root_override_must_be_absolute(self) -> None:
        controller = self._controller(root_override="relative/path")
        base, reason = controller.base_cgroup()
        self.assertIsNone(base)
        self.assertIn("absolute", reason or "")


class CgroupPrepareTests(_CgroupTreeCase):
    def test_prepare_writes_hard_limits_into_the_owned_child(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        self.assertEqual(child, self.base / CHILD_NAME)
        self.assertEqual((child / "memory.max").read_text(encoding="utf-8"), "134217728")
        self.assertEqual((child / "pids.max").read_text(encoding="utf-8"), "8")
        self.assertEqual((child / "memory.oom.group").read_text(encoding="utf-8"), "1")
        self.assertEqual((child / "memory.swap.max").read_text(encoding="utf-8"), "0")

    def test_prepare_enables_missing_controllers_when_the_subtree_is_empty(self) -> None:
        self._write_base("cgroup.subtree_control", "cpu\n")
        self._write_base("cgroup.procs", "")
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        self.assertEqual(
            (self.base / "cgroup.subtree_control").read_text(encoding="utf-8"),
            "+memory +pids",
        )

    def test_prepare_fails_when_the_kernel_does_not_expose_memory_max(self) -> None:
        def partial_child(path: Path) -> None:
            path.mkdir()
            (path / "cgroup.procs").write_text("", encoding="utf-8")
            (path / "pids.max").write_text("max", encoding="utf-8")

        controller = self._controller(child_factory=partial_child)
        error = controller.prepare()
        self.assertEqual(error, "memory.max is missing in the owned cgroup")
        self.assertIsNone(controller.owned_cgroup)

    def test_prepare_fails_when_written_limits_do_not_read_back(self) -> None:
        def readonly_child(path: Path) -> None:
            _kernel_child(path)
            (path / "memory.max").write_text("max", encoding="utf-8")
            os.chmod(path / "memory.max", 0o444)

        controller = self._controller(child_factory=readonly_child)
        error = controller.prepare()
        self.assertIsNotNone(error)
        self.assertIn("memory.max", error or "")

    def test_prepare_survives_a_kernel_without_optional_controls(self) -> None:
        def minimal_child(path: Path) -> None:
            path.mkdir()
            (path / "cgroup.procs").write_text("", encoding="utf-8")
            (path / "memory.max").write_text("max", encoding="utf-8")
            (path / "pids.max").write_text("max", encoding="utf-8")

        controller = self._controller(child_factory=minimal_child)
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        self.assertFalse((child / "memory.oom.group").exists())

    def test_spawn_limits_defer_to_the_cgroup_not_the_job_object(self) -> None:
        limits = self._controller().spawn_limits()
        self.assertIsNone(limits["active_process_limit"])
        self.assertIsNone(limits["job_memory_limit_bytes"])
        self.assertIsNone(limits["process_memory_limit_bytes"])

    def test_default_pids_max_is_the_task_ceiling(self) -> None:
        defaults = BrowserResourceLimits()
        self.assertEqual(defaults.max_tasks, 256)
        self.assertEqual(defaults.max_processes, 32)
        controller = LinuxCgroupV2ResourceController(
            defaults,
            cgroup_root=self.root,
            self_cgroup_reader=lambda: "/delegated.scope",
            child_factory=_kernel_child,
            platform_is_linux=True,
            child_name=CHILD_NAME,
            root_override="",
            empty_wait_seconds=0.05,
            tree_reader=lambda root: {root, 9191},
            child_remover=_kernel_remove,
        )
        self.assertEqual(controller.pid_ceiling(), (PID_CEILING_TASKS, 256))
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        self.assertEqual((child / "pids.max").read_text(encoding="utf-8"), "256")


class CgroupAttachmentTests(_CgroupTreeCase):
    def test_worker_pid_is_attached_and_verified(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        supervised = _FakeSupervised(pid=4242)
        self.assertIsNone(controller.confirm_containment(supervised, 9191))
        child = controller.owned_cgroup
        assert child is not None
        self.assertIn("9191", (child / "cgroup.procs").read_text(encoding="utf-8"))

    def test_a_launcher_descendant_is_attached_explicitly(self) -> None:
        controller = self._controller(tree_reader=lambda root: {root, 5555})
        self.assertIsNone(controller.prepare())
        self.assertIsNone(controller.confirm_containment(_FakeSupervised(pid=4242), 5555))

    def test_a_pid_outside_the_supervised_tree_is_refused(self) -> None:
        controller = self._controller(tree_reader=lambda root: {root})
        self.assertIsNone(controller.prepare())
        error = controller.confirm_containment(_FakeSupervised(pid=4242), 7777)
        self.assertEqual(error, "the announcing pid is not part of the supervised process tree")

    def test_attachment_fails_closed_when_the_cgroup_does_not_exist(self) -> None:
        controller = self._controller()
        error = controller.confirm_containment(_FakeSupervised(pid=4242), 9191)
        self.assertEqual(error, "the owned cgroup was not created")

    def test_attachment_fails_closed_when_membership_cannot_be_confirmed(self) -> None:
        def unwritable_procs(path: Path) -> None:
            _kernel_child(path)
            os.chmod(path / "cgroup.procs", 0o444)

        controller = self._controller(child_factory=unwritable_procs)
        self.assertIsNone(controller.prepare())
        error = controller.confirm_containment(_FakeSupervised(pid=4242), 9191)
        self.assertIsNotNone(error)
        self.assertIn("cgroup", error or "")


class CgroupBreachClassificationTests(_CgroupTreeCase):
    def test_memory_events_oom_kill_is_classified(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        (child / "memory.events").write_text("max 3\noom 1\noom_kill 1\n", encoding="utf-8")
        self.assertEqual(controller.resource_breach(), BREACH_MEMORY_MAX)

    def test_pids_events_max_is_classified(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        (child / "pids.events").write_text("max 7\n", encoding="utf-8")
        self.assertEqual(controller.resource_breach(), BREACH_PIDS_MAX)

    def test_no_breach_is_reported_for_a_quiet_cgroup(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        self.assertIsNone(controller.resource_breach())


class CgroupCleanupTests(_CgroupTreeCase):
    def test_cleanup_removes_only_the_owned_child(self) -> None:
        sibling = self.base / "someone-elses.scope"
        sibling.mkdir()
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        self.assertIsNone(controller.cleanup())
        self.assertFalse(child.exists())
        self.assertTrue(sibling.exists())
        self.assertTrue(self.base.exists())
        self.assertTrue((self.base / "cgroup.subtree_control").exists())

    def test_cleanup_refuses_a_cgroup_that_still_has_member_processes(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        (child / "cgroup.procs").write_text("9191\n", encoding="utf-8")
        error = controller.cleanup()
        self.assertEqual(error, "refusing to remove a cgroup that still has member processes")
        self.assertTrue(child.exists())

    def test_kill_contained_uses_cgroup_kill_and_verifies_emptiness(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        child = controller.owned_cgroup
        assert child is not None
        (child / "cgroup.procs").write_text("9191\n", encoding="utf-8")
        error = controller.kill_contained()
        self.assertEqual((child / "cgroup.kill").read_text(encoding="utf-8"), "1")
        self.assertEqual(
            error, "the owned cgroup still has member processes after cgroup.kill"
        )

    def test_kill_contained_is_quiet_when_the_cgroup_drains(self) -> None:
        controller = self._controller()
        self.assertIsNone(controller.prepare())
        self.assertIsNone(controller.kill_contained())


class WindowsJobObjectControllerTests(unittest.TestCase):
    def test_spawn_limits_carry_the_aggregate_job_memory_ceiling(self) -> None:
        controller = WindowsJobObjectResourceController(LIMITS)
        limits = controller.spawn_limits()
        self.assertEqual(limits["active_process_limit"], LIMITS.max_processes)
        self.assertEqual(limits["job_memory_limit_bytes"], LIMITS.max_memory_bytes)
        self.assertEqual(limits["process_memory_limit_bytes"], LIMITS.max_memory_bytes)
        self.assertEqual(controller.mechanism, MECHANISM_WINDOWS_JOB_OBJECT)
        self.assertEqual(controller.pid_ceiling(), (PID_CEILING_PROCESSES, LIMITS.max_processes))

    def test_default_active_process_limit_remains_32(self) -> None:
        defaults = BrowserResourceLimits()
        controller = WindowsJobObjectResourceController(defaults)
        self.assertEqual(defaults.max_processes, 32)
        self.assertEqual(defaults.max_tasks, 256)
        self.assertEqual(controller.spawn_limits()["active_process_limit"], 32)
        self.assertEqual(controller.pid_ceiling(), (PID_CEILING_PROCESSES, 32))

    def test_readiness_reports_the_aggregate_ceiling(self) -> None:
        state = WindowsJobObjectResourceController(LIMITS).readiness()
        self.assertEqual(state.ready, os.name == "nt")
        if state.ready:
            self.assertIn("JobMemoryLimit=134217728", state.detail or "")
            self.assertIn("ActiveProcessLimit=8", state.detail or "")

    def test_containment_fails_closed_without_a_job_handle(self) -> None:
        controller = WindowsJobObjectResourceController(LIMITS)
        error = controller.confirm_containment(
            _FakeSupervised(pid=1234, job_handle=None), 1234
        )
        self.assertIsNotNone(error)


@unittest.skipUnless(os.name == "nt", "the Job Object is a Windows mechanism")
class WindowsAppliedJobLimitTests(unittest.TestCase):
    """Read back from the kernel what the Job Object actually enforces."""

    def setUp(self) -> None:
        from zest.platform.process_tree import spawn_supervised, terminate_tree

        self._terminate = terminate_tree
        handle, raw = tempfile.mkstemp(prefix="g21-job-", suffix=".pid")
        os.close(handle)
        self.pid_path = Path(raw)
        self.addCleanup(lambda: self.pid_path.unlink(missing_ok=True))
        script = (
            "import subprocess, sys, time\n"
            f"path = {str(self.pid_path)!r}\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "open(path, 'w', encoding='utf-8').write(str(child.pid))\n"
            "time.sleep(30)\n"
        )
        self.controller = WindowsJobObjectResourceController(LIMITS)
        self.supervised = spawn_supervised(
            [sys.executable, "-c", script], **self.controller.spawn_limits()
        )
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        self._terminate(self.supervised)
        for pipe in (
            self.supervised.process.stdin,
            self.supervised.process.stdout,
            self.supervised.process.stderr,
        ):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass

    def _descendant_pid(self) -> int:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self.pid_path.exists() and self.pid_path.stat().st_size > 0:
                return int(self.pid_path.read_text(encoding="utf-8").strip())
            time.sleep(0.05)
        self.fail("the supervised process never spawned a descendant")
        raise AssertionError

    def test_the_kernel_applied_the_aggregate_job_memory_limit(self) -> None:
        from zest.platform.process_tree import (
            JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
            JOB_OBJECT_LIMIT_JOB_MEMORY,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOB_OBJECT_LIMIT_PROCESS_MEMORY,
            windows_job_limits,
        )

        applied = windows_job_limits(self.supervised.job_handle or 0)
        self.assertIsNotNone(applied, "the applied job limits could not be read")
        assert applied is not None
        self.assertTrue(applied["limit_flags"] & JOB_OBJECT_LIMIT_JOB_MEMORY)
        self.assertTrue(applied["limit_flags"] & JOB_OBJECT_LIMIT_ACTIVE_PROCESS)
        self.assertTrue(applied["limit_flags"] & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertTrue(applied["limit_flags"] & JOB_OBJECT_LIMIT_PROCESS_MEMORY)
        self.assertEqual(applied["job_memory_limit"], LIMITS.max_memory_bytes)
        self.assertEqual(applied["active_process_limit"], LIMITS.max_processes)
        self.assertEqual(applied["process_memory_limit"], LIMITS.max_memory_bytes)

    def test_the_controller_verifies_the_applied_limits(self) -> None:
        self.assertIsNone(self.controller.verify_applied_limits(self.supervised))

    def test_a_mismatched_ceiling_is_refused(self) -> None:
        stricter = WindowsJobObjectResourceController(
            BrowserResourceLimits(max_memory_bytes=LIMITS.max_memory_bytes // 2, max_processes=4)
        )
        error = stricter.verify_applied_limits(self.supervised)
        self.assertIsNotNone(error)
        self.assertIn("does not match the declared ceiling", error or "")

    def test_descendants_are_job_members_and_die_with_the_job(self) -> None:
        from zest.platform.process_tree import (
            terminate_tree,
            windows_process_in_job,
        )

        descendant = self._descendant_pid()
        self.assertTrue(
            windows_process_in_job(descendant, self.supervised.job_handle or 0),
            f"descendant {descendant} is not a job member",
        )
        self.assertIsNone(self.controller.confirm_containment(self.supervised, descendant))
        terminate_tree(self.supervised)
        deadline = time.time() + 2.0
        while time.time() < deadline and _windows_pid_alive(descendant):
            time.sleep(0.05)
        self.assertFalse(_windows_pid_alive(descendant), "the descendant survived the job kill")


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    exit_code = wintypes.DWORD()
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    return exit_code.value == 259  # STILL_ACTIVE


class ControllerSelectionTests(unittest.TestCase):
    def test_unsupported_platform_never_reports_ready(self) -> None:
        controller = UnsupportedPlatformResourceController(LIMITS)
        state = controller.readiness()
        self.assertFalse(state.ready)
        self.assertIsNotNone(controller.prepare())
        self.assertIsNotNone(controller.confirm_containment(_FakeSupervised(pid=1), 1))

    def test_factory_picks_the_host_mechanism(self) -> None:
        controller = browser_resource_controller(LIMITS)
        if os.name == "nt":
            self.assertIsInstance(controller, WindowsJobObjectResourceController)
        elif sys.platform.startswith("linux"):
            self.assertIsInstance(controller, LinuxCgroupV2ResourceController)
        else:
            self.assertIsInstance(controller, UnsupportedPlatformResourceController)

    def test_declared_worker_limits_match_the_enforced_platform_limits(self) -> None:
        from zest.worker_runtime.python.browser_engine import BrowserRuntimeLimits

        defaults = BrowserResourceLimits()
        worker = BrowserRuntimeLimits()
        self.assertEqual(defaults.max_memory_bytes, worker.max_memory_bytes)
        self.assertEqual(defaults.max_processes, worker.max_descendant_processes)
        self.assertEqual(defaults.max_tasks, worker.max_descendant_tasks)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None


class _FakeSupervised:
    def __init__(self, *, pid: int, job_handle: int | None = 1) -> None:
        self.process = _FakeProcess(pid)
        self.job_handle = job_handle
        self.cleanup_failed = False
        self.cleanup_reason: str | None = None


if __name__ == "__main__":
    unittest.main()
