"""Application helper: redirect Location becomes a fresh scope candidate. Not a grant."""

from __future__ import annotations

from typing import Any, Mapping

from zest.core.scope import ScopeCheck
from zest.core.scope_compiler import CompiledScope, evaluate_scope_candidate
from zest.platform.url_normalize import resolve_redirect_location

WORKER_CONTRACT_VERSION = "v1"
REDIRECT_PRESERVE_STATUSES = frozenset({307, 308})
REDIRECT_GET_STATUSES = frozenset({301, 302, 303})


def scope_candidate_from_redirect_location(
    location: str, *, response_url: str | None = None
):
    """Resolve Location against the actual response URL when provided, then normalize."""

    if response_url:
        return resolve_redirect_location(response_url, location)
    return resolve_redirect_location(location, location)


def reevaluate_redirect_location(
    location: str,
    compiled: CompiledScope,
    *,
    response_url: str | None = None,
) -> ScopeCheck:
    """Worker does not follow redirects. Core re-evaluates the resolved Location."""

    candidate = scope_candidate_from_redirect_location(location, response_url=response_url)
    return evaluate_scope_candidate(candidate, compiled)


def proposed_redirect_method(original_method: str, status: int) -> str:
    """Factual next-request method proposal. Worker does not follow the redirect."""

    method = original_method.upper()
    if status in REDIRECT_PRESERVE_STATUSES:
        return method
    if status in REDIRECT_GET_STATUSES:
        if method == "HEAD" and status != 303:
            return "HEAD"
        return "GET"
    return method


def reauthorization_request_from_worker_result(
    worker_request: Mapping[str, Any],
    worker_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Typed ReauthorizationRequest. Not authorization. Not a new WorkerRequest."""

    arguments = worker_request.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    diagnostics = worker_result.get("diagnostics")
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    raw_result = worker_result.get("raw_result")
    if not isinstance(raw_result, Mapping):
        raw_result = {}
    origin = str(arguments.get("authorized_origin") or "").rstrip("/")
    path = str(arguments.get("path") or "/")
    response_url = str(diagnostics.get("response_url") or f"{origin}{path}")
    raw_location = str(diagnostics.get("raw_location") or diagnostics.get("location") or "")
    candidate = resolve_redirect_location(response_url, raw_location)
    proposed = candidate.raw_target if candidate.normalization_error is None else raw_location
    original_method = str(raw_result.get("method") or arguments.get("method") or "GET")
    status = raw_result.get("status")
    redirect_status = status if isinstance(status, int) else None
    correlation = worker_result.get("correlation")
    if not isinstance(correlation, Mapping):
        correlation = worker_request.get("correlation")
    return {
        "contract_version": WORKER_CONTRACT_VERSION,
        "correlation": dict(correlation) if isinstance(correlation, Mapping) else {},
        "worker_id": str(worker_result.get("worker_id") or worker_request.get("worker_id") or ""),
        "current_target_reference": response_url,
        "proposed_target_reference": proposed,
        "reason": "redirect",
        "discovery_context": {
            "redirect_status": redirect_status,
            "raw_location": raw_location,
            "resolved_location": proposed,
            "response_url": response_url,
            "normalization_error": candidate.normalization_error,
            "original_method": original_method,
            "proposed_method": None
            if redirect_status is None
            else proposed_redirect_method(original_method, redirect_status),
            "proposed_body_preserved": redirect_status in REDIRECT_PRESERVE_STATUSES
            if redirect_status is not None
            else False,
            "followed": False,
            "self_authorized": False,
        },
    }


def evaluate_reauthorization_request(
    request: Mapping[str, Any],
    compiled: CompiledScope,
) -> ScopeCheck:
    """Evaluate the proposed target. Does not dispatch a WorkerRequest."""

    response_url = str(request.get("current_target_reference") or "")
    location = str(request.get("proposed_target_reference") or "")
    context = request.get("discovery_context")
    raw_location = location
    if isinstance(context, Mapping) and context.get("raw_location"):
        raw_location = str(context["raw_location"])
        response_url = str(context.get("response_url") or response_url)
    return reevaluate_redirect_location(raw_location, compiled, response_url=response_url)
