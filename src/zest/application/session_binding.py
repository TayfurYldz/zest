"""Identity/session binding for authorized HTTP dispatch. Not a scope grant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from zest.core.enums import ReasonCode
from zest.data.records import SessionContextRecord
from zest.platform.secrets import SecretPort, SecretReference, SecretScheme, SecretResolutionStatus
from zest.research.identity_session import HttpFormLoginProfile, Identity, SessionState
from zest.research.types import ExperimentPlan
from zest.tools.capabilities import (
    BROWSER_PAGE_CAPABILITY,
    HTTP_AUTHENTICATION_CAPABILITY,
    HTTP_AUTHENTICATION_LOGIN_ACTION,
    HTTP_TRANSACTION_CAPABILITY,
)


@dataclass(frozen=True)
class SessionBindingDecision:
    accepted: bool
    input_rejected: bool = False
    reason_code: ReasonCode | None = None
    message: str = ""
    session: SessionContextRecord | None = None
    resolved_secrets: dict[str, str] | None = None


def bind_identity_session(
    plan: ExperimentPlan,
    *,
    identity_id: str | None,
    identity: Identity | None,
    profile: HttpFormLoginProfile | None,
    session: SessionContextRecord | None,
    secret_port: SecretPort | None,
    now: datetime,
    research_run_id: str | None = None,
) -> SessionBindingDecision:
    if plan.required_capability == HTTP_AUTHENTICATION_CAPABILITY:
        return _bind_login(
            plan,
            identity_id=identity_id,
            identity=identity,
            profile=profile,
            secret_port=secret_port,
        )
    if plan.required_capability in {HTTP_TRANSACTION_CAPABILITY, BROWSER_PAGE_CAPABILITY}:
        session_ref = plan.arguments.get("session_context_reference")
        if session_ref is None:
            return SessionBindingDecision(accepted=True)
        return _bind_existing_session(
            plan,
            identity_id=identity_id,
            identity=identity,
            profile=profile,
            session=session,
            secret_port=secret_port,
            now=now,
            research_run_id=research_run_id,
        )
    return SessionBindingDecision(accepted=True)


def session_material_reference(session_context_id: str) -> SecretReference:
    return SecretReference(SecretScheme.SESSION_MATERIAL, f"session:{session_context_id}")


def normalized_origin(origin: str) -> str:
    return origin.strip().rstrip("/")


def origins_match(left: str, right: str) -> bool:
    return _origin_key(left) == _origin_key(right)


def _bind_login(
    plan: ExperimentPlan,
    *,
    identity_id: str | None,
    identity: Identity | None,
    profile: HttpFormLoginProfile | None,
    secret_port: SecretPort | None,
) -> SessionBindingDecision:
    if plan.action != HTTP_AUTHENTICATION_LOGIN_ACTION:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.UNKNOWN_ACTION,
            message="unknown authentication action",
        )
    if identity is None or profile is None or not identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="login requires a configured identity and authentication profile",
        )
    if identity.identity_id != identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="identity_id does not match the configured identity",
        )
    plan_identity = plan.arguments.get("identity_id")
    if plan_identity != identity.identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="plan identity_id does not match the configured identity",
        )
    if identity.authentication_profile_reference != profile.profile_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="identity is not bound to this authentication profile",
        )
    if identity.target_reference != plan.target_reference:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="identity is bound to a different target",
        )
    if plan.arguments.get("path") != profile.path:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="login path does not match the authentication profile",
        )
    if plan.arguments.get("password_secret_name") != profile.password_secret_name:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="password secret name does not match the authentication profile",
        )
    if secret_port is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="password SecretReference cannot be resolved",
        )
    try:
        password_ref = SecretReference(
            SecretScheme(identity.credential_reference.scheme),
            identity.credential_reference.name,
        )
    except ValueError:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="password SecretReference cannot be resolved",
        )
    resolution = secret_port.resolve(password_ref)
    if resolution.status is not SecretResolutionStatus.RESOLVED or resolution.value is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="password SecretReference cannot be resolved",
        )
    return SessionBindingDecision(
        accepted=True,
        resolved_secrets={profile.password_secret_name: resolution.value},
    )


def _bind_existing_session(
    plan: ExperimentPlan,
    *,
    identity_id: str | None,
    identity: Identity | None,
    profile: HttpFormLoginProfile | None,
    session: SessionContextRecord | None,
    secret_port: SecretPort | None,
    now: datetime,
    research_run_id: str | None,
) -> SessionBindingDecision:
    if not identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session_context_reference is not authority without identity_id",
        )
    if identity is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session reuse requires the configured identity",
        )
    if identity.identity_id != identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="identity_id does not match the configured identity",
        )
    if session is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session_context_reference does not exist",
        )
    if session.identity_id != identity_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session is bound to a different identity",
        )
    if research_run_id is None or session.research_run_id != research_run_id:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session is bound to a different research run",
        )
    if identity.target_reference != plan.target_reference:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="identity is bound to a different target",
        )
    if session.authentication_profile_reference != identity.authentication_profile_reference:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session is bound to a different authentication profile",
        )
    if (
        profile is not None
        and session.authentication_profile_reference != profile.profile_id
    ):
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session is bound to a different authentication profile",
        )
    plan_origin = str(plan.arguments.get("authorized_origin") or "")
    if not origins_match(session.origin, plan_origin):
        return SessionBindingDecision(
            accepted=False,
            reason_code=ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED,
            message="session is bound to a different origin",
        )
    if session.state == SessionState.EXPIRED.value or (
        session.expires_at is not None and session.expires_at <= now
    ):
        return SessionBindingDecision(
            accepted=False,
            reason_code=ReasonCode.AUTHORIZATION_INACTIVE,
            message="session is expired",
        )
    if session.state == SessionState.REVOKED.value:
        return SessionBindingDecision(
            accepted=False,
            reason_code=ReasonCode.AUTHORIZATION_INACTIVE,
            message="session is revoked",
        )
    if session.state != SessionState.ACTIVE.value:
        return SessionBindingDecision(
            accepted=False,
            reason_code=ReasonCode.AUTHORIZATION_INACTIVE,
            message="session is not ACTIVE",
        )
    if secret_port is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session material is unavailable; reauthentication required",
        )
    try:
        scheme = SecretScheme(session.secret_scheme)
    except ValueError:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session material is unavailable; reauthentication required",
        )
    reference = SecretReference(scheme, session.secret_name)
    resolution = secret_port.resolve(reference)
    if resolution.status is not SecretResolutionStatus.RESOLVED or resolution.value is None:
        return SessionBindingDecision(
            accepted=False,
            input_rejected=True,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            message="session material is unavailable; reauthentication required",
        )
    return SessionBindingDecision(
        accepted=True,
        session=session,
        resolved_secrets={"session_cookie": resolution.value},
    )


def _origin_key(origin: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(normalized_origin(origin))
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port)
