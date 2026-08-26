"""Bounded HTTP state-transition probe. Not a scanner. GET/POST loopback only."""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

from .browser_envelope import envelope_allows, parse_envelope

HTTP_STATE_TRANSITION_CAPABILITY = "http.state_transition"
HTTP_STATE_TRANSITION_ACTION = "probe"
ALLOWED_SCHEMES = frozenset({"http"})
ALLOWED_TRANSITIONS = frozenset({"submit", "review", "approve", "reject"})
ALLOWED_AREAS = frozenset({"workflow", "control", "redirect"})
MAX_RESPONSE_BYTES = 4096
MAX_POST_BYTES = 512
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


def execute_http_state_transition(
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
    actor = arguments.get("actor")
    resource_id = arguments.get("resource_id")
    transition = arguments.get("transition")
    area = arguments.get("area") or "workflow"
    if not all(
        isinstance(item, str) and item.strip()
        for item in (origin, actor, resource_id, transition)
    ):
        return (
            "EXECUTION_FAILED",
            {},
            {"error": "authorized_origin, actor, resource_id, transition are required"},
        )
    if area not in ALLOWED_AREAS:
        return "EXECUTION_FAILED", {}, {"error": "area must be workflow, control, or redirect"}
    if transition not in ALLOWED_TRANSITIONS:
        return "EXECUTION_FAILED", {}, {"error": "transition is not in the allowlist"}
    origin = origin.strip()
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return "BLOCKED", {}, {"error": origin_error, "contacted": False}
    actor = actor.strip()
    resource_id = resource_id.strip()
    if area == "redirect":
        planned = (("redirect", "GET", f"{origin}/redirect", actor, None),)
    else:
        prefix = f"{origin}/{area}/requests/{resource_id}"
        control = f"{origin}/control/requests/{resource_id}/{transition}"
        planned = (
            ("pre_state_request", "GET", prefix, actor, None),
            ("control_request", "POST", control, actor, {"transition": transition}),
            (
                "transition_request",
                "POST",
                f"{prefix}/{transition}",
                actor,
                {"transition": transition},
            ),
            ("post_state_request", "GET", prefix, actor, None),
        )
    if len(planned) > MAX_REQUESTS:
        return "EXECUTION_FAILED", {}, {"error": "request budget exceeded"}
    raw: dict[str, Any] = {
        "area": area,
        "authorized_origin": origin,
        "requested_transition": transition,
        "resource_id": resource_id,
    }
    for key, method, url, header_actor, body in planned:
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
            raw[key] = _http(method, url, origin, header_actor, body, envelope)
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
        except Exception as exc:  # noqa: BLE001 — worker must map transport failure
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


def _http(
    method: str,
    url: str,
    authorized_origin: str,
    actor: str | None,
    body: Mapping[str, Any] | None,
    envelope: object,
) -> dict[str, Any]:
    if method not in {"GET", "POST"}:
        raise _OriginRejected("method is not in the allowlist")
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
    encoded: bytes | None = None
    if method == "POST":
        if not isinstance(body, Mapping) or set(body.keys()) != {"transition"}:
            raise _OriginRejected("POST body must be a bounded transition object")
        encoded = json.dumps({"transition": body["transition"]}, separators=(",", ":")).encode(
            "utf-8"
        )
        if len(encoded) > MAX_POST_BYTES:
            raise _BoundExceeded()
        headers["Content-Type"] = "application/json"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port or 80,
        timeout=ABSOLUTE_TIMEOUT_SECONDS,
    )
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        status = int(response.status)
        if status in REDIRECT_STATUSES:
            location = response.getheader("Location") or ""
            response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
            raise _RedirectStopped(status, location or "", url)
        raw_body = response.read(ABSOLUTE_MAX_RESPONSE_BYTES + 1)
    finally:
        connection.close()
    if len(raw_body) > ABSOLUTE_MAX_RESPONSE_BYTES:
        raise _BoundExceeded()
    payload: dict[str, Any] = {"status": status, "method": method}
    if raw_body:
        try:
            parsed_body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_body = None
        if isinstance(parsed_body, dict):
            payload.update(_observed_workflow_fields(parsed_body))
    return payload


def _observed_workflow_fields(parsed_body: Mapping[str, Any]) -> dict[str, Any]:
    """Copy observed workflow fields. Do not infer vulnerability truth."""

    observed: dict[str, Any] = {}
    for key in (
        "request_id",
        "owner",
        "state",
        "approved_by",
        "actor_role",
        "approve_requires_role",
        "current_state",
        "error",
    ):
        value = parsed_body.get(key)
        if isinstance(value, str) and value.strip():
            observed[key] = value.strip()
    readers = parsed_body.get("delegated_reviewers")
    if isinstance(readers, list) and all(
        isinstance(entry, str) and entry.strip() for entry in readers
    ):
        observed["delegated_reviewers"] = [entry.strip() for entry in readers]
    states = parsed_body.get("approve_from_states")
    if isinstance(states, list) and all(
        isinstance(entry, str) and entry.strip() for entry in states
    ):
        observed["approve_from_states"] = [entry.strip() for entry in states]
    if parsed_body.get("ok") is True:
        observed["ok"] = True
    return observed
