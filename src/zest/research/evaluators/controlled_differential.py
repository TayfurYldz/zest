"""Controlled observation comparison. A raw difference is not a vulnerability."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.differential import DifferentialInterpretation

DIFFERENTIAL_EVALUATION_STRATEGY = "differential.controlled.v1"
NOISE_HEADER_KEYS = frozenset(
    {
        "date",
        "expires",
        "age",
        "etag",
        "last-modified",
        "cache-control",
        "pragma",
        "set-cookie",
        "cookie",
        "x-request-id",
        "x-correlation-id",
        "request-id",
        "cf-ray",
        "cf-request-id",
        "x-amzn-trace-id",
        "x-csrf-token",
        "csrf-token",
        "nonce",
        "server-timing",
    }
)
NOISE_BODY_KEYS = frozenset(
    {
        "timestamp",
        "ts",
        "nonce",
        "csrf",
        "csrf_token",
        "request_id",
        "requestId",
        "trace_id",
        "traceId",
        "session_id",
        "sessionId",
        "tracking",
        "pixel",
    }
)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class DifferentialSecurityJudgement(Enum):
    EQUIVALENT = "EQUIVALENT"
    NOISE_ONLY = "NOISE_ONLY"
    CONTROLLED_SIGNAL = "CONTROLLED_SIGNAL"
    INSUFFICIENT_CONTROL = "INSUFFICIENT_CONTROL"
    AMBIGUOUS = "AMBIGUOUS"
    INCOMPARABLE = "INCOMPARABLE"


@dataclass(frozen=True)
class ControlledDifferentialResult:
    judgement: DifferentialSecurityJudgement
    interpretation: DifferentialInterpretation
    reason_codes: tuple[str, ...]
    observed_differences: Mapping[str, Any]
    observed_similarities: Mapping[str, Any]
    not_a_vulnerability: bool = True


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    raw = payload.get("headers") or payload.get("response_headers") or {}
    if not isinstance(raw, Mapping):
        return {}
    return {str(key).lower(): str(value) for key, value in raw.items()}


def _strip_noise_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key not in NOISE_HEADER_KEYS}


def _normalize_body(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        cleaned = {
            key: item
            for key, item in value.items()
            if str(key) not in NOISE_BODY_KEYS
        }
        text = json.dumps(cleaned, sort_keys=True, default=str)
    else:
        text = str(value)
    text = _TIMESTAMP_RE.sub("<ts>", text)
    text = _UUID_RE.sub("<id>", text)
    return text


def compare_controlled_payloads(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    changed_identity: bool,
    same_resource: bool,
) -> ControlledDifferentialResult:
    if not same_resource:
        return ControlledDifferentialResult(
            judgement=DifferentialSecurityJudgement.INCOMPARABLE,
            interpretation=DifferentialInterpretation.INCOMPARABLE,
            reason_codes=("UNRELATED_RESOURCES",),
            observed_differences={},
            observed_similarities={"not_a_vulnerability": True},
        )
    left_status = left.get("status_code")
    right_status = right.get("status_code")
    left_body = _normalize_body(left.get("body") if "body" in left else left.get("body_text"))
    right_body = _normalize_body(right.get("body") if "body" in right else right.get("body_text"))
    left_headers = _strip_noise_headers(_headers(left))
    right_headers = _strip_noise_headers(_headers(right))
    raw_header_diff = _headers(left) != _headers(right)
    semantic_header_diff = left_headers != right_headers
    status_diff = left_status != right_status
    body_diff = left_body != right_body
    similarities = {
        "same_resource": True,
        "not_a_vulnerability": True,
        "not_authorization_proof": True,
    }
    differences: dict[str, Any] = {}
    if status_diff:
        differences["status_code"] = {"left": left_status, "right": right_status}
    if body_diff:
        differences["normalized_body"] = True
    if semantic_header_diff:
        differences["headers"] = True
    if not status_diff and not body_diff and not semantic_header_diff:
        if raw_header_diff:
            return ControlledDifferentialResult(
                judgement=DifferentialSecurityJudgement.NOISE_ONLY,
                interpretation=DifferentialInterpretation.EQUIVALENT,
                reason_codes=("NOISE_ONLY", "INSUFFICIENT_CONTROL"),
                observed_differences={"noise_headers": True},
                observed_similarities=similarities,
            )
        return ControlledDifferentialResult(
            judgement=DifferentialSecurityJudgement.EQUIVALENT,
            interpretation=DifferentialInterpretation.EQUIVALENT,
            reason_codes=("EQUIVALENT_AFTER_NORMALIZATION",),
            observed_differences={},
            observed_similarities=similarities,
        )
    if not changed_identity and status_diff:
        return ControlledDifferentialResult(
            judgement=DifferentialSecurityJudgement.AMBIGUOUS,
            interpretation=DifferentialInterpretation.INCOMPARABLE,
            reason_codes=("AMBIGUOUS", "UNDECLARED_ACTOR_CHANGE"),
            observed_differences=differences,
            observed_similarities=similarities,
        )
    if changed_identity and (status_diff or body_diff):
        return ControlledDifferentialResult(
            judgement=DifferentialSecurityJudgement.CONTROLLED_SIGNAL,
            interpretation=DifferentialInterpretation.CONTROLLED_DIFFERENCE,
            reason_codes=("CONTROLLED_IDENTITY_DIFFERENCE", "NOT_A_FINDING"),
            observed_differences=differences,
            observed_similarities=similarities,
        )
    return ControlledDifferentialResult(
        judgement=DifferentialSecurityJudgement.INSUFFICIENT_CONTROL,
        interpretation=DifferentialInterpretation.INCOMPARABLE,
        reason_codes=("INSUFFICIENT_CONTROL",),
        observed_differences=differences,
        observed_similarities=similarities,
    )
