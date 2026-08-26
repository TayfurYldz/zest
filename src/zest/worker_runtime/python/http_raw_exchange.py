"""Bounded loopback raw HTTP exchange. Not a scanner. Not a generic TCP client.

Writes only compiler-catalog framing profiles to a Core-derived envelope.
Never follows redirects. Never self-authorizes. No argv. No arbitrary host.
"""

from __future__ import annotations

import hashlib
import socket
from typing import Any, Mapping
from urllib.parse import urlsplit

from .browser_envelope import envelope_allows, parse_envelope

HTTP_RAW_EXCHANGE_CAPABILITY = "http.raw_exchange"
ALLOWED_SCHEMES = frozenset({"http"})
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_MAX_RESPONSE_BYTES = 4096
ABSOLUTE_MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 2.0
ABSOLUTE_TIMEOUT_SECONDS = 10.0
MAX_WRITES = 2
CRLF_MARKERS = ("\r", "\n", "\x00")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
FRAMING_PROFILES = frozenset(
    {
        "http1_canonical",
        "http1_header_case_fold",
        "http1_absolute_uri",
        "http1_cl_te",
        "http1_te_cl",
        "http1_connection_reuse",
        "http2_preface",
        "h2c_upgrade",
        "http1_cache_host",
        "http1_cache_scheme",
        "http1_cache_path",
        "http1_cache_query",
        "http1_cache_header",
    }
)


def execute_http_raw_exchange(
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
            {"error": "network_envelope is required", "contacted": False, "self_authorized": False},
        )
    origin = arguments.get("authorized_origin")
    path = arguments.get("path")
    profile = arguments.get("framing_profile")
    control = arguments.get("control")
    lane = arguments.get("lane")
    if not all(isinstance(item, str) and item.strip() for item in (origin, path, profile, control, lane)):
        return "EXECUTION_FAILED", {}, {"error": "authorized_origin, path, framing_profile, control, lane are required"}
    origin = origin.strip().rstrip("/")
    path = path.strip()
    profile = profile.strip()
    if profile not in FRAMING_PROFILES:
        return "BLOCKED", {}, {"error": "framing_profile is not in the closed catalog", "contacted": False}
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return "BLOCKED", {}, {"error": origin_error, "contacted": False, "self_authorized": False}
    path_error = _reject_path(path)
    if path_error is not None:
        return "BLOCKED", {}, {"error": path_error, "contacted": False, "self_authorized": False}
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
    parsed = urlsplit(origin)
    host = parsed.hostname or ""
    port = parsed.port or 80
    writes = _catalog_writes(profile, origin, path, control.strip(), lane.strip())
    if len(writes) > MAX_WRITES:
        return "BLOCKED", {}, {"error": "framing_profile exceeds write bound", "contacted": False}
    try:
        captured = _exchange(host, port, writes, max_response_bytes=max_response_bytes, timeout=timeout)
    except _RedirectStopped as exc:
        return (
            "REAUTHORIZATION_REQUIRED",
            {
                "stopped": True,
                "reason": "redirect_or_new_origin",
                "status_code": exc.status,
                "path": path,
                "framing_profile": profile,
            },
            {
                "redirect": True,
                "requires_core_re_evaluation": True,
                "followed": False,
                "self_authorized": False,
                "location": exc.location,
            },
        )
    except socket.timeout:
        return "TIMED_OUT", {}, {"error": "timeout", "contacted": True}
    except OSError as exc:
        return "EXECUTION_FAILED", {}, {"error": type(exc).__name__, "contacted": True}
    raw = {
        "authorized_origin": origin,
        "path": path,
        "framing_profile": profile,
        "lane": lane,
        "control": control.strip()[:64],
        "status_code": captured["status_code"],
        "write_count": len(writes),
        "bytes_written": sum(len(item) for item in writes),
        "body_length": captured["body_length"],
        "body_digest": captured["body_digest"],
        "request_fingerprint": hashlib.sha256(b"".join(writes)).hexdigest(),
        "self_authorized": False,
        "redirect": False,
    }
    return "SUCCEEDED", raw, None


class _RedirectStopped(Exception):
    def __init__(self, status: int, location: str) -> None:
        super().__init__("redirect stopped")
        self.status = status
        self.location = location


