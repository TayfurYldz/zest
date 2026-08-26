"""Authorization source eligibility. Persistence entities are not defined here."""

from dataclasses import dataclass

from zest.core.enums import AuthorizationSourceState, ReasonCode
from zest.core.errors import CoreInputError
from zest.core.identity import require_opaque_id


@dataclass(frozen=True)
class AuthorizationSourceView:
    authorization_source_id: str
    program_id: str
    state: AuthorizationSourceState

    def __post_init__(self) -> None:
        require_opaque_id(self.authorization_source_id, "authorization_source_id")
        require_opaque_id(self.program_id, "program_id")
        if not isinstance(self.state, AuthorizationSourceState):
            raise CoreInputError("state must be AuthorizationSourceState")


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
    return AuthorizationCheck(True, ReasonCode.ALLOWED, source.authorization_source_id)
