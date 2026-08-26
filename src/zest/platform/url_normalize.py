"""Platform URL normalization for scope candidates. Core does not parse URLs."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urljoin, urlsplit

from zest.core.scope_compiler import (
    NORMALIZATION_PATH_AMBIGUOUS,
    NORMALIZATION_USERINFO,
    NORMALIZATION_WILDCARD,
    ScopeCandidate,
)

DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}
USERINFO_ERROR = NORMALIZATION_USERINFO
WILDCARD_ERROR = NORMALIZATION_WILDCARD
IDNA_ERROR = "IDNA"
IPV6_ERROR = "IPV6"
IPV4_ERROR = "IPV4"
PARSE_ERROR = "PARSE"
PATH_AMBIGUOUS = NORMALIZATION_PATH_AMBIGUOUS
EMPTY_HOST = "EMPTY_HOST"
UNSUPPORTED_REDIRECT_SCHEMES = frozenset({"javascript", "data", "file", "blob", "about"})


def resolve_redirect_location(response_url: str, location: str) -> ScopeCandidate:
    """RFC 3986 relative resolution against the actual response URL, then normalize.

    Does not follow the redirect. Unsupported schemes fail closed.
    """

    if not isinstance(response_url, str) or not response_url.strip():
        return _error(response_url if isinstance(response_url, str) else "", PARSE_ERROR)
    raw_location = location if isinstance(location, str) else ""
    try:
        resolved = urljoin(response_url.strip(), raw_location.strip())
    except ValueError:
        return _error(raw_location, PARSE_ERROR)
    try:
        parts = urlsplit(resolved)
    except ValueError:
        return _error(resolved, PARSE_ERROR)
    scheme = (parts.scheme or "").lower()
    if scheme in UNSUPPORTED_REDIRECT_SCHEMES or not scheme:
        return _error(resolved, PARSE_ERROR)
    return normalize_url(resolved)


def normalize_url(raw_target: str) -> ScopeCandidate:
    """Fail-closed exact-host normalization. Does not resolve DNS or follow redirects."""

    raw = raw_target if isinstance(raw_target, str) else ""
    if not raw.strip():
        return _error(raw, PARSE_ERROR)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return _error(raw, PARSE_ERROR)
    if parts.username is not None or parts.password is not None:
        return _error(raw, USERINFO_ERROR)
    scheme = (parts.scheme or "").lower()
    if scheme not in DEFAULT_PORTS:
        return _error(raw, PARSE_ERROR)
    host = parts.hostname
    if not host:
        return _error(raw, EMPTY_HOST)
    if "*" in host:
        return _error(raw, WILDCARD_ERROR)
    canonical_host, host_error = _canonical_host(host)
    if canonical_host is None:
        return _error(raw, host_error or PARSE_ERROR)
    try:
        port = parts.port if parts.port is not None else DEFAULT_PORTS[scheme]
    except ValueError:
        return _error(raw, PARSE_ERROR)
    raw_path = parts.path if parts.path else "/"
    match_path, path_error = _scope_match_path(raw_path)
    if path_error is not None:
        return ScopeCandidate(
            raw_target=raw,
            normalized_scheme=scheme,
            normalized_host=canonical_host,
            normalized_port=port,
            raw_path=raw_path,
            scope_match_path=None,
            normalization_error=path_error,
        )
    return ScopeCandidate(
        raw_target=raw,
        normalized_scheme=scheme,
        normalized_host=canonical_host,
        normalized_port=port,
        raw_path=raw_path,
        scope_match_path=match_path,
        normalization_error=None,
    )


def _error(raw: str, code: str) -> ScopeCandidate:
    return ScopeCandidate(
        raw_target=raw,
        normalized_scheme=None,
        normalized_host=None,
        normalized_port=None,
        raw_path="",
        scope_match_path=None,
        normalization_error=code,
    )


def _canonical_host(host: str) -> tuple[str | None, str | None]:
    stripped = host.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        stripped = stripped[1:-1]
    if ":" in stripped:
        try:
            return str(ipaddress.IPv6Address(stripped)), None
        except ValueError:
            return None, IPV6_ERROR
    try:
        return str(ipaddress.IPv4Address(stripped)), None
    except ValueError:
        pass
    if any(label.lower() == "xn--" for label in stripped.split(".") if label):
        return None, IDNA_ERROR
    try:
        return stripped.encode("idna").decode("ascii").lower(), None
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
        return None, IDNA_ERROR


_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SLASH = re.compile(r"%2[fF]")
_ENCODED_DOT = re.compile(r"%2[eE]")


def _scope_match_path(raw_path: str) -> tuple[str | None, str | None]:
    if "\\" in raw_path:
        return None, PATH_AMBIGUOUS
    if _BAD_PERCENT.search(raw_path):
        return None, PATH_AMBIGUOUS
    if _ENCODED_SLASH.search(raw_path) or _ENCODED_DOT.search(raw_path):
        return None, PATH_AMBIGUOUS
    if not raw_path.startswith("/"):
        return None, PATH_AMBIGUOUS
    if "//" in raw_path:
        return None, PATH_AMBIGUOUS
    try:
        decoded_segments = []
        for segment in raw_path.split("/")[1:]:
            if segment in {".", ".."}:
                return None, PATH_AMBIGUOUS
            decoded_segments.append(segment.encode("ascii").decode("utf-8"))
    except UnicodeError:
        return None, PATH_AMBIGUOUS
    if any(not _is_unambiguous_segment(item) for item in decoded_segments if item):
        return None, PATH_AMBIGUOUS
    return raw_path, None


def _is_unambiguous_segment(segment: str) -> bool:
    if "%" not in segment:
        return True
    if _BAD_PERCENT.search(segment):
        return False
    try:
        bytes.fromhex("".join(_PERCENT.findall(segment)).replace("%", ""))
    except ValueError:
        return False
    return True
