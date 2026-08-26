"""Fail-closed browser.page argument policy. Not authorization. Not execution."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from zest.tools.registry import ArgumentValidationIssue

CRLF_MARKERS = ("\r", "\n", "\x00")
INTERACT_KINDS = frozenset({"click", "fill", "select", "submit"})
BROWSER_PAGE_MAX_NETWORK_REQUESTS = 16
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
FORBIDDEN_SELECTORS = ("css=", "xpath=", "//", ">>", "document.query", "javascript:")


def validate_browser_page_arguments(
    action_id: str, arguments: Mapping[str, object]
) -> ArgumentValidationIssue | None:
    origin = arguments.get("authorized_origin")
    if not isinstance(origin, str) or not origin.strip():
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin is required")
    if any(marker in origin for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin must not contain CRLF")
    parsed_origin = urlsplit(origin.strip())
    if parsed_origin.path not in {"", "/"}:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin must not include a path")
    if parsed_origin.query or parsed_origin.fragment:
        return ArgumentValidationIssue(
            "INVALID_ARGUMENT_TYPE", "authorized_origin must not include query or fragment"
        )
    path = arguments.get("path")
    if not isinstance(path, str):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must be a string")
    path_issue = _path_issue(path)
    if path_issue is not None:
        return path_issue
    if any(key.lower() in {"selector", "css", "xpath", "javascript", "script"} for key in arguments):
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "arbitrary selector or JavaScript is not allowed")
    session_ref = arguments.get("session_context_reference")
    if session_ref is not None:
        if not isinstance(session_ref, str) or not session_ref.strip():
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "session_context_reference must be a string")
        if any(marker in session_ref for marker in CRLF_MARKERS):
            return ArgumentValidationIssue(
                "INVALID_ARGUMENT_TYPE", "session_context_reference must not contain CRLF"
            )
    if action_id == "interact":
        return _interact_issue(arguments)
    if action_id in {"observe", "navigate"}:
        if "kind" in arguments or "value" in arguments or "element_reference" in arguments:
            return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", f"{action_id} cannot carry interact fields")
        return None
    return ArgumentValidationIssue("UNKNOWN_ACTION", "browser.page action is unknown")


def _interact_issue(arguments: Mapping[str, object]) -> ArgumentValidationIssue | None:
    kind = arguments.get("kind")
    if not isinstance(kind, str) or kind not in INTERACT_KINDS:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "interact kind is invalid")
    for name in ("browser_context_reference", "page_reference", "element_reference", "snapshot_fingerprint"):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", f"{name} is required")
        if any(marker in value for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", f"{name} must not contain CRLF")
    fill_value = arguments.get("value")
    if kind in {"fill", "select"}:
        if not isinstance(fill_value, str):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "fill/select requires a string value")
        if any(marker in fill_value for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "value must not contain CRLF")
        lowered = fill_value.lower()
        if any(token in lowered for token in FORBIDDEN_VALUE_TOKENS):
            return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "secret or credential material is not allowed")
        if any(item in lowered for item in FORBIDDEN_SELECTORS):
            return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "arbitrary selector or JavaScript is not allowed")
    elif fill_value is not None:
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "click/submit must not include a value")
    return None


def _path_issue(path: str) -> ArgumentValidationIssue | None:
    if any(marker in path for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must not contain CRLF")
    if path.startswith("//") or "://" in path:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must not be an absolute URL")
    if "\\" in path or "/../" in path or path.endswith("/..") or "/./" in path or path.endswith("/."):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path is ambiguous")
    if "%" in path and ("%2f" in path.lower() or "%2e" in path.lower()):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path is ambiguous")
    if not path.startswith("/"):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must be absolute")
    if "//" in path:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path is ambiguous")
    return None
