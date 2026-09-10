from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.bounded_model_failover import (
    BoundedRateLimitFailoverModelPort,
)
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelRole,
    ProviderRateLimitError,
    ProviderUsageLimitError,
)
from support.fake_model import ScriptedModelPort


def _request(
    role: ModelRole = ModelRole.GENERATOR,
) -> ModelCallRequest:
    return ModelCallRequest(
        role=role,
        correlation_id="corr-failover",
        context_fingerprint="fp-failover",
        instructions="reason",
        payload={"note": "ok"},
    )


class _RateLimitOnceThenSuccess:
    def __init__(self) -> None:
        self.calls = 0
        self._success = ScriptedModelPort()

    def complete(self, request):
        self.calls += 1

        if self.calls == 1:
            raise ProviderRateLimitError(
                "transient rate"
            )

        return self._success.complete(request)


class BoundedRateLimitFailoverTests(
    unittest.TestCase
):
    def test_transient_rate_limit_retries_same_runtime(
        self,
    ) -> None:
        primary = _RateLimitOnceThenSuccess()
        sleeps: list[float] = []

        port = BoundedRateLimitFailoverModelPort(
            (primary,),
            max_fallback_attempts=0,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0.25,
            sleep_fn=sleeps.append,
        )

        result = port.complete(
            _request()
        )

        self.assertIs(
            result.role,
            ModelRole.GENERATOR,
        )

        self.assertEqual(
            primary.calls,
            2,
        )

        self.assertEqual(
            port.rate_limit_retries_used,
            1,
        )

        self.assertEqual(
            port.fallbacks_used,
            0,
        )

        self.assertEqual(
            sleeps,
            [0.25],
        )

    def test_rate_limit_retries_then_moves_to_secondary_and_sticks(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "rate"
            )
        )

        secondary = ScriptedModelPort()

        port = BoundedRateLimitFailoverModelPort(
            (
                primary,
                secondary,
            ),
            max_fallback_attempts=1,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        generator = port.complete(
            _request(
                ModelRole.GENERATOR
            )
        )

        falsifier = port.complete(
            _request(
                ModelRole.FALSIFIER
            )
        )

        self.assertIs(
            generator.role,
            ModelRole.GENERATOR,
        )

        self.assertIs(
            falsifier.role,
            ModelRole.FALSIFIER,
        )

        self.assertEqual(
            len(primary.calls),
            2,
        )

        self.assertEqual(
            len(secondary.calls),
            2,
        )

        self.assertEqual(
            port.rate_limit_retries_used,
            1,
        )

        self.assertEqual(
            port.fallbacks_used,
            1,
        )

        self.assertEqual(
            port.active_index,
            1,
        )

    def test_retry_and_fallback_exhaustion_preserves_rate_limit(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "primary rate"
            )
        )

        secondary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "secondary rate"
            )
        )

        port = BoundedRateLimitFailoverModelPort(
            (
                primary,
                secondary,
            ),
            max_fallback_attempts=1,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        with self.assertRaises(
            ProviderRateLimitError
        ):
            port.complete(_request())

        self.assertEqual(
            len(primary.calls),
            2,
        )

        self.assertEqual(
            len(secondary.calls),
            2,
        )

        self.assertEqual(
            port.rate_limit_retries_used,
            2,
        )

        self.assertEqual(
            port.fallbacks_used,
            1,
        )

    def test_usage_limit_skips_immediate_retry_and_uses_bounded_fallback(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderUsageLimitError(
                "usage exhausted"
            )
        )
        secondary = ScriptedModelPort()

        port = BoundedRateLimitFailoverModelPort(
            (primary, secondary),
            max_fallback_attempts=1,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        result = port.complete(_request())

        self.assertIs(
            result.role,
            ModelRole.GENERATOR,
        )
        self.assertEqual(
            len(primary.calls),
            1,
        )
        self.assertEqual(
            len(secondary.calls),
            1,
        )
        self.assertEqual(
            port.rate_limit_retries_used,
            0,
        )
        self.assertEqual(
            port.fallbacks_used,
            1,
        )
        self.assertEqual(
            port.active_index,
            1,
        )

    def test_usage_limit_without_fallback_preserves_usage_subtype(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderUsageLimitError(
                "usage exhausted"
            )
        )

        port = BoundedRateLimitFailoverModelPort(
            (primary,),
            max_fallback_attempts=0,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        with self.assertRaises(
            ProviderUsageLimitError
        ):
            port.complete(_request())

        self.assertEqual(
            len(primary.calls),
            1,
        )
        self.assertEqual(
            port.rate_limit_retries_used,
            0,
        )
        self.assertEqual(
            port.fallbacks_used,
            0,
        )

    def test_usage_limit_fallback_exhaustion_does_not_retry_either_runtime(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderUsageLimitError(
                "primary usage exhausted"
            )
        )
        secondary = ScriptedModelPort(
            error=ProviderUsageLimitError(
                "secondary usage exhausted"
            )
        )

        port = BoundedRateLimitFailoverModelPort(
            (primary, secondary),
            max_fallback_attempts=1,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        with self.assertRaises(
            ProviderUsageLimitError
        ):
            port.complete(_request())

        self.assertEqual(
            len(primary.calls),
            1,
        )
        self.assertEqual(
            len(secondary.calls),
            1,
        )
        self.assertEqual(
            port.rate_limit_retries_used,
            0,
        )
        self.assertEqual(
            port.fallbacks_used,
            1,
        )

    def test_zero_retry_zero_fallback_preserves_rate_limit(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "rate"
            )
        )

        secondary = ScriptedModelPort()

        port = BoundedRateLimitFailoverModelPort(
            (
                primary,
                secondary,
            ),
            max_fallback_attempts=0,
            max_rate_limit_retries_per_runtime=0,
            retry_delay_seconds=0,
        )

        with self.assertRaises(
            ProviderRateLimitError
        ):
            port.complete(_request())

        self.assertEqual(
            len(primary.calls),
            1,
        )

        self.assertEqual(
            len(secondary.calls),
            0,
        )

    def test_content_policy_is_never_retried_or_failed_over(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ContentPolicyBlockedError(
                "policy"
            )
        )

        secondary = ScriptedModelPort()

        port = BoundedRateLimitFailoverModelPort(
            (
                primary,
                secondary,
            ),
            max_fallback_attempts=1,
            max_rate_limit_retries_per_runtime=1,
            retry_delay_seconds=0,
        )

        with self.assertRaises(
            ContentPolicyBlockedError
        ):
            port.complete(_request())

        self.assertEqual(
            len(primary.calls),
            1,
        )

        self.assertEqual(
            port.rate_limit_retries_used,
            0,
        )

        self.assertEqual(
            port.fallbacks_used,
            0,
        )

        self.assertEqual(
            len(secondary.calls),
            0,
        )


if __name__ == "__main__":
    unittest.main()
