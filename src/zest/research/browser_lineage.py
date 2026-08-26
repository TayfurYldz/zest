"""Deterministic G19 reproduction helper from sanitized browser NetworkEvents.

Browser capture is not executable authority. This never copies cookies.
"""

from __future__ import annotations

from typing import Any, Mapping

from zest.research.http_transaction import HttpRequestTemplate
from zest.research.types import ResearchInputError

REPRESENTABLE_METHODS = frozenset({"GET", "HEAD"})
FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "cookie",
        "set-cookie",
        "authorization",
        "proxy-authorization",
        "cookie2",
    }
)


def http_template_from_network_event(
    event: Mapping[str, Any],
    *,
    authorized_origin: str,
    session_context_reference: str | None = None,
) -> HttpRequestTemplate:
    """Build a G19 template from a representable sanitized NetworkEvent. Not a grant."""

    if event.get("representability") != "REPRESENTABLE":
        raise ResearchInputError("browser network event is not faithfully representable")
    method = event.get("method")
    if method not in REPRESENTABLE_METHODS:
        raise ResearchInputError("browser network event method is not representable")
    path = event.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ResearchInputError("browser network event path is not representable")
    if event.get("body_present") or event.get("body"):
        raise ResearchInputError("browser network event body is not representable")
    query = event.get("query") or {}
    if query is not None and not isinstance(query, Mapping):
        raise ResearchInputError("browser network event query is not representable")
    headers = event.get("headers") or {}
    if headers is not None and not isinstance(headers, Mapping):
        raise ResearchInputError("browser network event headers are not representable")
    safe_headers = {}
    if isinstance(headers, Mapping):
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ResearchInputError("browser network event headers are not representable")
            if name.lower() in FORBIDDEN_HEADER_NAMES:
                raise ResearchInputError("browser network event carries forbidden headers")
            safe_headers[name] = value
    return HttpRequestTemplate(
        authorized_origin=authorized_origin,
        method=str(method),
        path=path,
        query=dict(query) if isinstance(query, Mapping) and query else None,
        headers=safe_headers or None,
        session_context_reference=session_context_reference,
    )
