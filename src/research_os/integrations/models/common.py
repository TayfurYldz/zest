"""Shared ModelPort adapter. Maps provider JSON to untrusted structured output.

Does not silently fill missing Research fields. Does not admit Evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Protocol

from research_os.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelCallTelemetry,
    StructuredOutputTransportError,
)
from research_os.research.model_runtime import ModelRuntimeIdentity, api_runtime_identity

from research_os.integrations.models.json_schemas import schema_for_request
from research_os.integrations.models.secrets import redact_secret


@dataclass(frozen=True)
class ProviderInvocation:
    text: str | None
    model_id: str | None = None
    model_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    retries: int | None = None
    provider_reported_cost: float | None = None
    provider_cost_provenance: str | None = None


class ProviderTransport(Protocol):
    adapter_identity: str
    provider_adapter_identity: str

    def invoke(
        self,
        request: ModelCallRequest,
        schema: Mapping[str, Any],
    ) -> ProviderInvocation: ...


def parse_structured_object(text: str | None, *, secret: str | None = None) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        raise StructuredOutputTransportError("provider returned empty structured output")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputTransportError(
            redact_secret("provider returned non-JSON structured output", secret)
        ) from exc
    if not isinstance(parsed, dict):
        raise StructuredOutputTransportError("provider JSON was not an object")
    return parsed


class JsonSchemaModelAdapter:
    def __init__(
        self,
        transport: ProviderTransport,
        *,
        secret: str | None = None,
        runtime_identity: ModelRuntimeIdentity | None = None,
    ) -> None:
        self._transport = transport
        self._secret = secret
        self._runtime_identity = runtime_identity or api_runtime_identity(
            adapter_id=transport.adapter_identity,
            runtime_id=transport.provider_adapter_identity,
        )

    @property
    def adapter_identity(self) -> str:
        return self._transport.adapter_identity

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        started = perf_counter()
        schema = schema_for_request(request)
        invocation = self._transport.invoke(request, schema)
        structured = parse_structured_object(invocation.text, secret=self._secret)
        latency_ms = int((perf_counter() - started) * 1000)
        telemetry = ModelCallTelemetry(
            latency_ms=latency_ms,
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            retries=invocation.retries,
            provider_reported_cost=invocation.provider_reported_cost,
            provider_cost_provenance=invocation.provider_cost_provenance,
        )
        return ModelCallResult(
            role=request.role,
            adapter_identity=self._transport.adapter_identity,
            provider_adapter_identity=self._transport.provider_adapter_identity,
            structured_output=structured,
            model_id=invocation.model_id,
            model_version=invocation.model_version,
            telemetry=telemetry,
            runtime_identity=self._runtime_identity,
        )
