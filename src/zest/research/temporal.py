"""Temporal Intelligence. Target state at t1 is not target state at t2. Change is not a vulnerability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from zest.research.target_model import TargetObservationView
from zest.research.types import ResearchInputError

TEMPORAL_STRATEGY_VERSION = "temporal.diagnostic.echo.v1"
FORBIDDEN_TEMPORAL_KEYS = frozenset(
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


class ChangeCategory(Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    RELATION_CHANGED = "RELATION_CHANGED"
    STATE_CHANGED = "STATE_CHANGED"
    BEHAVIOR_CHANGED = "BEHAVIOR_CHANGED"
    UNKNOWN_CHANGE = "UNKNOWN_CHANGE"


class SnapshotOutcome(Enum):
    CAPTURED = "CAPTURED"
    REJECTED_EMPTY = "REJECTED_EMPTY"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"


class ChangeOutcome(Enum):
    COMPARED = "COMPARED"
    EQUIVALENT_NO_CHANGE = "EQUIVALENT_NO_CHANGE"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_CROSS_PROGRAM = "REJECTED_CROSS_PROGRAM"
    REJECTED_INCOMPATIBLE_TARGET = "REJECTED_INCOMPATIBLE_TARGET"
    REJECTED_INCOMPATIBLE_STRATEGY = "REJECTED_INCOMPATIBLE_STRATEGY"
    REJECTED_UNORDERED = "REJECTED_UNORDERED"
    REJECTED_MISSING_SOURCE = "REJECTED_MISSING_SOURCE"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ResearchInputError(f"{field_name} must be a non-empty tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_TEMPORAL_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class ResearchSnapshot:
    """Point-in-time research view by reference. Not a second SoR and not a Finding."""

    snapshot_id: str
    research_run_id: str
    program_id: str
    target_identity: str
    observation_ids: tuple[str, ...]
    captured_at: datetime
    strategy_version: str
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _require_text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(self, "program_id", _require_text(self.program_id, "program_id"))
        object.__setattr__(
            self, "target_identity", _require_text(self.target_identity, "target_identity")
        )
        object.__setattr__(
            self, "observation_ids", _require_ids(self.observation_ids, "observation_ids")
        )
        if not isinstance(self.captured_at, datetime) or self.captured_at.tzinfo is None:
            raise ResearchInputError("captured_at must be a timezone-aware datetime")
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )
        if self.attributes is None:
            object.__setattr__(self, "attributes", {"not_a_vulnerability": True})
        else:
            object.__setattr__(self, "attributes", _reject_forbidden(self.attributes, "attributes"))


@dataclass(frozen=True)
class ChangeEvent:
    """Deterministic snapshot delta. Not Evidence, Candidate, Finding, or a vulnerability."""

    change_event_id: str
    research_run_id: str
    baseline_snapshot_id: str
    variant_snapshot_id: str
    category: ChangeCategory
    statement: str
    source_refs: tuple[str, ...]
    strategy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "change_event_id", _require_text(self.change_event_id, "change_event_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        object.__setattr__(
            self,
            "baseline_snapshot_id",
            _require_text(self.baseline_snapshot_id, "baseline_snapshot_id"),
        )
        object.__setattr__(
            self,
            "variant_snapshot_id",
            _require_text(self.variant_snapshot_id, "variant_snapshot_id"),
        )
        if not isinstance(self.category, ChangeCategory):
            raise ResearchInputError("category must be a ChangeCategory")
        if self.category.value == "VULNERABILITY_INTRODUCED":
            raise ResearchInputError("vulnerability introduction is not a ChangeEvent category")
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )
        if "vulnerability" in self.statement.lower():
            raise ResearchInputError("ChangeEvent must not call a change a vulnerability")


@dataclass(frozen=True)
class ChangeDecision:
    outcome: ChangeOutcome
    reason_codes: tuple[str, ...]
    change_event: ChangeEvent | None

    @property
    def compared(self) -> bool:
        return self.outcome is ChangeOutcome.COMPARED


def _view_by_id(
    views: tuple[TargetObservationView, ...], observation_id: str
) -> TargetObservationView | None:
    for view in views:
        if view.observation_id == observation_id:
            return view
    return None


def _digest(
    snapshot: ResearchSnapshot, views: tuple[TargetObservationView, ...]
) -> tuple[tuple[str, str | None, str | None], ...]:
    rows: list[tuple[str, str | None, str | None]] = []
    for observation_id in snapshot.observation_ids:
        view = _view_by_id(views, observation_id)
        echoed = None
        submitted = None
        if view is not None:
            raw = view.payload.get("echoed")
            echoed = raw if isinstance(raw, str) else None
            submitted = view.submitted_input
        rows.append((observation_id, submitted, echoed))
    return tuple(rows)


def capture_diagnostic_snapshot(
    *,
    snapshot_id: str,
    research_run_id: str,
    program_id: str,
    target_identity: str,
    observation_ids: tuple[str, ...],
    captured_at: datetime,
    views: tuple[TargetObservationView, ...],
) -> tuple[SnapshotOutcome, ResearchSnapshot | None, tuple[str, ...]]:
    """Reference-only diagnostic snapshot. Does not copy SoR payloads as authority."""

    run_id = _require_text(research_run_id, "research_run_id")
    if not observation_ids:
        return SnapshotOutcome.REJECTED_EMPTY, None, ("EMPTY_SNAPSHOT",)
    for observation_id in observation_ids:
        view = _view_by_id(views, observation_id)
        if view is None:
            return SnapshotOutcome.REJECTED_EMPTY, None, ("HALLUCINATED_OBSERVATION",)
        if view.research_run_id != run_id:
            return SnapshotOutcome.REJECTED_CROSS_RUN, None, ("CROSS_RUN_SOURCE",)
    snapshot = ResearchSnapshot(
        snapshot_id=snapshot_id,
        research_run_id=run_id,
        program_id=_require_text(program_id, "program_id"),
        target_identity=target_identity,
        observation_ids=observation_ids,
        captured_at=captured_at,
        strategy_version=TEMPORAL_STRATEGY_VERSION,
        attributes={"not_a_vulnerability": True, "not_full_sor_copy": True},
    )
    return SnapshotOutcome.CAPTURED, snapshot, ("SNAPSHOT_CAPTURED",)


def compare_diagnostic_snapshots(
    baseline: ResearchSnapshot,
    variant: ResearchSnapshot,
    views: tuple[TargetObservationView, ...],
    *,
    change_event_id: str,
) -> ChangeDecision:
    """Deterministic diagnostic delta. Change is not a vulnerability."""

    if baseline.program_id != variant.program_id:
        return ChangeDecision(
            outcome=ChangeOutcome.REJECTED_CROSS_PROGRAM,
            reason_codes=("CROSS_PROGRAM_SNAPSHOT",),
            change_event=None,
        )
    if baseline.research_run_id != variant.research_run_id:
        return ChangeDecision(
            outcome=ChangeOutcome.REJECTED_CROSS_RUN,
            reason_codes=("CROSS_RUN_SNAPSHOT",),
            change_event=None,
        )
    if baseline.target_identity != variant.target_identity:
        return ChangeDecision(
            outcome=ChangeOutcome.REJECTED_INCOMPATIBLE_TARGET,
            reason_codes=("TARGET_IDENTITY_MISMATCH",),
            change_event=None,
        )
    if baseline.strategy_version != variant.strategy_version:
        return ChangeDecision(
            outcome=ChangeOutcome.REJECTED_INCOMPATIBLE_STRATEGY,
            reason_codes=("STRATEGY_VERSION_MISMATCH",),
            change_event=None,
        )
    if variant.captured_at <= baseline.captured_at:
        return ChangeDecision(
            outcome=ChangeOutcome.REJECTED_UNORDERED,
            reason_codes=("VARIANT_NOT_AFTER_BASELINE",),
            change_event=None,
        )
    for observation_id in baseline.observation_ids + variant.observation_ids:
        if _view_by_id(views, observation_id) is None:
            return ChangeDecision(
                outcome=ChangeOutcome.REJECTED_MISSING_SOURCE,
                reason_codes=("HALLUCINATED_OBSERVATION",),
                change_event=None,
            )
    left = _digest(baseline, views)
    right = _digest(variant, views)
    if left == right:
        return ChangeDecision(
            outcome=ChangeOutcome.EQUIVALENT_NO_CHANGE,
            reason_codes=("NO_MATERIAL_CHANGE", "NOT_A_VULNERABILITY"),
            change_event=None,
        )
    left_ids = set(baseline.observation_ids)
    right_ids = set(variant.observation_ids)
    left_echo = {item[0]: item[2] for item in left}
    right_echo = {item[0]: item[2] for item in right}
    if left_ids != right_ids and left_echo != right_echo:
        category = ChangeCategory.BEHAVIOR_CHANGED
        statement = (
            "Diagnostic echo behavior changed between snapshot t1 and t2. "
            "This is not a security issue."
        )
    elif left_ids < right_ids:
        category = ChangeCategory.ADDED
        statement = "A diagnostic observation was added between snapshot t1 and t2."
    elif right_ids < left_ids:
        category = ChangeCategory.REMOVED
        statement = "A diagnostic observation was removed between snapshot t1 and t2."
    elif left_echo != right_echo:
        category = ChangeCategory.STATE_CHANGED
        statement = "Diagnostic echoed state changed between snapshot t1 and t2."
    else:
        category = ChangeCategory.MODIFIED
        statement = "Diagnostic snapshot members changed between t1 and t2."
    event = ChangeEvent(
        change_event_id=change_event_id,
        research_run_id=baseline.research_run_id,
        baseline_snapshot_id=baseline.snapshot_id,
        variant_snapshot_id=variant.snapshot_id,
        category=category,
        statement=statement,
        source_refs=baseline.observation_ids + variant.observation_ids,
        strategy_version=TEMPORAL_STRATEGY_VERSION,
    )
    return ChangeDecision(
        outcome=ChangeOutcome.COMPARED,
        reason_codes=("DETERMINISTIC_DIAGNOSTIC_DELTA", "NOT_A_VULNERABILITY"),
        change_event=event,
    )
