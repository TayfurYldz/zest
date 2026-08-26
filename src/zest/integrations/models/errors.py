"""Map provider SDK exceptions to provider-neutral ModelPort errors.

Priority:
1. provider SDK structured exception class
2. provider structured error code/type
3. explicit policy/content/safety code
4. explicit auth code
5. rate-limit code
6. timeout/network/process
7. fallback HTTP status/text heuristic

HTTP 403 is not automatically AUTH_FAILED.
"""

from __future__ import annotations

from zest.research.model_port import (
    ContentPolicyBlockedError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRuntimeError,
    ProviderTimeoutError,
)
from zest.safe_data import sanitize_exception

POLICY_CLASS_MARKERS = (
    "contentpolicy",
    "contentfilter",
    "safetyrefusal",
    "moderation",
)
AUTH_CLASS_MARKERS = (
    "authenticationerror",
    "permissiondeniederror",
    "permissionerror",
    "invalidapikey",
    "apikeyerror",
)
RATE_CLASS_MARKERS = ("ratelimit", "overloaded", "quotaexceeded")
TIMEOUT_CLASS_MARKERS = ("timeout", "apitimeout", "readtimeout", "connecttimeout")
POLICY_CODES = frozenset(
    {
        "content_filter",
        "content_policy",
        "content_policy_violation",
        "content_policy_blocked",
        "safety",
        "safety_violation",
        "refusal",
        "moderation",
    }
)
AUTH_CODES = frozenset(
    {
        "invalid_api_key",
        "authentication_error",
        "invalid_authentication",
        "permission_error",
        "unauthorized",
        "unauthenticated",
    }
)
RATE_CODES = frozenset(
    {
        "rate_limit",
        "rate_limit_error",
        "insufficient_quota",
        "overloaded_error",
        "overloaded",
        "quota_exceeded",
    }
)


def classify_provider_exception(exc: BaseException, *, secret: str | None = None) -> Exception:
    safe = sanitize_exception(exc, secret=secret)
    message = str(safe["message"])
    status = safe["http_status"] if isinstance(safe["http_status"], int) else _status_code(exc)
    name = type(exc).__name__.lower().replace("_", "")
    code = _structured_code(exc)
    combined = f"{name} {code or ''} {message}".lower()

    if any(marker in name for marker in POLICY_CLASS_MARKERS):
        return ContentPolicyBlockedError(message)
    if any(marker in name for marker in AUTH_CLASS_MARKERS) and not _looks_like_policy(code, combined):
        return ProviderAuthError(message)
    if any(marker in name for marker in RATE_CLASS_MARKERS):
        return ProviderRateLimitError(message)
    if any(marker in name for marker in TIMEOUT_CLASS_MARKERS):
        return ProviderTimeoutError(message)

    if _looks_like_policy(code, combined):
        return ContentPolicyBlockedError(message)
    if code in AUTH_CODES:
        return ProviderAuthError(message)
    if code in RATE_CODES or status == 429 or "rate limit" in combined or "ratelimit" in combined:
        return ProviderRateLimitError(message)
    if status == 408 or "timeout" in combined or "timed out" in combined:
        return ProviderTimeoutError(message)
    if status == 401:
        return ProviderAuthError(message)
    if status == 403:
        if _looks_like_policy(code, combined):
            return ContentPolicyBlockedError(message)
        if "unauthorized" in combined or "unauthenticated" in combined or "api_key" in combined:
            return ProviderAuthError(message)
        return ProviderRuntimeError(message)
    if "unauthorized" in combined or "unauthenticated" in combined or "api_key" in combined:
        return ProviderAuthError(message)
    return ProviderRuntimeError(message)


def _looks_like_policy(code: str | None, combined: str) -> bool:
    if code is not None and code.lower() in POLICY_CODES:
        return True
    return any(
        marker in combined
        for marker in (
            "content_filter",
            "content_policy",
            "content policy",
            "safety policy",
            "blocked by safety",
        )
    )


def _structured_code(exc: BaseException) -> str | None:
    for attr in ("code", "type", "error_code", "error_type"):
        value = getattr(exc, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for attr in ("code", "type"):
                value = error.get(attr)
                if isinstance(value, str) and value.strip():
                    return value.strip().lower()
    error = getattr(exc, "error", None)
    if isinstance(error, dict):
        for attr in ("code", "type"):
            value = error.get(attr)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def _status_code(exc: BaseException) -> int | None:
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
