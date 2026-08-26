"""RFC 3339 / ISO 8601 timezone-aware timestamp parsing for WorkerResult fields."""

from __future__ import annotations

from datetime import datetime

from zest.application.transition_a.errors import MalformedNormalizedPayloadError


def parse_aware_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MalformedNormalizedPayloadError(f"{field_name} must be a timezone-aware timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MalformedNormalizedPayloadError(
            f"{field_name} is not a valid timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MalformedNormalizedPayloadError(f"{field_name} must be timezone-aware")
    return parsed
