"""Operational health states. Health is not research truth and must not contain secrets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ComponentHealth(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HealthCheck:
    component: str
    health: ComponentHealth
    detail: str
    contains_secrets: bool = False

    def __post_init__(self) -> None:
        if self.contains_secrets:
            raise ValueError("health checks must not contain secrets")
        if not isinstance(self.component, str) or not self.component.strip():
            raise ValueError("component must be a non-empty string")
        if not isinstance(self.detail, str):
            raise ValueError("detail must be a string")

    def to_mapping(self) -> dict[str, object]:
        return {
            "component": self.component,
            "health": self.health.value,
            "detail": self.detail,
            "contains_secrets": False,
            "not_research_truth": True,
        }
