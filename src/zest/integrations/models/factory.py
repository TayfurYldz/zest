"""Composition-root live adapter resolution.

Missing SDK, credential, or model id is not AVAILABLE. Discovery maps a present
SDK with a missing credential or model id to CONFIGURED_NOT_READY, and a missing
SDK to UNAVAILABLE.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from os import environ

from zest.research.model_port import ModelPort

from zest.integrations.models.availability import AdapterAvailability, UnavailableReason
from zest.integrations.models.common import JsonSchemaModelAdapter
from zest.integrations.models.secrets import (
    ANTHROPIC_API_KEY_ENV,
    ANTHROPIC_MODEL_ENV,
    GEMINI_MODEL_ENV,
    OPENAI_API_KEY_ENV,
    OPENAI_MODEL_ENV,
    SecretReference,
    gemini_key_reference,
)

LIVE_ADAPTER_IDS = ("openai", "anthropic", "gemini")

_SDK_MODULES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google.genai",
}


@dataclass(frozen=True)
class LiveAdapterHandle:
    availability: AdapterAvailability
    port: ModelPort | None
    adapter_identity: str | None
    provider_adapter_identity: str | None
    provider_model_id: str | None


def probe_live_adapter(
    adapter_id: str,
    *,
    model_id: str | None = None,
    env: dict[str, str] | None = None,
) -> AdapterAvailability:
    source = dict(environ if env is None else env)
    if adapter_id not in LIVE_ADAPTER_IDS:
        return AdapterAvailability(
            adapter_id=adapter_id,
            available=False,
            reason=UnavailableReason.UNKNOWN_ADAPTER,
            detail="adapter is not a live provider adapter",
        )
    module_name = _SDK_MODULES[adapter_id]
    if importlib.util.find_spec(module_name.split(".", 1)[0] if adapter_id != "gemini" else "google") is None:
        return AdapterAvailability(
            adapter_id=adapter_id,
            available=False,
            reason=UnavailableReason.MISSING_SDK,
            detail=f"{module_name} is not installed",
        )
    if adapter_id == "gemini":
        try:
            importlib.util.find_spec("google.genai")
        except (ModuleNotFoundError, ValueError):
            return AdapterAvailability(
                adapter_id=adapter_id,
                available=False,
                reason=UnavailableReason.MISSING_SDK,
                detail="google.genai is not installed",
            )
        if importlib.util.find_spec("google.genai") is None:
            return AdapterAvailability(
                adapter_id=adapter_id,
                available=False,
                reason=UnavailableReason.MISSING_SDK,
                detail="google.genai is not installed",
            )
    secret = _secret_for(adapter_id)
    if not secret.present(source):
        return AdapterAvailability(
            adapter_id=adapter_id,
            available=False,
            reason=UnavailableReason.MISSING_CREDENTIAL,
            detail=f"{secret.env_name} is not set",
        )
    resolved_model = model_id or source.get(_model_env(adapter_id))
    if not isinstance(resolved_model, str) or not resolved_model.strip():
        return AdapterAvailability(
            adapter_id=adapter_id,
            available=False,
            reason=UnavailableReason.MISSING_MODEL_ID,
            detail=f"model id required via --model or {_model_env(adapter_id)}",
        )
    return AdapterAvailability(
        adapter_id=adapter_id,
        available=True,
        reason=UnavailableReason.AVAILABLE,
        detail="sdk, credential reference, and model id are present",
    )


def resolve_live_adapter(
    adapter_id: str,
    *,
    model_id: str | None = None,
    env: dict[str, str] | None = None,
) -> LiveAdapterHandle:
    availability = probe_live_adapter(adapter_id, model_id=model_id, env=env)
    if not availability.available:
        return LiveAdapterHandle(
            availability=availability,
            port=None,
            adapter_identity=None,
            provider_adapter_identity=None,
            provider_model_id=None,
        )
    source = dict(environ if env is None else env)
    resolved_model = (model_id or source.get(_model_env(adapter_id)) or "").strip()
    secret = _secret_for(adapter_id)
    transport = _transport(adapter_id, model_id=resolved_model, secret=secret, env=source)
    port = JsonSchemaModelAdapter(transport, secret=secret.value(source))
    return LiveAdapterHandle(
        availability=availability,
        port=port,
        adapter_identity=transport.adapter_identity,
        provider_adapter_identity=transport.provider_adapter_identity,
        provider_model_id=resolved_model,
    )


def _secret_for(adapter_id: str) -> SecretReference:
    if adapter_id == "openai":
        return SecretReference(OPENAI_API_KEY_ENV)
    if adapter_id == "anthropic":
        return SecretReference(ANTHROPIC_API_KEY_ENV)
    return gemini_key_reference()


def _model_env(adapter_id: str) -> str:
    if adapter_id == "openai":
        return OPENAI_MODEL_ENV
    if adapter_id == "anthropic":
        return ANTHROPIC_MODEL_ENV
    return GEMINI_MODEL_ENV


def _transport(adapter_id: str, *, model_id: str, secret: SecretReference, env: dict[str, str]):
    if adapter_id == "openai":
        from zest.integrations.models.openai_adapter import OpenAIResponsesTransport

        return OpenAIResponsesTransport(model_id=model_id, secret=secret, env=env)
    if adapter_id == "anthropic":
        from zest.integrations.models.anthropic_adapter import AnthropicMessagesTransport

        return AnthropicMessagesTransport(model_id=model_id, secret=secret, env=env)
    from zest.integrations.models.gemini_adapter import GeminiGenerateContentTransport

    return GeminiGenerateContentTransport(model_id=model_id, secret=secret, env=env)
