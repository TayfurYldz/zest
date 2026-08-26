"""Fixture Workers for runtime tests. Not production capabilities."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone


def _worker_id() -> str:
    return os.environ.get("ZEST_WORKER_ID", "fixture-worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request() -> dict:
    return json.loads(sys.stdin.read())


def _result(request: dict, status: str = "SUCCEEDED") -> dict:
    return {
        "contract_version": "v1",
        "correlation": request.get("correlation"),
        "worker_id": _worker_id(),
        "status": status,
        "started_at": _now(),
        "completed_at": _now(),
        "raw_result": {"fixture": True},
    }


def crash() -> None:
    sys.exit(1)


def malformed() -> None:
    sys.stdout.write("this is not json\n")


def delay() -> None:
    time.sleep(5)
    json.dump(_result(_request()), sys.stdout)


def oversize_stdout() -> None:
    sys.stdout.write("x" * 2_000_000)


def oversize_stderr() -> None:
    request = _request()
    sys.stderr.write("y" * 200_000)
    json.dump(_result(request), sys.stdout)
    sys.stdout.write("\n")


def correlation_mismatch() -> None:
    request = _request()
    result = _result(request)
    correlation = dict(result["correlation"])
    correlation["correlation_id"] = "tampered-correlation"
    result["correlation"] = correlation
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


def invalid_schema() -> None:
    json.dump({"contract_version": "v1", "hello": "nope"}, sys.stdout)
    sys.stdout.write("\n")


def nonzero_with_result() -> None:
    json.dump(_result(_request()), sys.stdout)
    sys.stdout.write("\n")
    sys.exit(7)


def unknown_version() -> None:
    request = _request()
    result = _result(request)
    result["contract_version"] = "v2"
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


def extra_stdout() -> None:
    json.dump(_result(_request()), sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.write("banner\n")


HANDLERS = {
    "crash": crash,
    "malformed": malformed,
    "delay": delay,
    "oversize_stdout": oversize_stdout,
    "oversize_stderr": oversize_stderr,
    "correlation_mismatch": correlation_mismatch,
    "invalid_schema": invalid_schema,
    "nonzero_with_result": nonzero_with_result,
    "extra_stdout": extra_stdout,
    "unknown_version": unknown_version,
}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = HANDLERS.get(name)
    if handler is None:
        print("unknown fixture", file=sys.stderr)
        sys.exit(2)
    handler()
