"""One-shot Worker runtime: one stdin WorkerRequest, one stdout WorkerResult."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, TextIO

from .capabilities import execute

WORKER_ID_ENV = "ZEST_WORKER_ID"
DEFAULT_WORKER_ID = "local-python-diagnostic"


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_request(raw: str) -> Mapping[str, Any]:
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("WorkerRequest must be a JSON object")
    return document


def build_result(request: Mapping[str, Any], started_at: str) -> dict[str, Any]:
    worker_id = os.environ.get(WORKER_ID_ENV, DEFAULT_WORKER_ID).strip() or DEFAULT_WORKER_ID
    status, raw_result, diagnostics = execute(request)
    ephemeral = None
    if isinstance(raw_result, dict) and "_ephemeral_session_cookie" in raw_result:
        ephemeral = {"session_cookie": raw_result.pop("_ephemeral_session_cookie")}
    result: dict[str, Any] = {
        "contract_version": "v1",
        "correlation": request.get("correlation"),
        "worker_id": worker_id,
        "status": status,
        "started_at": started_at,
        "completed_at": utc_now_rfc3339(),
        "raw_result": raw_result,
    }
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    if ephemeral is not None:
        result["ephemeral_secrets"] = ephemeral
    return result


def run(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    if os.environ.get("ZEST_BROWSER_WORKER") == "1":
        from .persistent_runtime import run_persistent

        return run_persistent(stdin, stdout, stderr)
    started_at = utc_now_rfc3339()
    raw = stdin.read()
    try:
        request = load_request(raw)
        result = build_result(request, started_at)
    except Exception as exc:  # noqa: BLE001 — Worker must still emit one protocol document or exit non-zero without stdout junk
        print(f"worker failed to build result: {exc}", file=stderr)
        return 1
    json.dump(result, stdout, separators=(",", ":"))
    stdout.write("\n")
    stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
