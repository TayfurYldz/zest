"""Bounded operational resilience for rate-limited ModelPorts.

Runtime resilience only:
- does not grant authority;
- does not alter research truth;
- does not bypass content-policy failures;
- does not retry without a hard bound.
"""

from __future__ import annotations

from collections.abc import Callable
from time import sleep

from zest.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelPort,
    ProviderRateLimitError,
    ProviderUsageLimitError,
)


class BoundedRateLimitFailoverModelPort:
    """Retry rate limits boundedly, then move to the next configured runtime.

    Ports supplied here are expected to be independently budget-enforced.
    Every physical attempt therefore remains separately charged.

    Once a runtime fallback succeeds, that runtime remains active for the
    remainder of this logical proposal so Generator/Falsifier stay coherent.
    """

    def __init__(
        self,
        ports: tuple[ModelPort, ...],
        *,
        max_fallback_attempts: int,
        max_rate_limit_retries_per_runtime: int = 1,
        retry_delay_seconds: float = 1.0,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        if not ports:
            raise ValueError(
                "at least one model port is required"
            )

        if (
            not isinstance(max_fallback_attempts, int)
            or isinstance(max_fallback_attempts, bool)
            or max_fallback_attempts < 0
        ):
            raise ValueError(
                "max_fallback_attempts must be a non-negative int"
            )

        if (
            not isinstance(
                max_rate_limit_retries_per_runtime,
                int,
            )
            or isinstance(
                max_rate_limit_retries_per_runtime,
                bool,
            )
            or max_rate_limit_retries_per_runtime < 0
        ):
            raise ValueError(
                "max_rate_limit_retries_per_runtime "
                "must be a non-negative int"
            )

        if (
            not isinstance(
                retry_delay_seconds,
                (int, float),
            )
            or isinstance(
                retry_delay_seconds,
                bool,
            )
            or retry_delay_seconds < 0
        ):
            raise ValueError(
                "retry_delay_seconds must be non-negative"
            )

        self._ports = tuple(ports)

        self._max_fallback_attempts = min(
            max_fallback_attempts,
            len(self._ports) - 1,
        )

        self._max_rate_limit_retries_per_runtime = (
            max_rate_limit_retries_per_runtime
        )

        self._retry_delay_seconds = float(
            retry_delay_seconds
        )

        self._sleep = (
            sleep
            if sleep_fn is None
            else sleep_fn
        )

        self._active_index = 0
        self._fallbacks_used = 0
        self._rate_limit_retries_used = 0

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def fallbacks_used(self) -> int:
        return self._fallbacks_used

    @property
    def rate_limit_retries_used(self) -> int:
        return self._rate_limit_retries_used

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
        retries_for_active_runtime = 0

        while True:
            port = self._ports[
                self._active_index
            ]

            try:
                return port.complete(request)

            except ProviderUsageLimitError:
                # An explicit account/session usage envelope is not expected
                # to recover from a one-second retry. Skip only the immediate
                # same-runtime retry. Existing bounded fallback remains
                # available because a different configured model/runtime may
                # still have usable capacity.
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
                retries_for_active_runtime = 0
                continue

            except ProviderRateLimitError:
                can_retry_current = (
                    retries_for_active_runtime
                    < self._max_rate_limit_retries_per_runtime
                )

                if can_retry_current:
                    retries_for_active_runtime += 1
                    self._rate_limit_retries_used += 1

                    if self._retry_delay_seconds:
                        self._sleep(
                            self._retry_delay_seconds
                        )

                    continue

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
                retries_for_active_runtime = 0
