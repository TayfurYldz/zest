"""Deterministic fake ModelPort. Not a provider. Not a real model."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from zest.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelPortError,
    ModelRole,
)
from zest.research.planning import DIAGNOSTIC_CLAIM, DIAGNOSTIC_DISCONFIRMING_OBSERVATION
from zest.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY

FAKE_ADAPTER_IDENTITY = "fake-test"

GeneratorScript = Callable[[ModelCallRequest], Mapping[str, Any]]
FalsifierScript = Callable[[ModelCallRequest], Mapping[str, Any]]


def _source_refs_from(request: ModelCallRequest) -> list[str]:
    context = request.payload.get("research_context")
    if not isinstance(context, Mapping):
        return ["proc:research-question"]
    refs: list[str] = []
    for bucket in ("procedural_context", "observations", "authoritative_facts"):
        items = context.get(bucket, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("item_id"), str):
                refs.append(item["item_id"])
    return refs or ["proc:research-question"]


def default_generator_output(request: ModelCallRequest) -> dict[str, Any]:
    return {
        "proposed_claim": DIAGNOSTIC_CLAIM,
        "rationale": "Diagnostic echo should round-trip the submitted value.",
        "source_references": _source_refs_from(request)[:2],
        "assumptions": ["the diagnostic capability is available", "side_effect_estimate:0"],
        "unresolved_questions": ["whether the runtime protocol matches"],
        "suggested_disconfirming_test": "submit a value and observe mismatch or missing echo",
        "suggested_capability": DIAGNOSTIC_ECHO_CAPABILITY,
        "expected_security_relevance": None,
        "novelty_basis": "UNCLASSIFIED",
    }


def default_falsifier_output(request: ModelCallRequest) -> dict[str, Any]:
    return {
        "alternative_explanations": [
            "Could fail due to runtime/protocol mismatch.",
        ],
        "missing_preconditions": [],
        "contradictory_source_references": [],
        "required_negative_controls": ["repeat echo with the same input"],
        "reasons_not_to_test": [],
        "proposed_disconfirming_observation": DIAGNOSTIC_DISCONFIRMING_OBSERVATION,
        "ambiguity": "echo success is not a security conclusion",
    }


class ScriptedModelPort:
    """Returns structured mappings. Never fabricates model version or cost."""

    def __init__(
        self,
        *,
        generator: GeneratorScript | Mapping[str, Any] | None = None,
        falsifier: FalsifierScript | Mapping[str, Any] | None = None,
        error: Exception | None = None,
        fail_role: ModelRole | None = None,
        model_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        self._generator = generator
        self._falsifier = falsifier
        self._error = error
        self._fail_role = fail_role
        self._model_id = model_id
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        self.calls.append(request)
        if self._error is not None and (
            self._fail_role is None or request.role is self._fail_role
        ):
            raise self._error
        if request.role is ModelRole.GENERATOR:
            output = self._resolve(self._generator, request, default_generator_output)
        elif request.role is ModelRole.FALSIFIER:
            output = self._resolve(self._falsifier, request, default_falsifier_output)
        else:
            raise ModelPortError("unsupported model role")
        return ModelCallResult(
            role=request.role,
            adapter_identity=FAKE_ADAPTER_IDENTITY,
            provider_adapter_identity=FAKE_ADAPTER_IDENTITY,
            structured_output=dict(output),
            model_id=self._model_id,
            model_version=None,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )

    def _resolve(
        self,
        script: GeneratorScript | FalsifierScript | Mapping[str, Any] | None,
        request: ModelCallRequest,
        default: Callable[[ModelCallRequest], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if script is None:
            return default(request)
        if isinstance(script, Mapping):
            return script
        return script(request)
