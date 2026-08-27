"""NVIDIA-compatible Observer provider adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from zest.application.observer import (
    MAX_CONTEXT_BYTES,
    ObserverContext,
    ObserverProviderError,
    ObserverProviderUnavailableError,
    ObserverRateLimitError,
    ObserverTimeoutError,
)


class NvidiaCompatibleObserverProvider:
    """HTTP adapter kept outside application and domain layers."""

    provider_name = "nvidia-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 8.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        if not api_key.strip():
            raise ObserverProviderUnavailableError("NVIDIA observer key is not configured")
        self._api_key = api_key
        self.model_name = model.strip()[:120] or "unknown"
        self._base_url = base_url.rstrip("/") + "/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def observe(self, context: ObserverContext) -> Mapping[str, Any]:
        body = json.dumps({
            "model": self.model_name,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Return only the ObserverBrief JSON schema. Do not infer facts."},
                {"role": "user", "content": json.dumps(context.to_mapping(), separators=(",", ":"))},
            ],
        }).encode("utf-8")
        request = Request(
            self._base_url,
            data=body,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self._timeout_seconds)
            decoded = json.loads(response.read(MAX_CONTEXT_BYTES).decode("utf-8"))
            content = decoded["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
            if not isinstance(result, Mapping):
                raise ValueError("provider content is not an object")
            return result
        except HTTPError as exc:
            if exc.code == 429:
                raise ObserverRateLimitError("observer provider rate limited") from exc
            raise ObserverProviderError("observer provider request failed") from exc
        except (TimeoutError, URLError) as exc:
            raise ObserverTimeoutError("observer provider unavailable or timed out") from exc
        except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise ObserverProviderError("observer provider returned malformed output") from exc
