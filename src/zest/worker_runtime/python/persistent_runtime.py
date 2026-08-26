"""Persistent NDJSON Worker loop. One JSON object per stdin line, one WorkerResult per stdout line.

The loop refuses to process any request before the supervising parent has
acknowledged kernel-enforced resource containment.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TextIO

from .browser_containment import accept_containment, hello_document, set_containment
from .browser_engine import BrowserRuntimeLimits
from .browser_page import shutdown_engine
from .runtime import build_result, utc_now_rfc3339

BROWSER_WORKER_ENV = "ZEST_BROWSER_WORKER"


def _handshake(stdin: TextIO, stdout: TextIO) -> str | None:
    json.dump(hello_document(os.getpid()), stdout, separators=(",", ":"))
    stdout.write("\n")
    stdout.flush()
    line = stdin.readline()
    if not line:
        return "the parent closed stdin before acknowledging containment"
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        return f"containment acknowledgement is not JSON: {exc}"
    if not isinstance(message, dict):
        return "containment acknowledgement must be an object"
    limits = BrowserRuntimeLimits()
    ack, error = accept_containment(
        message,
        max_memory_bytes=limits.max_memory_bytes,
        max_descendant_processes=limits.max_descendant_processes,
        max_descendant_tasks=limits.max_descendant_tasks,
    )
    if ack is None:
        return error
    set_containment(ack)
    return None


def run_persistent(
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    handshake_error = _handshake(stdin, stdout)
    if handshake_error is not None:
        print(f"worker containment handshake failed: {handshake_error}", file=stderr)
        return 1
    exit_code = 0
    try:
        while True:
            line = stdin.readline()
            if not line:
                break
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"worker failed to parse request: {exc}", file=stderr)
                exit_code = 1
                break
            if isinstance(msg, dict) and msg.get("message_type") == "shutdown":
                break
            started_at = utc_now_rfc3339()
            try:
                result = build_result(msg, started_at)
            except Exception as exc:  # noqa: BLE001 — persistent loop must not leak traceback on stdout
                print(f"worker failed to build result: {exc}", file=stderr)
                exit_code = 1
                break
            json.dump(result, stdout, separators=(",", ":"))
            stdout.write("\n")
            stdout.flush()
    finally:
        try:
            shutdown_engine()
        except Exception as exc:  # noqa: BLE001 — shutdown must not hide the loop exit
            print(f"worker failed to stop browser engine: {exc}", file=stderr)
            exit_code = 1
    return exit_code
