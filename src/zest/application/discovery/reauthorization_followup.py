"""Compile fresh discovery work from a Core-allowed redirect reauthorization.

This module does not authorize or dispatch.  It only turns an already
Core-evaluated, same-origin, read-only redirect target into a fresh FrontierItem.
The follow-up must still pass the normal Core authorization path before Worker
execution.
"""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlsplit

from zest.application.identity import new_opaque_id
from zest.core.enums import ScopeClassification, ScopeDecision
from zest.core.scope import ScopeCheck
from zest.data.records import FrontierItemRecord
from zest.platform.url_normalize import normalize_url
from zest.research.discovery.canonical import canonical_key
from zest.research.discovery.frontier import FrontierItem
from zest.research.discovery.types import DiscoveryGoalKind
from zest.tools.capabilities import (
    HTTP_TRANSACTION_CAPABILITY,
    HTTP_TRANSACTION_READ_ACTION,
)

_SAFE_REDIRECT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def compile_allowed_same_origin_redirect_frontier(
    request: Mapping[str, object],
    check: ScopeCheck,
    *,
    record: FrontierItemRecord,
    normalized_origin: str,
) -> FrontierItem | None:
    """Return fresh read-only work only for an explicit Core ALLOW.

    Cross-origin redirects, ambiguous/denied scope, mutating methods, and
    targets carrying query/fragment state remain human-gated in this narrow
    P0 closure.  Returning a FrontierItem is not authorization.
    """

    if (
        check.decision is not ScopeDecision.ALLOW
        or check.classification is not ScopeClassification.IN_SCOPE
        or not check.matched_rule_ids
    ):
        return None

    target = request.get("proposed_target_reference")
    context = request.get("discovery_context")
    if not isinstance(target, str) or not target.strip() or not isinstance(context, Mapping):
        return None

    method = str(context.get("proposed_method") or "").upper()
    if method not in _SAFE_REDIRECT_METHODS:
        return None

    parsed = urlsplit(target)
    if parsed.query or parsed.fragment:
        return None

    candidate = normalize_url(target)
    if (
        candidate.normalization_error is not None
        or candidate.normalized_scheme is None
        or candidate.normalized_host is None
        or candidate.normalized_port is None
        or candidate.scope_match_path is None
    ):
        return None

    default_port = 80 if candidate.normalized_scheme == "http" else 443
    origin = f"{candidate.normalized_scheme}://{candidate.normalized_host}"
    if candidate.normalized_port != default_port:
        origin += f":{candidate.normalized_port}"

    if origin.rstrip("/") != normalized_origin.rstrip("/"):
        return None

    path = candidate.raw_path or "/"
    signature = canonical_key(
        "CHARACTERIZE_HTTP_OPERATION",
        origin,
        method,
        path,
    )

    return FrontierItem(
        frontier_id=new_opaque_id(),
        research_run_id=record.research_run_id,
        goal_kind=DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
        candidate_origin=origin,
        candidate_path=path,
        identity_id=record.identity_id,
        proposed_capability=HTTP_TRANSACTION_CAPABILITY,
        proposed_action=HTTP_TRANSACTION_READ_ACTION,
        expected_side_effect=0,
        budget_class=0,
        structural_signature=signature,
        dedupe_identity=signature,
        strategy_version=record.strategy_version,
        session_context_id=record.session_context_id,
        scope_hint="core_reauthorized_redirect",
        attributes={
            "method": method,
            "redirect_reauthorization": True,
            "auto_replay": False,
        },
    )
