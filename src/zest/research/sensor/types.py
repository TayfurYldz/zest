"""Sensor domain types. Observations are not facts and not findings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable

from zest.core.enums import ReasonCode, ScopeClassification
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError

SENSOR_EPISTEMIC_STATUS = TargetEpistemicStatus.UNTRUSTED_EXTERNAL


class SensorError(Exception):
    """Operational sensor failure. Does not create Evidence or Finding."""

    def __init__(self, message: str, reason_code: ReasonCode) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class SensorTimeoutError(SensorError):
    """Sensor collection exceeded its bounded timeout."""

    def __init__(self, message: str = "sensor collection timed out") -> None:
        super().__init__(message, ReasonCode.SENSOR_TIMEOUT)


@dataclass(frozen=True)
class ScopeCensusView:
    """Read-only scope classification for census decisions."""

    classification: ScopeClassification
    reason_code: ReasonCode
    matched_rule_ids: tuple[str, ...] = ()

    def allows_census(self) -> bool:
        return self.classification in {
            ScopeClassification.IN_SCOPE,
            ScopeClassification.UNKNOWN,
        }


@runtime_checkable
class FixtureLoader(Protocol):
    """Test-time fixture source for sensor responses."""

    def load(self, sensor_id: str, target_reference: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SensorObservation:
    """One raw observation produced by a sensor.

    The sensor is not authority: payload is UNTRUSTED_EXTERNAL and may only
    become a DiscoveryFact after deterministic admission.
    """

    observation_id: str
    research_run_id: str
    sensor_id: str
    target_reference: str
    collected_at: datetime
    payload_digest: str
    source_metadata: Mapping[str, Any]
    payload: Mapping[str, Any]
    epistemic_status: TargetEpistemicStatus = field(
        default=SENSOR_EPISTEMIC_STATUS, init=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(self, "sensor_id", _require_text(self.sensor_id, "sensor_id"))
        object.__setattr__(
            self, "target_reference", _require_text(self.target_reference, "target_reference")
        )
        object.__setattr__(self, "payload_digest", _require_text(self.payload_digest, "payload_digest"))
        if not isinstance(self.collected_at, datetime):
            raise ResearchInputError("collected_at must be a datetime")
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ResearchInputError("collected_at must be timezone-aware")
        if not isinstance(self.source_metadata, Mapping):
            raise ResearchInputError("source_metadata must be a mapping")
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")


@runtime_checkable
class SensorPort(Protocol):
    """Passive/semi-passive sensor contract."""

    sensor_id: str

    def collect(
        self,
        observation_id: str,
        target_reference: str,
        scope_view: ScopeCensusView,
        *,
        timeout_seconds: float = 30.0,
        research_run_id: str = "",
    ) -> SensorCollectionResult:
        """Return observations for the target.

        The caller (application layer) supplies observation_id. Sensors do not
        generate authoritative identifiers.

        Must not perform active probing when scope_view disallows census.
        Must bound runtime and report consumed budget units.
        """
        ...


@dataclass(frozen=True)
class SensorCollectionResult:
    sensor_id: str
    observations: tuple[SensorObservation, ...]
    errors: tuple[SensorError, ...]
    budget_units_consumed: int
    completed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "sensor_id", _require_text(self.sensor_id, "sensor_id"))
        if not isinstance(self.observations, tuple):
            raise ResearchInputError("observations must be a tuple")
        if not isinstance(self.errors, tuple):
            raise ResearchInputError("errors must be a tuple")
        if not isinstance(self.budget_units_consumed, int) or self.budget_units_consumed < 0:
            raise ResearchInputError("budget_units_consumed must be a non-negative int")
        if not isinstance(self.completed_at, datetime):
            raise ResearchInputError("completed_at must be a datetime")


def denied_result(sensor_id: str, error: SensorError) -> SensorCollectionResult:
    return SensorCollectionResult(
        sensor_id=sensor_id,
        observations=(),
        errors=(error,),
        budget_units_consumed=1,
        completed_at=datetime.now(timezone.utc),
    )


def error_result(sensor_id: str, error: SensorError) -> SensorCollectionResult:
    return SensorCollectionResult(
        sensor_id=sensor_id,
        observations=(),
        errors=(error,),
        budget_units_consumed=1,
        completed_at=datetime.now(timezone.utc),
    )


def empty_result(sensor_id: str) -> SensorCollectionResult:
    return SensorCollectionResult(
        sensor_id=sensor_id,
        observations=(),
        errors=(SensorError("no fixture loader configured", ReasonCode.SENSOR_FAILED),),
        budget_units_consumed=1,
        completed_at=datetime.now(timezone.utc),
    )


def build_observation(
    observation_id: str,
    sensor_id: str,
    target_reference: str,
    research_run_id: str,
    payload: Mapping[str, Any],
    source_metadata: Mapping[str, Any],
    collected_at: datetime | None = None,
) -> SensorObservation:
    when = collected_at if collected_at is not None else datetime.now(timezone.utc)
    payload_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return SensorObservation(
        observation_id=observation_id,
        research_run_id=research_run_id,
        sensor_id=sensor_id,
        target_reference=target_reference,
        collected_at=when,
        payload_digest=hashlib.sha256(payload_bytes).hexdigest(),
        source_metadata=dict(source_metadata),
        payload=dict(payload),
    )


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()
