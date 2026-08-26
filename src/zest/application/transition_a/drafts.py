"""Transition A drafts. Not Evidence. Not persisted Observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from zest.data.errors import PersistenceInputError
from zest.data.records import require_aware_datetime, require_opaque_id


@dataclass(frozen=True)
class ObservationDraft:
    """Deterministic normalized fact. Authoritative only after Data persistence."""

    observation_kind: str
    payload: Mapping[str, Any]
    normalization_version: str
    observed_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.observation_kind, "observation_kind")
        if not isinstance(self.normalization_version, str) or not self.normalization_version.strip():
            raise PersistenceInputError("normalization_version must be a non-empty string")
        require_aware_datetime(self.observed_at, "observed_at")
        if not isinstance(self.payload, Mapping):
            raise PersistenceInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))
        forbidden = {
            "severity",
            "confidence",
            "vulnerability_type",
            "impact",
            "evidence_status",
            "finding_status",
            "candidate_status",
        }
        overlap = forbidden.intersection(self.payload.keys())
        if overlap:
            raise PersistenceInputError(
                f"ObservationDraft payload must not carry {sorted(overlap)}"
            )
