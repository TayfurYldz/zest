"""Compile untrusted ExperimentIntent into a bound ExperimentPlan. Not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.types import ExperimentPlan, ResearchInputError
from zest.tools.http_transaction_policy import extra_argument_validator_for
from zest.tools.registry import (
    WORKER_EXECUTOR_CLASS,
    CapabilityRegistry,
    load_capability_registry,
    validate_action_arguments,
)

SUPPORTED_TARGET_TYPES = frozenset({"opaque_reference", "http_origin"})


class ExperimentCompileError(ResearchInputError):
    """Compile reject. Not a Core DENY and not a WorkerResult."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class ExperimentIntent:
    """Untrusted research proposal. Not an ExperimentPlan and not Core ALLOW."""

    hypothesis_id: str
    capability_id: str
    action: str
    target_reference: str
    arguments: Mapping[str, Any]
    requested_budget_id: str
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    requested_side_effect: int | None = None
    target_type: str = "opaque_reference"

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _text(self.hypothesis_id, "hypothesis_id"))
        object.__setattr__(self, "capability_id", _text(self.capability_id, "capability_id"))
        object.__setattr__(self, "action", _text(self.action, "action"))
        object.__setattr__(
            self, "target_reference", _text(self.target_reference, "target_reference")
        )
        object.__setattr__(
            self,
            "requested_budget_id",
            _text(self.requested_budget_id, "requested_budget_id"),
        )
        object.__setattr__(
            self,
            "expected_observation",
            _text(self.expected_observation, "expected_observation"),
        )
        object.__setattr__(
            self,
            "disconfirming_observation",
            _text(self.disconfirming_observation, "disconfirming_observation"),
        )
        object.__setattr__(
            self,
            "evaluation_strategy",
            _text(self.evaluation_strategy, "evaluation_strategy"),
        )
        if not isinstance(self.arguments, Mapping):
            raise ResearchInputError("arguments must be a mapping")
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.requested_side_effect is not None and self.requested_side_effect not in (
            0,
            1,
            2,
            3,
        ):
            raise ResearchInputError("requested_side_effect must be 0, 1, 2, 3, or None")
        object.__setattr__(self, "target_type", _text(self.target_type, "target_type"))


def compile_experiment_intent(
    intent: ExperimentIntent,
    *,
    registry: CapabilityRegistry | None = None,
) -> ExperimentPlan:
    """Bind capability/action/version/fingerprint and derive side effect. Does not authorize."""

    if not isinstance(intent, ExperimentIntent):
        raise ExperimentCompileError("INVALID_INTENT", "intent must be ExperimentIntent")
    catalog = registry if registry is not None else load_capability_registry()
    definition = catalog.get(intent.capability_id)
    if definition is None or definition.executor_class != WORKER_EXECUTOR_CLASS:
        raise ExperimentCompileError("UNKNOWN_CAPABILITY", "unknown capability")
    action = definition.action(intent.action)
    if action is None:
        raise ExperimentCompileError("UNKNOWN_ACTION", "unknown action")
    if intent.target_type not in action.target_types:
        raise ExperimentCompileError("WRONG_TARGET_TYPE", "target type is not allowed")
    if intent.target_type not in SUPPORTED_TARGET_TYPES:
        raise ExperimentCompileError("WRONG_TARGET_TYPE", "unsupported target type")
    if intent.target_type == "http_origin" and "://" not in intent.target_reference:
        raise ExperimentCompileError("WRONG_TARGET_TYPE", "http_origin target is not a URL")
    for requirement in action.requirements:
        if requirement not in {"loopback", "scope_derived"}:
            raise ExperimentCompileError(
                "UNSUPPORTED_REQUIREMENT", f"unsupported requirement {requirement}"
            )
    issue = validate_action_arguments(action.argument_schema, intent.arguments)
    if issue is not None:
        raise ExperimentCompileError(issue.reason_code, issue.message)
    extra = extra_argument_validator_for(definition.capability_id)
    if extra is not None:
        extra_issue = extra(action.action_id, intent.arguments)
        if extra_issue is not None:
            raise ExperimentCompileError(extra_issue.reason_code, extra_issue.message)
    if intent.requested_side_effect is not None:
        if intent.requested_side_effect < action.minimum_side_effect_level:
            raise ExperimentCompileError(
                "RISK_UNDERSTATEMENT", "requested side effect is below action minimum"
            )
        if intent.requested_side_effect > action.maximum_side_effect_level:
            raise ExperimentCompileError(
                "RISK_EXCEEDS_CAPABILITY", "requested side effect exceeds action maximum"
            )
    effective = (
        intent.requested_side_effect
        if intent.requested_side_effect is not None
        else action.minimum_side_effect_level
    )
    return ExperimentPlan(
        hypothesis_id=intent.hypothesis_id,
        required_capability=definition.capability_id,
        action=action.action_id,
        target_reference=intent.target_reference,
        side_effect_level=effective,
        arguments=dict(intent.arguments),
        requested_budget_id=intent.requested_budget_id,
        expected_observation=intent.expected_observation,
        disconfirming_observation=intent.disconfirming_observation,
        evaluation_strategy=intent.evaluation_strategy,
        capability_version=definition.version,
        capability_definition_fingerprint=definition.definition_fingerprint,
    )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()
