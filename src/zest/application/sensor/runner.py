"""Sensor acquisition runner.

Coordinates passive/semi-passive sensors for a single target under Core scope
control. Does not write domain truth: only SensorObservation records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ReasonCode
from zest.data.records import SensorObservationRecord
from zest.research.sensor import SensorObservation, SensorPort
from zest.research.sensor.types import ScopeCensusView, SensorError


@dataclass(frozen=True)
class SensorAcquisitionResult:
    research_run_id: str
    target_reference: str
    observations: tuple[SensorObservation, ...]
    errors: tuple[SensorError, ...]
    budget_units_consumed: int


class SensorAcquisitionRunner:
    """Run a sensor suite against one target, respecting scope census rules."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        sensors: Sequence[SensorPort],
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._sensors = tuple(sensors)
        self._clock = clock or SystemClock()

    def run(
        self,
        research_run_id: str,
        target_reference: str,
        scope_view: ScopeCensusView,
        *,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> SensorAcquisitionResult:
        if not scope_view.allows_census():
            return SensorAcquisitionResult(
                research_run_id=research_run_id,
                target_reference=target_reference,
                observations=(),
                errors=(
                    SensorError(
                        "census denied by scope classification",
                        ReasonCode.CENSUS_DENIED,
                    ),
                ),
                budget_units_consumed=0,
            )

        observations: list[SensorObservation] = []
        errors: list[SensorError] = []
        budget_units = 0
        completed_at = self._clock.now()

        for sensor in self._sensors:
            result = sensor.collect(
                new_opaque_id(),
                target_reference,
                scope_view,
                timeout_seconds=30.0,
                research_run_id=research_run_id,
            )
            observations.extend(result.observations)
            errors.extend(result.errors)
            budget_units += result.budget_units_consumed

        with self._uow_factory.open() as uow:
            for observation in observations:
                uow.sensor_observations.insert(
                    SensorObservationRecord(
                        observation_id=observation.observation_id,
                        research_run_id=observation.research_run_id,
                        sensor_id=observation.sensor_id,
                        target_reference=observation.target_reference,
                        collected_at=observation.collected_at,
                        payload_digest=observation.payload_digest,
                        epistemic_status=observation.epistemic_status.value,
                        source_metadata=dict(observation.source_metadata)
                        | dict(source_metadata or {}),
                        payload=dict(observation.payload),
                        created_at=completed_at,
                    )
                )
            uow.commit()

        return SensorAcquisitionResult(
            research_run_id=research_run_id,
            target_reference=target_reference,
            observations=tuple(observations),
            errors=tuple(errors),
            budget_units_consumed=budget_units,
        )
