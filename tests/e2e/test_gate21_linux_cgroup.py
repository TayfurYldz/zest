"""GATE 21 real Linux cgroup v2 containment.

This is the authoritative proof that the browser process tree runs under a
kernel-enforced memory and process ceiling. Unit tests with filesystem doubles do
not prove enforcement; only this module does.

The suite is platform-gated. Set ZEST_REQUIRE_CGROUP_TESTS=1 on the
authoritative validation host so an unavailable environment fails instead of
skipping. A skipped run must never be reported as a GATE 21 pass.

A delegated, writable cgroup v2 subtree is required. If the process runs directly
in a subtree that already owns processes and has no memory/pids delegation, start
the test under a delegated scope, for example:

    systemd-run --user --scope -p Delegate=yes -- python -m unittest tests.e2e.test_gate21_linux_cgroup

ZEST_BROWSER_CGROUP_ROOT may point at an already delegated subtree instead.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.platform.browser_resource_control import (
    BREACH_MEMORY_MAX,
    BREACH_PIDS_MAX,
    BrowserResourceLimits,
    LinuxCgroupV2ResourceController,
    browser_resource_controller,
)
from zest.platform.local_process_worker import LocalProcessWorkerConfig
from zest.platform.persistent_browser_worker import (
    RESOURCE_BREACH_REASON,
    PersistentBrowserWorkerAdapter,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from support.browser_worker_scripts import (
    descendant_script,
    memory_breach_script,
    pids_breach_script,
)
from support.worker_requests import valid_worker_request

REQUIRE_ENV = "ZEST_REQUIRE_CGROUP_TESTS"
BREACH_LIMITS = BrowserResourceLimits(
    max_memory_bytes=134_217_728,
    max_processes=32,
    max_tasks=8,
)
BREACH_STATUSES = (
    InvocationStatus.TIMED_OUT,
    InvocationStatus.PROCESS_FAILED,
    InvocationStatus.PROTOCOL_ERROR,
)


def require_succeeded_browser_result(outcome: WorkerInvocationOutcome) -> dict[str, object]:
    """COMPLETED is not success. Only a SUCCEEDED WorkerResult may prove Chromium ran."""

    if outcome.invocation_status != InvocationStatus.COMPLETED:
        raise AssertionError(f"invocation did not complete: {outcome.reason}")
    result = outcome.worker_result
    if result is None:
        raise AssertionError(f"no WorkerResult: {outcome.reason}")
    if result.get("status") != "SUCCEEDED":
        raise AssertionError(f"browser execution failed: {result.get('diagnostics')!r}")
    return dict(result)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _members(cgroup: Path) -> set[int]:
    try:
        raw = (cgroup / "cgroup.procs").read_text(encoding="utf-8")
    except OSError:
        return set()
    return {int(item) for item in raw.split() if item.isdigit()}


def _unavailable_reason() -> str | None:
    if not sys.platform.startswith("linux"):
        return "cgroup v2 containment is a Linux mechanism"
    controller = browser_resource_controller(BREACH_LIMITS)
    if not isinstance(controller, LinuxCgroupV2ResourceController):
        return "the Linux cgroup v2 controller was not selected"
    state = controller.readiness()
    if not state.ready:
        return f"cgroup v2 enforcement is not established: {state.reason}"
    return None


def _gate() -> str | None:
    reason = _unavailable_reason()
    if reason is None:
        return None
    if os.environ.get(REQUIRE_ENV) == "1":
        raise AssertionError(
            f"{REQUIRE_ENV}=1 but real cgroup containment cannot run: {reason}"
        )
    return reason


_SKIP_REASON = _gate()


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class Gate21LinuxCgroupContainmentTests(unittest.TestCase):
    def _adapter(
        self,
        script: str,
        *,
        limits: BrowserResourceLimits = BREACH_LIMITS,
        timeout_ms: int = 5_000,
    ) -> PersistentBrowserWorkerAdapter:
        adapter = PersistentBrowserWorkerAdapter(
            LocalProcessWorkerConfig(
                worker_id="browser-cgroup-test",
                argv_override=(sys.executable, "-c", script),
                default_timeout_ms=timeout_ms,
            ),
            resource_limits=limits,
        )
        self.addCleanup(adapter.shutdown)
        return adapter

    def test_1_worker_and_descendants_are_cgroup_members(self) -> None:
        pid_path = Path(f"/tmp/g21-cgroup-{os.getpid()}.pid")
        self.addCleanup(lambda: pid_path.unlink(missing_ok=True))
        adapter = self._adapter(descendant_script(str(pid_path)))
        self.assertIsNone(adapter._ensure_process())
        controller = adapter.resource_controller
        assert isinstance(controller, LinuxCgroupV2ResourceController)
        cgroup = controller.owned_cgroup
        assert cgroup is not None
        self.assertEqual((cgroup / "memory.max").read_text(encoding="utf-8").strip(), "134217728")
        self.assertEqual((cgroup / "pids.max").read_text(encoding="utf-8").strip(), "8")
        deadline = time.time() + 5.0
        descendant = None
        while time.time() < deadline:
            if pid_path.exists() and pid_path.stat().st_size > 0:
                descendant = int(pid_path.read_text(encoding="utf-8").strip())
                break
            time.sleep(0.05)
        self.assertIsNotNone(descendant, "the contained Worker never spawned a descendant")
        members = _members(cgroup)
        self.assertIn(descendant, members, f"descendant {descendant} escaped the cgroup")
        self.assertGreaterEqual(len(members), 1)

    def test_2_shutdown_empties_and_removes_the_owned_cgroup(self) -> None:
        pid_path = Path(f"/tmp/g21-cgroup-cleanup-{os.getpid()}.pid")
        self.addCleanup(lambda: pid_path.unlink(missing_ok=True))
        adapter = self._adapter(descendant_script(str(pid_path)))
        self.assertIsNone(adapter._ensure_process())
        controller = adapter.resource_controller
        assert isinstance(controller, LinuxCgroupV2ResourceController)
        cgroup = controller.owned_cgroup
        assert cgroup is not None
        deadline = time.time() + 5.0
        descendant = None
        while time.time() < deadline:
            if pid_path.exists() and pid_path.stat().st_size > 0:
                descendant = int(pid_path.read_text(encoding="utf-8").strip())
                break
            time.sleep(0.05)
        self.assertIsNotNone(descendant)
        self.assertTrue(_pid_alive(descendant))
        adapter.shutdown()
        deadline = time.time() + 3.0
        while time.time() < deadline and _pid_alive(descendant):
            time.sleep(0.05)
        self.assertFalse(_pid_alive(descendant), f"descendant {descendant} survived shutdown")
        self.assertFalse(cgroup.exists(), "the owned cgroup was not removed")
        self.assertIsNone(adapter.containment_failure_reason)

    def test_3_memory_max_is_enforced_by_the_kernel(self) -> None:
        adapter = self._adapter(memory_breach_script(), timeout_ms=6_000)
        outcome = adapter.invoke(valid_worker_request())
        self.assertIn(outcome.invocation_status, BREACH_STATUSES)
        self.assertIsNone(outcome.worker_result)
        self.assertIn(
            f"{RESOURCE_BREACH_REASON}: {BREACH_MEMORY_MAX}",
            outcome.reason or "",
            msg="the kernel memory ceiling was not observed as a resource-limit breach",
        )

    def test_4_pids_max_is_enforced_by_the_kernel(self) -> None:
        adapter = self._adapter(pids_breach_script(), timeout_ms=4_000)
        outcome = adapter.invoke(valid_worker_request())
        self.assertIn(outcome.invocation_status, BREACH_STATUSES)
        self.assertIsNone(outcome.worker_result)
        self.assertIn(
            f"{RESOURCE_BREACH_REASON}: {BREACH_PIDS_MAX}",
            outcome.reason or "",
            msg="the kernel process ceiling was not observed as a resource-limit breach",
        )
        self.assertIn("spawned=", outcome.stderr_diagnostics)


@unittest.skipUnless(_SKIP_REASON is None, _SKIP_REASON or "")
class Gate21ChromiumCgroupMembershipTests(unittest.TestCase):
    """Real Chromium must be a member of the owned cgroup, not merely a descendant."""

    def setUp(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Chromium/Playwright is not installed")
        from e2e.lab.browser_page_lab import Gate21BrowserLab

        self.lab = Gate21BrowserLab()
        self.origin = self.lab.start()
        self.addCleanup(self.lab.stop)
        self.adapter = PersistentBrowserWorkerAdapter(
            LocalProcessWorkerConfig(
                worker_id="browser-cgroup-chromium", default_timeout_ms=20_000
            )
        )
        self.addCleanup(self.adapter.shutdown)

    def _navigate_request(self) -> dict[str, object]:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.origin)
        return valid_worker_request(
            worker_capability="browser.page",
            action="navigate",
            arguments={"authorized_origin": self.origin, "path": "/"},
            network_envelope={
                "normalized_scheme": parsed.scheme,
                "normalized_host": parsed.hostname,
                "normalized_port": parsed.port,
                "document_path": "/",
                "origin_wide": True,
                "allowed_path_prefixes": ["/"],
                "denied_path_prefixes": [],
                "loopback_only": True,
                "source_scope_rule_ids": ["rule-allow"],
                "authorization_decision_reference": "authz-1",
            },
            max_attempted_requests=16,
            execution_budget={
                "budget_id": "budget-1",
                "max_requests": 16,
                "max_tool_calls": 4,
                "max_runtime_ms": 20_000,
                "max_concurrency": 1,
            },
        )

    def test_chromium_processes_join_the_owned_cgroup(self) -> None:
        outcome = self.adapter.invoke(self._navigate_request())
        require_succeeded_browser_result(outcome)
        controller = self.adapter.resource_controller
        assert isinstance(controller, LinuxCgroupV2ResourceController)
        cgroup = controller.owned_cgroup
        assert cgroup is not None
        members = _members(cgroup)
        names = []
        for pid in members:
            try:
                names.append(Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip())
            except OSError:
                continue
        self.assertTrue(
            any("chrom" in name.lower() for name in names),
            msg=f"no Chromium process is a member of the owned cgroup: {names}",
        )


class Gate21ChromiumResultPreconditionTests(unittest.TestCase):
    """These assertions must run on every host; they do not prove kernel enforcement."""

    def test_execution_failed_is_not_treated_as_chromium_success(self) -> None:
        now = datetime.now(timezone.utc)
        outcome = WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            worker_result={
                "status": "EXECUTION_FAILED",
                "diagnostics": {
                    "error": "browser engine is unavailable",
                    "reason_code": "IMPLEMENTATION_NOT_AVAILABLE",
                },
            },
        )
        with self.assertRaises(AssertionError) as ctx:
            require_succeeded_browser_result(outcome)
        self.assertIn("IMPLEMENTATION_NOT_AVAILABLE", str(ctx.exception))

    def test_breach_fixture_uses_a_small_task_ceiling(self) -> None:
        self.assertEqual(BREACH_LIMITS.max_tasks, 8)
        self.assertEqual(BREACH_LIMITS.max_processes, 32)
        self.assertLess(BREACH_LIMITS.max_tasks, BrowserResourceLimits().max_tasks)


if __name__ == "__main__":
    unittest.main()
