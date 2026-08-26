"""DiscoveryFact types. OBSERVED|DERIVED only. Not Findings and not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.discovery.types import (
    FORBIDDEN_DISCOVERY_KEYS,
    DiscoveryFactKind,
    DiscoverySourcePlane,
)
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_DISCOVERY_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class DiscoveryFactSourceView:
    """Typed provenance view. Persistence enforces FKs; this is not a SoR row."""

    source_plane: DiscoverySourcePlane | None
    observation_id: str | None = None
    sensor_observation_id: str | None = None
    control_event_id: str | None = None
    source_fact_id: str | None = None
    source_inference_id: str | None = None

    def __post_init__(self) -> None:
        present = [
            self.observation_id is not None,
            self.sensor_observation_id is not None,
            self.control_event_id is not None,
            self.source_fact_id is not None,
            self.source_inference_id is not None,
        ]
        if sum(present) != 1:
            raise ResearchInputError("exactly one primary discovery source is required")
        if self.observation_id is not None:
            _require_text(self.observation_id, "observation_id")
            if self.source_plane is not DiscoverySourcePlane.OBSERVATION:
                raise ResearchInputError("observation source requires OBSERVATION plane")
        if self.sensor_observation_id is not None:
            _require_text(self.sensor_observation_id, "sensor_observation_id")
            if self.source_plane is not DiscoverySourcePlane.OBSERVATION:
                raise ResearchInputError("sensor observation source requires OBSERVATION plane")
        if self.control_event_id is not None:
            _require_text(self.control_event_id, "control_event_id")
            if self.source_plane is not DiscoverySourcePlane.CONTROL_EVENT:
                raise ResearchInputError("control event source requires CONTROL_EVENT plane")
        if self.source_fact_id is not None:
            _require_text(self.source_fact_id, "source_fact_id")
        if self.source_inference_id is not None:
            _require_text(self.source_inference_id, "source_inference_id")


@dataclass(frozen=True)
class DiscoveryFact:
    fact_id: str
    research_run_id: str
    fact_kind: DiscoveryFactKind
    canonical_key: str
    epistemic_status: TargetEpistemicStatus
    identity_id: str
    target_reference: str
    sources: tuple[DiscoveryFactSourceView, ...]
    session_context_id: str | None = None
    normalized_origin: str | None = None
    normalized_path: str | None = None
    http_method: str | None = None
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "fact_id", _require_text(self.fact_id, "fact_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.fact_kind, DiscoveryFactKind):
            raise ResearchInputError("fact_kind must be a DiscoveryFactKind")
        object.__setattr__(
            self, "canonical_key", _require_text(self.canonical_key, "canonical_key")
        )
        if self.epistemic_status not in {
            TargetEpistemicStatus.OBSERVED,
            TargetEpistemicStatus.DERIVED,
        }:
            raise ResearchInputError("DiscoveryFact epistemic_status must be OBSERVED or DERIVED")
        if self.fact_kind is DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE:
            if self.epistemic_status is not TargetEpistemicStatus.DERIVED:
                raise ResearchInputError("SCOPE_BOUNDARY_CANDIDATE must be DERIVED")
        object.__setattr__(self, "identity_id", _require_text(self.identity_id, "identity_id"))
        object.__setattr__(
            self, "target_reference", _require_text(self.target_reference, "target_reference")
        )
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ResearchInputError("sources must be a non-empty tuple")
        if self.session_context_id is not None:
            _require_text(self.session_context_id, "session_context_id")
        if self.normalized_origin is not None:
            _require_text(self.normalized_origin, "normalized_origin")
        if self.normalized_path is not None:
            _require_text(self.normalized_path, "normalized_path")
        if self.http_method is not None:
            _require_text(self.http_method, "http_method")
        if self.attributes is not None:
            object.__setattr__(
                self, "attributes", _reject_forbidden(self.attributes, "attributes")
            )


def object_instance_from_numeric_path_rejected(normalized_path: str) -> bool:
    """Numeric/UUID path tokens are instance candidates, not ObjectInstance truth."""

    del normalized_path
    return True
