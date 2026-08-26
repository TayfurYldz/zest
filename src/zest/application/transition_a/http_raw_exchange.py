"""http.raw_exchange Transition A normalizer. Factual wire-exchange observations only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import HTTP_RAW_EXCHANGE_CAPABILITY, HTTP_RAW_EXCHANGE_PROBE_ACTION

HTTP_RAW_EXCHANGE_NORMALIZER_VERSION = "http.raw_exchange.v1"
HTTP_RAW_EXCHANGE_OBSERVATION_KIND = "HTTP_RAW_EXCHANGE"
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
        "request_bytes",
        "raw_request",
    }
)


class HttpRawExchangeNormalizer:
    capability = HTTP_RAW_EXCHANGE_CAPABILITY
    action = HTTP_RAW_EXCHANGE_PROBE_ACTION
    version = HTTP_RAW_EXCHANGE_NORMALIZER_VERSION

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
                f"unsupported WorkerResult.status for http.raw_exchange: {status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError("SUCCEEDED http.raw_exchange requires raw_result")
        arguments = request.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        payload = {
            "authorized_origin": raw_result.get("authorized_origin") or arguments.get("authorized_origin"),
            "path": raw_result.get("path") or arguments.get("path"),
            "framing_profile": raw_result.get("framing_profile") or arguments.get("framing_profile"),
            "lane": raw_result.get("lane") or arguments.get("lane"),
            "control": raw_result.get("control") or arguments.get("control"),
            "status_code": raw_result.get("status_code"),
            "write_count": raw_result.get("write_count"),
            "bytes_written": raw_result.get("bytes_written"),
            "body_length": raw_result.get("body_length"),
            "body_digest": raw_result.get("body_digest"),
            "request_fingerprint": raw_result.get("request_fingerprint"),
            "redirect": False,
        }
        if not isinstance(payload["status_code"], int):
            raise MalformedNormalizedPayloadError("raw_result.status_code must be an int")
        overlap = FORBIDDEN_PAYLOAD_KEYS.intersection(key.lower() for key in payload)
        if overlap:
            raise MalformedNormalizedPayloadError(
                f"http.raw_exchange observation must not carry {sorted(overlap)}"
            )
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=HTTP_RAW_EXCHANGE_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )
