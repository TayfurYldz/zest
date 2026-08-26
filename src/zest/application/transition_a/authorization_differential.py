"""http.authorization.differential Transition A normalizer. Observed behavior only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_ACTION,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
)

HTTP_AUTHORIZATION_DIFFERENTIAL_NORMALIZER_VERSION = "http.authorization.differential.v1"
HTTP_AUTHORIZATION_DIFFERENTIAL_OBSERVATION_KIND = "HTTP_AUTHORIZATION_DIFFERENTIAL"
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
_REQUEST_KEYS = (
    "owner_request",
    "cross_object_request",
    "secure_control",
    "unauthenticated_control",
)


class HttpAuthorizationDifferentialNormalizer:
    capability = HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY
    action = HTTP_AUTHORIZATION_DIFFERENTIAL_ACTION
    version = HTTP_AUTHORIZATION_DIFFERENTIAL_NORMALIZER_VERSION

    def normalize(
        self,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> tuple[ObservationDraft, ...]:
        status = result.get("status")
        if status in STATUSES_WITHOUT_TARGET_OBSERVATION:
            return ()
        if status != "SUCCEEDED":
            raise MalformedNormalizedPayloadError(
                "unsupported WorkerResult.status for http.authorization.differential: "
                f"{status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError(
                "SUCCEEDED http.authorization.differential requires raw_result"
            )
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        payload = {
            "authorized_origin": raw_result.get("authorized_origin")
            or arguments.get("authorized_origin"),
            "mode": raw_result.get("mode") or arguments.get("mode"),
            "actor": arguments.get("actor"),
            "own_object": arguments.get("own_object"),
            "cross_object": arguments.get("cross_object"),
        }
        for key in _REQUEST_KEYS:
            item = raw_result.get(key)
            if not isinstance(item, Mapping):
                raise MalformedNormalizedPayloadError(
                    f"SUCCEEDED http.authorization.differential raw_result.{key} must be an object"
                )
            status_value = item.get("status")
            if not isinstance(status_value, int):
                raise MalformedNormalizedPayloadError(
                    f"SUCCEEDED http.authorization.differential raw_result.{key}.status must be an int"
                )
            payload[f"{key}_status"] = status_value
            owner = item.get("object_owner")
            if owner is not None:
                if not isinstance(owner, str) or not owner.strip():
                    raise MalformedNormalizedPayloadError(
                        f"raw_result.{key}.object_owner must be a non-empty string when present"
                    )
                payload[f"{key}_object_owner"] = owner.strip()
            visibility = item.get("object_visibility")
            if visibility is not None:
                if not isinstance(visibility, str) or not visibility.strip():
                    raise MalformedNormalizedPayloadError(
                        f"raw_result.{key}.object_visibility must be a non-empty string when present"
                    )
                payload[f"{key}_visibility"] = visibility.strip()
            readers = item.get("object_authorized_readers")
            if readers is not None:
                if not isinstance(readers, list) or not all(
                    isinstance(entry, str) and entry.strip() for entry in readers
                ):
                    raise MalformedNormalizedPayloadError(
                        f"raw_result.{key}.object_authorized_readers must be a list of strings when present"
                    )
                payload[f"{key}_authorized_readers"] = [entry.strip() for entry in readers]
            resource_kind = item.get("object_resource_kind")
            if resource_kind is not None:
                if not isinstance(resource_kind, str) or not resource_kind.strip():
                    raise MalformedNormalizedPayloadError(
                        f"raw_result.{key}.object_resource_kind must be a non-empty string when present"
                    )
                payload[f"{key}_resource_kind"] = resource_kind.strip()
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=HTTP_AUTHORIZATION_DIFFERENTIAL_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )
