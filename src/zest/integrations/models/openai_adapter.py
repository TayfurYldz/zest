"""OpenAI Responses API transport. Not imported by Research."""

from __future__ import annotations

import json
from typing import Any, Mapping

from zest.research.model_port import ModelCallRequest, ProviderRuntimeError

from zest.integrations.models.common import ProviderInvocation
from zest.integrations.models.errors import classify_provider_exception
from zest.integrations.models.secrets import SecretReference, redact_secret


class OpenAIResponsesTransport:
    adapter_identity = "openai.responses"
    provider_adapter_identity = "openai"

    def __init__(self, *, model_id: str, secret: SecretReference, env: dict[str, str] | None = None) -> None:
        self._model_id = model_id
        self._secret = secret
        self._env = env

    def invoke(self, request: ModelCallRequest, schema: Mapping[str, Any]) -> ProviderInvocation:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderRuntimeError("openai SDK is not installed") from exc
        api_key = self._secret.value(self._env)
        if api_key is None:
            raise ProviderRuntimeError("OPENAI_API_KEY is not set")
        client = OpenAI(api_key=api_key)
        timeout_s = None if request.timeout_ms is None else request.timeout_ms / 1000
        try:
            response = client.responses.create(
                model=self._model_id,
                input=[
                    {"role": "system", "content": request.instructions},
                    {"role": "user", "content": json.dumps(request.payload, ensure_ascii=True)},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": f"research_{request.role.value.lower()}",
                        "strict": True,
                        "schema": dict(schema),
                    }
                },
                timeout=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - map any SDK/network failure
            mapped = classify_provider_exception(exc, secret=api_key)
            raise mapped from exc
        text = getattr(response, "output_text", None)
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) if usage is not None else None
        output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        returned_model = getattr(response, "model", None)
        return ProviderInvocation(
            text=None if text is None else redact_secret(str(text), api_key),
            model_id=self._model_id,
            model_version=returned_model if isinstance(returned_model, str) and returned_model.strip() else None,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            retries=0,
        )
