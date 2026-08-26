"""Typed browser.page plans. Not authorization. Not a WorkerRequest."""

from __future__ import annotations

from dataclasses import dataclass

from zest.research.compiler import ExperimentIntent, compile_experiment_intent
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import (
    BROWSER_PAGE_CAPABILITY,
    BROWSER_PAGE_INTERACT_ACTION,
    BROWSER_PAGE_NAVIGATE_ACTION,
    BROWSER_PAGE_OBSERVE_ACTION,
)

BROWSER_PAGE_EVALUATION_STRATEGY = "browser.page.v1"
BROWSER_PAGE_EXPECTED_OBSERVATION = "bounded browser page facts were observed"
BROWSER_PAGE_DISCONFIRMING_OBSERVATION = "no browser page facts were observed"
INTERACT_KINDS = frozenset({"click", "fill", "select", "submit"})


@dataclass(frozen=True)
class BrowserPageIntent:
    """Typed browser action specification. authorized_origin is not a scope grant."""

    authorized_origin: str
    path: str
    session_context_reference: str | None = None
    identity_id: str | None = None
    browser_context_reference: str | None = None
    page_reference: str | None = None
    element_reference: str | None = None
    snapshot_fingerprint: str | None = None
    kind: str | None = None
    value: str | None = None
    timeout_ms: int | None = None

    def arguments(self, action: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "authorized_origin": self.authorized_origin,
            "path": self.path,
        }
        if self.session_context_reference is not None:
            payload["session_context_reference"] = self.session_context_reference
        if self.identity_id is not None:
            payload["identity_id"] = self.identity_id
        if self.browser_context_reference is not None:
            payload["browser_context_reference"] = self.browser_context_reference
        if action == BROWSER_PAGE_OBSERVE_ACTION and self.page_reference is not None:
            payload["page_reference"] = self.page_reference
        if action == BROWSER_PAGE_INTERACT_ACTION:
            if self.page_reference is not None:
                payload["page_reference"] = self.page_reference
            if self.element_reference is not None:
                payload["element_reference"] = self.element_reference
            if self.snapshot_fingerprint is not None:
                payload["snapshot_fingerprint"] = self.snapshot_fingerprint
            if self.kind is not None:
                payload["kind"] = self.kind
            if self.value is not None:
                payload["value"] = self.value
        if self.timeout_ms is not None:
            payload["timeout_ms"] = self.timeout_ms
        return payload


def plan_browser_page(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    action: str,
    intent: BrowserPageIntent,
    expected_observation: str = BROWSER_PAGE_EXPECTED_OBSERVATION,
    disconfirming_observation: str = BROWSER_PAGE_DISCONFIRMING_OBSERVATION,
    requested_side_effect: int | None = None,
) -> ExperimentPlan:
    """Compile a typed browser.page experiment. Does not authorize or dispatch."""

    if action not in {
        BROWSER_PAGE_OBSERVE_ACTION,
        BROWSER_PAGE_NAVIGATE_ACTION,
        BROWSER_PAGE_INTERACT_ACTION,
    }:
        raise ResearchInputError("unknown browser.page action")
    if action == BROWSER_PAGE_INTERACT_ACTION and intent.kind not in INTERACT_KINDS:
        raise ResearchInputError("interact kind is invalid")
    return compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id=hypothesis_id,
            capability_id=BROWSER_PAGE_CAPABILITY,
            action=action,
            target_reference=target_reference,
            arguments=intent.arguments(action),
            requested_budget_id=budget_id,
            expected_observation=expected_observation,
            disconfirming_observation=disconfirming_observation,
            evaluation_strategy=BROWSER_PAGE_EVALUATION_STRATEGY,
            requested_side_effect=requested_side_effect,
        )
    )


def plan_browser_observe(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    authorized_origin: str,
    path: str,
    session_context_reference: str | None = None,
    identity_id: str | None = None,
    browser_context_reference: str | None = None,
    page_reference: str | None = None,
) -> ExperimentPlan:
    return plan_browser_page(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        action=BROWSER_PAGE_OBSERVE_ACTION,
        intent=BrowserPageIntent(
            authorized_origin=authorized_origin,
            path=path,
            session_context_reference=session_context_reference,
            identity_id=identity_id,
            browser_context_reference=browser_context_reference,
            page_reference=page_reference,
        ),
    )


def plan_browser_navigate(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    authorized_origin: str,
    path: str,
    session_context_reference: str | None = None,
    identity_id: str | None = None,
    browser_context_reference: str | None = None,
) -> ExperimentPlan:
    return plan_browser_page(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        action=BROWSER_PAGE_NAVIGATE_ACTION,
        intent=BrowserPageIntent(
            authorized_origin=authorized_origin,
            path=path,
            session_context_reference=session_context_reference,
            identity_id=identity_id,
            browser_context_reference=browser_context_reference,
        ),
    )


def plan_browser_interact(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    authorized_origin: str,
    path: str,
    browser_context_reference: str,
    page_reference: str,
    element_reference: str,
    snapshot_fingerprint: str,
    kind: str,
    value: str | None = None,
    session_context_reference: str | None = None,
    identity_id: str | None = None,
) -> ExperimentPlan:
    return plan_browser_page(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        action=BROWSER_PAGE_INTERACT_ACTION,
        intent=BrowserPageIntent(
            authorized_origin=authorized_origin,
            path=path,
            session_context_reference=session_context_reference,
            identity_id=identity_id,
            browser_context_reference=browser_context_reference,
            page_reference=page_reference,
            element_reference=element_reference,
            snapshot_fingerprint=snapshot_fingerprint,
            kind=kind,
            value=value,
        ),
    )
