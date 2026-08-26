"""Compile a claimed FrontierItem into an ExperimentPlan. Not Core ALLOW."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.data.records import FrontierItemRecord
from zest.research.browser_page import (
    BrowserPageIntent,
    plan_browser_observe,
    plan_browser_page,
)
from zest.research.discovery.control_resolve import (
    DurableControlSignature,
    LiveControlView,
    resolve_control_ref,
)
from zest.research.discovery.types import ANONYMOUS_IDENTITY_ID, DiscoveryGoalKind
from zest.research.http_transaction import HttpRequestTemplate, plan_http_transaction
from zest.research.types import ExperimentPlan
from zest.tools.capabilities import BROWSER_PAGE_INTERACT_ACTION


class ReobserveRequired(ApplicationError):
    """Stale or ambiguous control mapping. Do not click."""


@dataclass(frozen=True)
class LivePageSnapshot:
    """In-memory page lease mapping. Never persist el-N."""

    snapshot_fingerprint: str
    browser_context_reference: str
    page_reference: str
    controls: tuple[LiveControlView, ...]


def compile_frontier_plan(
    item: FrontierItemRecord,
    *,
    hypothesis_id: str,
    budget_id: str,
    target_reference: str,
    live_page: LivePageSnapshot | None = None,
    process_generation_changed: bool = False,
) -> ExperimentPlan:
    goal = DiscoveryGoalKind(item.goal_kind)
    identity_id = None if item.identity_id == ANONYMOUS_IDENTITY_ID else item.identity_id
    if goal in {
        DiscoveryGoalKind.INSPECT_PATH,
        DiscoveryGoalKind.INSPECT_SPA_PATH,
        DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY,
        DiscoveryGoalKind.RESOLVE_TRANSITION_RESULT,
        DiscoveryGoalKind.RESOLVE_OBJECT_TYPE,
    }:
        return plan_browser_observe(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=target_reference,
            authorized_origin=item.candidate_origin,
            path=item.candidate_path,
            session_context_reference=item.session_context_id,
            identity_id=identity_id,
        )
    if goal is DiscoveryGoalKind.INSPECT_CONTROL:
        attrs = dict(item.attributes or {})
        signature = DurableControlSignature(
            origin=item.candidate_origin,
            path=item.candidate_path,
            tag=str(attrs.get("tag") or ""),
            name=str(attrs.get("name") or ""),
            role=str(attrs.get("role") or ""),
            input_type=str(attrs.get("input_type") or ""),
        )
        live_controls = live_page.controls if live_page is not None else ()
        fingerprint = live_page.snapshot_fingerprint if live_page is not None else None
        resolved = resolve_control_ref(
            signature,
            live_controls,
            current_page_fingerprint=fingerprint,
            expected_page_fingerprint=fingerprint,
            lease_present=live_page is not None,
            process_generation_changed=process_generation_changed,
        )
        if live_page is None or not resolved.may_interact:
            raise ReobserveRequired(
                resolved.outcome.value if live_page is not None else "PAGE_CONTEXT_ABSENT"
            )
        return plan_browser_page(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=target_reference,
            action=BROWSER_PAGE_INTERACT_ACTION,
            intent=BrowserPageIntent(
                authorized_origin=item.candidate_origin,
                path=item.candidate_path,
                session_context_reference=item.session_context_id,
                identity_id=identity_id,
                browser_context_reference=live_page.browser_context_reference,
                page_reference=live_page.page_reference,
                element_reference=resolved.live_element_reference,
                snapshot_fingerprint=live_page.snapshot_fingerprint,
                kind="click",
            ),
        )
    if goal is DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION:
        method = str((item.attributes or {}).get("method") or "GET")
        return plan_http_transaction(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=target_reference,
            template=HttpRequestTemplate(
                authorized_origin=item.candidate_origin,
                method=method,
                path=item.candidate_path,
                session_context_reference=item.session_context_id,
            ),
        )
    return plan_browser_observe(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        authorized_origin=item.candidate_origin,
        path=item.candidate_path,
        identity_id=identity_id,
    )
