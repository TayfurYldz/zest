"""Bounded operational failover for rate-limited ModelPorts.

This is runtime resilience, not authorization and not research truth.
It never bypasses content-policy failures and never creates unlimited retry.
"""

from __future__ import annotations

from zest.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelPort,
    ProviderRateLimitError,
)


class BoundedRateLimitFailoverModelPort:
    """Move to the next configured runtime only after RATE_LIMITED.

    The supplied ports are expected to be independently budget-enforced.
    Therefore each physical external invocation remains separately charged.
    """

    def __init__(
        self,
        ports: tuple[ModelPort, ...],
        *,
        max_fallback_attempts: int,
    ) -> None:
        if not ports:
            raise ValueError("at least one model port is required")

        if (
            not isinstance(max_fallback_attempts, int)
            or isinstance(max_fallback_attempts, bool)
            or max_fallback_attempts < 0
        ):
            raise ValueError(
                "max_fallback_attempts must be a non-negative int"
            )

        self._ports = tuple(ports)
        self._max_fallback_attempts = min(
            max_fallback_attempts,
            len(self._ports) - 1,
        )

        self._active_index = 0
        self._fallbacks_used = 0

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def fallbacks_used(self) -> int:
        return self._fallbacks_used

    @property
    def reserved_invocations(self) -> tuple[str, ...]:
        rows: list[str] = []

        for port in self._ports:
            reserved = getattr(
                port,
                "reserved_invocations",
                (),
            )

            rows.extend(
                str(item)
                for item in reserved
            )

        return tuple(rows)

    def complete(
        self,
        request: ModelCallRequest,
    ) -> ModelCallResult:
        while True:
            port = self._ports[
                self._active_index
            ]

            try:
                return port.complete(request)

            except ProviderRateLimitError:
                can_fallback = (
                    self._fallbacks_used
                    < self._max_fallback_attempts
                    and self._active_index + 1
                    < len(self._ports)
                )

                if not can_fallback:
                    raise

                self._fallbacks_used += 1
                self._active_index += 1
