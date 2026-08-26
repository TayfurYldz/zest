"""EXTERNAL_AGENT runtime contract. Output remains UNTRUSTED. No Core authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.research.model_port import ModelCallRequest, ModelCallResult, ModelPortError, RuntimeUnavailableError
from zest.research.model_runtime import (
    AuthMode,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeKind,
    RuntimeOutcome,
    fingerprint_configuration,
)

UNRESTRICTED_MARKERS = frozenset({"*", "all", "unrestricted", "shell", "any"})


@dataclass(frozen=True)
class ExternalAgentAvailability:
    available: bool
    outcome: RuntimeOutcome
    detail: str

    def to_mapping(self) -> dict[str, str | bool]:
        return {
            "available": self.available,
            "outcome": self.outcome.value,
            "detail": self.detail,
            "untrusted": True,
        }


def probe_external_agent() -> ExternalAgentAvailability:
    return ExternalAgentAvailability(
        available=False,
        outcome=RuntimeOutcome.UNAVAILABLE,
        detail="external-agent/MCP runtime is a capability-controlled contract; host agent product is deferred",
    )


class ExternalAgentRuntimeAdapter:
    """AGENT_RUNTIME. Cannot alter scope, authorize, admit Evidence, or approve Findings."""

    def __init__(self, *, allowed_capabilities: tuple[str, ...]) -> None:
        if not allowed_capabilities:
            raise ModelPortError("external-agent runtime requires an explicit capability set")
        lowered = {item.lower() for item in allowed_capabilities}
        if lowered & UNRESTRICTED_MARKERS:
            raise ModelPortError("unrestricted tool capability is rejected")
        self._allowed = allowed_capabilities
        self._identity = ModelRuntimeIdentity(
            runtime_kind=RuntimeKind.EXTERNAL_AGENT,
            runtime_class=RuntimeClass.AGENT_RUNTIME,
            adapter_id="external.agent.contract",
            runtime_id="external-agent",
            auth_mode=AuthMode.EXTERNAL_RUNTIME_AUTH,
            configuration_fingerprint=fingerprint_configuration(
                {"runtime_kind": "EXTERNAL_AGENT", "allowed_capabilities": list(allowed_capabilities)}
            ),
        )

    @property
    def adapter_identity(self) -> str:
        return self._identity.adapter_id

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        del request
        raise RuntimeUnavailableError(
            "external-agent/MCP runtime is not a live host in GATE 10; result would remain untrusted"
        )

    def untrusted_result_envelope(self, payload: Mapping[str, object]) -> dict[str, object]:
        return {
            "untrusted": True,
            "instruction_authority": False,
            "not_authorization": True,
            "not_evidence": True,
            "not_finding": True,
            "payload": dict(payload),
            "runtime_identity": self._identity.to_mapping(),
        }
