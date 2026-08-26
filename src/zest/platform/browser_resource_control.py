"""Kernel-enforced resource containment for the persistent browser Worker.

The browser process tree must run under a hard, kernel-enforced memory ceiling
and a host-specific PID ceiling. Linux uses cgroup v2 ``memory.max`` and
``pids.max`` (a task/thread ceiling). Windows uses the existing validated Job
Object ``JobMemoryLimit`` and ``ActiveProcessLimit`` (a process ceiling).
Sampling RSS is not enforcement and is never used here.

If enforcement cannot be established the browser runtime is NOT READY and no
Chromium is started. There is no unlimited fallback. Other Worker capabilities
are unaffected because they do not use this controller.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from zest.platform.process_tree import (
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS,
    JOB_OBJECT_LIMIT_JOB_MEMORY,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    SupervisedProcess,
    posix_tree_members,
    windows_job_limits,
    windows_process_in_job,
)

CGROUP_V2_ROOT = Path("/sys/fs/cgroup")
CGROUP_ROOT_ENV = "ZEST_BROWSER_CGROUP_ROOT"
REQUIRED_CONTROLLERS = ("memory", "pids")
OWNED_CGROUP_PREFIX = "zest-browser"
CGROUP_EMPTY_WAIT_SECONDS = 1.0
CGROUP_POLL_SECONDS = 0.025

MECHANISM_LINUX_CGROUP_V2 = "linux.cgroup2"
MECHANISM_WINDOWS_JOB_OBJECT = "windows.jobobject"
MECHANISM_UNSUPPORTED = "unsupported"

BREACH_MEMORY_MAX = "MEMORY_MAX"
BREACH_PIDS_MAX = "PIDS_MAX"

DEFAULT_MAX_MEMORY_BYTES = 2_147_483_648
DEFAULT_MAX_PROCESSES = 32
DEFAULT_MAX_TASKS = 256

PID_CEILING_TASKS = "tasks"
PID_CEILING_PROCESSES = "processes"


@dataclass(frozen=True)
class BrowserResourceLimits:
    """Hard ceilings for the whole browser process tree.

    ``max_memory_bytes`` has one meaning on every host: the maximum aggregate
    memory available to the contained Browser Worker and its Chromium process
    tree. It is not a sampled RSS target. Linux enforces it with cgroup v2
    ``memory.max`` over the contained tree; Windows enforces it with the Job
    Object ``JobMemoryLimit``, which bounds committed memory across all job
    members. Windows additionally applies the same value as a per-process limit,
    which is stricter and never a substitute for the aggregate bound.

    ``max_processes`` and ``max_tasks`` are not interchangeable. Linux cgroup v2
    ``pids.max`` counts tasks, including threads, so the kernel ceiling is
    ``max_tasks``. Windows Job Object ``ActiveProcessLimit`` counts processes,
    so the kernel ceiling is ``max_processes``. The unused field on a host is
    not an enforcement claim.
    """

    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES
    max_processes: int = DEFAULT_MAX_PROCESSES
    max_tasks: int = DEFAULT_MAX_TASKS


@dataclass(frozen=True)
class BrowserResourceReadiness:
    ready: bool
    mechanism: str
    reason: str | None = None
    detail: str | None = None


class BrowserResourceController(Protocol):
    """Establishes and tears down kernel enforcement for one Worker process."""

    mechanism: str

    def readiness(self) -> BrowserResourceReadiness: ...

    def prepare(self) -> str | None: ...

    def spawn_limits(self) -> dict[str, int | None]: ...

    def pid_ceiling(self) -> tuple[str, int]: ...

    def confirm_containment(
        self, supervised: SupervisedProcess, worker_pid: int
    ) -> str | None: ...

    def resource_breach(self) -> str | None: ...

    def kill_contained(self) -> str | None: ...

    def cleanup(self) -> str | None: ...


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_text(path: Path, value: str) -> str | None:
    try:
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        return f"write to {path.name} failed: {type(exc).__name__}"
    return None


def _tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(text.split())


def _pids_in(path: Path) -> list[int]:
    text = _read_text(path)
    if not text:
        return []
    pids: list[int] = []
    for line in text.split():
        if line.isdigit():
            pids.append(int(line))
    return pids


def _event_count(path: Path, key: str) -> int:
    text = _read_text(path)
    if not text:
        return 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == key and fields[1].lstrip("-").isdigit():
            return int(fields[1])
    return 0


def _default_child_factory(path: Path) -> None:
    path.mkdir(mode=0o755)


def _default_child_remover(path: Path) -> None:
    """Remove one cgroup directory. Never recursive, never a parent cgroup."""

    path.rmdir()


class LinuxCgroupV2ResourceController:
    """Owns exactly one cgroup v2 child. Never modifies parent or system cgroups."""

    mechanism = MECHANISM_LINUX_CGROUP_V2

    def __init__(
        self,
        limits: BrowserResourceLimits,
        *,
        cgroup_root: Path = CGROUP_V2_ROOT,
        self_cgroup_reader: Callable[[], str | None] | None = None,
        child_factory: Callable[[Path], None] = _default_child_factory,
        root_override: str | None = None,
        platform_is_linux: bool | None = None,
        child_name: str | None = None,
        access: Callable[[Path, int], bool] = os.access,
        empty_wait_seconds: float = CGROUP_EMPTY_WAIT_SECONDS,
        tree_reader: Callable[[int], set[int]] = posix_tree_members,
        child_remover: Callable[[Path], None] = _default_child_remover,
    ) -> None:
        self._limits = limits
        self._root = cgroup_root
        self._self_cgroup_reader = self_cgroup_reader or _read_self_cgroup
        self._child_factory = child_factory
        self._access = access
        self._empty_wait_seconds = empty_wait_seconds
        self._tree_reader = tree_reader
        self._child_remover = child_remover
        self._root_override = (
            os.environ.get(CGROUP_ROOT_ENV) if root_override is None else root_override
        )
        self._platform_is_linux = (
            sys.platform.startswith("linux") if platform_is_linux is None else platform_is_linux
        )
        self._child_name = child_name or f"{OWNED_CGROUP_PREFIX}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._child: Path | None = None
        self._applied_oom_group = False
        self._applied_swap_max = False

    @property
    def owned_cgroup(self) -> Path | None:
        return self._child

    def base_cgroup(self) -> tuple[Path | None, str | None]:
        if self._root_override:
            candidate = Path(self._root_override)
            if not candidate.is_absolute():
                return None, f"{CGROUP_ROOT_ENV} must be an absolute path"
            resolved = candidate.resolve()
            root = self._root.resolve()
            if resolved != root and root not in resolved.parents:
                return None, f"{CGROUP_ROOT_ENV} must point inside {self._root}"
            if not resolved.is_dir():
                return None, f"{CGROUP_ROOT_ENV} does not exist"
            return resolved, None
        raw = self._self_cgroup_reader()
        if raw is None:
            return None, "cannot read the current cgroup v2 membership"
        relative = raw.strip().lstrip("/")
        base = self._root if not relative else self._root / relative
        if not base.is_dir():
            return None, "the current cgroup v2 path does not exist"
        return base, None

    def readiness(self) -> BrowserResourceReadiness:
        if not self._platform_is_linux:
            return BrowserResourceReadiness(
                ready=False, mechanism=self.mechanism, reason="host is not Linux"
            )
        if not (self._root / "cgroup.controllers").is_file():
            return BrowserResourceReadiness(
                ready=False, mechanism=self.mechanism, reason="cgroup v2 is not active"
            )
        base, reason = self.base_cgroup()
        if base is None:
            return BrowserResourceReadiness(
                ready=False, mechanism=self.mechanism, reason=reason
            )
        available = _tokens(_read_text(base / "cgroup.controllers"))
        for controller in REQUIRED_CONTROLLERS:
            if controller not in available:
                return BrowserResourceReadiness(
                    ready=False,
                    mechanism=self.mechanism,
                    reason=f"{controller} controller is not available in {base}",
                )
        if not self._access(base, os.W_OK):
            return BrowserResourceReadiness(
                ready=False,
                mechanism=self.mechanism,
                reason=f"cgroup subtree {base} is not writable; a delegated subtree is required",
            )
        subtree = base / "cgroup.subtree_control"
        enabled = _tokens(_read_text(subtree))
        missing = [item for item in REQUIRED_CONTROLLERS if item not in enabled]
        if missing:
            if not self._access(subtree, os.W_OK):
                return BrowserResourceReadiness(
                    ready=False,
                    mechanism=self.mechanism,
                    reason=f"cannot enable {' '.join(missing)} in {subtree}",
                )
            if _pids_in(base / "cgroup.procs"):
                return BrowserResourceReadiness(
                    ready=False,
                    mechanism=self.mechanism,
                    reason=(
                        f"{' '.join(missing)} must already be enabled in cgroup.subtree_control "
                        f"because {base} has member processes; run under a delegated cgroup subtree"
                    ),
                )
        return BrowserResourceReadiness(
            ready=True,
            mechanism=self.mechanism,
            detail=f"base={base} memory.max={self._limits.max_memory_bytes} pids.max={self._limits.max_tasks}",
        )

    def prepare(self) -> str | None:
        state = self.readiness()
        if not state.ready:
            return state.reason
        base, reason = self.base_cgroup()
        if base is None:
            return reason
        subtree = base / "cgroup.subtree_control"
        missing = [item for item in REQUIRED_CONTROLLERS if item not in _tokens(_read_text(subtree))]
        if missing:
            error = _write_text(subtree, " ".join(f"+{item}" for item in missing))
            if error is not None:
                return error
        child = base / self._child_name
        try:
            self._child_factory(child)
        except OSError as exc:
            return f"cannot create the owned cgroup: {type(exc).__name__}"
        self._child = child
        memory_max = child / "memory.max"
        pids_max = child / "pids.max"
        for control in (memory_max, pids_max, child / "cgroup.procs"):
            if not control.is_file():
                self.cleanup()
                return f"{control.name} is missing in the owned cgroup"
        error = _write_text(memory_max, str(self._limits.max_memory_bytes))
        if error is not None:
            self.cleanup()
            return error
        error = _write_text(pids_max, str(self._limits.max_tasks))
        if error is not None:
            self.cleanup()
            return error
        confirmed = self._confirm_written_limits(memory_max, pids_max)
        if confirmed is not None:
            self.cleanup()
            return confirmed
        oom_group = child / "memory.oom.group"
        if oom_group.is_file() and _write_text(oom_group, "1") is None:
            self._applied_oom_group = True
        swap_max = child / "memory.swap.max"
        if swap_max.is_file() and _write_text(swap_max, "0") is None:
            self._applied_swap_max = True
        return None

    def _confirm_written_limits(self, memory_max: Path, pids_max: Path) -> str | None:
        memory = (_read_text(memory_max) or "").strip()
        pids = (_read_text(pids_max) or "").strip()
        if memory != str(self._limits.max_memory_bytes):
            return f"memory.max was not applied (read {memory!r})"
        if pids != str(self._limits.max_tasks):
            return f"pids.max was not applied (read {pids!r})"
        return None

    def spawn_limits(self) -> dict[str, int | None]:
        return {
            "active_process_limit": None,
            "job_memory_limit_bytes": None,
            "process_memory_limit_bytes": None,
        }

    def pid_ceiling(self) -> tuple[str, int]:
        return PID_CEILING_TASKS, self._limits.max_tasks

    def confirm_containment(
        self, supervised: SupervisedProcess, worker_pid: int
    ) -> str | None:
        """Attach the spawned process and the announcing Worker to the owned cgroup.

        A launcher/trampoline executable makes the announcing Worker a descendant
        of the spawned pid. Moving a parent does not move processes that already
        exist, so the announcing pid must be attached explicitly. Chromium is
        created only after this returns, so every browser process inherits the
        cgroup.
        """

        child = self._child
        if child is None:
            return "the owned cgroup was not created"
        spawned_pid = supervised.process.pid
        if spawned_pid is None:
            return "the browser Worker has no pid"
        members = self._tree_members(spawned_pid)
        if worker_pid not in members:
            return "the announcing pid is not part of the supervised process tree"
        procs = child / "cgroup.procs"
        for pid in dict.fromkeys((spawned_pid, worker_pid)):
            error = _write_text(procs, str(pid))
            if error is not None:
                return f"cannot move the browser Worker into the owned cgroup: {error}"
            if pid not in _pids_in(procs):
                return "the browser Worker is not a member of the owned cgroup"
        return None

    def _tree_members(self, root_pid: int) -> set[int]:
        return self._tree_reader(root_pid)

    def resource_breach(self) -> str | None:
        child = self._child
        if child is None:
            return None
        memory_events = child / "memory.events"
        if _event_count(memory_events, "oom_kill") > 0 or _event_count(memory_events, "oom") > 0:
            return BREACH_MEMORY_MAX
        if _event_count(child / "pids.events", "max") > 0:
            return BREACH_PIDS_MAX
        return None

    def kill_contained(self) -> str | None:
        child = self._child
        if child is None:
            return None
        kill_file = child / "cgroup.kill"
        if not kill_file.is_file():
            return None
        error = _write_text(kill_file, "1")
        if error is not None:
            return error
        if not self._wait_until_empty():
            return "the owned cgroup still has member processes after cgroup.kill"
        return None

    def cleanup(self) -> str | None:
        child = self._child
        if child is None:
            return None
        if not self._wait_until_empty():
            return "refusing to remove a cgroup that still has member processes"
        self._child = None
        try:
            self._child_remover(child)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return f"cannot remove the owned cgroup: {type(exc).__name__}"
        return None

    def _wait_until_empty(self) -> bool:
        child = self._child
        if child is None:
            return True
        procs = child / "cgroup.procs"
        deadline = time.monotonic() + self._empty_wait_seconds
        while True:
            if not _pids_in(procs):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(CGROUP_POLL_SECONDS)


class WindowsJobObjectResourceController:
    """Kernel enforcement lives in the Job Object created at spawn time.

    ``JOB_OBJECT_LIMIT_JOB_MEMORY`` bounds the committed memory of every process
    in the job, so it is the aggregate tree ceiling. A per-process limit is kept
    in addition to it, never instead of it.
    """

    mechanism = MECHANISM_WINDOWS_JOB_OBJECT

    def __init__(self, limits: BrowserResourceLimits) -> None:
        self._limits = limits

    def readiness(self) -> BrowserResourceReadiness:
        if os.name != "nt":
            return BrowserResourceReadiness(
                ready=False, mechanism=self.mechanism, reason="host is not Windows"
            )
        try:
            import ctypes  # noqa: F401
        except ImportError:
            return BrowserResourceReadiness(
                ready=False, mechanism=self.mechanism, reason="ctypes is unavailable"
            )
        return BrowserResourceReadiness(
            ready=True,
            mechanism=self.mechanism,
            detail=(
                f"JobMemoryLimit={self._limits.max_memory_bytes} "
                f"ActiveProcessLimit={self._limits.max_processes}"
            ),
        )

    def prepare(self) -> str | None:
        return None

    def spawn_limits(self) -> dict[str, int | None]:
        return {
            "active_process_limit": self._limits.max_processes,
            "job_memory_limit_bytes": self._limits.max_memory_bytes,
            "process_memory_limit_bytes": self._limits.max_memory_bytes,
        }

    def pid_ceiling(self) -> tuple[str, int]:
        return PID_CEILING_PROCESSES, self._limits.max_processes

    def confirm_containment(
        self, supervised: SupervisedProcess, worker_pid: int
    ) -> str | None:
        """Kernel-verify job membership and the applied aggregate limits.

        Descendants of a job member join the job automatically, so verifying the
        announcing pid also proves it was not a foreign process. The limits are
        read back from the kernel rather than assumed from what was requested.
        """

        if not supervised.job_handle:
            return supervised.cleanup_reason or "the browser Worker has no job object"
        for pid in dict.fromkeys((supervised.process.pid, worker_pid)):
            member = windows_process_in_job(pid, supervised.job_handle)
            if member is None:
                return "cannot verify job object membership"
            if not member:
                return "the browser Worker is not a member of the job object"
        return self.verify_applied_limits(supervised)

    def verify_applied_limits(self, supervised: SupervisedProcess) -> str | None:
        if not supervised.job_handle:
            return "the browser Worker has no job object"
        applied = windows_job_limits(supervised.job_handle)
        if applied is None:
            return "cannot read the applied job object limits"
        flags = applied["limit_flags"]
        if not flags & JOB_OBJECT_LIMIT_JOB_MEMORY:
            return "the aggregate job memory limit is not applied"
        if not flags & JOB_OBJECT_LIMIT_ACTIVE_PROCESS:
            return "the active process limit is not applied"
        if not flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
            return "kill on job close is not applied"
        if applied["job_memory_limit"] != self._limits.max_memory_bytes:
            return "the applied aggregate job memory limit does not match the declared ceiling"
        if applied["active_process_limit"] != self._limits.max_processes:
            return "the applied active process limit does not match the declared ceiling"
        return None

    def resource_breach(self) -> str | None:
        return None

    def kill_contained(self) -> str | None:
        return None

    def cleanup(self) -> str | None:
        return None


class UnsupportedPlatformResourceController:
    """Fail closed. A host without kernel enforcement must not run Chromium."""

    mechanism = MECHANISM_UNSUPPORTED

    def __init__(self, limits: BrowserResourceLimits) -> None:
        self._limits = limits

    def readiness(self) -> BrowserResourceReadiness:
        return BrowserResourceReadiness(
            ready=False,
            mechanism=self.mechanism,
            reason=f"no kernel resource enforcement is implemented for {sys.platform}",
        )

    def prepare(self) -> str | None:
        return self.readiness().reason

    def spawn_limits(self) -> dict[str, int | None]:
        return {
            "active_process_limit": None,
            "job_memory_limit_bytes": None,
            "process_memory_limit_bytes": None,
        }

    def pid_ceiling(self) -> tuple[str, int]:
        return PID_CEILING_PROCESSES, self._limits.max_processes

    def confirm_containment(
        self, supervised: SupervisedProcess, worker_pid: int
    ) -> str | None:
        return self.readiness().reason

    def resource_breach(self) -> str | None:
        return None

    def kill_contained(self) -> str | None:
        return None

    def cleanup(self) -> str | None:
        return None


def _read_self_cgroup() -> str | None:
    text = _read_text(Path("/proc/self/cgroup"))
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[3:]
    return None


def browser_resource_controller(
    limits: BrowserResourceLimits | None = None,
) -> BrowserResourceController:
    effective = limits or BrowserResourceLimits()
    if os.name == "nt":
        return WindowsJobObjectResourceController(effective)
    if sys.platform.startswith("linux"):
        return LinuxCgroupV2ResourceController(effective)
    return UnsupportedPlatformResourceController(effective)
