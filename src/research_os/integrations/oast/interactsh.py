"""Interactsh v1.3.1 JSONL normalization boundary.

Provider data is untrusted. This adapter does not authorize execution,
select target/scope/identity, or create Evidence/Candidate/Finding.
Raw request/response material is deliberately not persisted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from research_os.research.oast.types import OastCallbackDelivery


INTERACTSH_PROVIDER_ADAPTER_ID = "interactsh-v1.3.1"

# Bound the untrusted provider line before JSON parsing.
MAX_INTERACTSH_JSONL_BYTES = 65_536

_ALLOWED_PROTOCOLS = frozenset({"dns", "http", "https"})


class InteractshAdapterError(Exception):
    """Provider interaction cannot be normalized safely."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(
    event: Mapping[str, Any],
    key: str,
    *,
    max_bytes: int,
    optional: bool = False,
) -> str | None:
    value = event.get(key)

    if optional and (value is None or value == ""):
        return None

    if not isinstance(value, str) or not value.strip():
        raise InteractshAdapterError(f"{key} must be a non-empty string")

    text = value.strip()
    if len(text.encode("utf-8")) > max_bytes:
        raise InteractshAdapterError(f"{key} exceeds its bounded length")

    return text


def _parse_provider_timestamp(value: str) -> datetime:
    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise InteractshAdapterError("timestamp is not valid RFC3339/ISO-8601") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InteractshAdapterError("timestamp must be timezone-aware")

    return parsed


def _decode_json_object(line: str) -> dict[str, Any]:
    if not isinstance(line, str) or not line.strip():
        raise InteractshAdapterError("interaction JSONL line is empty")

    if len(line.encode("utf-8")) > MAX_INTERACTSH_JSONL_BYTES:
        raise InteractshAdapterError("interaction JSONL line exceeds the bounded input limit")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InteractshAdapterError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        decoded = json.loads(line, object_pairs_hook=reject_duplicate_keys)
    except InteractshAdapterError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InteractshAdapterError("interaction line is not valid JSON") from exc

    if not isinstance(decoded, dict):
        raise InteractshAdapterError("interaction JSON must be an object")

    return decoded


def normalize_interactsh_jsonl(
    line: str,
    *,
    correlation_id: str,
) -> OastCallbackDelivery:
    """Normalize one exact Interactsh v1.3.1 JSONL interaction.

    correlation_id is supplied by the already-authoritative Research OS
    correlation binding. Provider-controlled fields never select it.
    """

    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise InteractshAdapterError("correlation_id must be a non-empty string")
    correlation_id = correlation_id.strip()

    event = _decode_json_object(line)

    protocol_value = _text(event, "protocol", max_bytes=16)
    assert protocol_value is not None
    protocol = protocol_value.lower()

    if protocol not in _ALLOWED_PROTOCOLS:
        raise InteractshAdapterError("interaction protocol is not allowlisted")

    unique_id = _text(event, "unique-id", max_bytes=1024)
    full_id = _text(event, "full-id", max_bytes=2048)
    remote_address = _text(event, "remote-address", max_bytes=256)
    timestamp_text = _text(event, "timestamp", max_bytes=64)
    q_type = _text(event, "q-type", max_bytes=64, optional=True)

    assert unique_id is not None
    assert full_id is not None
    assert remote_address is not None
    assert timestamp_text is not None

    if protocol == "dns" and q_type is None:
        raise InteractshAdapterError("DNS interaction is missing q-type")

    received_at = _parse_provider_timestamp(timestamp_text)

    normalized_payload: dict[str, Any] = {
        "protocol": protocol,
        "provider_unique_id": unique_id,
        "provider_full_id": full_id,
        "remote_address": remote_address,
        # Preserve the provider's exact timestamp text. Python datetime has
        # lower fractional precision than Go time.Time may emit.
        "provider_timestamp": timestamp_text,
    }
    if q_type is not None:
        normalized_payload["q_type"] = q_type

    normalized_digest = _sha256(_canonical_bytes(normalized_payload))

    # Raw provider request/response may contain credentials. They are never
    # persisted, but their digests may participate transiently in event
    # identity so otherwise-identical provider events remain distinguishable.
    event_identity: dict[str, Any] = dict(normalized_payload)

    for raw_key in ("raw-request", "raw-response"):
        raw_value = event.get(raw_key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, str):
            raise InteractshAdapterError(f"{raw_key} must be a string when present")
        event_identity[f"{raw_key}-sha256"] = _sha256(raw_value.encode("utf-8"))

    provider_event_id = _sha256(
        _canonical_bytes(
            {
                "provider_adapter_id": INTERACTSH_PROVIDER_ADAPTER_ID,
                "interaction": event_identity,
            }
        )
    )

    delivery_id = _sha256(
        (
            f"{INTERACTSH_PROVIDER_ADAPTER_ID}\0"
            f"{correlation_id}\0"
            f"{provider_event_id}"
        ).encode("utf-8")
    )

    return OastCallbackDelivery(
        delivery_id=delivery_id,
        correlation_id=correlation_id,
        provider_adapter_id=INTERACTSH_PROVIDER_ADAPTER_ID,
        provider_event_id=provider_event_id,
        received_at=received_at,
        normalized_payload=normalized_payload,
        normalized_digest=normalized_digest,
    )
