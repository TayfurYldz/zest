"""Model runtime identity and outcome taxonomy. Not a vendor SDK and not process logic.

Research knows runtime classification. It does not spawn CLI processes, hold OAuth
tokens, or talk to local model transports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.types import ResearchInputError
from zest.safe_data import SecretMaterialError
from zest.safe_data import reject_secret_keys as reject_secret_structure

RUNTIME_SECRET_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "authorization",
        "session_token",
        "access_token",
        "refresh_token",
        "secret",
        "credential",
        "openai_api_key",
        "anthropic_api_key",
    }
)


class RuntimeKind(Enum):
    API = "API"
    SUBSCRIPTION_OAUTH = "SUBSCRIPTION_OAUTH"
    CLI_SESSION = "CLI_SESSION"
    LOCAL_MODEL = "LOCAL_MODEL"
    EXTERNAL_AGENT = "EXTERNAL_AGENT"


class RuntimeClass(Enum):
    """Inference is not an agent with tools. An authenticated CLI is not automatically inference-only."""

    INFERENCE_RUNTIME = "INFERENCE_RUNTIME"
    AGENT_RUNTIME = "AGENT_RUNTIME"


class ModelPriceClass(Enum):
    """Cost class used by the routing policy. Not a vendor tier."""

    CHEAP = "cheap"
    EXPENSIVE = "expensive"


class AuthMode(Enum):
    API_KEY = "API_KEY"
    SUBSCRIPTION_OAUTH = "SUBSCRIPTION_OAUTH"
    AUTHENTICATED_CLI_SESSION = "AUTHENTICATED_CLI_SESSION"
    LOCAL_NO_REMOTE_AUTH = "LOCAL_NO_REMOTE_AUTH"
    EXTERNAL_RUNTIME_AUTH = "EXTERNAL_RUNTIME_AUTH"


class RuntimeOutcome(Enum):
    """Operational runtime outcome. Not Hypothesis rejection, Evidence, or a research conclusion."""

    COMPLETED = "COMPLETED"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMED_OUT = "TIMED_OUT"
    PROCESS_FAILED = "PROCESS_FAILED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
    CONTENT_POLICY_BLOCKED = "CONTENT_POLICY_BLOCKED"
    ESCALATION_NEEDED = "ESCALATION_NEEDED"
    CANCELLED = "CANCELLED"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def reject_secret_keys(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    try:
        cleaned = reject_secret_structure(payload, field_name)
    except SecretMaterialError as exc:
        raise ResearchInputError(str(exc)) from exc
    if not isinstance(cleaned, dict):
        raise ResearchInputError(f"{field_name} must be a mapping")
    return cleaned


def fingerprint_configuration(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelRuntimeIdentity:
    """Replaceable runtime configuration identity. Same model via API vs CLI are different identities."""

    runtime_kind: RuntimeKind
    runtime_class: RuntimeClass
    adapter_id: str
    runtime_id: str
    auth_mode: AuthMode
    configuration_fingerprint: str
    runtime_version: str | None = None
    session_reference: str | None = None
    price_class: ModelPriceClass = ModelPriceClass.CHEAP

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_kind, RuntimeKind):
            raise ResearchInputError("runtime_kind must be a RuntimeKind")
        if not isinstance(self.runtime_class, RuntimeClass):
            raise ResearchInputError("runtime_class must be a RuntimeClass")
        if not isinstance(self.price_class, ModelPriceClass):
            raise ResearchInputError("price_class must be a ModelPriceClass")
        if not isinstance(self.auth_mode, AuthMode):
            raise ResearchInputError("auth_mode must be an AuthMode")
        object.__setattr__(self, "adapter_id", _require_text(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "runtime_id", _require_text(self.runtime_id, "runtime_id"))
        object.__setattr__(
            self,
            "configuration_fingerprint",
            _require_text(self.configuration_fingerprint, "configuration_fingerprint"),
        )
        if self.runtime_version is not None:
            object.__setattr__(
                self, "runtime_version", _require_text(self.runtime_version, "runtime_version")
            )
        if self.session_reference is not None:
            object.__setattr__(
                self,
                "session_reference",
                _require_text(self.session_reference, "session_reference"),
            )
            lowered = self.session_reference.lower()
            if any(marker in lowered for marker in ("sk-", "token=", "bearer ")):
                raise ResearchInputError("session_reference must not contain secret material")

    def to_mapping(self) -> dict[str, str | None]:
        return {
            "runtime_kind": self.runtime_kind.value,
            "runtime_class": self.runtime_class.value,
            "adapter_id": self.adapter_id,
            "runtime_id": self.runtime_id,
            "auth_mode": self.auth_mode.value,
            "runtime_version": self.runtime_version,
            "session_reference": self.session_reference,
            "configuration_fingerprint": self.configuration_fingerprint,
            "contains_secrets": False,
        }


def api_runtime_identity(*, adapter_id: str, runtime_id: str, runtime_version: str | None = None) -> ModelRuntimeIdentity:
    fingerprint = fingerprint_configuration(
        {
            "runtime_kind": RuntimeKind.API.value,
            "adapter_id": adapter_id,
            "runtime_id": runtime_id,
            "auth_mode": AuthMode.API_KEY.value,
        }
    )
    return ModelRuntimeIdentity(
        runtime_kind=RuntimeKind.API,
        runtime_class=RuntimeClass.INFERENCE_RUNTIME,
        adapter_id=adapter_id,
        runtime_id=runtime_id,
        auth_mode=AuthMode.API_KEY,
        configuration_fingerprint=fingerprint,
        runtime_version=runtime_version,
        session_reference=None,
    )


def cli_session_runtime_identity(
    *,
    adapter_id: str,
    runtime_id: str,
    runtime_version: str | None = None,
    session_reference: str | None = None,
    model_id: str | None = None,
    runtime_configuration: Mapping[str, Any] | None = None,
) -> ModelRuntimeIdentity:
    payload: dict[str, Any] = {
        "runtime_kind": RuntimeKind.CLI_SESSION.value,
        "adapter_id": adapter_id,
        "runtime_id": runtime_id,
        "auth_mode": AuthMode.AUTHENTICATED_CLI_SESSION.value,
        "runtime_version": runtime_version,
        "session_reference": session_reference,
    }
    if model_id is not None:
        payload["model_id"] = _require_text(model_id, "model_id")
    if runtime_configuration:
        payload["runtime_configuration"] = reject_secret_keys(
            runtime_configuration, "runtime_configuration"
        )
    fingerprint = fingerprint_configuration(payload)
    return ModelRuntimeIdentity(
        runtime_kind=RuntimeKind.CLI_SESSION,
        runtime_class=RuntimeClass.AGENT_RUNTIME,
        adapter_id=adapter_id,
        runtime_id=runtime_id,
        auth_mode=AuthMode.AUTHENTICATED_CLI_SESSION,
        configuration_fingerprint=fingerprint,
        runtime_version=runtime_version,
        session_reference=session_reference,
    )
