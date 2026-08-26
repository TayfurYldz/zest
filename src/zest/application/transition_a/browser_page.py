"""browser.page Transition A normalizer. Bounded page facts only. Not a verdict."""

from __future__ import annotations

from typing import Any, Mapping

from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import MalformedNormalizedPayloadError
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.tools.capabilities import BROWSER_PAGE_CAPABILITY

BROWSER_PAGE_NORMALIZER_VERSION = "browser.page.v1"
BROWSER_PAGE_OBSERVATION_KIND = "BROWSER_PAGE_STATE"
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
        "candidate",
        "cookie",
        "authorization",
        "set-cookie",
        "password",
        "token",
        "secret",
        "html",
        "innerhtml",
        "textcontent",
        "screenshot",
    }
)
FORBIDDEN_CONTROL_KEYS = frozenset(
    {
        "value",
        "input_value",
        "password",
        "innerhtml",
        "textcontent",
        "html",
        "cookie",
        "token",
        "secret",
    }
)
FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "password",
        "token",
        "secret",
        "body",
        "headers",
    }
)
ALLOWED_CONTROL_KEYS = (
    "element_reference",
    "snapshot_fingerprint",
    "tag",
    "role",
    "input_type",
    "disabled",
    "checked",
    "name",
    "aria_label",
    "placeholder",
    "href_scheme",
    "href_origin",
    "href_path",
)
ALLOWED_EVENT_KEYS = (
    "event_id",
    "method",
    "resource_type",
    "normalized_target",
    "path",
    "status_code",
    "request_bytes",
    "response_bytes",
    "redirect",
    "representability",
    "body_digest",
)
MAX_CONTROLS = 32
MAX_EVENTS = 32
CONTROL_TEXT_CAP = 64


class BrowserPageNormalizer:
    capability = BROWSER_PAGE_CAPABILITY
    version = BROWSER_PAGE_NORMALIZER_VERSION

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
                f"unsupported WorkerResult.status for browser.page: {status!r}"
            )
        raw_result = result.get("raw_result")
        if not isinstance(raw_result, Mapping):
            raise MalformedNormalizedPayloadError("SUCCEEDED browser.page requires raw_result")
        overlap = FORBIDDEN_PAYLOAD_KEYS.intersection(key.lower() for key in raw_result)
        if overlap:
            raise MalformedNormalizedPayloadError(
                f"browser.page observation must not carry {sorted(overlap)}"
            )
        payload = _factual_payload(raw_result)
        overlap = FORBIDDEN_PAYLOAD_KEYS.intersection(key.lower() for key in payload)
        if overlap:
            raise MalformedNormalizedPayloadError(
                f"browser.page observation must not carry {sorted(overlap)}"
            )
        observed_at = parse_aware_timestamp(
            result.get("completed_at") or result.get("started_at"),
            "completed_at",
        )
        return (
            ObservationDraft(
                observation_kind=BROWSER_PAGE_OBSERVATION_KIND,
                payload=payload,
                normalization_version=self.version,
                observed_at=observed_at,
            ),
        )


