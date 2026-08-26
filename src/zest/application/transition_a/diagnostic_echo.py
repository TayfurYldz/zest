"""diagnostic.echo Transition A normalizer. Not a security scanner."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import DIAGNOSTIC_ECHO_ACTION, DIAGNOSTIC_ECHO_CAPABILITY

DIAGNOSTIC_ECHO_NORMALIZER_VERSION = "diagnostic.echo.v1"
DIAGNOSTIC_ECHO_OBSERVATION_KIND = "diagnostic.echo"
STATUSES_WITHOUT_TARGET_OBSERVATION = frozenset(
    {
        "EXECUTION_FAILED",
        "BLOCKED",
        "CANCELLED",
        "TIMED_OUT",
        "BUDGET_EXHAUSTED",
        "REAUTHORIZATION_REQUIRED",
    }
)


class DiagnosticEchoNormalizer:
    capability = DIAGNOSTIC_ECHO_CAPABILITY
    action = DIAGNOSTIC_ECHO_ACTION
    version = DIAGNOSTIC_ECHO_NORMALIZER_VERSION

    def normalize(
        self,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> tuple[ObservationDraft, ...]:
        del request
        status = result.get("status")
        if status in STATUSES_WITHOUT_TARGET_OBSERVATION:
            return ()
        if status != "SUCCEEDED":
            raise MalformedNormalizedPayloadError(
                f"unsupported WorkerResult.status for diagnostic.echo: {status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError("SUCCEEDED diagnostic.echo requires raw_result")
        echoed = raw_result.get("echoed")
        if not isinstance(echoed, str):
            raise MalformedNormalizedPayloadError(
                "SUCCEEDED diagnostic.echo raw_result.echoed must be a string"
            )
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=DIAGNOSTIC_ECHO_OBSERVATION_KIND,
                payload={"echoed": echoed},
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )
