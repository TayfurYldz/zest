"""Classify envelope-denied browser requests.

Does not authorize. Does not grant egress. Distinguishes a blocked
network boundary from an authority transition that requires human
reauthorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .browser_envelope import UNSUPPORTED_SCHEME

ROUTE_BLOCK_UNSUPPORTED = "BLOCK_UNSUPPORTED"
ROUTE_BLOCK_PASSIVE_BOUNDARY = "BLOCK_PASSIVE_BOUNDARY"
ROUTE_AUTHORITY_TRANSITION = "AUTHORITY_TRANSITION"

PASSIVE_SUBRESOURCE_TYPES = frozenset(
    {
        "stylesheet",
        "image",
        "font",
        "media",
        "script",
        "manifest",
        "texttrack",
        "ping",
        "prefetch",
    }
)
DATAFLOW_SUBRESOURCE_TYPES = frozenset({"xhr", "fetch", "eventsource"})
DOCUMENT_RESOURCE_TYPES = frozenset({"document", "subframe"})
FRAME_MAIN = "main_frame"
FRAME_CHILD = "child_frame"
FRAME_UNKNOWN = "unknown"
ACTION_INTERACT = "interact"
REASON_PASSIVE_EXTERNAL_RESOURCE_BLOCKED = "PASSIVE_EXTERNAL_RESOURCE_BLOCKED"
REASON_EMBEDDED_SUBDOCUMENT_BLOCKED = "EMBEDDED_SUBDOCUMENT_BLOCKED"
REASON_ACTIVE_DATAFLOW_BLOCKED = "ACTIVE_DATAFLOW_BLOCKED"
REASON_AUTHORITY_TRANSITION_REQUIRED = "AUTHORITY_TRANSITION_REQUIRED"
REASON_UNSUPPORTED_SCHEME = "UNSUPPORTED_SCHEME"
BOUNDARY_PASSIVE_EXTERNAL_RESOURCE = "PASSIVE_EXTERNAL_RESOURCE"
BOUNDARY_EMBEDDED_SUBDOCUMENT = "EMBEDDED_SUBDOCUMENT"
BOUNDARY_ACTIVE_DATAFLOW = "ACTIVE_DATAFLOW"
BOUNDARY_AUTHORITY_TRANSITION = "AUTHORITY_TRANSITION"


@dataclass(frozen=True)
class DeniedBrowserRequest:
    url: str
    resource_type: str
    is_navigation_request: bool
    frame_kind: str
    browser_action: str
    deny_reason: str
    representable: bool
    source_page: str = ""


@dataclass(frozen=True)
class RouteClassification:
    decision: str
    channel: str | None
    boundary_kind: str
    reason: str
    reauth_required: bool
    main_observation_preserved: bool
    diagnostic_frame_kind: str


def resolve_callable_flag(value: Any) -> bool:
    """Read Playwright flags that are methods, not bool properties.

    `bool(request.is_navigation_request)` is True because the bound method
    is truthy. The navigation bit is the method's return value.
    """

    if callable(value):
        try:
            value = value()
        except TypeError:
            return False
    return bool(value)


def resolve_resource_type(value: Any) -> str:
    if callable(value):
        try:
            value = value()
        except TypeError:
            value = "other"
    text = str(value or "other").strip().lower()
    return text or "other"


def classify_denied_browser_request(request: DeniedBrowserRequest) -> RouteClassification:
    """Decide abort-only vs reauthorization. Never allows the denied URL."""

    resource = (request.resource_type or "other").strip().lower() or "other"
    frame = request.frame_kind if request.frame_kind in {FRAME_MAIN, FRAME_CHILD} else FRAME_UNKNOWN
    diagnostic_frame = _diagnostic_frame_kind(frame, request.is_navigation_request, resource)
    if request.deny_reason == UNSUPPORTED_SCHEME or not request.representable:
        return RouteClassification(
            decision=ROUTE_BLOCK_UNSUPPORTED,
            channel=None,
            boundary_kind=BOUNDARY_PASSIVE_EXTERNAL_RESOURCE,
            reason=REASON_UNSUPPORTED_SCHEME,
            reauth_required=False,
            main_observation_preserved=False,
            diagnostic_frame_kind=diagnostic_frame,
        )
    if _is_main_frame_navigation(request, resource, frame):
        return RouteClassification(
            decision=ROUTE_AUTHORITY_TRANSITION,
            channel="REDIRECT",
            boundary_kind=BOUNDARY_AUTHORITY_TRANSITION,
            reason=REASON_AUTHORITY_TRANSITION_REQUIRED,
            reauth_required=True,
            main_observation_preserved=False,
            diagnostic_frame_kind=FRAME_MAIN,
        )
    if _is_child_document_navigation(request, resource, frame):
        if request.browser_action == ACTION_INTERACT:
            return RouteClassification(
                decision=ROUTE_AUTHORITY_TRANSITION,
                channel="IFRAME",
                boundary_kind=BOUNDARY_AUTHORITY_TRANSITION,
                reason=REASON_AUTHORITY_TRANSITION_REQUIRED,
                reauth_required=True,
                main_observation_preserved=False,
                diagnostic_frame_kind=FRAME_CHILD,
            )
        return RouteClassification(
            decision=ROUTE_BLOCK_PASSIVE_BOUNDARY,
            channel=None,
            boundary_kind=BOUNDARY_EMBEDDED_SUBDOCUMENT,
            reason=REASON_EMBEDDED_SUBDOCUMENT_BLOCKED,
            reauth_required=False,
            main_observation_preserved=True,
            diagnostic_frame_kind=FRAME_CHILD,
        )
    if resource in PASSIVE_SUBRESOURCE_TYPES and not request.is_navigation_request:
        return RouteClassification(
            decision=ROUTE_BLOCK_PASSIVE_BOUNDARY,
            channel=None,
            boundary_kind=BOUNDARY_PASSIVE_EXTERNAL_RESOURCE,
            reason=REASON_PASSIVE_EXTERNAL_RESOURCE_BLOCKED,
            reauth_required=False,
            main_observation_preserved=True,
            diagnostic_frame_kind=diagnostic_frame,
        )
    if resource in DATAFLOW_SUBRESOURCE_TYPES and not request.is_navigation_request:
        return RouteClassification(
            decision=ROUTE_BLOCK_PASSIVE_BOUNDARY,
            channel=None,
            boundary_kind=BOUNDARY_ACTIVE_DATAFLOW,
            reason=REASON_ACTIVE_DATAFLOW_BLOCKED,
            reauth_required=False,
            main_observation_preserved=True,
            diagnostic_frame_kind=diagnostic_frame,
        )
    if request.is_navigation_request:
        channel = "IFRAME" if frame == FRAME_CHILD else "REDIRECT"
        return RouteClassification(
            decision=ROUTE_AUTHORITY_TRANSITION,
            channel=channel,
            boundary_kind=BOUNDARY_AUTHORITY_TRANSITION,
            reason=REASON_AUTHORITY_TRANSITION_REQUIRED,
            reauth_required=True,
            main_observation_preserved=False,
            diagnostic_frame_kind=diagnostic_frame,
        )
    return RouteClassification(
        decision=ROUTE_BLOCK_PASSIVE_BOUNDARY,
        channel=None,
        boundary_kind=BOUNDARY_PASSIVE_EXTERNAL_RESOURCE,
        reason=REASON_PASSIVE_EXTERNAL_RESOURCE_BLOCKED,
        reauth_required=False,
        main_observation_preserved=True,
        diagnostic_frame_kind=diagnostic_frame,
    )


def blocked_boundary_record(
    request: DeniedBrowserRequest,
    classification: RouteClassification,
) -> dict[str, object]:
    """Machine-readable blocked-boundary forensic. Not an authorization grant."""

    return {
        "requested_url": request.url,
        "resource_type": (request.resource_type or "other").strip().lower() or "other",
        "is_navigation_request": bool(request.is_navigation_request),
        "frame_kind": classification.diagnostic_frame_kind,
        "envelope_allowed": False,
        "route_decision": classification.decision,
        "egress_occurred": False,
        "reauth_required": classification.reauth_required,
        "main_observation_preserved": classification.main_observation_preserved,
        "blocked_before_egress": True,
        "followed": False,
        "self_authorized": False,
        "authority_granted": False,
        "reason": classification.reason,
        "source_page": request.source_page,
        "action": request.browser_action,
        "boundary_kind": classification.boundary_kind,
        "browser_action": request.browser_action,
    }


def succeeded_boundary_diagnostics(
    boundaries: tuple[dict[str, object], ...] | list[dict[str, object]],
) -> dict[str, object] | None:
    if not boundaries:
        return None
    return {
        "self_authorized": False,
        "followed": False,
        "authority_granted": False,
        "page_degraded_by_blocked_external_dependency": True,
        "reason": REASON_PASSIVE_EXTERNAL_RESOURCE_BLOCKED,
        "blocked_boundaries": list(boundaries),
    }


def _is_main_frame_navigation(request: DeniedBrowserRequest, resource: str, frame: str) -> bool:
    if frame != FRAME_MAIN:
        return False
    if request.is_navigation_request:
        return True
    return resource in DOCUMENT_RESOURCE_TYPES


def _is_child_document_navigation(request: DeniedBrowserRequest, resource: str, frame: str) -> bool:
    if frame != FRAME_CHILD:
        return False
    return request.is_navigation_request or resource in DOCUMENT_RESOURCE_TYPES


def _diagnostic_frame_kind(frame: str, is_navigation: bool, resource: str) -> str:
    if frame == FRAME_MAIN and not is_navigation and resource not in DOCUMENT_RESOURCE_TYPES:
        return "main_page_subresource"
    if frame == FRAME_CHILD:
        return FRAME_CHILD
    if frame == FRAME_MAIN:
        return FRAME_MAIN
    return FRAME_UNKNOWN
