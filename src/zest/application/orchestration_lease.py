"""Fenced ownership of one ResearchOrchestration by one runtime instance.

Reuses the existing `research_orchestration` checkpoint row (no new table)
per `IMPLEMENTATION_SEQUENCE_LOCK.md` Slice 1 / campaign Phase B. Ownership
is a CAS + monotonically increasing `lease_epoch`, not a distributed lock:
`LocalRunSupervisor` enforces it by renewing this lease at a fixed
heartbeat and stopping ticking immediately (no further step() calls) the
first time a renewal is rejected, i.e. once another runtime instance has
acquired a newer epoch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LeaseConfig:
    """Configurable lease timing. Not an architectural magic constant."""

    heartbeat_interval_seconds: float = 30.0
    lease_ttl_seconds: float = 90.0

    def __post_init__(self) -> None:
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be > 0")
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be > 0")
        if self.lease_ttl_seconds <= self.heartbeat_interval_seconds:
            raise ValueError(
                "lease_ttl_seconds must be greater than heartbeat_interval_seconds "
                "or a live owner could expire itself between heartbeats"
            )
