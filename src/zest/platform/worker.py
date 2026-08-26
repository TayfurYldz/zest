"""Worker invocation port. Not a transport architecture. Not WorkerResult."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping, Protocol


class InvocationStatus(Enum):
    """Control-plane runtime outcome. Distinct from WorkerResult.status."""

    COMPLETED = "COMPLETED"
    START_FAILED = "START_FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    CONTRACT_INVALID = "CONTRACT_INVALID"


@dataclass(frozen=True)
class WorkerInvocationOutcome:
    """Result of attempting to invoke a Worker. worker_result is set only when COMPLETED."""

    invocation_status: InvocationStatus
    started_at: datetime
    completed_at: datetime
    worker_result: Mapping[str, object] | None = None
    exit_code: int | None = None
    stderr_diagnostics: str = ""
    stderr_truncated: bool = False
    reason: str | None = None


class WorkerPort(Protocol):
    """Invoke one authorized WorkerRequest. Implementations live in adapters, not Core."""

    def invoke(
        self,
        request: Mapping[str, object],
        *,
        timeout_ms: int | None = None,
    ) -> WorkerInvocationOutcome: ...
