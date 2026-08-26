"""Fail-closed rate-limit checks for program policy. Pure Core, no network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from zest.core.enums import ReasonCode
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id


@dataclass(frozen=True)
class RateLimitProfile:
    """Immutable rate-limit profile. 0 allowance means DENY."""

    profile_id: str
    program_id: str
    max_requests_per_window: int
    window_seconds: int

    def __post_init__(self) -> None:
        require_opaque_id(self.profile_id, "profile_id")
        require_opaque_id(self.program_id, "program_id")
        if not isinstance(self.max_requests_per_window, int) or isinstance(self.max_requests_per_window, bool) or self.max_requests_per_window < 0:
            raise CoreInputError("max_requests_per_window must be a non-negative int")
        if not isinstance(self.window_seconds, int) or isinstance(self.window_seconds, bool) or self.window_seconds < 0:
            raise CoreInputError("window_seconds must be a non-negative int")


@dataclass(frozen=True)
class RateLimitCheck:
    """Rate-limit decision. Not a scope decision and not a grant."""

    allowed: bool
    reason_code: ReasonCode
    next_allowed_at: datetime | None = None


def check_rate_limit(
    profile: RateLimitProfile,
    attempt_times: Sequence[datetime],
    now: datetime,
) -> RateLimitCheck:
    """Count authorized attempts inside the rolling window; fail closed.

    `now` is injected by the caller; production code must not call datetime.now()
    directly here (D4).
    """
    if not isinstance(profile, RateLimitProfile):
        raise CoreInputError("rate_limit_profile is required")
    if not isinstance(now, datetime):
        raise CoreInputError("now must be an aware datetime")
    if profile.max_requests_per_window == 0 or profile.window_seconds == 0:
        return RateLimitCheck(
            allowed=False,
            reason_code=ReasonCode.RATE_LIMIT_DENIED,
            next_allowed_at=None,
        )
    window_start = now - timedelta(seconds=profile.window_seconds)
    recent = [t for t in attempt_times if t > window_start and t <= now]
    if len(recent) >= profile.max_requests_per_window:
        oldest_in_window = min(recent)
        next_allowed_at = oldest_in_window + timedelta(seconds=profile.window_seconds)
        return RateLimitCheck(
            allowed=False,
            reason_code=ReasonCode.RATE_LIMIT_DENIED,
            next_allowed_at=next_allowed_at,
        )
    return RateLimitCheck(
        allowed=True,
        reason_code=ReasonCode.ALLOWED,
        next_allowed_at=None,
    )
