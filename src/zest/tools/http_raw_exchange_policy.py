"""Fail-closed http.raw_exchange argument policy. Not authorization. Not execution."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from zest.tools.registry import ArgumentValidationIssue

CRLF_MARKERS = ("\r", "\n", "\x00")
ALLOWED_PROFILES = frozenset(
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
ALLOWED_LANES = frozenset(
    {"http_request_smuggling_desync", "http_cache_poisoning_deception"}
)


def validate_http_raw_exchange_arguments(
    action_id: str, arguments: Mapping[str, object]
) -> ArgumentValidationIssue | None:
    if action_id != "probe":
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "http.raw_exchange action must be probe")
    origin = arguments.get("authorized_origin")
    if not isinstance(origin, str) or not origin.strip():
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin is required")
    if any(marker in origin for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin must not contain CRLF")
    parsed = urlsplit(origin.strip())
    if parsed.path not in {"", "/"}:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "authorized_origin must not include a path")
    if parsed.query or parsed.fragment:
        return ArgumentValidationIssue(
            "INVALID_ARGUMENT_TYPE", "authorized_origin must not include query or fragment"
        )
    path = arguments.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must be an absolute path")
    if any(marker in path for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must not contain CRLF")
    if path.startswith("//") or "://" in path:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "path must not be an absolute URL")
    profile = arguments.get("framing_profile")
    if not isinstance(profile, str) or profile not in ALLOWED_PROFILES:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "framing_profile is not in the closed catalog")
    lane = arguments.get("lane")
    if not isinstance(lane, str) or lane not in ALLOWED_LANES:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "lane is not in the closed catalog")
    control = arguments.get("control")
    if not isinstance(control, str) or not control.strip():
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "control is required")
    if any(marker in control for marker in CRLF_MARKERS):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "control must not contain CRLF")
    return None
