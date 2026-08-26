"""Historical URL sensor (Wayback CDX-compatible).

Fixture format is a CDX JSON response.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from zest.core.enums import ReasonCode
from zest.research.sensor.types import (
    FixtureLoader,
    ScopeCensusView,
    SensorCollectionResult,
    SensorError,
    SensorPort,
    SensorTimeoutError,
    build_observation,
    denied_result,
    empty_result,
    error_result,
)


class WaybackArchiveSensor:
    """Passive historical URL census sensor."""

    sensor_id = "sensor.archive"

    def __init__(self, fixture_loader: FixtureLoader | None = None) -> None:
        self._fixture_loader = fixture_loader

    def collect(
        self,
        observation_id: str,
        target_reference: str,
        scope_view: ScopeCensusView,
        *,
        timeout_seconds: float = 30.0,
        research_run_id: str = "",
    ) -> SensorCollectionResult:
        del timeout_seconds
        if not scope_view.allows_census():
            return denied_result(
                self.sensor_id,
                SensorError("census denied by scope", ReasonCode.CENSUS_DENIED),
            )

        if self._fixture_loader is None:
            return empty_result(self.sensor_id)

        data = self._fixture_loader.load(self.sensor_id, target_reference)
        if data.get("error") == "timeout":
            return error_result(self.sensor_id, SensorTimeoutError())
        if data.get("error") == "malformed":
            return error_result(
                self.sensor_id,
                SensorError("malformed fixture response", ReasonCode.SENSOR_FAILED),
            )

        payload = dict(data.get("payload", {}))
        source_metadata = dict(data.get("source_metadata", {}))
        source_metadata["fixture"] = True
        completed_at = datetime.now(timezone.utc)
        return SensorCollectionResult(
            sensor_id=self.sensor_id,
            observations=(
                build_observation(
                    observation_id,
                    self.sensor_id,
                    target_reference,
                    research_run_id,
                    payload,
                    source_metadata,
                ),
            ),
            errors=(),
            budget_units_consumed=1,
            completed_at=completed_at,
        )


# Protocol conformance is checked by unit tests, not at import time.