def _catalog_writes(profile: str, origin: str, path: str, control: str, lane: str) -> tuple[bytes, ...]:
    host = urlsplit(origin).netloc
    marker = f"x-ros-ctl: {control}\r\nx-ros-lane: {lane}\r\n"
    if profile == "http1_canonical":
        return (_http1(host, path, extra=marker),)
    if profile == "http1_header_case_fold":
        return (_http1(host, path, extra=marker, host_header="HOST"),)
    if profile == "http1_absolute_uri":
        target = f"{origin}{path}"
        return (_http1(host, target, extra=marker),)
    if profile == "http1_cl_te":
        return (_cl_te(host, path, marker),)
    if profile == "http1_te_cl":
        return (_te_cl(host, path, marker),)
    if profile == "http1_connection_reuse":
        first = _http1(host, path, extra=marker + "connection: keep-alive\r\n")
        second = _http1(host, path, extra=marker + "connection: close\r\n")
        return (first, second)
    if profile == "http2_preface":
        return (b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n",)
    if profile == "h2c_upgrade":
        extra = (
            marker
            + "connection: Upgrade, HTTP2-Settings\r\n"
            + "upgrade: h2c\r\n"
            + "http2-settings: AAMAAABkAAQAAP__\r\n"
        )
        return (_http1(host, path, extra=extra),)
    if profile == "http1_cache_host":
        return (_http1(host, f"{origin}{path}", extra=marker),)
    if profile == "http1_cache_scheme":
        return (_http1(host, path, extra=marker + "x-forwarded-proto: https\r\n"),)
    if profile == "http1_cache_path":
        return (_http1(host, path, extra=marker + "x-ros-cache-path: 1\r\n"),)
    if profile == "http1_cache_query":
        joined = path if "?" in path else f"{path}?ros_ck=1"
        return (_http1(host, joined, extra=marker),)
    if profile == "http1_cache_header":
        return (_http1(host, path, extra=marker + "x-ros-cache-key: 1\r\n"),)
    raise ValueError("unknown framing_profile")


def _http1(host: str, target: str, *, extra: str = "", host_header: str = "Host") -> bytes:
    return (
        f"GET {target} HTTP/1.1\r\n"
        f"{host_header}: {host}\r\n"
        f"{extra}"
        "\r\n"
    ).encode("ascii")


def _cl_te(host: str, path: str, marker: str) -> bytes:
    body = b"0\r\n\r\n"
    head = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"{marker}"
        "Content-Length: 6\r\n"
        "Transfer-Encoding: chunked\r\n"
        "\r\n"
    ).encode("ascii")
    return head + body


def _te_cl(host: str, path: str, marker: str) -> bytes:
    body = b"0\r\n\r\n"
    head = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"{marker}"
        "Transfer-Encoding: chunked\r\n"
        "Content-Length: 6\r\n"
        "\r\n"
    ).encode("ascii")
    return head + body


def _exchange(
    host: str,
    port: int,
    writes: tuple[bytes, ...],
    *,
    max_response_bytes: int,
    timeout: float,
) -> dict[str, Any]:
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        chunks: list[bytes] = []
        total = 0
        for payload in writes:
            sock.sendall(payload)
            while total < max_response_bytes:
                piece = sock.recv(min(1024, max_response_bytes - total))
                if not piece:
                    break
                chunks.append(piece)
                total += len(piece)
                if b"\r\n\r\n" in b"".join(chunks) and len(writes) == 1:
                    break
        raw = b"".join(chunks)[:max_response_bytes]
    finally:
        sock.close()
    status, location = _status_and_location(raw)
    if status in REDIRECT_STATUSES:
        raise _RedirectStopped(status, location)
    digest = hashlib.sha256(raw).hexdigest()
    return {"status_code": status, "body_length": len(raw), "body_digest": digest}


def _status_and_location(raw: bytes) -> tuple[int, str]:
    text = raw.decode("latin-1", errors="replace")
    first = text.split("\r\n", 1)[0]
    status = 0
    parts = first.split(" ")
    if len(parts) >= 2 and parts[1].isdigit():
        status = int(parts[1])
    location = ""
    for line in text.split("\r\n"):
        if line.lower().startswith("location:"):
            location = line.split(":", 1)[1].strip()
            break
    return status, location


def _reject_origin(origin: str) -> str | None:
    if any(marker in origin for marker in CRLF_MARKERS):
        return "authorized_origin must not contain CRLF"
    parsed = urlsplit(origin)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return "authorized_origin scheme is not allowed"
    if parsed.hostname != LOOPBACK_HOST:
        return "authorized_origin must be loopback"
    if parsed.path not in {"", "/"}:
        return "authorized_origin must not include a path"
    if parsed.query or parsed.fragment:
        return "authorized_origin must not include query or fragment"
    if parsed.username or parsed.password:
        return "authorized_origin must not include userinfo"
    return None


def _reject_path(path: str) -> str | None:
    if any(marker in path for marker in CRLF_MARKERS):
        return "path must not contain CRLF"
    if not path.startswith("/"):
        return "path must be absolute"
    if path.startswith("//") or "://" in path:
        return "path must not be an absolute URL"
    if "\\" in path or "/../" in path or path.endswith("/.."):
        return "path is ambiguous"
    return None
