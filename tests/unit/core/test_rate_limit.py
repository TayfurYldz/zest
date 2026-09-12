"""Core rate-limit tests. Clock injected; no datetime.now() calls."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.core.enums import ReasonCode
from zest.core.errors import CoreInputError
from zest.core.rate_limit import (
    RateLimitProfile,
    RequestReservation,
    check_rate_limit,
    check_rate_limit_reservation,
)


class RateLimitCheckTests(unittest.TestCase):
    def test_under_limit_allowed(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=3,
            window_seconds=60,
        )
        attempts = (now - timedelta(seconds=10), now - timedelta(seconds=20))
        check = check_rate_limit(profile, attempts, now)
        self.assertTrue(check.allowed)
        self.assertEqual(check.reason_code, ReasonCode.ALLOWED)
        self.assertIsNone(check.next_allowed_at)

    def test_at_limit_denied(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=2,
            window_seconds=60,
        )
        attempts = (now - timedelta(seconds=10), now - timedelta(seconds=20))
        check = check_rate_limit(profile, attempts, now)
        self.assertFalse(check.allowed)
        self.assertEqual(check.reason_code, ReasonCode.RATE_LIMIT_DENIED)
        self.assertIsNotNone(check.next_allowed_at)

    def test_zero_max_requests_denied(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=0,
            window_seconds=60,
        )
        check = check_rate_limit(profile, (), now)
        self.assertFalse(check.allowed)
        self.assertEqual(check.reason_code, ReasonCode.RATE_LIMIT_DENIED)
        self.assertIsNone(check.next_allowed_at)

    def test_zero_window_seconds_denied(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=10,
            window_seconds=0,
        )
        check = check_rate_limit(profile, (), now)
        self.assertFalse(check.allowed)
        self.assertEqual(check.reason_code, ReasonCode.RATE_LIMIT_DENIED)

    def test_outside_window_not_counted(self) -> None:
        now = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=1,
            window_seconds=60,
        )
        attempts = (now - timedelta(seconds=61),)
        check = check_rate_limit(profile, attempts, now)
        self.assertTrue(check.allowed)

    def test_negative_amount_rejected(self) -> None:
        with self.assertRaises(CoreInputError):
            RateLimitProfile(
                profile_id="rl-1",
                program_id="prog-1",
                max_requests_per_window=-1,
                window_seconds=60,
            )


class WeightedRateLimitReservationTests(unittest.TestCase):
    def test_browser_partial_reservation(self) -> None:
        now = datetime(
            2026, 8, 19, 12, 0, 0,
            tzinfo=timezone.utc,
        )
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=10,
            window_seconds=60,
        )

        check = check_rate_limit_reservation(
            profile,
            (),
            64,
            now,
            allow_partial=True,
        )

        self.assertTrue(check.allowed)
        self.assertEqual(
            check.permitted_requests,
            10,
        )

    def test_non_partial_four_request_reservation_denied(self) -> None:
        now = datetime(
            2026, 8, 19, 12, 0, 0,
            tzinfo=timezone.utc,
        )
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=3,
            window_seconds=60,
        )

        check = check_rate_limit_reservation(
            profile,
            (),
            4,
            now,
        )

        self.assertFalse(check.allowed)
        self.assertEqual(
            check.reason_code,
            ReasonCode.RATE_LIMIT_DENIED,
        )

    def test_weighted_window_exhaustion(self) -> None:
        now = datetime(
            2026, 8, 19, 12, 0, 0,
            tzinfo=timezone.utc,
        )
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=20,
            window_seconds=60,
        )

        reservations = (
            RequestReservation(
                occurred_at=(
                    now - timedelta(seconds=10)
                ),
                amount=20,
            ),
        )

        check = check_rate_limit_reservation(
            profile,
            reservations,
            1,
            now,
        )

        self.assertFalse(check.allowed)
        self.assertEqual(
            check.used_requests,
            20,
        )
        self.assertIsNotNone(
            check.next_allowed_at
        )

    def test_expired_reservation_not_counted(self) -> None:
        now = datetime(
            2026, 8, 19, 12, 0, 0,
            tzinfo=timezone.utc,
        )
        profile = RateLimitProfile(
            profile_id="rl-1",
            program_id="prog-1",
            max_requests_per_window=10,
            window_seconds=60,
        )

        reservations = (
            RequestReservation(
                occurred_at=(
                    now - timedelta(seconds=61)
                ),
                amount=10,
            ),
        )

        check = check_rate_limit_reservation(
            profile,
            reservations,
            10,
            now,
        )

        self.assertTrue(check.allowed)
        self.assertEqual(
            check.used_requests,
            0,
        )


if __name__ == "__main__":
    unittest.main()
