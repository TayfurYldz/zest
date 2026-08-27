"""browser.page Worker executor. Not authorization. Not a scanner."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urlsplit

from .browser_engine import (
    BrowserActionResult,
    BrowserContextLease,
    BrowserEngine,
    BrowserEngineUnavailable,
    BrowserRuntimeLimits,
    PROCESS_GENERATION,
    lease_binding_matches,
)
from .browser_containment import CONTAINMENT_NOT_ESTABLISHED, containment
from .browser_envelope import parse_envelope

BROWSER_PAGE_CAPABILITY = "browser.page"
SNAPSHOT_SCHEMA_VERSION = "browser.page.snapshot.v1"
ALLOWED_SCHEMES = frozenset({"http", "https"})
INTERACT_KINDS = frozenset({"click", "fill", "select", "submit"})
FORBIDDEN_HEADERS = frozenset(
    {"cookie", "cookie2", "set-cookie", "authorization", "proxy-authorization"}
)
FORBIDDEN_VALUE_TOKENS = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "password",
        "token",
        "secret",
        "bearer",
    }
)
CRLF_MARKERS = ("\r", "\n", "\x00")
MAX_ATTEMPTED_REQUESTS = 16

_ENGINE: BrowserEngine | None = None
_ENGINE_UNAVAILABLE = False


def execute_browser_page(
    request: Mapping[str, Any],
    *,
    engine: BrowserEngine | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        return _fail("EXECUTION_FAILED", "arguments must be an object")
    envelope = parse_envelope(request.get("network_envelope"))
    if envelope is None:
        return _fail("EXECUTION_FAILED", "network_envelope is required")
    max_attempted = request.get("max_attempted_requests")
    if not isinstance(max_attempted, int) or max_attempted < 1:
        return _fail("EXECUTION_FAILED", "max_attempted_requests is required")
    max_attempted = min(max_attempted, MAX_ATTEMPTED_REQUESTS)
    origin = arguments.get("authorized_origin")
    path = arguments.get("path")
    action = request.get("action")
    if not isinstance(origin, str) or not origin.strip():
        return _fail("EXECUTION_FAILED", "authorized_origin is required")
    if not isinstance(path, str):
        return _fail("EXECUTION_FAILED", "path is required")
    origin_error = _reject_origin(origin)
    if origin_error is not None:
        return _fail("BLOCKED", origin_error)
    path_error = _reject_path(path)
    if path_error is not None:
        return _fail("BLOCKED", path_error)
    header_error = _reject_caller_headers(arguments)
    if header_error is not None:
        return _fail("BLOCKED", header_error)
    origin = origin.strip().rstrip("/")
    cookie, cookie_error = _session_cookie(request, arguments)
    if cookie_error is not None:
        return cookie_error
    binding = _binding_from_request(request, origin)
    limits = _limits_for(action, arguments, max_attempted)
    url = f"{origin}{path}"
    runtime, engine_error = _resolve_engine(engine)
    if engine_error is not None:
        return engine_error
    lease_ref = arguments.get("browser_context_reference")
    lease, lease_error = _require_lease(runtime, lease_ref, binding, action)
    if lease_error is not None:
        return lease_error
    if action == "navigate":
        result = runtime.navigate(
            lease.context_ref if lease is not None else None,
            url,
            envelope,
            limits,
            cookie=cookie,
            binding=binding,
        )
        return _from_engine(result)
    if action == "observe":
        if lease is None:
            result = runtime.navigate(
                None, url, envelope, limits, cookie=cookie, binding=binding
            )
            return _from_engine(result)
        result = runtime.observe(lease, envelope, limits)
        return _from_engine(result)
    if action == "interact":
        return _interact(runtime, request, arguments, lease, envelope, limits)
    return _fail("EXECUTION_FAILED", "browser.page action is unknown")


def shutdown_engine() -> None:
    global _ENGINE, _ENGINE_UNAVAILABLE
    if _ENGINE is not None:
        try:
            _ENGINE.close_all()
        finally:
            _ENGINE.stop()
            _ENGINE = None
    _ENGINE_UNAVAILABLE = False


def _resolve_engine(
    engine: BrowserEngine | None,
) -> tuple[BrowserEngine | None, tuple[str, dict[str, Any], dict[str, Any]] | None]:
    if engine is not None:
        return engine, None
    if containment() is None:
        return None, (
            "EXECUTION_FAILED",
            {},
            {
                "error": "kernel resource containment was not acknowledged",
                "reason_code": CONTAINMENT_NOT_ESTABLISHED,
            },
        )
    production = _production_engine()
    if production is None:
        return None, (
            "EXECUTION_FAILED",
            {},
            {
                "error": "browser engine is unavailable",
                "reason_code": "IMPLEMENTATION_NOT_AVAILABLE",
            },
        )
    return production, None


def _production_engine() -> BrowserEngine | None:
    global _ENGINE, _ENGINE_UNAVAILABLE
    if _ENGINE is not None:
        return _ENGINE
    if _ENGINE_UNAVAILABLE:
        return None
    if containment() is None:
        return None
    try:
        from .playwright_chromium_engine import PlaywrightChromiumEngine

        created = PlaywrightChromiumEngine()
        created.start()
    except (BrowserEngineUnavailable, ImportError, OSError):
        _ENGINE_UNAVAILABLE = True
        return None
    except Exception:  # noqa: BLE001 — production engine must fail closed, never fall back
        _ENGINE_UNAVAILABLE = True
        return None
    _ENGINE = created
    return _ENGINE


def _interact(
    runtime: BrowserEngine,
    request: Mapping[str, Any],
    arguments: Mapping[str, Any],
    lease: BrowserContextLease | None,
    envelope: Any,
    limits: BrowserRuntimeLimits,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if lease is None:
        return _fail("EXECUTION_FAILED", "browser_context_reference is required")
    kind = arguments.get("kind")
    element_ref = arguments.get("element_reference")
    snapshot_fp = arguments.get("snapshot_fingerprint")
    page_ref = arguments.get("page_reference")
    if kind not in INTERACT_KINDS:
        return _fail("BLOCKED", "interact kind is invalid")
    if not isinstance(element_ref, str) or not element_ref.strip():
        return _fail("EXECUTION_FAILED", "element_reference is required")
    if not isinstance(snapshot_fp, str) or not snapshot_fp.strip():
        return _fail("EXECUTION_FAILED", "snapshot_fingerprint is required")
    if not isinstance(page_ref, str) or page_ref != lease.page_ref:
        return _fail("BLOCKED", "page_reference does not match the leased page")
    current_fp = runtime.snapshot_fingerprint_for(lease.context_ref)
    if current_fp is None or snapshot_fp != current_fp:
        return _fail("BLOCKED", "stale snapshot")
    value = arguments.get("value")
    if kind in {"fill", "select"}:
        if not isinstance(value, str):
            return _fail("EXECUTION_FAILED", "fill/select requires a string value")
        if any(marker in value for marker in CRLF_MARKERS):
            return _fail("BLOCKED", "value must not contain CRLF")
        lowered = value.lower()
        if any(token in lowered for token in FORBIDDEN_VALUE_TOKENS):
            return _fail("BLOCKED", "secret or credential material is not allowed")
    elif value is not None:
        return _fail("BLOCKED", "click/submit must not include a value")
    result = runtime.interact(
        lease,
        str(kind),
        element_ref,
        snapshot_fp,
        value if isinstance(value, str) else None,
        envelope,
        limits,
    )
    return _from_engine(result)


def _require_lease(
    runtime: BrowserEngine,
    lease_ref: object,
    binding: dict[str, Any],
    action: object,
) -> tuple[BrowserContextLease | None, tuple[str, dict[str, Any], dict[str, Any]] | None]:
    if lease_ref is None:
        if action == "interact":
            return None, _fail("EXECUTION_FAILED", "browser_context_reference is required")
        return None, None
    if not isinstance(lease_ref, str) or not lease_ref.strip():
        return None, _fail("EXECUTION_FAILED", "browser_context_reference is invalid")
    lease = runtime.get_lease(lease_ref)
    if lease is None or lease.generation != PROCESS_GENERATION:
        return None, _fail("BLOCKED", "unknown browser_context_reference")
    if not lease_binding_matches(lease, binding):
        return None, _fail("BLOCKED", "browser context binding mismatch")
    if lease.frozen:
        return None, _fail("BLOCKED", "browser context is frozen")
    return lease, None


def _binding_from_request(request: Mapping[str, Any], origin: str) -> dict[str, Any]:
    correlation = request.get("correlation")
    if not isinstance(correlation, Mapping):
        correlation = {}
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    identity = arguments.get("identity_id")
    session_ref = arguments.get("session_context_reference")
    headers = arguments.get("headers")
    required_user_agent = (
        headers.get("User-Agent")
        if isinstance(headers, Mapping)
        else None
    )
    required_headers = {}
    if isinstance(headers, Mapping):
        required_headers = {
            name: value
            for name, value in headers.items()
            if isinstance(name, str)
            and name.lower() != "user-agent"
        }
    return {
        "research_run_id": correlation.get("research_run_id"),
        "identity_id": identity if isinstance(identity, str) else None,
        "session_context_reference": session_ref if isinstance(session_ref, str) else None,
        "origin": origin,
        "target_reference": request.get("target_reference"),
        "capability_version": request.get("capability_version"),
        "fingerprint": request.get("capability_definition_fingerprint"),
        "required_user_agent": required_user_agent,
        "required_headers": required_headers,
    }


def _limits_for(
    action: object, arguments: Mapping[str, Any], max_attempted: int
) -> BrowserRuntimeLimits:
    limits = BrowserRuntimeLimits(max_attempted_network_requests_per_action=max_attempted)
    timeout_ms = arguments.get("timeout_ms")
    if not isinstance(timeout_ms, int) or timeout_ms < 1:
        return limits
    if action == "navigate":
        return replace(
            limits,
            max_navigation_runtime_ms=min(timeout_ms, limits.max_navigation_runtime_ms),
            max_action_runtime_ms=min(timeout_ms, limits.max_action_runtime_ms),
        )
    return replace(limits, max_action_runtime_ms=min(timeout_ms, limits.max_action_runtime_ms))


def _session_cookie(
    request: Mapping[str, Any], arguments: Mapping[str, Any]
) -> tuple[str | None, tuple[str, dict[str, Any], dict[str, Any]] | None]:
    session_ref = arguments.get("session_context_reference")
    if session_ref is None:
        return None, None
    if not isinstance(session_ref, str) or not session_ref.strip():
        return None, _fail("BLOCKED", "session_context_reference is invalid")
    resolved = request.get("resolved_secret_values")
    if not isinstance(resolved, Mapping) or not isinstance(resolved.get("session_cookie"), str):
        return None, (
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
        return None, _fail("BLOCKED", "session material is invalid")
    return cookie_value, None


def _reject_caller_headers(arguments: Mapping[str, Any]) -> str | None:
    headers = arguments.get("headers")
    if headers is None:
        return None
    if not isinstance(headers, Mapping):
        return "headers must be an object"
    for name in headers:
        if not isinstance(name, str):
            return "header names must be strings"
        if name.lower() in FORBIDDEN_HEADERS:
            return f"header {name} is not allowed"
        value = headers[name]
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(marker in value for marker in CRLF_MARKERS)
            or len(value) > 256
        ):
            return f"header {name} is invalid"
        if name.lower() == "user-agent" and len(value) > 128:
            return "User-Agent is invalid"
    return None


def _reject_origin(origin: str) -> str | None:
    if any(marker in origin for marker in CRLF_MARKERS):
        return "authorized_origin must not contain CRLF"
    try:
        parsed = urlsplit(origin.strip())
    except ValueError:
        return "authorized_origin is invalid"
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
    if ".." in path:
        return "path is ambiguous"
    return None


def _from_engine(
    result: BrowserActionResult,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    raw = dict(result.raw)
    _strip_forbidden(raw)
    diagnostics = None if result.diagnostics is None else dict(result.diagnostics)
    if diagnostics is not None:
        _strip_forbidden(diagnostics)
    return result.status, raw, diagnostics


def _strip_forbidden(payload: dict[str, Any]) -> None:
    forbidden = {"cookie", "set-cookie", "password", "html", "screenshot", "innerhtml", "authorization"}
    for key in list(payload):
        if key.lower() in forbidden:
            payload.pop(key, None)


def _fail(status: str, error: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return status, {}, {"error": error, "self_authorized": False, "contacted": False}
