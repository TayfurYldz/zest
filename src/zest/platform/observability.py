"""Operational telemetry foundation. Not AuditEvent, Evidence, or domain truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol


from zest.safe_data import SecretMaterialError, reject_secret_keys


def _reject_secrets(payload: Mapping[str, object], field_name: str) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        cleaned = reject_secret_keys(payload, field_name)
    except SecretMaterialError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(cleaned, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return cleaned


@dataclass(frozen=True)
class TelemetryEvent:
    event: str
    outcome: str
    duration_ms: int = 0
    correlation_id: str | None = None
    research_run_id: str | None = None
    experiment_id: str | None = None
    request_id: str | None = None
    runtime_identity: str | None = None
    orchestration_cycle: int | None = None
    fields: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event, str) or not self.event.strip():
            raise ValueError("event must be a non-empty string")
        if not isinstance(self.outcome, str) or not self.outcome.strip():
            raise ValueError("outcome must be a non-empty string")
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise ValueError("duration_ms must be a non-negative int")
        object.__setattr__(self, "fields", _reject_secrets(self.fields, "fields"))

    def to_mapping(self) -> dict[str, object]:
        return {
            "event": self.event,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "correlation_id": self.correlation_id,
            "research_run_id": self.research_run_id,
            "experiment_id": self.experiment_id,
            "request_id": self.request_id,
            "runtime_identity": self.runtime_identity,
            "orchestration_cycle": self.orchestration_cycle,
            "fields": dict(self.fields),
            "contains_secrets": False,
        }


class ObservabilityPort(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
    def increment(self, metric: str, amount: int = 1) -> None: ...
    def snapshot(self) -> Mapping[str, int]: ...


class InMemoryObservability:
    """Process-local counters and events. Not authoritative domain state."""

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []
        self.metrics: dict[str, int] = {}

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    def increment(self, metric: str, amount: int = 1) -> None:
        if not isinstance(metric, str) or not metric.strip():
            raise ValueError("metric must be a non-empty string")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise ValueError("amount must be a non-negative int")
        self.metrics[metric] = self.metrics.get(metric, 0) + amount

    def snapshot(self) -> Mapping[str, int]:
        return dict(self.metrics)
