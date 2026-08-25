"""OAST domain types. Pure research layer; no network, no execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

from research_os.research.types import ResearchInputError
from research_os.safe_data import SecretMaterialError, reject_secret_keys


class OastError(Exception):
    """OAST operational failure. Does not create Evidence or Finding."""


OAST_MAX_NORMALIZED_PAYLOAD_BYTES = 16_384
OAST_MAX_PROVIDER_ADAPTER_ID_LENGTH = 128
OAST_MAX_PROVIDER_EVENT_ID_LENGTH = 256


def _require_aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ResearchInputError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchInputError(f"{field_name} must be timezone-aware")
    return value


def _require_bounded_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchInputError("normalized_payload must be a mapping")
    try:
        import json

        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResearchInputError("normalized_payload must be JSON-serializable") from exc
    if len(encoded) > OAST_MAX_NORMALIZED_PAYLOAD_BYTES:
        raise ResearchInputError("normalized_payload exceeds the bounded payload limit")
    try:
        reject_secret_keys(value, "normalized_payload")
    except SecretMaterialError as exc:
        raise ResearchInputError(str(exc)) from exc
    return dict(value)


@dataclass(frozen=True)
class OastCorrelation:
    """Immutable provider-neutral callback correlation binding."""

    correlation_id: str
    attempt_id: str
    experiment_id: str
    research_run_id: str
    target_reference: str
    identity_id: str
    armed_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "correlation_id",
            "attempt_id",
            "experiment_id",
            "research_run_id",
            "target_reference",
            "identity_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name),
            )
        armed_at = _require_aware_datetime(self.armed_at, "armed_at")
        expires_at = _require_aware_datetime(self.expires_at, "expires_at")
        if expires_at <= armed_at:
            raise ResearchInputError("expires_at must be later than armed_at")


@dataclass(frozen=True)
class OastCallbackDelivery:
    """One bounded provider-normalized delivery; not an authoritative observation."""

    delivery_id: str
    correlation_id: str
    provider_adapter_id: str
    provider_event_id: str
    received_at: datetime
    normalized_payload: Mapping[str, Any]
    normalized_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery_id", _require_text(self.delivery_id, "delivery_id"))
        object.__setattr__(
            self, "correlation_id", _require_text(self.correlation_id, "correlation_id")
        )
        adapter_id = _require_text(self.provider_adapter_id, "provider_adapter_id")
        event_id = _require_text(self.provider_event_id, "provider_event_id")
        if len(adapter_id) > OAST_MAX_PROVIDER_ADAPTER_ID_LENGTH:
            raise ResearchInputError("provider_adapter_id exceeds its length limit")
        if len(event_id) > OAST_MAX_PROVIDER_EVENT_ID_LENGTH:
            raise ResearchInputError("provider_event_id exceeds its length limit")
        object.__setattr__(self, "provider_adapter_id", adapter_id)
        object.__setattr__(self, "provider_event_id", event_id)
        _require_aware_datetime(self.received_at, "received_at")
        object.__setattr__(
            self,
            "normalized_payload",
            _require_bounded_payload(self.normalized_payload),
        )
        digest = _require_text(self.normalized_digest, "normalized_digest").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ResearchInputError("normalized_digest must be a SHA-256 hexadecimal digest")
        object.__setattr__(self, "normalized_digest", digest)


class OastTokenExpiredError(OastError):
    """Callback received for an expired token."""


class OastCallbackNotFoundError(OastError):
    """No callback recorded for the requested token."""


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class OastToken:
    """Opaque callback token bound to a research run + hypothesis + target."""

    token_id: str
    research_run_id: str
    hypothesis_id: str
    target_reference: str
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_id", _require_text(self.token_id, "token_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "target_reference", _require_text(self.target_reference, "target_reference")
        )
        if not isinstance(self.expires_at, datetime):
            raise ResearchInputError("expires_at must be a datetime")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ResearchInputError("expires_at must be timezone-aware")


@dataclass(frozen=True)
class OastCallback:
    """One callback hit matched to a token."""

    callback_id: str
    token_id: str
    received_at: datetime
    source_address: str
    request_summary: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "callback_id", _require_text(self.callback_id, "callback_id"))
        object.__setattr__(self, "token_id", _require_text(self.token_id, "token_id"))
        object.__setattr__(
            self, "source_address", _require_text(self.source_address, "source_address")
        )
        if not isinstance(self.received_at, datetime):
            raise ResearchInputError("received_at must be a datetime")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ResearchInputError("received_at must be timezone-aware")
        if not isinstance(self.request_summary, Mapping):
            raise ResearchInputError("request_summary must be a mapping")
        object.__setattr__(self, "request_summary", dict(self.request_summary))


@runtime_checkable
class OastPort(Protocol):
    """Out-of-band callback port. Production implementation lives in workers/."""

    def mint_token(
        self,
        *,
        token_id: str,
        research_run_id: str,
        hypothesis_id: str,
        target_reference: str,
        expires_at: datetime,
    ) -> OastToken: ...

    def poll(self, token_id: str, *, now: datetime) -> tuple[OastCallback, ...]: ...
