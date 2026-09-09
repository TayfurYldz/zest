"""Configured research identities for the global fabric. Never stores secrets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zest.application.identity import new_opaque_id
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.identity_session import (
    CredentialReference,
    HttpFormLoginProfile,
    Identity,
    SessionState,
)
from zest.research.types import ExperimentPlan
from zest.safe_data import reject_secret_keys as reject_secret_structure

RESEARCH_IDENTITIES_CONFIGURED = "RESEARCH_IDENTITIES_CONFIGURED"
SECRET_PAYLOAD_KEYS = frozenset(
    {
        "password",
        "token",
        "cookie",
        "authorization",
        "bearer",
        "secret",
        "mfa",
        "session_cookie",
        "api_token",
    }
)


@dataclass(frozen=True)
class ResearchIdentityCatalog:
    identities: tuple[Identity, ...]
    profiles: tuple[HttpFormLoginProfile, ...]

    def identity(self, identity_id: str) -> Identity | None:
        for item in self.identities:
            if item.identity_id == identity_id:
                return item
        return None

    def identity_by_actor(self, actor_reference: str) -> Identity | None:
        for item in self.identities:
            if item.actor_reference == actor_reference:
                return item
        return None

    def profile(self, profile_id: str) -> HttpFormLoginProfile | None:
        for item in self.profiles:
            if item.profile_id == profile_id:
                return item
        return None


def persist_research_identities(
    uow,
    *,
    research_run_id: str,
    catalog: ResearchIdentityCatalog,
    now: datetime,
    actor_id: str,
) -> None:
    if not catalog.identities and not catalog.profiles:
        return
    payload = catalog_to_payload(catalog)
    reject_secret_structure(payload, "research_identities")
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id=actor_id,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=RESEARCH_IDENTITIES_CONFIGURED,
            subject_type="research_run",
            subject_id=research_run_id,
            payload=payload,
        )
    )


def load_research_identity_catalog(uow, research_run_id: str) -> ResearchIdentityCatalog:
    events = [
        item
        for item in uow.audit_events.list_for_subject("research_run", research_run_id)
        if item.event_type == RESEARCH_IDENTITIES_CONFIGURED
    ]
    if not events:
        return ResearchIdentityCatalog(identities=(), profiles=())
    latest = sorted(events, key=lambda item: item.occurred_at)[-1]
    return catalog_from_payload(latest.payload or {})


def catalog_to_payload(catalog: ResearchIdentityCatalog) -> dict:
    payload = {
        "identities": [
            {
                "identity_id": item.identity_id,
                "actor_reference": item.actor_reference,
                "target_reference": item.target_reference,
                "credential_scheme": item.credential_reference.scheme,
                "credential_name": item.credential_reference.name,
                "authentication_profile_reference": item.authentication_profile_reference,
                "role_reference": item.role_reference,
            }
            for item in catalog.identities
        ],
        "profiles": [
            {
                "profile_id": item.profile_id,
                "path": item.path,
                "username_field": item.username_field,
                "password_secret_name": item.password_secret_name,
                "session_cookie_name": item.session_cookie_name,
                "success_status_codes": list(item.success_status_codes),
                "method": item.method,
            }
            for item in catalog.profiles
        ],
        "not_secret_material": True,
    }
    overlap = SECRET_PAYLOAD_KEYS.intersection(key.lower() for key in _keys(payload))
    if overlap:
        raise ValueError(f"identity catalog must not persist {sorted(overlap)}")
    return payload


def catalog_from_payload(payload: dict) -> ResearchIdentityCatalog:
    identities = []
    for item in payload.get("identities") or ():
        identities.append(
            Identity(
                identity_id=str(item["identity_id"]),
                actor_reference=str(item["actor_reference"]),
                target_reference=str(item["target_reference"]),
                credential_reference=CredentialReference(
                    str(item["credential_scheme"]),
                    str(item["credential_name"]),
                ),
                authentication_profile_reference=str(
                    item["authentication_profile_reference"]
                ),
                role_reference=item.get("role_reference"),
            )
        )
    profiles = []
    for item in payload.get("profiles") or ():
        profiles.append(
            HttpFormLoginProfile(
                profile_id=str(item["profile_id"]),
                path=str(item["path"]),
                username_field=str(item["username_field"]),
                password_secret_name=str(item["password_secret_name"]),
                session_cookie_name=str(item["session_cookie_name"]),
                success_status_codes=tuple(item.get("success_status_codes") or (200,)),
                method=str(item.get("method") or "POST"),
            )
        )
    return ResearchIdentityCatalog(identities=tuple(identities), profiles=tuple(profiles))


def active_session_for_identity(
    uow,
    *,
    research_run_id: str,
    identity_id: str,
    origin: str,
    now: datetime,
) -> object | None:
    matches = [
        item
        for item in uow.session_contexts.list_for_research_run(research_run_id)
        if item.identity_id == identity_id
        and item.origin.rstrip("/") == origin.rstrip("/")
        and item.state == SessionState.ACTIVE.value
    ]
    if not matches:
        return None
    latest = sorted(matches, key=lambda item: item.updated_at)[-1]
    if latest.expires_at is not None and latest.expires_at <= now:
        return None
    return latest


def resolve_identity_for_plan(
    uow, research_run_id: str, plan: ExperimentPlan
) -> tuple[Identity | None, HttpFormLoginProfile | None]:
    catalog = load_research_identity_catalog(uow, research_run_id)
    identity_id = plan.arguments.get("identity_id")
    actor = plan.arguments.get("actor")
    identity = None
    if isinstance(identity_id, str) and identity_id.strip():
        identity = catalog.identity(identity_id)
    if identity is None and isinstance(actor, str) and actor.strip():
        identity = catalog.identity_by_actor(actor)
    if identity is None:
        return None, None
    return identity, catalog.profile(identity.authentication_profile_reference)


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            found.add(str(key))
            found.update(_keys(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found.update(_keys(nested))
    return found
