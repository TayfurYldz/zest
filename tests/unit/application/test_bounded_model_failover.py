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


class BoundedRateLimitFailoverTests(
    unittest.TestCase
):
    def test_rate_limit_moves_to_secondary_and_sticks(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "rate"
            )
        )
        secondary = ScriptedModelPort()

        port = (
            BoundedRateLimitFailoverModelPort(
                (
                    primary,
                    secondary,
                ),
                max_fallback_attempts=1,
            )
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
            1,
        )

        self.assertEqual(
            len(secondary.calls),
            2,
        )

        self.assertEqual(
            port.fallbacks_used,
            1,
        )

        self.assertEqual(
            port.active_index,
            1,
        )

    def test_zero_fallback_preserves_rate_limit(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ProviderRateLimitError(
                "rate"
            )
        )
        secondary = ScriptedModelPort()

        port = (
            BoundedRateLimitFailoverModelPort(
                (
                    primary,
                    secondary,
                ),
                max_fallback_attempts=0,
            )
        )

        with self.assertRaises(
            ProviderRateLimitError
        ):
            port.complete(_request())

        self.assertEqual(
            len(secondary.calls),
            0,
        )

    def test_content_policy_is_never_failed_over(
        self,
    ) -> None:
        primary = ScriptedModelPort(
            error=ContentPolicyBlockedError(
                "policy"
            )
        )
        secondary = ScriptedModelPort()

        port = (
            BoundedRateLimitFailoverModelPort(
                (
                    primary,
                    secondary,
                ),
                max_fallback_attempts=1,
            )
        )

        with self.assertRaises(
            ContentPolicyBlockedError
        ):
            port.complete(_request())

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
