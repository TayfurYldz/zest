"""Normalizer registry keyed by trusted request capability/action, not Worker payload."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from zest.application.transition_a.authorization_differential import (
    HttpAuthorizationDifferentialNormalizer,
)
from zest.application.transition_a.browser_page import BrowserPageNormalizer
from zest.application.transition_a.diagnostic_echo import DiagnosticEchoNormalizer
from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import UnsupportedNormalizerError
from zest.application.transition_a.http_authentication import HttpAuthenticationNormalizer
from zest.application.transition_a.http_raw_exchange import HttpRawExchangeNormalizer
from zest.application.transition_a.http_transaction import HttpTransactionNormalizer
from zest.application.transition_a.state_transition import HttpStateTransitionNormalizer
from zest.tools.capabilities import (
    BROWSER_PAGE_INTERACT_ACTION,
    BROWSER_PAGE_NAVIGATE_ACTION,
    BROWSER_PAGE_OBSERVE_ACTION,
    HTTP_TRANSACTION_MUTATE_ACTION,
    HTTP_TRANSACTION_READ_ACTION,
)


class ObservationNormalizer(Protocol):
    capability: str
    action: str
    version: str

    def normalize(
        self,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> tuple[ObservationDraft, ...]: ...


class NormalizerRegistry:
    def __init__(self, normalizers: tuple[ObservationNormalizer, ...] | None = None) -> None:
        registered = (
            normalizers
            if normalizers is not None
            else (
                DiagnosticEchoNormalizer(),
                HttpAuthorizationDifferentialNormalizer(),
                HttpStateTransitionNormalizer(),
                HttpTransactionNormalizer(HTTP_TRANSACTION_READ_ACTION),
                HttpTransactionNormalizer(HTTP_TRANSACTION_MUTATE_ACTION),
                HttpAuthenticationNormalizer(),
                HttpRawExchangeNormalizer(),
                BrowserPageNormalizer(BROWSER_PAGE_OBSERVE_ACTION),
                BrowserPageNormalizer(BROWSER_PAGE_NAVIGATE_ACTION),
                BrowserPageNormalizer(BROWSER_PAGE_INTERACT_ACTION),
            )
        )
        self._normalizers: dict[tuple[str, str], ObservationNormalizer] = {}
        for normalizer in registered:
            key = (normalizer.capability, normalizer.action)
            self._normalizers[key] = normalizer

    def get(self, capability: str, action: str) -> ObservationNormalizer:
        try:
            return self._normalizers[(capability, action)]
        except KeyError as exc:
            raise UnsupportedNormalizerError(
                f"no Transition A normalizer for capability={capability!r} action={action!r}"
            ) from exc
