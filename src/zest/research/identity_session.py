"""Configured research identities. Not authorization. Not raw credentials."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zest.research.types import ResearchInputError

HTTP_FORM_LOGIN = "HTTP_FORM_LOGIN"
ALLOWED_AUTHENTICATION_METHODS = frozenset({HTTP_FORM_LOGIN})
ALLOWED_CREDENTIAL_SCHEMES = frozenset({"LOCAL_DEV", "ENV_REFERENCE"})


class SessionState(Enum):
    NEW = "NEW"
    AUTHENTICATING = "AUTHENTICATING"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CredentialReference:
    """Handle only. Never a password, token, or cookie value."""

    scheme: str
    name: str

    def __post_init__(self) -> None:
        if self.scheme not in ALLOWED_CREDENTIAL_SCHEMES:
            raise ResearchInputError("credential scheme is not supported")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ResearchInputError("credential name must be a non-empty string")


@dataclass(frozen=True)
class HttpFormLoginProfile:
    """Bounded first authentication profile. Not a universal auth language."""

    profile_id: str
    path: str
    username_field: str
    password_secret_name: str
    session_cookie_name: str
    success_status_codes: tuple[int, ...] = (200,)
    method: str = "POST"

    def __post_init__(self) -> None:
        for name in ("profile_id", "path", "username_field", "password_secret_name", "session_cookie_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ResearchInputError(f"{name} must be a non-empty string")
        if self.method != "POST":
            raise ResearchInputError("HTTP form login supports POST only")
        if not self.path.startswith("/") or "://" in self.path or self.path.startswith("//"):
            raise ResearchInputError("login path is invalid")
        if not self.success_status_codes:
            raise ResearchInputError("success_status_codes is required")
        for status in self.success_status_codes:
            if not isinstance(status, int) or status < 100 or status > 599:
                raise ResearchInputError("success_status_codes must be HTTP statuses")


@dataclass(frozen=True)
class Identity:
    identity_id: str
    actor_reference: str
    target_reference: str
    credential_reference: CredentialReference
    authentication_profile_reference: str
    role_reference: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "identity_id",
            "actor_reference",
            "target_reference",
            "authentication_profile_reference",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ResearchInputError(f"{name} must be a non-empty string")
        if not isinstance(self.credential_reference, CredentialReference):
            raise ResearchInputError("credential_reference is required")
        if self.role_reference is not None and (
            not isinstance(self.role_reference, str) or not self.role_reference.strip()
        ):
            raise ResearchInputError("role_reference must be a non-empty string when set")


def local_dev_credential(name: str) -> CredentialReference:
    return CredentialReference("LOCAL_DEV", name)
