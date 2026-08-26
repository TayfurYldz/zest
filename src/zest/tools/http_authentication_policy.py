"""Fail-closed HTTP authentication argument policy. Not authorization."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from zest.tools.registry import ArgumentValidationIssue

CRLF_MARKERS = ("\r", "\n", "\x00")


def validate_http_authentication_arguments(
    action_id: str, arguments: Mapping[str, object]
) -> ArgumentValidationIssue | None:
    if action_id != "login":
        return ArgumentValidationIssue("UNKNOWN_ACTION", "http.authentication supports login only")
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
    for name in (
        "username",
        "username_field",
        "password_secret_name",
        "session_cookie_name",
        "session_context_id",
    ):
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", f"{name} must be a string")
        if any(marker in value for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", f"{name} must not contain CRLF")
    identity_id = arguments.get("identity_id")
    if identity_id is not None:
        if not isinstance(identity_id, str) or not identity_id.strip():
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "identity_id must be a string")
        if any(marker in identity_id for marker in CRLF_MARKERS):
            return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "identity_id must not contain CRLF")
    if "headers" in arguments:
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "login must not include caller headers")
    lowered_keys = {str(key).lower() for key in arguments}
    if "cookie" in lowered_keys:
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "raw cookie material is not allowed")
    if "authorization" in lowered_keys:
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", "raw authorization material is not allowed")
    return None
