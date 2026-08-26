"""Session metadata lifecycle. Never persists cookie, token, or password values."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from zest.application.session_binding import normalized_origin, session_material_reference
from zest.data.records import SessionContextRecord
from zest.data.unit_of_work import UnitOfWork
from zest.platform.secrets import CompositeSecretPort, SecretScheme
from zest.research.identity_session import HTTP_FORM_LOGIN, HttpFormLoginProfile, Identity, SessionState
from zest.research.types import ExperimentPlan
from zest.tools.capabilities import HTTP_AUTHENTICATION_CAPABILITY, HTTP_TRANSACTION_CAPABILITY


def authenticating_session_record(
    plan: ExperimentPlan,
    *,
    identity: Identity,
    profile: HttpFormLoginProfile,
    research_run_id: str,
    now: datetime,
) -> SessionContextRecord:
    session_context_id = str(plan.arguments["session_context_id"])
    reference = session_material_reference(session_context_id)
    return SessionContextRecord(
        session_context_id=session_context_id,
        research_run_id=research_run_id,
        identity_id=identity.identity_id,
        actor_reference=identity.actor_reference,
        origin=normalized_origin(str(plan.arguments["authorized_origin"])),
        authentication_profile_reference=profile.profile_id,
        authentication_method=HTTP_FORM_LOGIN,
        secret_scheme=reference.scheme.value,
        secret_name=reference.name,
        state=SessionState.AUTHENTICATING.value,
        created_at=now,
        updated_at=now,
        session_cookie_name=profile.session_cookie_name,
    )


def capture_login_result(
    uow: UnitOfWork,
    secret_port: CompositeSecretPort | None,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    ephemeral: Mapping[str, Any] | None,
    now: datetime,
) -> None:
    if request.get("worker_capability") != HTTP_AUTHENTICATION_CAPABILITY:
        return
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        return
    session_context_id = arguments.get("session_context_id")
    if not isinstance(session_context_id, str) or not session_context_id.strip():
        return
    if uow.session_contexts.get(session_context_id) is None:
        return
    if result.get("status") == "REAUTHORIZATION_REQUIRED":
        return
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), Mapping) else {}
    established = bool(raw.get("session_established")) and result.get("status") == "SUCCEEDED"
    cookie = None
    if isinstance(ephemeral, Mapping):
        cookie = ephemeral.get("session_cookie")
    if established and isinstance(cookie, str) and cookie and secret_port is not None:
        secret_port.put_session(session_material_reference(session_context_id), cookie)
        uow.session_contexts.set_state(
            session_context_id,
            SessionState.ACTIVE.value,
            established_at=now,
            updated_at=now,
        )
        return
    uow.session_contexts.set_state(
        session_context_id,
        SessionState.FAILED.value,
        updated_at=now,
    )


def expire_session_on_unauthenticated_response(
    uow: UnitOfWork,
    secret_port: CompositeSecretPort | None,
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    now: datetime,
) -> None:
    if request.get("worker_capability") != HTTP_TRANSACTION_CAPABILITY:
        return
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        return
    session_ref = arguments.get("session_context_reference")
    if not isinstance(session_ref, str) or not session_ref.strip():
        return
    raw = result.get("raw_result") if isinstance(result.get("raw_result"), Mapping) else {}
    status_code = raw.get("status_code")
    if status_code not in {401, 403}:
        return
    session = uow.session_contexts.get(session_ref)
    if session is None or session.state != SessionState.ACTIVE.value:
        return
    uow.session_contexts.set_state(
        session_ref,
        SessionState.EXPIRED.value,
        updated_at=now,
    )
    if secret_port is not None:
        secret_port.delete_session(session_material_reference(session_ref))


def revoke_session(
    uow: UnitOfWork,
    session_context_id: str,
    secret_port: CompositeSecretPort | None,
    now: datetime,
) -> None:
    session = uow.session_contexts.get(session_context_id)
    if session is None:
        return
    uow.session_contexts.set_state(
        session_context_id,
        SessionState.REVOKED.value,
        updated_at=now,
    )
    if secret_port is not None:
        secret_port.delete_session(session_material_reference(session_context_id))


def strip_ephemeral_secrets(result: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    cleaned = dict(result)
    ephemeral = cleaned.pop("ephemeral_secrets", None)
    raw = cleaned.get("raw_result")
    if isinstance(raw, Mapping):
        raw_copy = dict(raw)
        raw_copy.pop("_ephemeral_session_cookie", None)
        cleaned["raw_result"] = raw_copy
    if isinstance(ephemeral, Mapping):
        return cleaned, ephemeral
    return cleaned, None
