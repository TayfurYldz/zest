"""Shared recursive secret protection. Not authorization and not Evidence.

Reject fail-closed where secrets must never be supplied.
Redact where operational output must remain useful.
Never include the offending secret value in the exception.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, is_dataclass, fields
from enum import Enum
from typing import Any, Iterable

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "password",
        "cookie",
        "set_cookie",
        "session",
        "client_secret",
        "session_token",
        "raw_secret",
        "secret_value",
        "credential",
        "openai_api_key",
        "anthropic_api_key",
    }
)

_HEADER_ATTRS = frozenset({"headers", "request_headers", "response_headers"})


class SecretMaterialError(ValueError):
    """Secret-bearing payload was rejected. The secret value is not included."""


@dataclass(frozen=True)
class SessionReference:
    """Opaque session handle. Not a token, cookie, or credential value."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("SessionReference.value must be a non-empty opaque id")
        object.__setattr__(self, "value", self.value.strip())


def normalize_secret_key(key: object) -> str:
    if not isinstance(key, str):
        return ""
    return key.strip().lower().replace("-", "_")


def is_sensitive_key(key: object) -> bool:
    normalized = normalize_secret_key(key)
    if not normalized:
        return False
    return normalized in SENSITIVE_KEYS


def _is_safe_reference(value: object) -> bool:
    if isinstance(value, SessionReference):
        return True
    cls = type(value)
    if cls.__name__ in {"SecretReference", "SessionReference"}:
        return True
    if is_dataclass(value) and not isinstance(value, type):
        names = {item.name for item in fields(value)}
        if names == {"scheme", "name"} or names == {"env_name"} or names == {"value"}:
            if cls.__name__ in {"SecretReference", "SessionReference"}:
                return True
    return False


def _mapping_items(value: Mapping[Any, Any]) -> Iterable[tuple[Any, Any]]:
    return value.items()


def walk_sensitive_keys(
    value: object,
    *,
    path: str = "payload",
) -> list[str]:
    """Return dotted paths of sensitive keys. Does not return secret values."""

    found: list[str] = []
    _collect_sensitive(value, path, found, redact=False, replace=None)
    return found


def _collect_sensitive(
    value: object,
    path: str,
    found: list[str],
    *,
    redact: bool,
    replace: dict[str, Any] | None,
) -> object:
    if _is_safe_reference(value):
        return value
    if isinstance(value, Mapping):
        cleaned: dict[Any, Any] = {}
        for key, item in _mapping_items(value):
            child_path = f"{path}.{key}" if path else str(key)
            if is_sensitive_key(key) and not _is_safe_reference(item):
                found.append(child_path)
                if redact:
                    cleaned[key] = REDACTED
                continue
            cleaned[key] = _collect_sensitive(
                item, child_path, found, redact=redact, replace=replace
            )
        return cleaned
    if isinstance(value, (list, tuple)):
        items = [
            _collect_sensitive(item, f"{path}[{index}]", found, redact=redact, replace=replace)
            for index, item in enumerate(value)
        ]
        return type(value)(items) if not isinstance(value, list) else items
    if isinstance(value, Set) and not isinstance(value, (str, bytes, bytearray)):
        return type(value)(
            _collect_sensitive(item, f"{path}[]", found, redact=redact, replace=replace)
            for item in value
        )
    return value


def reject_secret_keys(payload: object, field_name: str) -> Any:
    """Fail closed if secret keys are present. Safe references are allowed."""

    found: list[str] = []
    cleaned = _collect_sensitive(payload, field_name, found, redact=False, replace=None)
    if found:
        raise SecretMaterialError(
            f"{field_name} must not contain secret keys: {sorted(set(found))}"
        )
    return cleaned


def redact_secret_keys(payload: object, field_name: str = "payload") -> Any:
    """Replace secret values with a constant. Never echoes the secret."""

    found: list[str] = []
    return _collect_sensitive(payload, field_name, found, redact=True, replace=None)


def redact_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, REDACTED)


def sanitize_exception(
    exc: BaseException,
    *,
    secret: str | None = None,
    max_message_chars: int = 500,
) -> dict[str, object]:
    """Extract a safe exception mapping. Headers, tokens, and bodies are omitted."""

    status = _safe_status(exc)
    code = _safe_provider_code(exc)
    message = redact_secret(str(exc), secret)
    message = _strip_header_like_text(message)
    if len(message) > max_message_chars:
        message = message[:max_message_chars]
    return {
        "exception_type": type(exc).__name__,
        "provider_code": code,
        "http_status": status,
        "message": message,
        "contains_secrets": False,
    }


def _safe_status(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _safe_provider_code(exc: BaseException) -> str | None:
    for attr in ("code", "type", "error_code", "error_type"):
        value = getattr(exc, attr, None)
        if isinstance(value, Enum):
            return value.value if isinstance(value.value, str) else None
        if isinstance(value, str) and value.strip() and not is_sensitive_key(value):
            return value.strip()
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            for attr in ("code", "type"):
                value = error.get(attr)
                if isinstance(value, str) and value.strip() and not is_sensitive_key(value):
                    return value.strip()
    return None


def _strip_header_like_text(message: str) -> str:
    lowered = message.lower()
    for marker in ("authorization:", "api-key:", "api_key=", "bearer ", "cookie:"):
        index = lowered.find(marker)
        if index >= 0:
            return message[:index] + REDACTED
    return message


def require_mapping_without_secrets(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SecretMaterialError(f"{field_name} must be a mapping")
    cleaned = reject_secret_keys(payload, field_name)
    if not isinstance(cleaned, dict):
        raise SecretMaterialError(f"{field_name} must be a mapping")
    return cleaned
