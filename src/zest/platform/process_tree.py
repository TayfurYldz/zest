"""Process-tree supervision. Timeout/cancel must terminate descendants.

POSIX uses a new session/process group. Windows uses a Job Object via ctypes.
Never uses shell=True. Cleanup failure is reported; it is not silently ignored.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_GRACE_SECONDS = 1.0
DEFAULT_MAX_STDOUT_BYTES = 1_048_576
DEFAULT_MAX_STDERR_BYTES = 65_536
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_SUSPENDED = 0x00000004
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
PROCESS_ALL_ACCESS = 0x1F0FFF
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INFINITE = 0xFFFFFFFF


@dataclass(frozen=True)
class ProcessTreeResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    cancelled: bool
    cleanup_failed: bool
    cleanup_reason: str | None


@dataclass
class SupervisedProcess:
    process: subprocess.Popen[bytes]
    job_handle: int | None = None
    process_group: int | None = None
    cleanup_failed: bool = False
    cleanup_reason: str | None = None


def spawn_kwargs() -> dict[str, object]:
    """Keyword arguments that contain the child in a killable tree."""

    if os.name == "nt":
        return {
            "start_new_session": False,
            "creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED,
        }
    return {"start_new_session": True}


def spawn_supervised(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    stdin: int | None = subprocess.PIPE,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
    active_process_limit: int | None = None,
    job_memory_limit_bytes: int | None = None,
    process_memory_limit_bytes: int | None = None,
) -> SupervisedProcess:
    if not argv or not str(argv[0]).strip():
        raise FileNotFoundError("executable is empty")
    kwargs: dict[str, object] = {
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "env": dict(env) if env is not None else None,
        "shell": False,
        "cwd": str(cwd) if cwd is not None else None,
    }
    kwargs.update(spawn_kwargs())
    process = subprocess.Popen(list(argv), **kwargs)  # type: ignore[arg-type]
    supervised = SupervisedProcess(process=process)
    if os.name == "posix" and process.pid:
        supervised.process_group = process.pid
    if os.name == "nt":
        handle, reason = _windows_assign_job(
            process.pid,
            active_process_limit=active_process_limit,
            job_memory_limit_bytes=job_memory_limit_bytes,
            process_memory_limit_bytes=process_memory_limit_bytes,
        )
        resume_reason = _windows_resume(process.pid)
        supervised.job_handle = handle
        if handle is None:
            supervised.cleanup_failed = True
            supervised.cleanup_reason = reason or "windows job object assignment failed"
        elif resume_reason:
            supervised.cleanup_failed = True
            supervised.cleanup_reason = resume_reason
    return supervised


def release_supervision(supervised: SupervisedProcess) -> None:
    """Close containment handles. Terminates the tree only if the process is still running."""

    if supervised.process.poll() is None:
        terminate_tree(supervised)
        return
    _close_job(supervised)


def terminate_tree(
    supervised: SupervisedProcess,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
) -> None:
    process = supervised.process
    if process.poll() is not None:
        _close_job(supervised)
        return
    try:
        if os.name == "nt":
            _windows_terminate(supervised, grace_seconds=grace_seconds)
        else:
            _posix_terminate(supervised, grace_seconds=grace_seconds)
    except OSError as exc:
        supervised.cleanup_failed = True
        supervised.cleanup_reason = f"process tree cleanup failed: {type(exc).__name__}"
    finally:
        _close_job(supervised)


def run_supervised(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    stdin_bytes: bytes | None = None,
    timeout_seconds: float | None = None,
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES,
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES,
    cancelled: threading.Event | None = None,
) -> ProcessTreeResult:
    try:
        supervised = spawn_supervised(argv, env=env, cwd=cwd)
    except FileNotFoundError:
        raise
    stdout_box: dict[str, object] = {}
    stderr_box: dict[str, object] = {}
    assert supervised.process.stdout is not None
    assert supervised.process.stderr is not None
    stdout_thread = threading.Thread(
        target=_read_stream,
        args=(supervised.process.stdout, max_stdout_bytes, False, stdout_box),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_stream,
        args=(supervised.process.stderr, max_stderr_bytes, True, stderr_box),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    was_cancelled = False
    try:
        if stdin_bytes is not None and supervised.process.stdin is not None:
            try:
                supervised.process.stdin.write(stdin_bytes)
            except OSError:
                pass
        if supervised.process.stdin is not None:
            try:
                supervised.process.stdin.close()
            except OSError:
                pass
        wait_timeout = timeout_seconds
        while True:
            if cancelled is not None and cancelled.is_set():
                was_cancelled = True
                terminate_tree(supervised)
                break
            try:
                supervised.process.wait(timeout=0.05 if wait_timeout is None else min(0.05, wait_timeout))
                break
            except subprocess.TimeoutExpired:
                if wait_timeout is None:
                    continue
                wait_timeout -= 0.05
                if wait_timeout <= 0:
                    timed_out = True
                    terminate_tree(supervised)
                    try:
                        supervised.process.wait(timeout=DEFAULT_GRACE_SECONDS + 1.0)
                    except subprocess.TimeoutExpired:
                        supervised.cleanup_failed = True
                        if supervised.cleanup_reason is None:
                            supervised.cleanup_reason = "process still alive after timeout terminate"
                    break
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
    finally:
        for pipe in (supervised.process.stdin, supervised.process.stdout, supervised.process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass
        if supervised.process.poll() is None:
            terminate_tree(supervised)
    return ProcessTreeResult(
        exit_code=supervised.process.returncode,
        stdout=bytes(stdout_box.get("data", b"") or b""),
        stderr=bytes(stderr_box.get("data", b"") or b""),
        stdout_truncated=bool(stdout_box.get("exceeded")),
        stderr_truncated=bool(stderr_box.get("exceeded")),
        timed_out=timed_out,
        cancelled=was_cancelled,
        cleanup_failed=supervised.cleanup_failed,
        cleanup_reason=supervised.cleanup_reason,
    )


def _read_stream(stream, max_bytes: int, truncate: bool, collected: dict[str, object]) -> None:
    buf = bytearray()
    exceeded = False
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        if len(buf) + len(chunk) > max_bytes:
            exceeded = True
            if truncate:
                remain = max_bytes - len(buf)
                if remain > 0:
                    buf.extend(chunk[:remain])
            while True:
                extra = stream.read(65536)
                if not extra:
                    break
            break
        buf.extend(chunk)
    collected["data"] = bytes(buf)
    collected["exceeded"] = exceeded


def _posix_terminate(supervised: SupervisedProcess, *, grace_seconds: float) -> None:
    pid = supervised.process_group or supervised.process.pid
    if pid is None:
        return
    tracked = _posix_descendants(pid) | {pid}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        supervised.process.terminate()
    _posix_signal_pids(tracked, signal.SIGTERM)
    try:
        supervised.process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    tracked |= _posix_descendants(pid)
    tracked |= _posix_descendants_of(tracked)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            supervised.process.kill()
        except OSError:
            pass
    _posix_signal_pids(tracked, signal.SIGKILL)
    try:
        supervised.process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        supervised.cleanup_failed = True
        supervised.cleanup_reason = "posix process group still alive after SIGKILL"
    leftover = {item for item in tracked if _pid_alive(item)}
    if leftover:
        supervised.cleanup_failed = True
        supervised.cleanup_reason = "posix descendants still alive after SIGKILL"


def _posix_signal_pids(pids: set[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            continue


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def posix_tree_members(root_pid: int) -> set[int]:
    """Root pid plus every live descendant, read from /proc. POSIX only."""

    return _posix_descendants_of({root_pid})


def _posix_descendants(root_pid: int) -> set[int]:
    return _posix_descendants_of({root_pid}) - {root_pid}


def _posix_descendants_of(roots: set[int]) -> set[int]:
    if os.name != "posix" or not roots:
        return set(roots)
    proc = Path("/proc")
    if not proc.is_dir():
        return set(roots)
    children: dict[int, set[int]] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        ppid = _posix_ppid(entry)
        if ppid is None:
            continue
        children.setdefault(ppid, set()).add(pid)
    found = set(roots)
    stack = list(roots)
    while stack:
        current = stack.pop()
        for child in children.get(current, ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _posix_ppid(proc_dir: Path) -> int | None:
    try:
        stat = (proc_dir / "stat").read_text(encoding="utf-8")
    except OSError:
        return None
    close = stat.rfind(")")
    if close < 0:
        return None
    fields = stat[close + 1 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _windows_job_structures():
    """ctypes mirrors of the Win32 job object limit structures."""

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def windows_job_limits(job_handle: int) -> dict[str, int] | None:
    """Read the limits the kernel actually applied to a job. None means undecidable."""

    if not job_handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    extended = _windows_job_structures()
    info = extended()
    returned = wintypes.DWORD()
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    ok = kernel32.QueryInformationJobObject(
        wintypes.HANDLE(job_handle),
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(returned),
    )
    if not ok:
        return None
    return {
        "limit_flags": int(info.BasicLimitInformation.LimitFlags),
        "active_process_limit": int(info.BasicLimitInformation.ActiveProcessLimit),
        "job_memory_limit": int(info.JobMemoryLimit),
        "process_memory_limit": int(info.ProcessMemoryLimit),
    }


def _windows_assign_job(
    pid: int | None,
    *,
    active_process_limit: int | None = None,
    job_memory_limit_bytes: int | None = None,
    process_memory_limit_bytes: int | None = None,
) -> tuple[int | None, str | None]:
    if pid is None:
        return None, "missing pid"
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None, "ctypes unavailable"

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION = _windows_job_structures()

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None, f"CreateJobObjectW failed ({ctypes.get_last_error()})"
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if active_process_limit is not None and active_process_limit > 0:
        flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = int(active_process_limit)
    if job_memory_limit_bytes is not None and job_memory_limit_bytes > 0:
        # Aggregate ceiling for every process in the job, which is the tree-wide
        # bound. A per-process limit alone would not bound the tree.
        flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
        info.JobMemoryLimit = int(job_memory_limit_bytes)
    if process_memory_limit_bytes is not None and process_memory_limit_bytes > 0:
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = int(process_memory_limit_bytes)
    info.BasicLimitInformation.LimitFlags = flags
    if not kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return None, f"SetInformationJobObject failed ({ctypes.get_last_error()})"
    process_handle = kernel32.OpenProcess(
        PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SUSPEND_RESUME,
        False,
        pid,
    )
    if not process_handle:
        kernel32.CloseHandle(job)
        return None, f"OpenProcess failed ({ctypes.get_last_error()})"
    assigned = kernel32.AssignProcessToJobObject(job, process_handle)
    kernel32.CloseHandle(process_handle)
    if not assigned:
        kernel32.CloseHandle(job)
        return None, f"AssignProcessToJobObject failed ({ctypes.get_last_error()})"
    return int(job), None


def windows_process_in_job(pid: int | None, job_handle: int) -> bool | None:
    """Kernel check that pid is a member of job_handle. None means undecidable."""

    if pid is None or not job_handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    member = wintypes.BOOL()
    ok = kernel32.IsProcessInJob(handle, job_handle, ctypes.byref(member))
    kernel32.CloseHandle(handle)
    if not ok:
        return None
    return bool(member.value)


def _windows_resume(pid: int | None) -> str | None:
    """Resume a CREATE_SUSPENDED child after job assignment. Always attempt resume."""

    if pid is None:
        return "missing pid for resume"
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return "ctypes unavailable for resume"

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME | PROCESS_TERMINATE, False, pid)
    if not handle:
        return f"OpenProcess for resume failed ({ctypes.get_last_error()})"
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(handle)
    kernel32.CloseHandle(handle)
    if status != 0:
        return f"NtResumeProcess failed status={status}"
    return None


def _windows_terminate(supervised: SupervisedProcess, *, grace_seconds: float) -> None:
    if supervised.job_handle:
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateJobObject.restype = wintypes.BOOL
            kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            if not kernel32.TerminateJobObject(supervised.job_handle, 1):
                supervised.cleanup_failed = True
                supervised.cleanup_reason = f"TerminateJobObject failed ({ctypes.get_last_error()})"
        except OSError as exc:
            supervised.cleanup_failed = True
            supervised.cleanup_reason = f"TerminateJobObject raised {type(exc).__name__}"
    else:
        supervised.process.terminate()
    try:
        supervised.process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        supervised.process.kill()
        supervised.process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        supervised.cleanup_failed = True
        supervised.cleanup_reason = "windows process tree still alive after terminate"


def _close_job(supervised: SupervisedProcess) -> None:
    if not supervised.job_handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(supervised.job_handle)
    except OSError as exc:
        supervised.cleanup_failed = True
        supervised.cleanup_reason = f"CloseHandle failed: {type(exc).__name__}"
    finally:
        supervised.job_handle = None
