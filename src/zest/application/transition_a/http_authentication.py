"""http.authentication Transition A normalizer. Factual auth observations only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import HTTP_AUTHENTICATION_CAPABILITY

HTTP_AUTHENTICATION_NORMALIZER_VERSION = "http.authentication.v1"
HTTP_AUTHENTICATION_OBSERVATION_KIND = "HTTP_AUTHENTICATION"
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
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "is_vulnerable",
        "severity",
        "finding",
        "confidence",
        "cookie",
        "authorization",
        "set-cookie",
        "password",
        "token",
        "secret",
        "session",
    }
)


class HttpAuthenticationNormalizer:
    capability = HTTP_AUTHENTICATION_CAPABILITY
    action = "login"
    version = HTTP_AUTHENTICATION_NORMALIZER_VERSION

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
                f"unsupported WorkerResult.status for http.authentication: {status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError("SUCCEEDED http.authentication requires raw_result")
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        payload = _factual_payload(raw_result, arguments)
        overlap = FORBIDDEN_PAYLOAD_KEYS.intersection(key.lower() for key in payload)
        if overlap:
            raise MalformedNormalizedPayloadError(
                f"http.authentication observation must not carry {sorted(overlap)}"
            )
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=HTTP_AUTHENTICATION_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )


def _factual_payload(raw_result: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    status_code = raw_result.get("status_code")
    if not isinstance(status_code, int):
        raise MalformedNormalizedPayloadError("raw_result.status_code must be an int")
    established = raw_result.get("session_established")
    if not isinstance(established, bool):
        raise MalformedNormalizedPayloadError("raw_result.session_established must be a bool")
    path = raw_result.get("path") or arguments.get("path")
    origin = raw_result.get("authorized_origin") or arguments.get("authorized_origin")
    identity_id = raw_result.get("identity_id") or arguments.get("identity_id")
    session_context_id = raw_result.get("session_context_id") or arguments.get("session_context_id")
    if not isinstance(path, str) or not isinstance(origin, str):
        raise MalformedNormalizedPayloadError("raw_result origin and path must be strings")
    payload: dict[str, Any] = {
        "authorized_origin": origin,
        "method": "POST",
        "path": path,
        "status_code": status_code,
        "status_class": _status_class(status_code),
        "session_established": established,
        "identity_id": identity_id,
        "session_context_id": session_context_id,
        "redirect": False,
    }
    return payload


def _status_class(status_code: int) -> str:
    if 100 <= status_code <= 199:
        return "1xx"
    if 200 <= status_code <= 299:
        return "2xx"
    if 300 <= status_code <= 399:
        return "3xx"
    if 400 <= status_code <= 499:
        return "4xx"
    if 500 <= status_code <= 599:
        return "5xx"
    return "unknown"
