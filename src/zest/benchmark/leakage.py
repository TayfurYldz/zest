"""Prove hidden evaluator material never reaches model-visible structures."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from zest.benchmark.scenarios import HIDDEN_KEY_SENTINELS, BenchmarkScenario
from zest.research.context import ResearchContext
from zest.research.cycle import context_model_payload
from zest.research.model_port import ModelCallRequest


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)


def _walk_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                found.add(key)
            found.update(_walk_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_walk_keys(item))
    return found


def request_context_payloads(requests: Iterable[ModelCallRequest]) -> list[object]:
    payloads: list[object] = []
    for request in requests:
        payloads.append({"instructions": request.instructions})
        payloads.append(request.payload.get("research_context"))
        payloads.append(request.payload.get("instructions_channel"))
    return payloads


def model_visible_blob(
    context: ResearchContext, requests: Iterable[ModelCallRequest] = ()
) -> str:
    payload = context_model_payload(context)
    parts = [
        context.research_question,
        context.fingerprint,
        _dump(payload),
    ]
    for item in context.all_items():
        parts.append(item.statement)
        if item.payload is not None:
            parts.append(_dump(item.payload))
    for request in requests:
        parts.append(request.instructions)
        parts.append(_dump(request.payload.get("research_context")))
        parts.append(_dump(request.payload.get("instructions_channel")))
    return "\n".join(parts)


def leakage_hits(
    scenario: BenchmarkScenario,
    context: ResearchContext,
    requests: Iterable[ModelCallRequest] = (),
) -> tuple[str, ...]:
    """Return leakage reasons. Empty means no hidden evaluator data was visible."""
    payload = context_model_payload(context)
    visible_structures: list[object] = [payload, *request_context_payloads(requests)]
    keys: set[str] = set()
    for structure in visible_structures:
        keys.update(_walk_keys(structure))
    hits: list[str] = []
    leaked_keys = sorted(keys.intersection(HIDDEN_KEY_SENTINELS))
    if leaked_keys:
        hits.append(f"hidden_keys:{','.join(leaked_keys)}")
    blob = model_visible_blob(context, requests)
    canary = scenario.hidden_evaluation.leakage_canary
    if canary and canary in blob:
        hits.append("leakage_canary")
    dumped = _dump(visible_structures)
    for token in scenario.hidden_evaluation.forbidden_fabricated_source_ids:
        if token in dumped:
            hits.append(f"forbidden_id_in_visible_context:{token}")
    return tuple(hits)
