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


def _capacity_domain_id(
    port: ModelPort,
) -> str | None:
    value = getattr(
        port,
        "capacity_domain_id",
        None,
    )

    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        return None

    return value.strip()


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
        self._usage_capacity_domain_skips = 0

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
    def usage_capacity_domain_skips(
        self,
    ) -> int:
        return self._usage_capacity_domain_skips

    def _next_usage_capacity_fallback_index(
        self,
    ) -> int | None:
        current_domain = _capacity_domain_id(
            self._ports[self._active_index]
        )

        # Legacy/third-party ports without declared capacity-domain
        # semantics retain the previous immediate-next behavior.
        if current_domain is None:
            candidate = self._active_index + 1
            return (
                candidate
                if candidate < len(self._ports)
                else None
            )

        for index in range(
            self._active_index + 1,
            len(self._ports),
        ):
            candidate_domain = _capacity_domain_id(
                self._ports[index]
            )

            # For a known exhausted capacity domain, independence
            # must be positively known. Unknown is not proof of
            # independent quota.
            if (
                candidate_domain is not None
                and candidate_domain != current_domain
            ):
                return index

            self._usage_capacity_domain_skips += 1

        return None

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
                # Account/session usage exhaustion is a capacity-domain
                # failure, not merely a model-id failure. Do not burn another
                # physical attempt against a fallback that is known to share
                # the same quota/session envelope.
                #
                # Generic/transient ProviderRateLimitError retains the
                # existing bounded retry/fallback policy below.
                can_fallback = (
                    self._fallbacks_used
                    < self._max_fallback_attempts
                )

                if not can_fallback:
                    raise

                next_index = (
                    self._next_usage_capacity_fallback_index()
                )

                if next_index is None:
                    raise

                self._fallbacks_used += 1
                self._active_index = next_index
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
