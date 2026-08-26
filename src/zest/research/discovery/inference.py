"""Admitted discovery inferences. Never OBSERVED. Cross-run sources fail closed."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.discovery.types import (
    FORBIDDEN_DISCOVERY_KEYS,
    DiscoveryInferenceKind,
)
from zest.research.target_model import TargetEpistemicStatus, TargetInferenceOutcome
from zest.research.types import ResearchInputError


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


class DiscoveryInferenceAdmissionOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_EPISTEMIC_UPGRADE = "REJECTED_EPISTEMIC_UPGRADE"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_INSUFFICIENT_EVIDENCE = "REJECTED_INSUFFICIENT_EVIDENCE"
    REJECTED_FORBIDDEN_KEYS = "REJECTED_FORBIDDEN_KEYS"


@dataclass(frozen=True)
class DiscoveryInferenceDraft:
    research_run_id: str
    inference_kind: DiscoveryInferenceKind
    canonical_key: str
    epistemic_status: TargetEpistemicStatus
    identity_id: str
    source_run_ids: tuple[str, ...]
    source_fact_ids: tuple[str, ...] = ()
    source_inference_ids: tuple[str, ...] = ()
    source_observation_ids: tuple[str, ...] = ()
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.inference_kind, DiscoveryInferenceKind):
            raise ResearchInputError("inference_kind must be a DiscoveryInferenceKind")
        object.__setattr__(
            self, "canonical_key", _require_text(self.canonical_key, "canonical_key")
        )
        if not isinstance(self.epistemic_status, TargetEpistemicStatus):
            raise ResearchInputError("epistemic_status must be TargetEpistemicStatus")
        object.__setattr__(self, "identity_id", _require_text(self.identity_id, "identity_id"))
        if not isinstance(self.source_run_ids, tuple) or not self.source_run_ids:
            raise ResearchInputError("source_run_ids must be a non-empty tuple")


@dataclass(frozen=True)
class DiscoveryInference:
    inference_id: str
    research_run_id: str
    inference_kind: DiscoveryInferenceKind
    canonical_key: str
    epistemic_status: TargetEpistemicStatus
    identity_id: str
    source_fact_ids: tuple[str, ...]
    source_inference_ids: tuple[str, ...]
    source_observation_ids: tuple[str, ...]
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "inference_id", _require_text(self.inference_id, "inference_id"))
        if self.epistemic_status not in {
            TargetEpistemicStatus.INFERRED,
            TargetEpistemicStatus.HYPOTHESIZED,
        }:
            raise ResearchInputError("DiscoveryInference cannot be OBSERVED or DERIVED")


@dataclass(frozen=True)
class DiscoveryInferenceDecision:
    outcome: DiscoveryInferenceAdmissionOutcome
    inference: DiscoveryInference | None
    reason_codes: tuple[str, ...]

    @property
    def admitted(self) -> bool:
        return self.outcome is DiscoveryInferenceAdmissionOutcome.ADMITTED


def admit_discovery_inference(
    draft: DiscoveryInferenceDraft,
    *,
    inference_id: str,
) -> DiscoveryInferenceDecision:
    if draft.epistemic_status in {
        TargetEpistemicStatus.OBSERVED,
        TargetEpistemicStatus.DERIVED,
    }:
        return DiscoveryInferenceDecision(
            outcome=DiscoveryInferenceAdmissionOutcome.REJECTED_EPISTEMIC_UPGRADE,
            inference=None,
            reason_codes=(TargetInferenceOutcome.REJECTED_EPISTEMIC_UPGRADE.value,),
        )
    if any(run_id != draft.research_run_id for run_id in draft.source_run_ids):
        return DiscoveryInferenceDecision(
            outcome=DiscoveryInferenceAdmissionOutcome.REJECTED_CROSS_RUN,
            inference=None,
            reason_codes=(TargetInferenceOutcome.REJECTED_CROSS_RUN.value,),
        )
    attributes = dict(draft.attributes) if draft.attributes is not None else None
    if attributes is not None:
        found = FORBIDDEN_DISCOVERY_KEYS.intersection(attributes.keys())
        if found:
            return DiscoveryInferenceDecision(
                outcome=DiscoveryInferenceAdmissionOutcome.REJECTED_FORBIDDEN_KEYS,
                inference=None,
                reason_codes=("FORBIDDEN_ATTRIBUTE_KEYS",),
            )
    if draft.inference_kind is DiscoveryInferenceKind.ROUTE_TEMPLATE:
        paths = attributes.get("exact_paths") if attributes else None
        if not isinstance(paths, list) or len(paths) < 3:
            return DiscoveryInferenceDecision(
                outcome=DiscoveryInferenceAdmissionOutcome.REJECTED_INSUFFICIENT_EVIDENCE,
                inference=None,
                reason_codes=("ROUTE_TEMPLATE_REQUIRES_THREE_PATHS",),
            )
    inference = DiscoveryInference(
        inference_id=_require_text(inference_id, "inference_id"),
        research_run_id=draft.research_run_id,
        inference_kind=draft.inference_kind,
        canonical_key=draft.canonical_key,
        epistemic_status=draft.epistemic_status,
        identity_id=draft.identity_id,
        source_fact_ids=draft.source_fact_ids,
        source_inference_ids=draft.source_inference_ids,
        source_observation_ids=draft.source_observation_ids,
        attributes=attributes,
    )
    return DiscoveryInferenceDecision(
        outcome=DiscoveryInferenceAdmissionOutcome.ADMITTED,
        inference=inference,
        reason_codes=(),
    )
