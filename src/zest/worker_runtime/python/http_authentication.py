"""Bounded HTTP form login. Not a browser. Never echoes credentials or cookies."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

from .browser_envelope import envelope_allows, parse_envelope

HTTP_AUTHENTICATION_CAPABILITY = "http.authentication"
ALLOWED_SCHEMES = frozenset({"http"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_MAX_RESPONSE_BYTES = 4096
DEFAULT_TIMEOUT_SECONDS = 2.0
ABSOLUTE_MAX_RESPONSE_BYTES = 1_048_576
ABSOLUTE_TIMEOUT_SECONDS = 10.0
CRLF_MARKERS = ("\r", "\n", "\x00")


class _RedirectStopped(Exception):
    def __init__(self, status: int, raw_location: str, response_url: str) -> None:
        super().__init__("redirect stopped")
        self.status = status
        self.raw_location = raw_location
        self.response_url = response_url
        self.new_url = _resolve_location(response_url, raw_location)


class _BoundExceeded(Exception):
    pass


class _OriginRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def execute_http_authentication(
    request: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        return "EXECUTION_FAILED", {}, {"error": "arguments must be an object"}
    envelope = parse_envelope(request.get("network_envelope"))
    if envelope is None:
        return (
            "EXECUTION_FAILED",
            {},
            {
                "error": "network_envelope is required",
                "contacted": False,
                "self_authorized": False,
            },
        )
    origin = arguments.get("authorized_origin")
    path = arguments.get("path")
    username = arguments.get("username")
    username_field = arguments.get("username_field")
    password_secret_name = arguments.get("password_secret_name")
    cookie_name = arguments.get("session_cookie_name")
    session_context_id = arguments.get("session_context_id")
    if not all(
        isinstance(item, str) and item.strip()
        for item in (origin, path, username, username_field, password_secret_name, cookie_name, session_context_id)
    ):
        return "EXECUTION_FAILED", {}, {"error": "login arguments are incomplete"}
    if any(marker in path for marker in CRLF_MARKERS) or path.startswith("//") or "://" in path:
        return "BLOCKED", {}, {"error": "path is invalid", "contacted": False, "self_authorized": False}
    origin = origin.strip().rstrip("/")
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return "BLOCKED", {}, {"error": origin_error, "contacted": False, "self_authorized": False}
    resolved = request.get("resolved_secret_values")
    if not isinstance(resolved, Mapping):
        return (
            "BLOCKED",
            {},
            {"error": "password secret is unavailable", "contacted": False, "self_authorized": False},
        )
    password = resolved.get(password_secret_name)
    if not isinstance(password, str) or not password:
        return (
            "BLOCKED",
            {},
            {"error": "password secret is unavailable", "contacted": False, "self_authorized": False},
        )
    success_codes = arguments.get("success_status_codes") or [200]
    if not isinstance(success_codes, list) or not all(isinstance(item, int) for item in success_codes):
        return "EXECUTION_FAILED", {}, {"error": "success_status_codes is invalid"}
    body = urlencode({username_field: username, "password": password})
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Connection": "close",
    }
    url = f"{origin}{path}"
    allowed, reason = envelope_allows(envelope, url)
    if not allowed:
        return (
            "EXECUTION_FAILED",
            {},
            {
                "error": f"request is outside authorized network envelope: {reason}",
                "contacted": False,
                "self_authorized": False,
            },
        )
    try:
        captured = _post(origin, path, headers, body.encode("utf-8"), envelope)
    except _RedirectStopped as exc:
        new_origin = _origin_of(exc.new_url) if exc.new_url else ""
        return (
            "REAUTHORIZATION_REQUIRED",
            {
                "stopped": True,
                "reason": "redirect_or_new_origin",
                "status": exc.status,
                "method": "POST",
                "path": path,
            },
            {
                "redirect": True,
                "new_origin": new_origin,
                "location": exc.new_url,
                "raw_location": exc.raw_location,
                "response_url": exc.response_url,
                "requires_core_re_evaluation": True,
                "followed": False,
                "self_authorized": False,
            },
        )
    except _OriginRejected as exc:
        return "BLOCKED", {}, {"error": str(exc), "contacted": False, "self_authorized": False}
    except _BoundExceeded:
        return "EXECUTION_FAILED", {}, {"error": "response exceeded byte bound", "contacted": True}
    except (TimeoutError, socket.timeout):
        return "TIMED_OUT", {}, {"error": "timeout", "contacted": True}
    except Exception as exc:  # noqa: BLE001 — worker must map transport failure
        return "EXECUTION_FAILED", {}, {"error": type(exc).__name__, "contacted": True}
    status_code = captured["status_code"]
    cookie_value = captured.get("cookies", {}).get(cookie_name)
    established = status_code in success_codes and isinstance(cookie_value, str) and cookie_value.strip()
    raw = {
        "authorized_origin": origin,
        "path": path,
        "method": "POST",
        "status_code": status_code,
        "session_established": bool(established),
        "session_context_id": session_context_id,
        "session_cookie_name": cookie_name,
        "identity_id": arguments.get("identity_id"),
        "self_authorized": False,
    }
    if not established:
        return "SUCCEEDED", raw, {"session_established": False, "self_authorized": False}
    raw = dict(raw)
    raw["_ephemeral_session_cookie"] = f"{cookie_name}={cookie_value}"
    return "SUCCEEDED", raw, None


def _reject_origin(origin: str) -> str | None:
    parsed = urlsplit(origin)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return "scheme must be http"
    if parsed.username or parsed.password:
        return "userinfo is not allowed"
    if parsed.path not in {"", "/"}:
        return "authorized_origin must not include a path"
    if parsed.query or parsed.fragment:
        return "authorized_origin must not include query or fragment"
    return None


def _origin_of(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port
    if not parsed.scheme:
        return ""
    if port is None:
        return f"{parsed.scheme}://{host}"
    return f"{parsed.scheme}://{host}:{port}"


def _resolve_location(response_url: str, location: str) -> str:
    raw = location if isinstance(location, str) else ""
    return urljoin(response_url, raw.strip())


def _post(
    origin: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
    envelope: object,
) -> dict[str, Any]:
    parsed = urlsplit(origin)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise _OriginRejected("non-loopback HTTP is blocked")
    url = f"{origin}{path}"
    allowed, reason = envelope_allows(envelope, url)
    if not allowed:
        raise _OriginRejected(f"request is outside authorized network envelope: {reason}")
    connection = http.client.HTTPConnection(
        parsed.hostname, parsed.port or 80, timeout=ABSOLUTE_TIMEOUT_SECONDS
    )
    try:
        connection.request("POST", path, body=body, headers=dict(headers))
        response = connection.getresponse()
        status = int(response.status)
        if status in REDIRECT_STATUSES:
            location = response.getheader("Location") or ""
            response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
            raise _RedirectStopped(status, location or "", f"{origin}{path}")
        body_bytes = response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
        cookies = _parse_set_cookie(response.getheader("Set-Cookie"))
    finally:
        connection.close()
    if len(body_bytes) > ABSOLUTE_MAX_RESPONSE_BYTES:
        raise _BoundExceeded()
    payload: dict[str, Any] = {"status_code": status, "cookies": cookies}
    if body_bytes:
        try:
            parsed_body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_body = None
        if isinstance(parsed_body, dict):
            payload["json_top_level_keys"] = sorted(str(key) for key in parsed_body)
    return payload


def _parse_set_cookie(header: str | None) -> dict[str, str]:
    if not header:
        return {}
    first = header.split(";", 1)[0]
    if "=" not in first:
        return {}
    name, value = first.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        return {}
    if any(marker in name or marker in value for marker in CRLF_MARKERS):
        return {}
    return {name: value}
