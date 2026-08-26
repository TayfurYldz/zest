"""Worker-side network envelope enforcement. Not a ScopeRule compiler.

The Worker only tightens a Core-derived envelope. It does not authorize.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}
UNSUPPORTED_SCHEMES = frozenset(
    {"file", "data", "javascript", "blob", "ftp", "about", "ws", "wss"}
)
ALLOWED_SCHEMES = frozenset({"http", "https"})
LOOPBACK_HOST = "127.0.0.1"
UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"
OUTSIDE_ENVELOPE = "OUTSIDE_ENVELOPE"


@dataclass(frozen=True)
class BrowserNetworkEnvelope:
    """Dispatch-time bounds supplied by Core. Worker may only enforce more strictly."""

    normalized_scheme: str
    normalized_host: str
    normalized_port: int
    document_path: str
    origin_wide: bool
    allowed_path_prefixes: tuple[str, ...]
    denied_path_prefixes: tuple[str, ...]
    loopback_only: bool
    source_scope_rule_ids: tuple[str, ...]
    authorization_decision_reference: str | None = None


def parse_envelope(mapping: object) -> BrowserNetworkEnvelope | None:
    """Parse a WorkerRequest network_envelope mapping. None means fail closed."""

    if not isinstance(mapping, Mapping):
        return None
    try:
        scheme = str(mapping["normalized_scheme"]).strip().lower()
        host = str(mapping["normalized_host"]).strip().lower()
        port = int(mapping["normalized_port"])
        document_path = str(mapping["document_path"])
        origin_wide = bool(mapping.get("origin_wide"))
        allowed = mapping.get("allowed_path_prefixes") or ()
        denied = mapping.get("denied_path_prefixes") or ()
        loopback_only = bool(mapping.get("loopback_only"))
        rule_ids = mapping.get("source_scope_rule_ids") or ()
        auth_ref = mapping.get("authorization_decision_reference")
        if "*" in host or "*" in scheme:
            return None
        if not scheme or not host:
            return None
        if port < 1:
            return None
        if not document_path.startswith("/"):
            return None
        if not isinstance(allowed, (list, tuple)) or not isinstance(denied, (list, tuple)):
            return None
        if not isinstance(rule_ids, (list, tuple)):
            return None
        return BrowserNetworkEnvelope(
            normalized_scheme=scheme,
            normalized_host=host,
            normalized_port=port,
            document_path=document_path,
            origin_wide=origin_wide,
            allowed_path_prefixes=tuple(str(item) for item in allowed),
            denied_path_prefixes=tuple(str(item) for item in denied),
            loopback_only=loopback_only,
            source_scope_rule_ids=tuple(str(item) for item in rule_ids),
            authorization_decision_reference=None if auth_ref is None else str(auth_ref),
        )
    except (KeyError, TypeError, ValueError):
        return None


def envelope_allows(envelope: BrowserNetworkEnvelope, url: str) -> tuple[bool, str]:
    """Return (allowed, reason). reason is empty on allow."""

    parsed, reason = _split_url(url)
    if parsed is None:
        return False, reason
    scheme, host, port, path = parsed
    if envelope.loopback_only and host != LOOPBACK_HOST:
        return False, OUTSIDE_ENVELOPE
    if scheme != envelope.normalized_scheme:
        return False, OUTSIDE_ENVELOPE
    if host != envelope.normalized_host:
        return False, OUTSIDE_ENVELOPE
    if port != envelope.normalized_port:
        return False, OUTSIDE_ENVELOPE
    for prefix in envelope.denied_path_prefixes:
        if prefix and path.startswith(prefix):
            return False, OUTSIDE_ENVELOPE
    if envelope.origin_wide:
        return True, ""
    if path == envelope.document_path:
        return True, ""
    for prefix in envelope.allowed_path_prefixes:
        if prefix and path.startswith(prefix):
            return True, ""
    return False, OUTSIDE_ENVELOPE


def url_is_representable(url: str) -> bool:
    parsed, reason = _split_url(url)
    if parsed is None:
        return False
    return reason != UNSUPPORTED_SCHEME


def normalize_target(url: str) -> str | None:
    parsed, _reason = _split_url(url)
    if parsed is None:
        return None
    scheme, host, port, path = parsed
    default = DEFAULT_PORTS.get(scheme)
    if default is not None and port == default:
        return f"{scheme}://{host}{path}"
    return f"{scheme}://{host}:{port}{path}"


def _split_url(url: str) -> tuple[tuple[str, str, int, str] | None, str]:
    if not isinstance(url, str) or not url.strip():
        return None, OUTSIDE_ENVELOPE
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None, OUTSIDE_ENVELOPE
    if parts.username is not None or parts.password is not None:
        return None, OUTSIDE_ENVELOPE
    scheme = (parts.scheme or "").lower()
    if scheme in UNSUPPORTED_SCHEMES or scheme not in ALLOWED_SCHEMES:
        return None, UNSUPPORTED_SCHEME
    host = (parts.hostname or "").lower()
    if not host:
        return None, OUTSIDE_ENVELOPE
    if "*" in host:
        return None, OUTSIDE_ENVELOPE
    try:
        port = parts.port if parts.port is not None else DEFAULT_PORTS[scheme]
    except ValueError:
        return None, OUTSIDE_ENVELOPE
    path = parts.path if parts.path else "/"
    if _path_ambiguous(path):
        return None, OUTSIDE_ENVELOPE
    return (scheme, host, port, path), ""


def _path_ambiguous(path: str) -> bool:
    lowered = path.lower()
    if "\\" in path or "/../" in path or path.endswith("/..") or "/./" in path or path.endswith("/."):
        return True
    if "//" in path:
        return True
    if "%" in path and ("%2f" in lowered or "%2e" in lowered):
        return True
    return False
