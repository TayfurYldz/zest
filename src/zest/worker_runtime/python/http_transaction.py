"""Bounded authorized HTTP transaction executor. Not a scanner. Not a crawler.

Connects only to a normalized loopback origin. Never follows redirects.
Never self-authorizes. Never persists secrets.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import time
from typing import Any, Mapping
from urllib.parse import urlencode, urljoin, urlsplit

from .browser_envelope import envelope_allows, parse_envelope

HTTP_TRANSACTION_CAPABILITY = "http.transaction"
ALLOWED_SCHEMES = frozenset({"http", "https"})

ALLOWED_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_MUTATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FORBIDDEN_REQUEST_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-authorization",
        "proxy-connection",
        "authorization",
        "cookie",
        "cookie2",
        "set-cookie",
    }
)
ALLOWED_REQUEST_HEADERS = frozenset(
    {
        "user-agent",
        "accept",
        "accept-language",
        "content-type",
        "x-request-id",
    }
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_MAX_RESPONSE_BYTES = 4096
DEFAULT_TIMEOUT_SECONDS = 2.0
ABSOLUTE_MAX_RESPONSE_BYTES = 1_048_576
ABSOLUTE_TIMEOUT_SECONDS = 10.0
MAX_HEADER_COUNT = 8
MAX_HEADER_NAME_LENGTH = 64
MAX_HEADER_VALUE_LENGTH = 128
CRLF_MARKERS = ("\r", "\n", "\x00")
RESPONSE_HEADER_ALLOWLIST = frozenset({"content-type", "location"})


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


class _RequestRejected(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def execute_http_transaction(
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
    if arguments.get("session_context_reference") is not None:
        session_ref = arguments.get("session_context_reference")
        if not isinstance(session_ref, str) or not session_ref.strip():
            return (
                "BLOCKED",
                {},
                {"error": "session_context_reference is invalid", "contacted": False, "self_authorized": False},
            )
        resolved = request.get("resolved_secret_values")
        if not isinstance(resolved, Mapping) or not isinstance(resolved.get("session_cookie"), str):
            return (
                "BLOCKED",
                {},
                {
                    "error": "session material is unavailable",
                    "contacted": False,
                    "self_authorized": False,
                    "reauthentication_required": True,
                },
            )
        cookie_value = resolved["session_cookie"]
        if any(marker in cookie_value for marker in CRLF_MARKERS) or cookie_value.strip().lower().startswith(
            ("authorization:", "cookie:")
        ):
            return (
                "BLOCKED",
                {},
                {"error": "session material is invalid", "contacted": False, "self_authorized": False},
            )
    origin = arguments.get("authorized_origin")
    method = arguments.get("method")
    path = arguments.get("path")
    action = request.get("action")
    if not all(isinstance(item, str) and item.strip() for item in (origin, method, path)):
        return "EXECUTION_FAILED", {}, {"error": "authorized_origin, method, and path are required"}
    origin = origin.strip().rstrip("/")
    method = method.strip().upper()
    path_error = _reject_path(path)
    if path_error is not None:
        return "BLOCKED", {}, {"error": path_error, "contacted": False, "self_authorized": False}
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return "BLOCKED", {}, {"error": origin_error, "contacted": False, "self_authorized": False}
    allowed_methods = ALLOWED_READ_METHODS if action == "read" else ALLOWED_MUTATE_METHODS
    if action not in {"read", "mutate"} or method not in allowed_methods:
        return (
            "BLOCKED",
            {},
            {"error": "method is not allowed for this action", "contacted": False, "self_authorized": False},
        )
    try:
        headers = _request_headers(arguments)
        if arguments.get("session_context_reference") is not None:
            headers["Cookie"] = str(request["resolved_secret_values"]["session_cookie"])
        query = _query_string(arguments.get("query") or {})
        body = _request_body(action, arguments)
    except _RequestRejected as exc:
        return "BLOCKED", {}, {"error": str(exc), "contacted": False, "self_authorized": False}
    max_response_bytes = arguments.get("max_response_bytes") or DEFAULT_MAX_RESPONSE_BYTES
    timeout_ms = arguments.get("timeout_ms")
    if not isinstance(max_response_bytes, int) or max_response_bytes < 1:
        return "EXECUTION_FAILED", {}, {"error": "max_response_bytes is invalid"}
    max_response_bytes = min(max_response_bytes, ABSOLUTE_MAX_RESPONSE_BYTES)
    timeout = DEFAULT_TIMEOUT_SECONDS
    if timeout_ms is not None:
        if not isinstance(timeout_ms, int) or timeout_ms < 1:
            return "EXECUTION_FAILED", {}, {"error": "timeout_ms is invalid"}
        timeout = min(timeout_ms / 1000.0, ABSOLUTE_TIMEOUT_SECONDS)
    request_path = path if not query else f"{path}?{query}"
    url = f"{origin}{request_path}"
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
    started = time.perf_counter()
    try:
        captured = _exchange(
            origin=origin,
            method=method,
            request_path=request_path,
            headers=headers,
            body=body,
            max_response_bytes=max_response_bytes,
            timeout=timeout,
        )
    except _RedirectStopped as exc:
        new_origin = _origin_of(exc.new_url) if exc.new_url else ""
        return (
            "REAUTHORIZATION_REQUIRED",
            {
                "stopped": True,
                "reason": "redirect_or_new_origin",
                "status": exc.status,
                "method": method,
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
    except _RequestRejected as exc:
        return "BLOCKED", {}, {"error": str(exc), "contacted": False, "self_authorized": False}
    except _BoundExceeded:
        return "EXECUTION_FAILED", {}, {"error": "response exceeded byte bound", "contacted": True}
    except (TimeoutError, socket.timeout):
        return "TIMED_OUT", {}, {"error": "timeout", "contacted": True}
    except Exception as exc:  # noqa: BLE001 — worker must map transport failure, not raise through protocol
        return "EXECUTION_FAILED", {}, {"error": type(exc).__name__, "contacted": True}
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    raw = {
        "authorized_origin": origin,
        "method": method,
        "path": path,
        "status_code": captured["status_code"],
        "content_type": captured.get("content_type"),
        "response_headers": captured.get("response_headers") or {},
        "body_length": captured["body_length"],
        "body_digest": captured["body_digest"],
        "json_value_kind": captured.get("json_value_kind"),
        "json_top_level_keys": captured.get("json_top_level_keys") or [],
        "elapsed_ms": elapsed_ms,
        "request_fingerprint": _request_fingerprint(method, path, arguments.get("query") or {}, body),
        "url": url,
        "self_authorized": False,
    }
    return "SUCCEEDED", raw, None


def _reject_origin(origin: str) -> str | None:
    parsed = urlsplit(origin)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return "scheme must be http or https"
    if parsed.username or parsed.password:
        return "userinfo is not allowed"
    if parsed.path not in {"", "/"}:
        return "authorized_origin must not include a path"
    if parsed.query or parsed.fragment:
        return "authorized_origin must not include query or fragment"
    return None


def _reject_path(path: str) -> str | None:
    if any(marker in path for marker in CRLF_MARKERS):
        return "path must not contain CRLF"
    if path.startswith("//") or "://" in path:
        return "path must not be an absolute URL"
    if "\\" in path or "/../" in path or path.endswith("/..") or "/./" in path or path.endswith("/."):
        return "path is ambiguous"
    if "%" in path and ("%2f" in path.lower() or "%2e" in path.lower()):
        return "path is ambiguous"
    if not path.startswith("/"):
        return "path must be absolute"
    if "//" in path:
        return "path is ambiguous"
    return None


def _request_headers(arguments: Mapping[str, Any]) -> dict[str, str]:
    headers_in = arguments.get("headers") or {}
    if headers_in is None:
        headers_in = {}
    if not isinstance(headers_in, Mapping):
        raise _RequestRejected("headers must be an object")
    if len(headers_in) > MAX_HEADER_COUNT:
        raise _RequestRejected("header count exceeds bound")
    headers: dict[str, str] = {"Accept": "application/json", "Connection": "close"}
    for name, value in headers_in.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise _RequestRejected("header names and values must be strings")
        if any(marker in name or marker in value for marker in CRLF_MARKERS):
            raise _RequestRejected("headers must not contain CRLF")
        if len(name) > MAX_HEADER_NAME_LENGTH or len(value) > MAX_HEADER_VALUE_LENGTH:
            raise _RequestRejected("header exceeds bound")
        lowered = name.lower()
        if lowered in FORBIDDEN_REQUEST_HEADERS:
            raise _RequestRejected(f"header {name} is not allowed")
        if lowered not in ALLOWED_REQUEST_HEADERS:
            raise _RequestRejected(f"header {name} is not allowed")
        if not all(ch.isalnum() or ch == "-" for ch in name):
            raise _RequestRejected("header name is invalid")
        headers[name] = value
    content_type = arguments.get("content_type")
    if content_type is not None:
        if not isinstance(content_type, str) or any(marker in content_type for marker in CRLF_MARKERS):
            raise _RequestRejected("content_type is invalid")
        headers["Content-Type"] = content_type
    return headers


def _query_string(query: object) -> str:
    if query is None:
        return ""
    if not isinstance(query, Mapping):
        raise _RequestRejected("query must be an object")
    pairs = []
    for key, value in query.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise _RequestRejected("query keys and values must be strings")
        if any(marker in key or marker in value for marker in CRLF_MARKERS):
            raise _RequestRejected("query must not contain CRLF")
        pairs.append((key, value))
    if not pairs:
        return ""
    return urlencode(sorted(pairs))


def _request_body(action: str, arguments: Mapping[str, Any]) -> bytes | None:
    body = arguments.get("body")
    if body is None:
        return None
    if action == "read":
        raise _RequestRejected("read action must not include a body")
    if not isinstance(body, str):
        raise _RequestRejected("body must be a string")
    if body.strip().lower().startswith("file:"):
        raise _RequestRejected("body must not be a filesystem source")
    encoded = body.encode("utf-8")
    if len(encoded) > DEFAULT_MAX_RESPONSE_BYTES:
        raise _RequestRejected("body exceeds bound")
    return encoded


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


def _exchange(
    *,
    origin: str,
    method: str,
    request_path: str,
    headers: Mapping[str, str],
    body: bytes | None,
    max_response_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    parsed = urlsplit(origin)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise _OriginRejected("non-loopback HTTP(S) is blocked")
    connection = _connection(parsed, timeout)
    try:
        connection.request(method, request_path, body=body, headers=dict(headers))
        response = connection.getresponse()
        status = int(response.status)
        captured_headers = {}
        content_type = response.getheader("Content-Type")
        if content_type:
            captured_headers["content-type"] = content_type
        location = response.getheader("Location")
        if status in REDIRECT_STATUSES:
            response.read(max_response_bytes + 1)
            raise _RedirectStopped(status, location or "", f"{origin}{request_path}")
        if location:
            captured_headers["location"] = location
        body_bytes = response.read(max_response_bytes + 1)
    finally:
        connection.close()
    if len(body_bytes) > max_response_bytes:
        raise _BoundExceeded()
    payload: dict[str, Any] = {
        "status_code": status,
        "content_type": content_type,
        "response_headers": captured_headers,
        "body_length": len(body_bytes),
        "body_digest": hashlib.sha256(body_bytes).hexdigest(),
    }
    if body_bytes:
        try:
            parsed_body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_body = None
        if parsed_body is None:
            payload["json_value_kind"] = "invalid"
        elif isinstance(parsed_body, dict):
            payload["json_value_kind"] = "object"
            payload["json_top_level_keys"] = sorted(str(key) for key in parsed_body)
        elif isinstance(parsed_body, list):
            payload["json_value_kind"] = "array"
        elif isinstance(parsed_body, str):
            payload["json_value_kind"] = "string"
        elif isinstance(parsed_body, bool):
            payload["json_value_kind"] = "boolean"
        elif isinstance(parsed_body, (int, float)):
            payload["json_value_kind"] = "number"
        elif parsed_body is None:
            payload["json_value_kind"] = "null"
    return payload


def _connection(parsed, timeout: float):
    host = parsed.hostname
    if parsed.scheme == "https":
        context = (
            ssl._create_unverified_context()
            if host in {"127.0.0.1", "localhost"}
            else ssl.create_default_context()
        )
        return http.client.HTTPSConnection(
            host,
            parsed.port or 443,
            timeout=timeout,
            context=context,
        )
    return http.client.HTTPConnection(
        host,
        parsed.port or 80,
        timeout=timeout,
    )


def _request_fingerprint(method: str, path: str, query: object, body: bytes | None) -> str:
    canonical = json.dumps(
        {
            "method": method,
            "path": path,
            "query": query if isinstance(query, Mapping) else {},
            "body_digest": hashlib.sha256(body or b"").hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
