"""Google Gemini generate_content transport. Not imported by Research."""

from __future__ import annotations

import json
from typing import Any, Mapping

from zest.research.model_port import ModelCallRequest, ProviderRuntimeError

from zest.integrations.models.common import ProviderInvocation
from zest.integrations.models.errors import classify_provider_exception
from zest.integrations.models.secrets import SecretReference, redact_secret


class GeminiGenerateContentTransport:
    adapter_identity = "gemini.generate_content"
    provider_adapter_identity = "gemini"

    def __init__(self, *, model_id: str, secret: SecretReference, env: dict[str, str] | None = None) -> None:
        self._model_id = model_id
        self._secret = secret
        self._env = env

    def invoke(self, request: ModelCallRequest, schema: Mapping[str, Any]) -> ProviderInvocation:
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderRuntimeError("google-genai SDK is not installed") from exc
        api_key = self._secret.value(self._env)
        if api_key is None:
            raise ProviderRuntimeError("GEMINI_API_KEY/GOOGLE_API_KEY is not set")
        client = genai.Client(api_key=api_key)
        timeout_ms = request.timeout_ms
        config: dict[str, Any] = {
            "system_instruction": request.instructions,
            "response_mime_type": "application/json",
            "response_json_schema": dict(schema),
        }
        if timeout_ms is not None:
            config["http_options"] = {"timeout": timeout_ms}
        try:
            response = client.models.generate_content(
                model=self._model_id,
                contents=json.dumps(request.payload, ensure_ascii=True),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            mapped = classify_provider_exception(exc, secret=api_key)
            raise mapped from exc
        text = getattr(response, "text", None)
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage is not None else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage is not None else None
        returned_model = getattr(response, "model_version", None) or getattr(response, "model", None)
        return ProviderInvocation(
            text=None if text is None else redact_secret(str(text), api_key),
            model_id=self._model_id,
            model_version=returned_model if isinstance(returned_model, str) and returned_model.strip() else None,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            retries=0,
        )
