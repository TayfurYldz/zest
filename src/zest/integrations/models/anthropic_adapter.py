"""Anthropic Messages API transport. Not imported by Research."""

from __future__ import annotations

import json
from typing import Any, Mapping

from zest.research.model_port import ModelCallRequest, ProviderRuntimeError

from zest.integrations.models.common import ProviderInvocation
from zest.integrations.models.errors import classify_provider_exception
from zest.integrations.models.secrets import SecretReference, redact_secret


class AnthropicMessagesTransport:
    adapter_identity = "anthropic.messages"
    provider_adapter_identity = "anthropic"

    def __init__(self, *, model_id: str, secret: SecretReference, env: dict[str, str] | None = None) -> None:
        self._model_id = model_id
        self._secret = secret
        self._env = env

    def invoke(self, request: ModelCallRequest, schema: Mapping[str, Any]) -> ProviderInvocation:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ProviderRuntimeError("anthropic SDK is not installed") from exc
        api_key = self._secret.value(self._env)
        if api_key is None:
            raise ProviderRuntimeError("ANTHROPIC_API_KEY is not set")
        client = Anthropic(api_key=api_key)
        timeout_s = None if request.timeout_ms is None else request.timeout_ms / 1000
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": 4096,
            "system": request.instructions,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(request.payload, ensure_ascii=True),
                }
            ],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": dict(schema),
                }
            },
        }
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s
        try:
            response = client.messages.create(**kwargs)
        except TypeError:
            kwargs.pop("output_config", None)
            kwargs["extra_body"] = {
                "output_config": {
                    "format": {"type": "json_schema", "schema": dict(schema)}
                }
            }
            try:
                response = client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                mapped = classify_provider_exception(exc, secret=api_key)
                raise mapped from exc
        except Exception as exc:  # noqa: BLE001
            mapped = classify_provider_exception(exc, secret=api_key)
            raise mapped from exc
        text = _message_text(response)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        returned_model = getattr(response, "model", None)
        return ProviderInvocation(
            text=None if text is None else redact_secret(text, api_key),
            model_id=self._model_id,
            model_version=returned_model if isinstance(returned_model, str) and returned_model.strip() else None,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            retries=0,
        )


def _message_text(response: Any) -> str | None:
    content = getattr(response, "content", None)
    if not content:
        return None
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        return None
    return "".join(parts)
