"""http.transaction Transition A normalizer. Factual HTTP observations only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import HTTP_TRANSACTION_CAPABILITY

HTTP_TRANSACTION_NORMALIZER_VERSION = "http.transaction.v1"
HTTP_TRANSACTION_OBSERVATION_KIND = "HTTP_TRANSACTION"
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
    }
)
ALLOWED_RESPONSE_HEADER_KEYS = frozenset({"content-type", "location"})


class HttpTransactionNormalizer:
    capability = HTTP_TRANSACTION_CAPABILITY
    version = HTTP_TRANSACTION_NORMALIZER_VERSION

    def __init__(self, action: str) -> None:
        self.action = action

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
                f"unsupported WorkerResult.status for http.transaction: {status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError("SUCCEEDED http.transaction requires raw_result")
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        payload = _factual_payload(raw_result, arguments)
        overlap = FORBIDDEN_PAYLOAD_KEYS.intersection(key.lower() for key in payload)
        if overlap:
            raise MalformedNormalizedPayloadError(
                f"http.transaction observation must not carry {sorted(overlap)}"
            )
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=HTTP_TRANSACTION_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )


def _factual_payload(raw_result: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict[str, Any]:
    status_code = raw_result.get("status_code")
    if not isinstance(status_code, int):
        raise MalformedNormalizedPayloadError("raw_result.status_code must be an int")
    method = raw_result.get("method") or arguments.get("method")
    path = raw_result.get("path") or arguments.get("path")
    if not isinstance(method, str) or not isinstance(path, str):
        raise MalformedNormalizedPayloadError("raw_result method and path must be strings")
    headers_in = raw_result.get("response_headers") or {}
    headers: dict[str, str] = {}
    if isinstance(headers_in, Mapping):
        for name, value in headers_in.items():
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            lowered = name.lower()
            if lowered in ALLOWED_RESPONSE_HEADER_KEYS:
                headers[lowered] = value
    payload: dict[str, Any] = {
        "authorized_origin": raw_result.get("authorized_origin") or arguments.get("authorized_origin"),
        "method": method,
        "path": path,
        "status_code": status_code,
        "status_class": _status_class(status_code),
        "content_type": raw_result.get("content_type") or headers.get("content-type"),
        "body_length": raw_result.get("body_length"),
        "body_digest": raw_result.get("body_digest"),
        "request_fingerprint": raw_result.get("request_fingerprint"),
        "elapsed_ms": raw_result.get("elapsed_ms"),
        "redirect": False,
    }
    if headers:
        payload["response_headers"] = headers
    json_kind = raw_result.get("json_value_kind")
    if isinstance(json_kind, str) and json_kind.strip():
        payload["json_value_kind"] = json_kind
    keys = raw_result.get("json_top_level_keys")
    if isinstance(keys, list) and all(isinstance(item, str) for item in keys):
        payload["json_top_level_keys"] = list(keys)
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
