"""Test WorkerPort that records invocations. Not a production adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome, WorkerPort
from support.fake_unit_of_work import _Store

STARTED_AT = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
COMPLETED_AT = datetime(2026, 8, 16, 20, 0, 1, tzinfo=timezone.utc)


def completed_diagnostic_outcome(request: Mapping[str, Any]) -> WorkerInvocationOutcome:
    arguments = request.get("arguments")
    message = ""
    if isinstance(arguments, Mapping):
        raw = arguments.get("message", "")
        message = raw if isinstance(raw, str) else ""
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        worker_result={
            "contract_version": "v1",
            "correlation": dict(request["correlation"]),
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-16T20:00:00Z",
            "completed_at": "2026-08-16T20:00:01Z",
            "raw_result": {"echoed": message, "capability": "diagnostic.echo"},
        },
        exit_code=0,
    )


def invocation_outcome(
    status: InvocationStatus,
    *,
    reason: str = "injected",
) -> WorkerInvocationOutcome:
    return WorkerInvocationOutcome(
        invocation_status=status,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        worker_result=None,
        reason=reason,
    )


class RecordingWorkerPort:
    def __init__(
        self,
        *,
        store: _Store | None = None,
        handler: Callable[[Mapping[str, Any]], WorkerInvocationOutcome] | None = None,
        outcome: WorkerInvocationOutcome | None = None,
        inner: WorkerPort | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._store = store
        self._handler = handler or completed_diagnostic_outcome
        self._outcome = outcome
        self._inner = inner

    def invoke(
        self,
        request: Mapping[str, object],
        *,
        timeout_ms: int | None = None,
    ) -> WorkerInvocationOutcome:
        if self._store is not None and self._store.transaction_open_on_current_thread():
            raise AssertionError("Worker invoked while a Data transaction is open")
        if self._store is not None:
            correlation = request.get("correlation")
            if not isinstance(correlation, Mapping):
                raise AssertionError("WorkerRequest missing correlation")
            request_id = str(correlation["request_id"])
            attempt_id = self._store.execution_attempts_by_request.get(request_id)
            record = (
                self._store.execution_attempts.get(attempt_id) if attempt_id else None
            )
            if record is None or record.state != "DISPATCHING":
                raise AssertionError("Worker invoked before durable DISPATCHING intent")
        self.calls.append({"request": dict(request), "timeout_ms": timeout_ms})
        if self._inner is not None:
            return self._inner.invoke(request, timeout_ms=timeout_ms)
        if self._outcome is not None:
            return self._outcome
        return self._handler(request)
