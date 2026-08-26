"""Fail-closed HTTP transaction argument policy. Not authorization. Not execution."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from zest.tools.browser_page_policy import validate_browser_page_arguments
from zest.tools.http_authentication_policy import validate_http_authentication_arguments
from zest.tools.http_raw_exchange_policy import validate_http_raw_exchange_arguments
from zest.tools.registry import ArgumentValidationIssue

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
ALLOWED_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_MUTATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_HEADER_NAME_LENGTH = 64
CRLF_MARKERS = ("\r", "\n", "\x00")


def extra_argument_validator_for(capability_id: str):
    if capability_id == "http.transaction":
        return validate_http_transaction_arguments
    if capability_id == "http.authentication":
        return validate_http_authentication_arguments
    if capability_id == "browser.page":
        return validate_browser_page_arguments
    if capability_id == "http.raw_exchange":
        return validate_http_raw_exchange_arguments
    return None


def validate_http_transaction_arguments(
    action_id: str, arguments: Mapping[str, object]
) -> ArgumentValidationIssue | None:
    method = arguments.get("method")
    if not isinstance(method, str):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "method must be a string")
    if action_id == "read" and method not in ALLOWED_READ_METHODS:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "read action methods are GET, HEAD, OPTIONS")
    if action_id == "mutate" and method not in ALLOWED_MUTATE_METHODS:
        return ArgumentValidationIssue(
            "INVALID_ARGUMENT_TYPE", "mutate action methods are POST, PUT, PATCH, DELETE"
        )
    path = arguments.get("path")
    if not isinstance(path, str):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must be a string")
    path_issue = _path_issue(path)
    if path_issue is not None:
        return path_issue
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
    headers = arguments.get("headers") or {}
    if headers is None:
        headers = {}
    if not isinstance(headers, Mapping):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "headers must be an object")
    header_issue = _headers_issue(headers)
    if header_issue is not None:
        return header_issue
    query = arguments.get("query") or {}
    if query is None:
        query = {}
    if not isinstance(query, Mapping):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "query must be an object")
    for key, value in query.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "query keys and values must be strings")
        if any(marker in key or marker in value for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "query must not contain CRLF")
    body = arguments.get("body")
    if body is not None:
        if action_id == "read":
            return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "read action must not include a body")
        if not isinstance(body, str):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "body must be a string")
        if any(marker in body for marker in CRLF_MARKERS[:2]) and "\r\n\r\n" in body:
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "body must not smuggle headers")
        if body.strip().lower().startswith("file:"):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "body must not be a filesystem source")
    session_ref = arguments.get("session_context_reference")
    if session_ref is None:
        return None
    if not isinstance(session_ref, str) or not session_ref.strip():
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "session_context_reference must be a string")
    if any(marker in session_ref for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "session_context_reference must not contain CRLF")
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


def _headers_issue(headers: Mapping[str, object]) -> ArgumentValidationIssue | None:
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "header names and values must be strings")
        if any(marker in name or marker in value for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "headers must not contain CRLF")
        if len(name) > MAX_HEADER_NAME_LENGTH:
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "header name exceeds bound")
        lowered = name.lower()
        if lowered in FORBIDDEN_REQUEST_HEADERS:
            return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", f"header {name} is not allowed")
        if not all(ch.isalnum() or ch == "-" for ch in name):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "header name is invalid")
    return None
