"""Persistent browser Worker adapter. One long-lived process, one WorkerRequest per invoke.

The adapter establishes kernel-enforced resource containment before the Worker is
allowed to create Chromium, and it refuses to run the browser at all when that
enforcement cannot be established.

Does not import Playwright. Does not import application, research, data, or core.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable, Mapping

from zest.platform.browser_resource_control import (
    BrowserResourceController,
    BrowserResourceLimits,
    browser_resource_controller,
)
from zest.platform.contract_validation import (
    ContractValidationError,
    ContractValidator,
)
from zest.platform.local_process_worker import (
    DEFAULT_MAX_STDERR_BYTES,
    LocalProcessWorkerConfig,
    build_worker_environment,
)
from zest.platform.process_tree import (
    SupervisedProcess,
    release_supervision,
    spawn_supervised,
    terminate_tree,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome

BROWSER_WORKER_ENV = "ZEST_BROWSER_WORKER"
DEFAULT_MAX_STDOUT_BYTES = 65_536
SHUTDOWN_WAIT_SECONDS = 2.0
HANDSHAKE_TIMEOUT_SECONDS = 10.0
CONTAINMENT_MESSAGE_TYPE = "containment_ready"
BROWSER_WORKER_PROTOCOL = "browser.worker.v2"
HELLO_MESSAGE_TYPE = "hello"
CONTAINMENT_UNAVAILABLE_REASON = "browser resource containment unavailable"
RESOURCE_BREACH_REASON = "browser resource limit breached"
CLEANUP_FAILED_REASON = "browser resource containment cleanup failed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class PersistentBrowserWorkerAdapter:
    """Keep a supervised Python Worker alive across invoke() calls."""

    def __init__(
        self,
        config: LocalProcessWorkerConfig | None = None,
        validator: ContractValidator | None = None,
        *,
        resource_limits: BrowserResourceLimits | None = None,
        controller_factory: Callable[[BrowserResourceLimits], BrowserResourceController]
        | None = None,
    ) -> None:
        self._config = config or LocalProcessWorkerConfig(worker_id="local-python-browser")
        self._validator = validator or ContractValidator()
        self._supervised: SupervisedProcess | None = None
        self._stderr_box: dict[str, object] = {"data": b"", "exceeded": False}
        self._stderr_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._resource_limits = resource_limits or BrowserResourceLimits()
        self._controller_factory = controller_factory or browser_resource_controller
        self._controller: BrowserResourceController | None = None
        self.containment_failure_reason: str | None = None

    @property
    def resource_controller(self) -> BrowserResourceController | None:
        return self._controller

    def probe_startup_readiness(self) -> tuple[bool, str]:
        """Prove Worker process startup and containment without dispatching a request.

        This establishes the same supervised process and containment handshake
        used by the first real invocation, but sends no WorkerRequest and causes
        no target contact.
        """

        with self._lock:
            error = self._ensure_process()
            if error is not None:
                return False, error
            return (
                True,
                "persistent browser worker process and containment handshake ready",
            )

    def invoke(
        self,
        request: Mapping[str, object],
        *,
        timeout_ms: int | None = None,
    ) -> WorkerInvocationOutcome:
        started = _utc_now()
        try:
            self._validator.validate_worker_request(request)
        except ContractValidationError as exc:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.CONTRACT_INVALID,
                started_at=started,
                completed_at=_utc_now(),
                reason=str(exc),
            )
        effective_timeout = self._effective_timeout_ms(request, timeout_ms)
        if effective_timeout <= 0:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.TIMED_OUT,
                started_at=started,
                completed_at=_utc_now(),
                reason="timeout_ms/budget max_runtime_ms is 0; worker not started",
            )
        with self._lock:
            spawn_error = self._ensure_process()
            if spawn_error is not None:
                return WorkerInvocationOutcome(
                    invocation_status=InvocationStatus.START_FAILED,
                    started_at=started,
                    completed_at=_utc_now(),
                    reason=spawn_error,
                )
            supervised = self._supervised
            assert supervised is not None
            process = supervised.process
            payload = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.flush()
            except OSError:
                return self._terminal_outcome(
                    InvocationStatus.PROCESS_FAILED,
                    started,
                    process,
                    "worker stdin write failed",
                )
            line, exceeded, timed_out, crashed = self._read_result_line(
                process, timeout_s=effective_timeout / 1000.0
            )
            if timed_out:
                return self._terminal_outcome(
                    InvocationStatus.TIMED_OUT,
                    started,
                    process,
                    "worker exceeded caller/budget timeout; no WorkerResult fabricated",
                )
            if crashed or not line:
                status = (
                    InvocationStatus.PROCESS_FAILED
                    if process.returncode not in (None, 0)
                    else InvocationStatus.PROTOCOL_ERROR
                )
                return self._terminal_outcome(
                    status,
                    started,
                    process,
                    "no stdout WorkerResult document",
                )
            if exceeded:
                return self._terminal_outcome(
                    InvocationStatus.PROTOCOL_ERROR,
                    started,
                    process,
                    "stdout exceeded max_stdout_bytes; truncated output is not a WorkerResult",
                )
            try:
                document = json.loads(_decode(line))
            except ValueError as exc:
                return self._terminal_outcome(
                    InvocationStatus.PROTOCOL_ERROR,
                    started,
                    process,
                    str(exc),
                )
            if not isinstance(document, dict):
                return self._terminal_outcome(
                    InvocationStatus.PROTOCOL_ERROR,
                    started,
                    process,
                    "stdout JSON must be an object",
                )
            stderr_text, stderr_truncated = self._stderr_snapshot()
            try:
                self._validator.validate_worker_result(document)
            except ContractValidationError as exc:
                return WorkerInvocationOutcome(
                    invocation_status=InvocationStatus.CONTRACT_INVALID,
                    started_at=started,
                    completed_at=_utc_now(),
                    exit_code=process.returncode,
                    stderr_diagnostics=stderr_text,
                    stderr_truncated=stderr_truncated,
                    reason=str(exc),
                )
            if not self._validator.correlation_matches(request, document):
                return WorkerInvocationOutcome(
                    invocation_status=InvocationStatus.CONTRACT_INVALID,
                    started_at=started,
                    completed_at=_utc_now(),
                    exit_code=process.returncode,
                    stderr_diagnostics=stderr_text,
                    stderr_truncated=stderr_truncated,
                    reason="correlation mismatch; WorkerResult not rewritten and not ingested",
                )
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=started,
                completed_at=_utc_now(),
                worker_result=document,
                exit_code=0,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
            )

    def shutdown(self) -> None:
        with self._lock:
            supervised = self._supervised
            if supervised is None:
                return
            process = supervised.process
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write(b'{"message_type":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=SHUTDOWN_WAIT_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._kill()

    def _ensure_process(self) -> str | None:
        supervised = self._supervised
        if supervised is not None and supervised.process.poll() is None:
            return None
        if supervised is not None:
            self._kill()
        if self.containment_failure_reason is not None:
            return f"{CLEANUP_FAILED_REASON}: {self.containment_failure_reason}"
        controller = self._controller_factory(self._resource_limits)
        readiness = controller.readiness()
        if not readiness.ready:
            return f"{CONTAINMENT_UNAVAILABLE_REASON}: {readiness.reason}"
        prepare_error = controller.prepare()
        if prepare_error is not None:
            controller.cleanup()
            return f"{CONTAINMENT_UNAVAILABLE_REASON}: {prepare_error}"
        self._controller = controller
        env = build_worker_environment(
            self._config.workers_python_path, self._config.worker_id
        )
        env[BROWSER_WORKER_ENV] = "1"
        cwd = (
            str(self._config.working_directory)
            if self._config.working_directory is not None
            else (
                str(self._config.workers_python_path)
                if self._config.workers_python_path is not None
                else None
            )
        )
        try:
            spawned = spawn_supervised(
                self._argv(),
                env=env,
                cwd=cwd,
                **controller.spawn_limits(),
            )
        except OSError as exc:
            controller.cleanup()
            self._controller = None
            return str(exc)
        self._supervised = spawned
        self._stderr_box = {"data": b"", "exceeded": False}
        assert spawned.process.stderr is not None
        self._stderr_thread = threading.Thread(
            target=_read_stream,
            args=(spawned.process.stderr, self._stderr_max_bytes()),
            kwargs={"truncate": True, "collected": self._stderr_box},
            daemon=True,
        )
        self._stderr_thread.start()
        handshake_error = self._complete_containment_handshake(spawned, controller)
        if handshake_error is not None:
            self._kill()
            return f"{CONTAINMENT_UNAVAILABLE_REASON}: {handshake_error}"
        return None

    def _complete_containment_handshake(
        self, spawned: SupervisedProcess, controller: BrowserResourceController
    ) -> str | None:
        """Contain the Worker pid before it is permitted to create Chromium."""

        line, exceeded, timed_out, crashed = self._read_result_line(
            spawned.process, timeout_s=HANDSHAKE_TIMEOUT_SECONDS
        )
        if timed_out:
            return "the browser Worker did not announce itself"
        if crashed or not line or exceeded:
            return "the browser Worker exited before announcing itself"
        try:
            hello = json.loads(_decode(line))
        except ValueError:
            return "the browser Worker announcement is not JSON"
        if not isinstance(hello, dict) or hello.get("message_type") != HELLO_MESSAGE_TYPE:
            return "the browser Worker announcement is not a hello message"
        if hello.get("protocol") != BROWSER_WORKER_PROTOCOL:
            return "browser Worker protocol mismatch"
        worker_pid = hello.get("pid")
        if not isinstance(worker_pid, int) or isinstance(worker_pid, bool) or worker_pid < 1:
            return "the browser Worker announced no pid"
        containment_error = controller.confirm_containment(spawned, worker_pid)
        if containment_error is not None:
            return containment_error
        kind, value = controller.pid_ceiling()
        ack = {
            "message_type": CONTAINMENT_MESSAGE_TYPE,
            "protocol": BROWSER_WORKER_PROTOCOL,
            "mechanism": controller.mechanism,
            "max_memory_bytes": self._resource_limits.max_memory_bytes,
            "pid_ceiling_kind": kind,
            "pid_ceiling_value": value,
        }
        try:
            assert spawned.process.stdin is not None
            spawned.process.stdin.write(
                (json.dumps(ack, separators=(",", ":")) + "\n").encode("utf-8")
            )
            spawned.process.stdin.flush()
        except OSError:
            return "cannot send the containment acknowledgement"
        return None

    def _read_result_line(
        self, process: subprocess.Popen[bytes], *, timeout_s: float
    ) -> tuple[bytes, bool, bool, bool]:
        collected: dict[str, object] = {"line": b"", "exceeded": False, "crashed": False}
        assert process.stdout is not None
        thread = threading.Thread(
            target=_read_one_line,
            args=(process.stdout, self._stdout_max_bytes(), collected),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=timeout_s)
        if thread.is_alive():
            return b"", False, True, False
        if process.poll() is not None and not collected.get("line"):
            return b"", bool(collected.get("exceeded")), False, True
        return (
            bytes(collected.get("line", b"") or b""),
            bool(collected.get("exceeded")),
            False,
            False,
        )

    def _kill(self) -> None:
        supervised = self._supervised
        controller = self._controller
        self._supervised = None
        self._controller = None
        if supervised is not None:
            process = supervised.process
            # Terminate before closing pipes. A reader thread blocked on stdout
            # holds the stream lock, so close() would wait for the child to exit
            # on its own instead of for the kill.
            terminate_tree(supervised)
            release_supervision(supervised)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
            if supervised.cleanup_failed and self.containment_failure_reason is None:
                self.containment_failure_reason = (
                    supervised.cleanup_reason or "process tree cleanup failed"
                )
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
            self._stderr_thread = None
        if controller is None:
            return
        kill_error = controller.kill_contained()
        cleanup_error = controller.cleanup()
        failure = kill_error or cleanup_error
        if failure is not None and self.containment_failure_reason is None:
            self.containment_failure_reason = failure

    def _terminal_outcome(
        self,
        status: InvocationStatus,
        started: datetime,
        process: subprocess.Popen[bytes],
        reason_base: str,
    ) -> WorkerInvocationOutcome:
        """Classify a breach, kill the tree, then snapshot drained stderr."""

        reason = self._breach_reason(reason_base)
        self._kill()
        stderr_text, stderr_truncated = self._stderr_snapshot()
        return WorkerInvocationOutcome(
            invocation_status=status,
            started_at=started,
            completed_at=_utc_now(),
            exit_code=process.returncode,
            stderr_diagnostics=stderr_text,
            stderr_truncated=stderr_truncated,
            reason=reason,
        )

    def _stderr_snapshot(self) -> tuple[str, bool]:
        return (
            _decode(self._stderr_box.get("data", b"") or b""),
            bool(self._stderr_box.get("exceeded")),
        )

    def _breach_reason(self, base: str) -> str:
        controller = self._controller
        breach = controller.resource_breach() if controller is not None else None
        if breach is None:
            return base
        return f"{RESOURCE_BREACH_REASON}: {breach}; {base}"

    def _argv(self) -> list[str]:
        if self._config.argv_override is not None:
            return list(self._config.argv_override)
        return [self._config.python_executable, "-m", self._config.module]

    def _effective_timeout_ms(
        self, request: Mapping[str, object], timeout_ms: int | None
    ) -> int:
        effective = self._config.default_timeout_ms if timeout_ms is None else timeout_ms
        budget = request.get("execution_budget")
        if isinstance(budget, Mapping):
            runtime = budget.get("max_runtime_ms")
            if isinstance(runtime, int):
                effective = min(effective, runtime)
        return effective

    def _stdout_max_bytes(self) -> int:
        configured = getattr(self._config, "max_stdout_bytes", DEFAULT_MAX_STDOUT_BYTES)
        return min(int(configured), DEFAULT_MAX_STDOUT_BYTES)

    def _stderr_max_bytes(self) -> int:
        return int(getattr(self._config, "max_stderr_bytes", DEFAULT_MAX_STDERR_BYTES))


def _read_one_line(stream, max_bytes: int, collected: dict[str, object]) -> None:
    buf = bytearray()
    exceeded = False
    while True:
        chunk = stream.read(1)
        if not chunk:
            break
        if chunk == b"\n":
            break
        if len(buf) >= max_bytes:
            exceeded = True
            while True:
                extra = stream.read(1)
                if not extra or extra == b"\n":
                    break
            break
        buf.extend(chunk)
    collected["line"] = bytes(buf)
    collected["exceeded"] = exceeded


def _read_stream(stream, max_bytes: int, *, truncate: bool, collected: dict[str, object]) -> None:
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
