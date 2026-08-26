"""Experiment feedback. Reconstructs what happened. Not a vulnerability verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.types import ResearchInputError


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class ObservedFact:
    """One Observation reference for assessment. Not Evidence."""

    observation_id: str
    observation_kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self,
            "observation_kind",
            _require_text(self.observation_kind, "observation_kind"),
        )
        if not isinstance(self.payload, Mapping):
            raise ResearchInputError("payload must be a mapping")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class ExperimentFeedback:
    """Enough context to ask what happened. Not Hypothesis truth and not Evidence."""

    hypothesis_id: str
    experiment_id: str
    research_run_id: str
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    execution_outcome: str
    observations: tuple[ObservedFact, ...]
    submitted_value: str | None = None
    invocation_status: str | None = None
    experiment_execution_state: str | None = None
    attempt_state: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
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
        object.__setattr__(
            self,
            "execution_outcome",
            _require_text(self.execution_outcome, "execution_outcome"),
        )
        if not isinstance(self.observations, tuple):
            raise ResearchInputError("observations must be a tuple")

    @property
    def observation_ids(self) -> tuple[str, ...]:
        return tuple(item.observation_id for item in self.observations)
