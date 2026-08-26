"""Argv process runner for CLI/session runtimes. First transport, not architecture.

Does not import Research. Does not copy database URLs or provider API keys.
Delegates to ProcessTreeSupervisor with shell=False.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from zest.platform.process_tree import run_supervised

DEFAULT_MAX_STDOUT_BYTES = 1_048_576
DEFAULT_MAX_STDERR_BYTES = 65_536
DEFAULT_TIMEOUT_MS = 15_000
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
    "ZEST_",
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
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "COMSPEC",
)


class ArgvProcessStatus(Enum):
    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMED_OUT = "TIMED_OUT"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ArgvProcessResult:
    status: ArgvProcessStatus
    argv: tuple[str, ...]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stderr_truncated: bool = False
    reason: str | None = None
    cleanup_failed: bool = False
    cleanup_reason: str | None = None


@dataclass(frozen=True)
class ArgvProcessConfig:
    executable: str
    extra_argv: tuple[str, ...] = ()
    working_directory: Path | None = None
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    extra_env: tuple[tuple[str, str], ...] = ()


def build_cli_environment(extra_env: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    for key, value in extra_env:
        env[key] = value
    for key in list(env):
        upper = key.upper()
        if key in FORBIDDEN_CHILD_ENV_EXACT or any(
            marker in upper for marker in FORBIDDEN_CHILD_ENV_MARKERS
        ):
            del env[key]
    return env


def resolve_executable(name: str) -> str | None:
    return shutil.which(name)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def run_argv(
    argv: tuple[str, ...],
    *,
    config: ArgvProcessConfig | None = None,
    stdin_bytes: bytes | None = None,
    timeout_ms: int | None = None,
) -> ArgvProcessResult:
    """Run one argv vector. Never uses a shell. Terminates the process tree on timeout."""

    if not argv or not argv[0].strip():
        return ArgvProcessResult(
            status=ArgvProcessStatus.UNAVAILABLE,
            argv=argv,
            reason="executable is empty",
        )
    cfg = config or ArgvProcessConfig(executable=argv[0])
    timeout = timeout_ms if timeout_ms is not None else cfg.timeout_ms
    env = build_cli_environment(cfg.extra_env)
    cwd = str(cfg.working_directory) if cfg.working_directory is not None else None
    try:
        result = run_supervised(
            argv,
            env=env,
            cwd=cwd,
            stdin_bytes=stdin_bytes,
            timeout_seconds=timeout / 1000.0,
            max_stdout_bytes=cfg.max_stdout_bytes,
            max_stderr_bytes=cfg.max_stderr_bytes,
        )
    except FileNotFoundError:
        return ArgvProcessResult(
            status=ArgvProcessStatus.UNAVAILABLE,
            argv=argv,
            reason="executable not found",
        )
    except OSError as exc:
        return ArgvProcessResult(
            status=ArgvProcessStatus.PROCESS_FAILED,
            argv=argv,
            reason=str(exc),
        )

    stdout_text = _decode(result.stdout)
    stderr_text = _decode(result.stderr)
    if result.timed_out:
        return ArgvProcessResult(
            status=ArgvProcessStatus.TIMED_OUT,
            argv=argv,
            exit_code=result.exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            stderr_truncated=result.stderr_truncated,
            reason=result.cleanup_reason or "process exceeded timeout",
            cleanup_failed=result.cleanup_failed,
            cleanup_reason=result.cleanup_reason,
        )
    if result.cancelled:
        return ArgvProcessResult(
            status=ArgvProcessStatus.CANCELLED,
            argv=argv,
            exit_code=result.exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            stderr_truncated=result.stderr_truncated,
            reason=result.cleanup_reason or "process cancelled",
            cleanup_failed=result.cleanup_failed,
            cleanup_reason=result.cleanup_reason,
        )
    if result.stdout_truncated:
        return ArgvProcessResult(
            status=ArgvProcessStatus.PROTOCOL_ERROR,
            argv=argv,
            exit_code=result.exit_code,
            stdout="",
            stderr=stderr_text,
            stderr_truncated=result.stderr_truncated,
            reason="stdout exceeded max_stdout_bytes",
            cleanup_failed=result.cleanup_failed,
            cleanup_reason=result.cleanup_reason,
        )
    if result.exit_code != 0:
        return ArgvProcessResult(
            status=ArgvProcessStatus.PROCESS_FAILED,
            argv=argv,
            exit_code=result.exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            stderr_truncated=result.stderr_truncated,
            reason="non-zero exit",
            cleanup_failed=result.cleanup_failed,
            cleanup_reason=result.cleanup_reason,
        )
    return ArgvProcessResult(
        status=ArgvProcessStatus.COMPLETED,
        argv=argv,
        exit_code=result.exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
        stderr_truncated=result.stderr_truncated,
        cleanup_failed=result.cleanup_failed,
        cleanup_reason=result.cleanup_reason,
    )