def _factual_payload(raw_result: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_fp = raw_result.get("snapshot_fingerprint")
    if not isinstance(snapshot_fp, str) or not snapshot_fp.strip():
        raise MalformedNormalizedPayloadError("raw_result.snapshot_fingerprint must be a string")
    normalized_url = raw_result.get("normalized_url")
    if not isinstance(normalized_url, str) or not normalized_url.strip():
        raise MalformedNormalizedPayloadError("raw_result.normalized_url must be a string")
    context_ref = raw_result.get("browser_context_reference")
    page_ref = raw_result.get("page_reference")
    if not isinstance(context_ref, str) or not isinstance(page_ref, str):
        raise MalformedNormalizedPayloadError("browser context and page references must be strings")
    attempted = raw_result.get("attempted_network_requests")
    if not isinstance(attempted, int) or isinstance(attempted, bool) or attempted < 0:
        raise MalformedNormalizedPayloadError("attempted_network_requests must be a non-negative int")
    payload: dict[str, Any] = {
        "browser_context_reference": context_ref,
        "page_reference": page_ref,
        "snapshot_fingerprint": snapshot_fp,
        "normalized_url": normalized_url,
        "ready_state": _optional_text(raw_result.get("ready_state"), "complete"),
        "frame_count": _optional_int(raw_result.get("frame_count"), 1),
        "attempted_network_requests": attempted,
        "snapshot_schema_version": _optional_text(
            raw_result.get("snapshot_schema_version"),
            BROWSER_PAGE_NORMALIZER_VERSION,
        ),
        "controls": _controls(raw_result.get("controls")),
        "network_events": _network_events(raw_result.get("network_events")),
    }
    return payload


def _controls(raw: object) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MalformedNormalizedPayloadError("raw_result.controls must be an array")
    controls: list[dict[str, Any]] = []
    for item in raw[:MAX_CONTROLS]:
        if not isinstance(item, Mapping):
            raise MalformedNormalizedPayloadError("control entries must be objects")
        overlap = FORBIDDEN_CONTROL_KEYS.intersection(key.lower() for key in item)
        if overlap:
            raise MalformedNormalizedPayloadError(f"control must not carry {sorted(overlap)}")
        element_ref = item.get("element_reference")
        snapshot_fp = item.get("snapshot_fingerprint")
        if not isinstance(element_ref, str) or not isinstance(snapshot_fp, str):
            raise MalformedNormalizedPayloadError("control references must be strings")
        control = {
            "element_reference": element_ref,
            "snapshot_fingerprint": snapshot_fp,
            "tag": _capped_text(item.get("tag"), ""),
            "role": _capped_text(item.get("role"), ""),
            "input_type": _capped_text(item.get("input_type"), ""),
            "disabled": bool(item.get("disabled")),
            "checked": bool(item.get("checked")),
            "name": _capped_text(item.get("name"), ""),
            "aria_label": _capped_text(item.get("aria_label"), ""),
            "placeholder": _capped_text(item.get("placeholder"), ""),
            "href_scheme": _capped_text(item.get("href_scheme"), ""),
            "href_origin": _capped_text(item.get("href_origin"), ""),
            "href_path": _capped_text(item.get("href_path"), ""),
        }
        extra = set(item) - set(ALLOWED_CONTROL_KEYS)
        if extra:
            raise MalformedNormalizedPayloadError(f"control has unsupported fields {sorted(extra)}")
        controls.append(control)
    return controls


def _network_events(raw: object) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MalformedNormalizedPayloadError("raw_result.network_events must be an array")
    events: list[dict[str, Any]] = []
    for item in raw[:MAX_EVENTS]:
        if not isinstance(item, Mapping):
            raise MalformedNormalizedPayloadError("network event entries must be objects")
        overlap = FORBIDDEN_EVENT_KEYS.intersection(key.lower() for key in item)
        if overlap:
            raise MalformedNormalizedPayloadError(f"network event must not carry {sorted(overlap)}")
        event_id = item.get("event_id")
        method = item.get("method")
        target = item.get("normalized_target")
        if not isinstance(event_id, str) or not isinstance(method, str) or not isinstance(target, str):
            raise MalformedNormalizedPayloadError("network event identity fields must be strings")
        extra = set(item) - set(ALLOWED_EVENT_KEYS)
        if extra:
            raise MalformedNormalizedPayloadError(
                f"network event has unsupported fields {sorted(extra)}"
            )
        event: dict[str, Any] = {
            "event_id": event_id,
            "method": method,
            "resource_type": _optional_text(item.get("resource_type"), "other"),
            "normalized_target": target,
            "path": _optional_text(item.get("path"), "/"),
            "redirect": bool(item.get("redirect")),
            "representability": _optional_text(item.get("representability"), "NOT_REPRESENTABLE"),
        }
        status_code = item.get("status_code")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            event["status_code"] = status_code
        for numeric_name in ("request_bytes", "response_bytes"):
            numeric = item.get(numeric_name)
            if isinstance(numeric, int) and not isinstance(numeric, bool) and numeric >= 0:
                event[numeric_name] = numeric
        digest = item.get("body_digest")
        if isinstance(digest, str) and digest.strip():
            event["body_digest"] = digest
        events.append(event)
    return events


def _optional_text(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value[:CONTROL_TEXT_CAP] if len(value) > 2048 else value
    return default


def _capped_text(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    return value[:CONTROL_TEXT_CAP]


def _optional_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default
