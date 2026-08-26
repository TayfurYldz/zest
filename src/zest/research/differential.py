"""Differential reasoning. Difference is not a vulnerability and not Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.target_model import TargetObservationView
from zest.research.temporal import ResearchSnapshot
from zest.research.types import ResearchInputError

DIFFERENTIAL_STRATEGY_VERSION = "differential.diagnostic.echo.v1"
FORBIDDEN_DIFF_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "idor",
        "confidence",
        "evidence",
        "candidate",
        "finding",
        "authorization",
        "token",
        "session_token",
        "password",
    }
)


class DifferentialDimension(Enum):
    ACTOR = "ACTOR"
    ROLE = "ROLE"
    SESSION = "SESSION"
    RESOURCE = "RESOURCE"
    STATE = "STATE"
    ACTION = "ACTION"
    INPUT = "INPUT"
    TIME = "TIME"


class DifferentialInterpretation(Enum):
    CONTROLLED_DIFFERENCE = "CONTROLLED_DIFFERENCE"
    EQUIVALENT = "EQUIVALENT"
    INCOMPARABLE = "INCOMPARABLE"


class DifferentialOutcome(Enum):
    COMPARED = "COMPARED"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_MISSING_SOURCE = "REJECTED_MISSING_SOURCE"
    REJECTED_UNCONTROLLED = "REJECTED_UNCONTROLLED"
    REJECTED_DEFERRED_DIMENSION = "REJECTED_DEFERRED_DIMENSION"
    REJECTED_MISSING_TEMPORAL_PROVENANCE = "REJECTED_MISSING_TEMPORAL_PROVENANCE"
    REJECTED_CROSS_PROGRAM = "REJECTED_CROSS_PROGRAM"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ResearchInputError(f"{field_name} must be a non-empty tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _require_dimensions(value: object, field_name: str) -> tuple[DifferentialDimension, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    dims: list[DifferentialDimension] = []
    for index, item in enumerate(value):
        if not isinstance(item, DifferentialDimension):
            raise ResearchInputError(f"{field_name}[{index}] must be a DifferentialDimension")
        dims.append(item)
    return tuple(dims)


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_DIFF_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class DifferentialCase:
    """Controlled comparison. The comparison must know which dimensions changed."""

    case_id: str
    research_run_id: str
    baseline_observation_ids: tuple[str, ...]
    variant_observation_ids: tuple[str, ...]
    changed_dimensions: tuple[DifferentialDimension, ...]
    common_dimensions: tuple[DifferentialDimension, ...]
    expected_equivalence: str | None = None
    baseline_snapshot_id: str | None = None
    variant_snapshot_id: str | None = None
    strategy_version: str = DIFFERENTIAL_STRATEGY_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _require_text(self.case_id, "case_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self,
            "baseline_observation_ids",
            _require_ids(self.baseline_observation_ids, "baseline_observation_ids"),
        )
        object.__setattr__(
            self,
            "variant_observation_ids",
            _require_ids(self.variant_observation_ids, "variant_observation_ids"),
        )
        object.__setattr__(
            self,
            "changed_dimensions",
            _require_dimensions(self.changed_dimensions, "changed_dimensions"),
        )
        object.__setattr__(
            self,
            "common_dimensions",
            _require_dimensions(self.common_dimensions, "common_dimensions"),
        )
        if self.expected_equivalence is not None:
            object.__setattr__(
                self,
                "expected_equivalence",
                _require_text(self.expected_equivalence, "expected_equivalence"),
            )
        if self.baseline_snapshot_id is not None:
            object.__setattr__(
                self,
                "baseline_snapshot_id",
                _require_text(self.baseline_snapshot_id, "baseline_snapshot_id"),
            )
        if self.variant_snapshot_id is not None:
            object.__setattr__(
                self,
                "variant_snapshot_id",
                _require_text(self.variant_snapshot_id, "variant_snapshot_id"),
            )
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )


@dataclass(frozen=True)
class DifferentialObservation:
    """Structured comparison result. Not Evidence, Candidate, Finding, or vulnerability."""

    differential_id: str
    research_run_id: str
    case_id: str
    baseline_observation_ids: tuple[str, ...]
    variant_observation_ids: tuple[str, ...]
    changed_dimensions: tuple[DifferentialDimension, ...]
    common_dimensions: tuple[DifferentialDimension, ...]
    observed_differences: Mapping[str, Any]
    observed_similarities: Mapping[str, Any]
    interpretation: DifferentialInterpretation
    source_refs: tuple[str, ...]
    strategy_version: str
    alternative_explanation_slots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "differential_id", _require_text(self.differential_id, "differential_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(self, "case_id", _require_text(self.case_id, "case_id"))
        object.__setattr__(
            self,
            "baseline_observation_ids",
            _require_ids(self.baseline_observation_ids, "baseline_observation_ids"),
        )
        object.__setattr__(
            self,
            "variant_observation_ids",
            _require_ids(self.variant_observation_ids, "variant_observation_ids"),
        )
        object.__setattr__(
            self,
            "changed_dimensions",
            _require_dimensions(self.changed_dimensions, "changed_dimensions"),
        )
        object.__setattr__(
            self,
            "common_dimensions",
            _require_dimensions(self.common_dimensions, "common_dimensions"),
        )
        object.__setattr__(
            self,
            "observed_differences",
            _reject_forbidden(self.observed_differences, "observed_differences"),
        )
        object.__setattr__(
            self,
            "observed_similarities",
            _reject_forbidden(self.observed_similarities, "observed_similarities"),
        )
        if not isinstance(self.interpretation, DifferentialInterpretation):
            raise ResearchInputError("interpretation must be a DifferentialInterpretation")
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self,
            "strategy_version",
            _require_text(self.strategy_version, "strategy_version"),
        )
        object.__setattr__(
            self,
            "alternative_explanation_slots",
            tuple(
                _require_text(item, f"alternative_explanation_slots[{index}]")
                for index, item in enumerate(self.alternative_explanation_slots)
            ),
        )


@dataclass(frozen=True)
class DifferentialDecision:
    outcome: DifferentialOutcome
    reason_codes: tuple[str, ...]
    observation: DifferentialObservation | None

    @property
    def compared(self) -> bool:
        return self.outcome is DifferentialOutcome.COMPARED


def _view_by_id(
    views: tuple[TargetObservationView, ...], observation_id: str
) -> TargetObservationView | None:
    for view in views:
        if view.observation_id == observation_id:
            return view
    return None


def _dimension_values(view: TargetObservationView) -> dict[DifferentialDimension, str | None]:
    echoed = view.payload.get("echoed")
    echoed_text = echoed if isinstance(echoed, str) else None
    return {
        DifferentialDimension.ACTOR: view.actor_handle,
        DifferentialDimension.ACTION: f"{view.capability}:{view.action}",
        DifferentialDimension.RESOURCE: view.resource_handle,
        DifferentialDimension.INPUT: view.submitted_input,
        DifferentialDimension.STATE: echoed_text,
        DifferentialDimension.ROLE: None,
        DifferentialDimension.SESSION: None,
        DifferentialDimension.TIME: None,
    }


def compare_diagnostic_differential(
    case: DifferentialCase,
    views: tuple[TargetObservationView, ...],
    *,
    differential_id: str,
    snapshots: tuple[ResearchSnapshot, ...] = (),
) -> DifferentialDecision:
    """Compare two diagnostic observations with explicit changed dimensions."""

    if DifferentialDimension.TIME in case.changed_dimensions:
        if case.baseline_snapshot_id is None or case.variant_snapshot_id is None:
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE,
                reason_codes=("TIME_REQUIRES_SNAPSHOT_PROVENANCE",),
                observation=None,
            )
        by_id = {item.snapshot_id: item for item in snapshots}
        baseline_snap = by_id.get(case.baseline_snapshot_id)
        variant_snap = by_id.get(case.variant_snapshot_id)
        if baseline_snap is None or variant_snap is None:
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE,
                reason_codes=("SNAPSHOT_NOT_RESOLVED",),
                observation=None,
            )
        if baseline_snap.program_id != variant_snap.program_id:
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_CROSS_PROGRAM,
                reason_codes=("CROSS_PROGRAM_SNAPSHOT",),
                observation=None,
            )
        if (
            baseline_snap.research_run_id != case.research_run_id
            or variant_snap.research_run_id != case.research_run_id
        ):
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_CROSS_RUN,
                reason_codes=("CROSS_RUN_SNAPSHOT",),
                observation=None,
            )
        if set(case.baseline_observation_ids) - set(baseline_snap.observation_ids):
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE,
                reason_codes=("BASELINE_NOT_IN_SNAPSHOT",),
                observation=None,
            )
        if set(case.variant_observation_ids) - set(variant_snap.observation_ids):
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE,
                reason_codes=("VARIANT_NOT_IN_SNAPSHOT",),
                observation=None,
            )
        if (
            baseline_snap.observation_ids == variant_snap.observation_ids
            and baseline_snap.target_identity == variant_snap.target_identity
            and baseline_snap.captured_at != variant_snap.captured_at
        ):
            # Timestamp-only difference is not a temporal differential.
            pass
    if not case.changed_dimensions:
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_UNCONTROLLED,
            reason_codes=("CHANGED_DIMENSIONS_REQUIRED",),
            observation=None,
        )
    for view in views:
        if view.research_run_id != case.research_run_id:
            return DifferentialDecision(
                outcome=DifferentialOutcome.REJECTED_CROSS_RUN,
                reason_codes=("CROSS_RUN_SOURCE",),
                observation=None,
            )
    baseline = _view_by_id(views, case.baseline_observation_ids[0])
    variant = _view_by_id(views, case.variant_observation_ids[0])
    if baseline is None or variant is None:
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_MISSING_SOURCE,
            reason_codes=("HALLUCINATED_SOURCE",),
            observation=None,
        )
    if (
        baseline.research_run_id != case.research_run_id
        or variant.research_run_id != case.research_run_id
    ):
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_CROSS_RUN,
            reason_codes=("CROSS_RUN_SOURCE",),
            observation=None,
        )

    left = _dimension_values(baseline)
    right = _dimension_values(variant)
    detected_changed = tuple(
        dim
        for dim in (
            DifferentialDimension.ACTOR,
            DifferentialDimension.ACTION,
            DifferentialDimension.RESOURCE,
            DifferentialDimension.INPUT,
            DifferentialDimension.STATE,
        )
        if left[dim] != right[dim]
    )
    if DifferentialDimension.TIME in case.changed_dimensions and not detected_changed:
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_UNCONTROLLED,
            reason_codes=("TIMESTAMP_ONLY_NOT_TEMPORAL",),
            observation=None,
        )
    undeclared = tuple(
        dim for dim in detected_changed if dim not in case.changed_dimensions
        and dim is not DifferentialDimension.STATE
    )
    if undeclared:
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_UNCONTROLLED,
            reason_codes=("UNDECLARED_CHANGED_DIMENSION",),
            observation=None,
        )
    declared_but_equal = tuple(
        dim
        for dim in case.changed_dimensions
        if dim not in {DifferentialDimension.STATE, DifferentialDimension.TIME}
        and left[dim] == right[dim]
    )
    if declared_but_equal and DifferentialDimension.INPUT in case.changed_dimensions:
        return DifferentialDecision(
            outcome=DifferentialOutcome.REJECTED_UNCONTROLLED,
            reason_codes=("DECLARED_DIMENSION_DID_NOT_CHANGE",),
            observation=None,
        )

    differences: dict[str, Any] = {
        dim.value: {"baseline": left[dim], "variant": right[dim]}
        for dim in detected_changed
    }
    if DifferentialDimension.TIME in case.changed_dimensions:
        differences["TIME"] = {
            "baseline_snapshot_id": case.baseline_snapshot_id,
            "variant_snapshot_id": case.variant_snapshot_id,
            "not_timestamp_only": True,
        }
    similarities: dict[str, Any] = {
        dim.value: left[dim]
        for dim in case.common_dimensions
        if left[dim] == right[dim]
    }
    similarities["not_authorization_proof"] = True
    similarities["not_a_vulnerability"] = True
    if not detected_changed:
        interpretation = DifferentialInterpretation.EQUIVALENT
        reason = ("EQUIVALENT_NOT_AUTHORIZATION",)
    else:
        interpretation = DifferentialInterpretation.CONTROLLED_DIFFERENCE
        reason = ("CONTROLLED_DIAGNOSTIC_DIFFERENCE", "NOT_A_VULNERABILITY")
    observation = DifferentialObservation(
        differential_id=differential_id,
        research_run_id=case.research_run_id,
        case_id=case.case_id,
        baseline_observation_ids=case.baseline_observation_ids,
        variant_observation_ids=case.variant_observation_ids,
        changed_dimensions=case.changed_dimensions,
        common_dimensions=case.common_dimensions,
        observed_differences=differences,
        observed_similarities=similarities,
        interpretation=interpretation,
        source_refs=case.baseline_observation_ids + case.variant_observation_ids,
        strategy_version=case.strategy_version,
        alternative_explanation_slots=(
            "intended_input_difference",
            "runtime_protocol_difference",
            "asynchronous_processing",
        ),
    )
    return DifferentialDecision(
        outcome=DifferentialOutcome.COMPARED,
        reason_codes=reason,
        observation=observation,
    )
