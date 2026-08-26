"""One-shot local process Worker adapter. First transport, not architecture.

Process-tree supervision uses shell=False. Timeout/cancel terminates descendants.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from zest.platform.contract_validation import (
    ContractValidationError,
    ContractValidator,
)
from zest.platform.process_tree import release_supervision, spawn_supervised, terminate_tree
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome

DEFAULT_MAX_STDOUT_BYTES = 1_048_576
DEFAULT_MAX_STDERR_BYTES = 65_536
DEFAULT_TIMEOUT_MS = 30_000
FORBIDDEN_CHILD_ENV_MARKERS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "DATABASE_URL",
    "POSTGRES",
    "OPENAI",
    "ANTHROPIC",
    "AWS_",
)
FORBIDDEN_CHILD_ENV_EXACT = {
    "ZEST_DATABASE_URL",
    "ZEST_TEST_DATABASE_URL",
}
PASSTHROUGH_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


PACKAGED_WORKER_MODULE = "zest.worker_runtime.python"


@dataclass(frozen=True)
class LocalProcessWorkerConfig:
    worker_id: str = "local-python-diagnostic"
    python_executable: str = sys.executable
    workers_python_path: Path | None = None
    module: str = PACKAGED_WORKER_MODULE
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    default_timeout_ms: int = DEFAULT_TIMEOUT_MS
    argv_override: tuple[str, ...] | None = None
    working_directory: Path | None = None


def checkout_src_pythonpath() -> Path | None:
    """If running from a source checkout, return src/ so the child can import zest.

    Installed wheels already have the package on sys.path and return None.
    """

    import zest

    root = Path(zest.__file__).resolve().parent.parent
    if root.name == "src" and (root / "zest").is_dir():
        return root
    return None


def build_worker_environment(
    pythonpath: Path | None,
    worker_id: str,
) -> dict[str, str]:
    """Explicit child env. Does not copy application secrets or database URLs."""
    env: dict[str, str] = {}
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    resolved_parts: list[str] = []
    if pythonpath is not None:
        resolved_parts.append(str(pythonpath))
    checkout = checkout_src_pythonpath()
    if checkout is not None and str(checkout) not in resolved_parts:
        resolved_parts.append(str(checkout))
    if resolved_parts:
        env["PYTHONPATH"] = os.pathsep.join(resolved_parts)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["ZEST_WORKER_ID"] = worker_id
    for key in list(env):
        upper = key.upper()
        if key in FORBIDDEN_CHILD_ENV_EXACT or any(
            marker in upper for marker in FORBIDDEN_CHILD_ENV_MARKERS
        ):
            del env[key]
    return env


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _read_stream(
    stream,
    max_bytes: int,
    *,
    truncate: bool,
    collected: dict[str, object],
) -> None:
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


def _parse_single_json(text: str) -> Mapping[str, object]:
    decoder = json.JSONDecoder()
    document, index = decoder.raw_decode(text)
    if text[index:].strip():
        raise ValueError("stdout contained extra protocol output after the JSON document")
    if not isinstance(document, dict):
        raise ValueError("stdout JSON must be an object")
    return document


class LocalProcessWorkerAdapter:
    """Spawn one child, send one WorkerRequest, receive one WorkerResult, exit."""

    def __init__(
        self,
        config: LocalProcessWorkerConfig | None = None,
        validator: ContractValidator | None = None,
    ) -> None:
        self._config = config or LocalProcessWorkerConfig()
        self._validator = validator or ContractValidator()

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

        argv = self._argv()
        env = build_worker_environment(
            self._config.workers_python_path, self._config.worker_id
        )
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
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
            supervised = spawn_supervised(argv, env=env, cwd=cwd)
            process = supervised.process
        except OSError as exc:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.START_FAILED,
                started_at=started,
                completed_at=_utc_now(),
                reason=str(exc),
            )

        stdout_box: dict[str, object] = {}
        stderr_box: dict[str, object] = {}
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_thread = threading.Thread(
            target=_read_stream,
            args=(process.stdout, self._config.max_stdout_bytes),
            kwargs={"truncate": False, "collected": stdout_box},
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stream,
            args=(process.stderr, self._config.max_stderr_bytes),
            kwargs={"truncate": True, "collected": stderr_box},
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            try:
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
            except OSError:
                terminate_tree(supervised)

            timeout_s = effective_timeout / 1000.0
            try:
                exit_code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                terminate_tree(supervised)
                stdout_thread.join(timeout=1.0)
                stderr_thread.join(timeout=1.0)
                return WorkerInvocationOutcome(
                    invocation_status=InvocationStatus.TIMED_OUT,
                    started_at=started,
                    completed_at=_utc_now(),
                    exit_code=process.returncode,
                    stderr_diagnostics=_decode(stderr_box.get("data", b"") or b""),
                    stderr_truncated=bool(stderr_box.get("exceeded")),
                    reason="worker exceeded caller/budget timeout; no WorkerResult fabricated",
                )

            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)
        finally:
            self._close_pipes(process)
            release_supervision(supervised)
        completed = _utc_now()
        stderr_text = _decode(stderr_box.get("data", b"") or b"")
        stderr_truncated = bool(stderr_box.get("exceeded"))

        if stdout_box.get("exceeded"):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.PROTOCOL_ERROR,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason="stdout exceeded max_stdout_bytes; truncated output is not a WorkerResult",
            )

        stdout_text = _decode(stdout_box.get("data", b"") or b"").strip()
        if not stdout_text:
            status = (
                InvocationStatus.PROCESS_FAILED
                if exit_code != 0
                else InvocationStatus.PROTOCOL_ERROR
            )
            return WorkerInvocationOutcome(
                invocation_status=status,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason="no stdout WorkerResult document",
            )

        try:
            document = _parse_single_json(stdout_text)
        except ValueError as exc:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.PROTOCOL_ERROR,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason=str(exc),
            )

        try:
            self._validator.validate_worker_result(document)
        except ContractValidationError as exc:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.CONTRACT_INVALID,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason=str(exc),
            )

        if not self._validator.correlation_matches(request, document):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.CONTRACT_INVALID,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason="correlation mismatch; WorkerResult not rewritten and not ingested",
            )

        if exit_code != 0:
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.PROTOCOL_ERROR,
                started_at=started,
                completed_at=completed,
                exit_code=exit_code,
                stderr_diagnostics=stderr_text,
                stderr_truncated=stderr_truncated,
                reason="valid-looking WorkerResult with unexpected non-zero exit; fail closed",
            )

        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=started,
            completed_at=completed,
            worker_result=document,
            exit_code=exit_code,
            stderr_diagnostics=stderr_text,
            stderr_truncated=stderr_truncated,
        )

    def _argv(self) -> list[str]:
        if self._config.argv_override is not None:
            return list(self._config.argv_override)
        return [self._config.python_executable, "-m", self._config.module]

    def _effective_timeout_ms(
        self, request: Mapping[str, object], timeout_ms: int | None
    ) -> int:
        effective = (
            self._config.default_timeout_ms if timeout_ms is None else timeout_ms
        )
        budget = request.get("execution_budget")
        if isinstance(budget, Mapping):
            runtime = budget.get("max_runtime_ms")
            if isinstance(runtime, int):
                effective = min(effective, runtime)
        return effective

    def _close_pipes(self, process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
