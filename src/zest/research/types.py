"""Research-layer types. Not Worker wire contracts.

Hypothesis is not fact. ExperimentPlan is not authorization. Observation is not
Evidence. These types must not grow severity, confidence, or Finding fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ResearchInputError(ValueError):
    """Invalid Research proposal input. Not a Core DENY."""


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value


def _require_side_effect_level(value: object) -> int:
    if value not in (0, 1, 2, 3):
        raise ResearchInputError("side_effect_level must be 0, 1, 2, or 3")
    return int(value)


@dataclass(frozen=True)
class HypothesisDraft:
    """Human-seeded claim to test. Not fact, Evidence, Candidate, or Finding."""

    statement: str
    origin: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        object.__setattr__(self, "origin", _require_text(self.origin, "origin"))


@dataclass(frozen=True)
class ExperimentPlan:
    """Proposed Worker invocation. Not a WorkerRequest and not Core ALLOW."""

    hypothesis_id: str
    required_capability: str
    action: str
    target_reference: str
    side_effect_level: int
    arguments: Mapping[str, Any]
    requested_budget_id: str
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    capability_version: str | None = None
    capability_definition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self,
            "required_capability",
            _require_text(self.required_capability, "required_capability"),
        )
        object.__setattr__(self, "action", _require_text(self.action, "action"))
        object.__setattr__(
            self,
            "target_reference",
            _require_text(self.target_reference, "target_reference"),
        )
        object.__setattr__(
            self,
            "requested_budget_id",
            _require_text(self.requested_budget_id, "requested_budget_id"),
        )
        object.__setattr__(
            self, "side_effect_level", _require_side_effect_level(self.side_effect_level)
        )
        object.__setattr__(
            self,
            "expected_observation",
            _require_text(self.expected_observation, "expected_observation"),
        )
        object.__setattr__(
            self,
            "disconfirming_observation",
            _require_text(self.disconfirming_observation, "disconfirming_observation"),
        )
        object.__setattr__(
            self,
            "evaluation_strategy",
            _require_text(self.evaluation_strategy, "evaluation_strategy"),
        )
        if not isinstance(self.arguments, Mapping):
            raise ResearchInputError("arguments must be a mapping")
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.capability_version is not None:
            object.__setattr__(
                self,
                "capability_version",
                _require_text(self.capability_version, "capability_version"),
            )
        if self.capability_definition_fingerprint is not None:
            object.__setattr__(
                self,
                "capability_definition_fingerprint",
                _require_text(
                    self.capability_definition_fingerprint,
                    "capability_definition_fingerprint",
                ),
            )
