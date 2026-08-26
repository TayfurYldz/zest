"""Live coverage-debt change projection. Change is not Evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from zest.research.types import ResearchInputError

FORBIDDEN_LIVE_COVERAGE_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "evidence",
        "candidate",
        "finding",
        "token",
        "session_token",
        "password",
        "raw_request",
        "raw_response",
        "body",
    }
)


@dataclass(frozen=True)
class CoverageDebtSnapshotView:
    """Durable coverage-debt summary. The full matrix rebuilds from the ledger."""

    snapshot_id: str
    research_run_id: str
    matrix_hash: str
    cell_counts: Mapping[str, Any]
    total_debt: int
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.snapshot_id, "snapshot_id")
        _require_text(self.research_run_id, "research_run_id")
        if not isinstance(self.matrix_hash, str) or len(self.matrix_hash) != 64:
            raise ResearchInputError("matrix_hash must be a SHA-256 hex digest")
        if not isinstance(self.cell_counts, Mapping):
            raise ResearchInputError("cell_counts must be a mapping")
        _reject_forbidden(self.cell_counts, "cell_counts")
        if (
            not isinstance(self.total_debt, int)
            or isinstance(self.total_debt, bool)
            or self.total_debt < 0
        ):
            raise ResearchInputError("total_debt must be a non-negative int")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ResearchInputError("created_at must be a timezone-aware datetime")


@dataclass(frozen=True)
class CoverageChangeEventView:
    """Temporal source for coverage refresh. Not a vulnerability signal."""

    change_event_id: str
    research_run_id: str
    category: str
    statement: str
    source_refs: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_text(self.change_event_id, "change_event_id")
        _require_text(self.research_run_id, "research_run_id")
        _require_text(self.category, "category")
        statement = _require_text(self.statement, "statement")
        if "vulnerability" in statement.lower():
            raise ResearchInputError("coverage change input must not label vulnerability truth")
        if not isinstance(self.source_refs, tuple):
            raise ResearchInputError("source_refs must be a tuple")
        for index, source_ref in enumerate(self.source_refs):
            _require_text(source_ref, f"source_refs[{index}]")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ResearchInputError("created_at must be a timezone-aware datetime")


@dataclass(frozen=True)
class LiveCoverageDebtImpact:
    """Operator-facing coverage delta. Cannot promote to Evidence/Candidate/Finding."""

    research_run_id: str
    previous_snapshot_id: str | None
    current_snapshot_id: str
    previous_matrix_hash: str | None
    current_matrix_hash: str
    total_debt_before: int | None
    total_debt_after: int
    total_debt_delta: int | None
    cell_count_delta: Mapping[str, int]
    change_event_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    not_a_vulnerability: bool = True
    not_evidence: bool = True
    not_candidate: bool = True
    not_finding: bool = True

    def __post_init__(self) -> None:
        _require_text(self.research_run_id, "research_run_id")
        _require_text(self.current_snapshot_id, "current_snapshot_id")
        if self.previous_snapshot_id is not None:
            _require_text(self.previous_snapshot_id, "previous_snapshot_id")
        if self.previous_matrix_hash is not None and len(self.previous_matrix_hash) != 64:
            raise ResearchInputError("previous_matrix_hash must be a SHA-256 hex digest or None")
        if not isinstance(self.current_matrix_hash, str) or len(self.current_matrix_hash) != 64:
            raise ResearchInputError("current_matrix_hash must be a SHA-256 hex digest")
        if self.total_debt_before is not None and (
            not isinstance(self.total_debt_before, int)
            or isinstance(self.total_debt_before, bool)
            or self.total_debt_before < 0
        ):
            raise ResearchInputError("total_debt_before must be a non-negative int or None")
        if (
            not isinstance(self.total_debt_after, int)
            or isinstance(self.total_debt_after, bool)
            or self.total_debt_after < 0
        ):
            raise ResearchInputError("total_debt_after must be a non-negative int")
        if not isinstance(self.cell_count_delta, Mapping):
            raise ResearchInputError("cell_count_delta must be a mapping")
        for key, value in self.cell_count_delta.items():
            _require_text(key, "cell_count_delta key")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ResearchInputError("cell_count_delta values must be ints")
        if not isinstance(self.change_event_ids, tuple):
            raise ResearchInputError("change_event_ids must be a tuple")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ResearchInputError("reason_codes must be a non-empty tuple")
        if not (
            self.not_a_vulnerability
            and self.not_evidence
            and self.not_candidate
            and self.not_finding
        ):
            raise ResearchInputError("live coverage impact cannot be promoted to a finding class")


def assess_live_coverage_debt(
    *,
    current: CoverageDebtSnapshotView,
    previous: CoverageDebtSnapshotView | None,
    change_events: tuple[CoverageChangeEventView, ...],
) -> LiveCoverageDebtImpact:
    """Compare coverage snapshots and attach temporal context without promotion."""

    for event in change_events:
        if event.research_run_id != current.research_run_id:
            raise ResearchInputError("change event is cross-run")
    if previous is not None and previous.research_run_id != current.research_run_id:
        raise ResearchInputError("previous coverage snapshot is cross-run")

    if previous is None:
        delta = None
        count_delta = dict(_int_counts(current.cell_counts))
        reasons = ["LIVE_COVERAGE_BASELINE_CREATED", "CHANGE_EVENT_NOT_VULNERABILITY"]
    else:
        delta = current.total_debt - previous.total_debt
        count_delta = _count_delta(previous.cell_counts, current.cell_counts)
        reasons = ["LIVE_COVERAGE_REFRESHED", "CHANGE_EVENT_NOT_VULNERABILITY"]
        if current.matrix_hash == previous.matrix_hash:
            reasons.append("MATRIX_UNCHANGED")
        elif delta is not None and delta > 0:
            reasons.append("COVERAGE_DEBT_INCREASED")
        elif delta is not None and delta < 0:
            reasons.append("COVERAGE_DEBT_DECREASED")
        else:
            reasons.append("COVERAGE_DEBT_REBALANCED")

    if change_events:
        reasons.append("TEMPORAL_CHANGE_CONTEXT_ATTACHED")

    return LiveCoverageDebtImpact(
        research_run_id=current.research_run_id,
        previous_snapshot_id=previous.snapshot_id if previous is not None else None,
        current_snapshot_id=current.snapshot_id,
        previous_matrix_hash=previous.matrix_hash if previous is not None else None,
        current_matrix_hash=current.matrix_hash,
        total_debt_before=previous.total_debt if previous is not None else None,
        total_debt_after=current.total_debt,
        total_debt_delta=delta,
        cell_count_delta=count_delta,
        change_event_ids=tuple(sorted(event.change_event_id for event in change_events)),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


def _count_delta(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, int]:
    left = _int_counts(previous)
    right = _int_counts(current)
    keys = sorted(set(left) | set(right))
    return {key: right.get(key, 0) - left.get(key, 0) for key in keys}


def _int_counts(value: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, raw in value.items():
        _require_text(key, "cell_counts key")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise ResearchInputError("cell_counts values must be non-negative ints")
        counts[key] = raw
    return counts


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> None:
    found = FORBIDDEN_LIVE_COVERAGE_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()
