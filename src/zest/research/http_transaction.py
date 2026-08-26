"""Typed HTTP transaction plans. Not authorization. Not a WorkerRequest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.research.compiler import ExperimentIntent, compile_experiment_intent
from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.capabilities import (
    HTTP_TRANSACTION_CAPABILITY,
    HTTP_TRANSACTION_MUTATE_ACTION,
    HTTP_TRANSACTION_READ_ACTION,
)

HTTP_TRANSACTION_EVALUATION_STRATEGY = "http.transaction.v1"
HTTP_TRANSACTION_EXPECTED_OBSERVATION = "authorized HTTP response facts were observed"
HTTP_TRANSACTION_DISCONFIRMING_OBSERVATION = "no HTTP response facts were observed"
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MUTATE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class HttpRequestTemplate:
    """Typed request specification. authorized_origin is not a scope grant."""

    authorized_origin: str
    method: str
    path: str
    query: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    body: str | None = None
    content_type: str | None = None
    session_context_reference: str | None = None
    max_response_bytes: int | None = None
    timeout_ms: int | None = None

    def arguments(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "authorized_origin": self.authorized_origin,
            "method": self.method,
            "path": self.path,
        }
        if self.query:
            payload["query"] = dict(self.query)
        if self.headers:
            payload["headers"] = dict(self.headers)
        if self.body is not None:
            payload["body"] = self.body
        if self.content_type is not None:
            payload["content_type"] = self.content_type
        if self.session_context_reference is not None:
            payload["session_context_reference"] = self.session_context_reference
        if self.max_response_bytes is not None:
            payload["max_response_bytes"] = self.max_response_bytes
        if self.timeout_ms is not None:
            payload["timeout_ms"] = self.timeout_ms
        return payload


def plan_http_transaction(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    template: HttpRequestTemplate,
    action: str | None = None,
    expected_observation: str = HTTP_TRANSACTION_EXPECTED_OBSERVATION,
    disconfirming_observation: str = HTTP_TRANSACTION_DISCONFIRMING_OBSERVATION,
) -> ExperimentPlan:
    """Compile a typed authorized HTTP experiment. Does not authorize or dispatch."""

    resolved_action = action or _action_for_method(template.method)
    return compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id=hypothesis_id,
            capability_id=HTTP_TRANSACTION_CAPABILITY,
            action=resolved_action,
            target_reference=target_reference,
            arguments=template.arguments(),
            requested_budget_id=budget_id,
            expected_observation=expected_observation,
            disconfirming_observation=disconfirming_observation,
            evaluation_strategy=HTTP_TRANSACTION_EVALUATION_STRATEGY,
        )
    )


def plan_http_transaction_read(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    authorized_origin: str,
    path: str,
    method: str = "GET",
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    max_response_bytes: int | None = None,
    timeout_ms: int | None = None,
) -> ExperimentPlan:
    return plan_http_transaction(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        template=HttpRequestTemplate(
            authorized_origin=authorized_origin,
            method=method,
            path=path,
            query=query,
            headers=headers,
            max_response_bytes=max_response_bytes,
            timeout_ms=timeout_ms,
        ),
        action=HTTP_TRANSACTION_READ_ACTION,
    )


def plan_http_transaction_mutate(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    authorized_origin: str,
    path: str,
    method: str,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: str | None = None,
    content_type: str | None = None,
    max_response_bytes: int | None = None,
    timeout_ms: int | None = None,
) -> ExperimentPlan:
    return plan_http_transaction(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        template=HttpRequestTemplate(
            authorized_origin=authorized_origin,
            method=method,
            path=path,
            query=query,
            headers=headers,
            body=body,
            content_type=content_type,
            max_response_bytes=max_response_bytes,
            timeout_ms=timeout_ms,
        ),
        action=HTTP_TRANSACTION_MUTATE_ACTION,
    )


def baseline_http_transaction(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    template: HttpRequestTemplate,
) -> ExperimentPlan:
    return plan_http_transaction(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        template=template,
        expected_observation="baseline authorized HTTP response facts were observed",
    )


def control_http_transaction(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    template: HttpRequestTemplate,
) -> ExperimentPlan:
    return plan_http_transaction(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        template=template,
        expected_observation="control authorized HTTP response facts were observed",
    )


def variant_http_transaction(
    hypothesis_id: str,
    *,
    budget_id: str,
    target_reference: str,
    template: HttpRequestTemplate,
) -> ExperimentPlan:
    return plan_http_transaction(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        template=template,
        expected_observation="variant authorized HTTP response facts were observed",
    )


def replay_http_transaction_plan(plan: ExperimentPlan) -> ExperimentPlan:
    """Recompile a persisted HTTP request spec. Fingerprint drift fails closed."""

    if plan.required_capability != HTTP_TRANSACTION_CAPABILITY:
        raise ResearchInputError("replay requires http.transaction")
    if not plan.capability_version or not plan.capability_definition_fingerprint:
        raise ResearchInputError("replay requires durable capability bindings")
    replayed = compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id=plan.hypothesis_id,
            capability_id=plan.required_capability,
            action=plan.action,
            target_reference=plan.target_reference,
            arguments=dict(plan.arguments),
            requested_budget_id=plan.requested_budget_id,
            expected_observation=plan.expected_observation,
            disconfirming_observation=plan.disconfirming_observation,
            evaluation_strategy=plan.evaluation_strategy,
            requested_side_effect=plan.side_effect_level,
        )
    )
    if replayed.capability_definition_fingerprint != plan.capability_definition_fingerprint:
        raise ResearchInputError("capability fingerprint drift blocks replay")
    if replayed.capability_version != plan.capability_version:
        raise ResearchInputError("capability version drift blocks replay")
    if replayed.target_reference != plan.target_reference:
        raise ResearchInputError("replay must preserve target binding")
    if replayed.action != plan.action or replayed.arguments != plan.arguments:
        raise ResearchInputError("replay must preserve request semantics")
    return replayed


def _action_for_method(method: str) -> str:
    if method in READ_METHODS:
        return HTTP_TRANSACTION_READ_ACTION
    if method in MUTATE_METHODS:
        return HTTP_TRANSACTION_MUTATE_ACTION
    raise ResearchInputError("HTTP method is not in the http.transaction allowlist")
