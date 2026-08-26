"""Composition-root secret references. Values never enter ResearchContext or reports."""

from __future__ import annotations

from dataclasses import dataclass
from os import environ

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GOOGLE_API_KEY_ENV = "GOOGLE_API_KEY"
OPENAI_MODEL_ENV = "ZEST_OPENAI_MODEL"
ANTHROPIC_MODEL_ENV = "ZEST_ANTHROPIC_MODEL"
GEMINI_MODEL_ENV = "ZEST_GEMINI_MODEL"

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class SecretReference:
    env_name: str

    def present(self, env: dict[str, str] | None = None) -> bool:
        source = environ if env is None else env
        value = source.get(self.env_name)
        return isinstance(value, str) and bool(value.strip())

    def value(self, env: dict[str, str] | None = None) -> str | None:
        source = environ if env is None else env
        value = source.get(self.env_name)
        if not isinstance(value, str) or not value.strip():
            return None
        return value


def gemini_key_reference() -> SecretReference:
    if SecretReference(GEMINI_API_KEY_ENV).present():
        return SecretReference(GEMINI_API_KEY_ENV)
    return SecretReference(GOOGLE_API_KEY_ENV)


def redact_secret(message: str, secret: str | None) -> str:
    if not secret:
        return message
    return message.replace(secret, REDACTED)
