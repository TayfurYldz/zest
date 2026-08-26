"""LOCAL_MODEL runtime contract. Product selection (Ollama/LM Studio/etc.) is deferred."""

from __future__ import annotations

from dataclasses import dataclass

from zest.research.model_port import ModelCallRequest, ModelCallResult, RuntimeUnavailableError
from zest.research.model_runtime import (
    AuthMode,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeKind,
    RuntimeOutcome,
    fingerprint_configuration,
)


@dataclass(frozen=True)
class LocalRuntimeAvailability:
    available: bool
    outcome: RuntimeOutcome
    endpoint_reference: str | None
    detail: str

    def to_mapping(self) -> dict[str, str | bool | None]:
        return {
            "available": self.available,
            "outcome": self.outcome.value,
            "endpoint_reference": self.endpoint_reference,
            "detail": self.detail,
            "product_not_architecture": True,
        }


def probe_local_model(*, endpoint_reference: str | None = None) -> LocalRuntimeAvailability:
    if not endpoint_reference:
        return LocalRuntimeAvailability(
            available=False,
            outcome=RuntimeOutcome.UNAVAILABLE,
            endpoint_reference=None,
            detail="local model endpoint is not configured; product selection is deferred",
        )
    return LocalRuntimeAvailability(
        available=False,
        outcome=RuntimeOutcome.UNAVAILABLE,
        endpoint_reference=endpoint_reference,
        detail="local model transport is a runtime kind, not a committed product",
    )


class LocalModelRuntimeAdapter:
    """INFERENCE_RUNTIME contract. Does not select Ollama/LM Studio as architecture."""

    def __init__(self, *, endpoint_reference: str | None = None) -> None:
        self._endpoint = endpoint_reference
        self._identity = ModelRuntimeIdentity(
            runtime_kind=RuntimeKind.LOCAL_MODEL,
            runtime_class=RuntimeClass.INFERENCE_RUNTIME,
            adapter_id="local.model.contract",
            runtime_id="local-model",
            auth_mode=AuthMode.LOCAL_NO_REMOTE_AUTH,
            configuration_fingerprint=fingerprint_configuration(
                {"runtime_kind": "LOCAL_MODEL", "endpoint_configured": bool(endpoint_reference)}
            ),
            session_reference=None,
        )

    @property
    def adapter_identity(self) -> str:
        return self._identity.adapter_id

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        del request
        raise RuntimeUnavailableError(
            "LOCAL_MODEL runtime is a contract only; product transport is deferred"
        )
