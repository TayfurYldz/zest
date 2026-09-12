"""Authorization source eligibility. Persistence entities are not defined here."""

from dataclasses import dataclass
from datetime import datetime

from zest.core.enums import AuthorizationSourceState, ReasonCode
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id


@dataclass(frozen=True)
class AuthorizationSourceView:
    authorization_source_id: str
    program_id: str
    state: AuthorizationSourceState
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    evaluated_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.authorization_source_id, "authorization_source_id")
        require_opaque_id(self.program_id, "program_id")
        if not isinstance(self.state, AuthorizationSourceState):
            raise CoreInputError("state must be AuthorizationSourceState")

        for field_name, value in (
            ("effective_from", self.effective_from),
            ("effective_until", self.effective_until),
            ("evaluated_at", self.evaluated_at),
        ):
            if value is None:
                continue
            if not isinstance(value, datetime):
                raise CoreInputError(f"{field_name} must be a datetime")
            if value.tzinfo is None or value.utcoffset() is None:
                raise CoreInputError(f"{field_name} must be timezone-aware")

        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_from > self.effective_until
        ):
            raise CoreInputError(
                "effective_from must not be after effective_until"
            )

        if (
            self.effective_from is not None
            or self.effective_until is not None
        ) and self.evaluated_at is None:
            raise CoreInputError(
                "evaluated_at is required for temporal authorization"
            )


@dataclass(frozen=True)
class AuthorizationCheck:
    allowed_to_continue: bool
    reason_code: ReasonCode
    authorization_source_id: str | None


def check_authorization(
    source: AuthorizationSourceView | None,
) -> AuthorizationCheck:
    if source is None:
        return AuthorizationCheck(False, ReasonCode.AUTHORIZATION_MISSING, None)
    if source.state is not AuthorizationSourceState.ACTIVE:
        return AuthorizationCheck(
            False, ReasonCode.AUTHORIZATION_INACTIVE, source.authorization_source_id
        )

    if source.effective_from is not None:
        assert source.evaluated_at is not None
        if source.evaluated_at < source.effective_from:
            return AuthorizationCheck(
                False,
                ReasonCode.AUTHORIZATION_INACTIVE,
                source.authorization_source_id,
            )

    if source.effective_until is not None:
        assert source.evaluated_at is not None
        if source.evaluated_at > source.effective_until:
            return AuthorizationCheck(
                False,
                ReasonCode.AUTHORIZATION_INACTIVE,
                source.authorization_source_id,
            )

    return AuthorizationCheck(True, ReasonCode.ALLOWED, source.authorization_source_id)
