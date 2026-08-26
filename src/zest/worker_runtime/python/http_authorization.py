"""Bounded read-only HTTP authorization differential probe. Not a scanner.

GET + loopback + exact origin only. Redirects stop. No shell. No writes.
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from .browser_envelope import envelope_allows, parse_envelope

HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY = "http.authorization.differential"
HTTP_AUTHORIZATION_DIFFERENTIAL_ACTION = "probe"
ALLOWED_SCHEMES = frozenset({"http"})
MAX_RESPONSE_BYTES = 4096
MAX_REQUESTS = 4
TIMEOUT_SECONDS = 2.0
ABSOLUTE_MAX_RESPONSE_BYTES = 1_048_576
ABSOLUTE_TIMEOUT_SECONDS = 10.0
ACTOR_HEADER = "X-Lab-Actor"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


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


def execute_http_authorization(request: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
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
    actor = arguments.get("actor")
    own_object = arguments.get("own_object")
    cross_object = arguments.get("cross_object")
    mode = arguments.get("mode") or "vulnerable"
    if not all(isinstance(item, str) and item.strip() for item in (origin, actor, own_object, cross_object)):
        return "EXECUTION_FAILED", {}, {"error": "authorized_origin, actor, own_object, cross_object are required"}
    if mode not in {"vulnerable", "secure_only", "redirect"}:
        return "EXECUTION_FAILED", {}, {"error": "mode must be vulnerable, secure_only, or redirect"}
    origin = origin.strip()
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return "BLOCKED", {}, {"error": origin_error, "contacted": False}
    if mode == "redirect":
        planned = (("redirect", f"{origin}/redirect", actor.strip()),)
    else:
        prefix = "/vulnerable" if mode == "vulnerable" else "/secure"
        planned = (
            ("owner_request", f"{origin}{prefix}/accounts/{own_object.strip()}", actor.strip()),
            ("cross_object_request", f"{origin}{prefix}/accounts/{cross_object.strip()}", actor.strip()),
            ("secure_control", f"{origin}/secure/accounts/{cross_object.strip()}", actor.strip()),
            ("unauthenticated_control", f"{origin}{prefix}/accounts/{cross_object.strip()}", None),
        )
    if len(planned) > MAX_REQUESTS:
        return "EXECUTION_FAILED", {}, {"error": "request budget exceeded"}
    raw: dict[str, Any] = {"mode": mode, "authorized_origin": origin}
    for key, url, header_actor in planned:
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
            raw[key] = _get(url, origin, header_actor, envelope)
        except _RedirectStopped as exc:
            new_origin = _origin_of(exc.new_url) if exc.new_url else ""
            return (
                "REAUTHORIZATION_REQUIRED",
                {"stopped": True, "reason": "redirect_or_new_origin", "status": exc.status},
                {
                    "redirect": True,
                    "new_origin": new_origin,
                    "location": exc.new_url,
                    "raw_location": exc.raw_location,
                    "response_url": exc.response_url,
                    "requires_core_re_evaluation": True,
                    "followed": False,
                },
            )
        except _OriginRejected as exc:
            return "BLOCKED", {}, {"error": str(exc), "contacted": False}
        except _BoundExceeded:
            return "EXECUTION_FAILED", {}, {"error": "response exceeded byte bound", "contacted": True}
        except (TimeoutError, socket.timeout):
            return "TIMED_OUT", {}, {"error": "timeout", "contacted": True}
        except Exception as exc:  # noqa: BLE001 — worker must map transport failure, not raise through protocol
            return "EXECUTION_FAILED", {}, {"error": type(exc).__name__, "contacted": True}
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


def _get(url: str, authorized_origin: str, actor: str | None, envelope: object) -> dict[str, Any]:
    if _origin_of(url) != authorized_origin:
        raise _OriginRejected("request origin does not match authorized_origin")
    parsed = urlsplit(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise _OriginRejected("non-loopback HTTP is blocked")
    allowed, reason = envelope_allows(envelope, url)
    if not allowed:
        raise _OriginRejected(f"request is outside authorized network envelope: {reason}")
    headers = {"Accept": "application/json", "Connection": "close"}
    if actor is not None:
        headers[ACTOR_HEADER] = actor
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port or 80,
        timeout=ABSOLUTE_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        status = int(response.status)
        if status in REDIRECT_STATUSES:
            location = response.getheader("Location") or ""
            response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
            raise _RedirectStopped(status, location or "", url)
        body = response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
    finally:
        connection.close()
    if len(body) > ABSOLUTE_MAX_RESPONSE_BYTES:
        raise _BoundExceeded()
    payload: dict[str, Any] = {"status": status}
    if body:
        try:
            parsed_body = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_body = None
        if isinstance(parsed_body, dict):
            payload.update(_observed_object_fields(parsed_body))
    return payload


def _observed_object_fields(parsed_body: Mapping[str, Any]) -> dict[str, Any]:
    """Copy observed authorization-context fields. Do not infer vulnerability truth."""

    observed: dict[str, Any] = {}
    raw_owner = parsed_body.get("owner")
    if isinstance(raw_owner, str) and raw_owner.strip():
        observed["object_owner"] = raw_owner.strip()
    raw_visibility = parsed_body.get("visibility")
    if isinstance(raw_visibility, str) and raw_visibility.strip():
        observed["object_visibility"] = raw_visibility.strip()
    raw_readers = parsed_body.get("authorized_readers")
    if isinstance(raw_readers, list) and all(
        isinstance(entry, str) and entry.strip() for entry in raw_readers
    ):
        observed["object_authorized_readers"] = [entry.strip() for entry in raw_readers]
    raw_kind = parsed_body.get("resource_kind")
    if isinstance(raw_kind, str) and raw_kind.strip():
        observed["object_resource_kind"] = raw_kind.strip()
    return observed
