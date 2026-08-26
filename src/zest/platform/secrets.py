"""SecretPort boundary. Values are never SoR, Evidence, ResearchContext, or AuditEvent.

ENV_REFERENCE is not a production secret manager. LOCAL_DEV is for fixtures.
Do not scrape OAuth/CLI tokens from another runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from os import environ
from typing import Mapping, Protocol


class SecretScheme(Enum):
    LOCAL_DEV = "LOCAL_DEV"
    ENV_REFERENCE = "ENV_REFERENCE"
    SESSION_MATERIAL = "SESSION_MATERIAL"


class SecretResolutionStatus(Enum):
    RESOLVED = "RESOLVED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_REFERENCE = "INVALID_REFERENCE"


@dataclass(frozen=True)
class SecretReference:
    """Handle only. Never serialize the secret value into domain records."""

    scheme: SecretScheme
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.scheme, SecretScheme):
            raise ValueError("scheme must be SecretScheme")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        object.__setattr__(self, "name", self.name.strip())

    def to_mapping(self) -> dict[str, str]:
        return {"scheme": self.scheme.value, "name": self.name}


@dataclass(frozen=True)
class SecretResolution:
    status: SecretResolutionStatus
    reference: SecretReference
    value: str | None = None

    def __post_init__(self) -> None:
        if self.status is not SecretResolutionStatus.RESOLVED and self.value is not None:
            raise ValueError("unavailable secrets must not carry a value")
        if self.status is SecretResolutionStatus.RESOLVED:
            if not isinstance(self.value, str) or not self.value:
                raise ValueError("resolved secret value is required")


class SecretPort(Protocol):
    def resolve(self, reference: SecretReference) -> SecretResolution: ...


class EnvSecretResolver:
    """Resolves ENV_REFERENCE / LOCAL_DEV from a mapping. Not a production vault."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = environ if env is None else env

    def resolve(self, reference: SecretReference) -> SecretResolution:
        if reference.scheme not in {SecretScheme.ENV_REFERENCE, SecretScheme.LOCAL_DEV}:
            return SecretResolution(
                SecretResolutionStatus.INVALID_REFERENCE, reference, None
            )
        raw = self._env.get(reference.name)
        if not isinstance(raw, str) or not raw.strip():
            return SecretResolution(SecretResolutionStatus.UNAVAILABLE, reference, None)
        return SecretResolution(SecretResolutionStatus.RESOLVED, reference, raw)


class UnavailableSecretResolver:
    """Fail-closed resolver used when no adapter is configured."""

    def resolve(self, reference: SecretReference) -> SecretResolution:
        return SecretResolution(SecretResolutionStatus.UNAVAILABLE, reference, None)


class InMemorySecretStore:
    """Process-local secret material. Not durable across restart. Not SoR."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def put(self, reference: SecretReference, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value is required")
        self._values[(reference.scheme.value, reference.name)] = value

    def delete(self, reference: SecretReference) -> None:
        self._values.pop((reference.scheme.value, reference.name), None)

    def resolve(self, reference: SecretReference) -> SecretResolution:
        value = self._values.get((reference.scheme.value, reference.name))
        if not isinstance(value, str) or not value:
            return SecretResolution(SecretResolutionStatus.UNAVAILABLE, reference, None)
        return SecretResolution(SecretResolutionStatus.RESOLVED, reference, value)


class CompositeSecretPort:
    """Resolve session material first, then env/local-dev. Never fabricates values."""

    def __init__(self, session_store: InMemorySecretStore, env: SecretPort | None = None) -> None:
        self._session_store = session_store
        self._env = env or EnvSecretResolver()

    def put_session(self, reference: SecretReference, value: str) -> None:
        if reference.scheme is not SecretScheme.SESSION_MATERIAL:
            raise ValueError("only SESSION_MATERIAL may be stored as session material")
        self._session_store.put(reference, value)

    def delete_session(self, reference: SecretReference) -> None:
        if reference.scheme is not SecretScheme.SESSION_MATERIAL:
            raise ValueError("only SESSION_MATERIAL may be deleted as session material")
        self._session_store.delete(reference)

    def resolve(self, reference: SecretReference) -> SecretResolution:
        if reference.scheme is SecretScheme.SESSION_MATERIAL:
            return self._session_store.resolve(reference)
        return self._env.resolve(reference)
