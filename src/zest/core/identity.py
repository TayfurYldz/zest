"""Actors that may appear in Core evaluation. Models are not authorization principals."""

from dataclasses import dataclass

from zest.core.enums import ActorType
from zest.core.errors import CoreInputError


def require_opaque_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoreInputError(f"{field_name} must be a non-empty opaque id")
    return value


@dataclass(frozen=True)
class Actor:
    actor_id: str
    actor_type: ActorType

    def __post_init__(self) -> None:
        require_opaque_id(self.actor_id, "actor_id")
        if not isinstance(self.actor_type, ActorType):
            raise CoreInputError("actor_type is required")
