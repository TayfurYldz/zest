"""Consolidated recall report. Hard-gate metrics, not a weighted score."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zest.security_benchmark.scorecard import (
    ResearchSelectionScorecard,
    SecurityScorecard,
    WorkflowScorecard,
)


class RecallReportError(ValueError):
    pass


@dataclass(frozen=True)
class RecallFamilyRow:
    family_id: str
    benchmark_version: str
    expected_positive: int
    validated_positive: int
    missed_positive: int
    false_finding: int
    hard_failures: tuple[str, ...]
    source: str

    @property
    def recall_fraction(self) -> tuple[int, int]:
        return (self.validated_positive, self.expected_positive)

    @property
    def pass_gate(self) -> bool:
        return (
            self.expected_positive > 0
            and self.missed_positive == 0
            and self.false_finding == 0
            and not self.hard_failures
            and self.validated_positive == self.expected_positive
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "benchmark_version": self.benchmark_version,
            "expected_positive": self.expected_positive,
            "validated_positive": self.validated_positive,
            "missed_positive": self.missed_positive,
            "false_finding": self.false_finding,
            "hard_failures": list(self.hard_failures),
            "recall_fraction": list(self.recall_fraction),
            "pass_gate": self.pass_gate,
            "source": self.source,
        }


@dataclass(frozen=True)
class ConsolidatedRecallReport:
    report_version: str
    families: tuple[RecallFamilyRow, ...]
    total_expected_positive: int
    total_validated_positive: int
    total_missed_positive: int
    total_false_finding: int
    hard_failures: tuple[str, ...]
    weighted_average_allowed: bool = False
    not_a_finding: bool = True

    @property
    def pass_gate(self) -> bool:
        return (
            bool(self.families)
            and self.total_expected_positive > 0
            and self.total_missed_positive == 0
            and self.total_false_finding == 0
            and not self.hard_failures
            and all(row.pass_gate for row in self.families)
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "weighted_average_allowed": self.weighted_average_allowed,
            "not_a_finding": self.not_a_finding,
            "pass_gate": self.pass_gate,
            "total_expected_positive": self.total_expected_positive,
            "total_validated_positive": self.total_validated_positive,
            "total_missed_positive": self.total_missed_positive,
            "total_false_finding": self.total_false_finding,
            "hard_failures": list(self.hard_failures),
            "families": [row.to_mapping() for row in self.families],
        }


def consolidate_recall_report(
    *,
    object_authorization: SecurityScorecard | None = None,
    workflow_authorization: WorkflowScorecard | None = None,
    research_selection: ResearchSelectionScorecard | None = None,
) -> ConsolidatedRecallReport:
    rows: list[RecallFamilyRow] = []
    if object_authorization is not None:
        rows.append(
            RecallFamilyRow(
                family_id="object_authorization",
                benchmark_version=object_authorization.benchmark_version,
                expected_positive=(
                    object_authorization.true_vulnerability_validated
                    + object_authorization.true_vulnerability_missed
                ),
                validated_positive=object_authorization.true_vulnerability_validated,
                missed_positive=object_authorization.true_vulnerability_missed,
                false_finding=object_authorization.false_finding,
                hard_failures=object_authorization.hard_failures,
                source="SecurityScorecard",
            )
        )
    if workflow_authorization is not None:
        rows.append(
            RecallFamilyRow(
                family_id="workflow_authorization",
                benchmark_version=workflow_authorization.benchmark_version,
                expected_positive=(
                    workflow_authorization.workflow_true_positive
                    + workflow_authorization.workflow_missed
                ),
                validated_positive=workflow_authorization.workflow_true_positive,
                missed_positive=workflow_authorization.workflow_missed,
                false_finding=workflow_authorization.workflow_false_finding,
                hard_failures=workflow_authorization.hard_failures,
                source="WorkflowScorecard",
            )
        )
    if research_selection is not None:
        rows.append(
            RecallFamilyRow(
                family_id="research_selection",
                benchmark_version=research_selection.benchmark_version,
                expected_positive=research_selection.correct_hypothesis_survival,
                validated_positive=research_selection.correct_hypothesis_survival,
                missed_positive=research_selection.incorrect_hypothesis_survival,
                false_finding=research_selection.false_finding,
                hard_failures=research_selection.hard_failures,
                source="ResearchSelectionScorecard",
            )
        )

    if not rows:
        raise RecallReportError("at least one scorecard is required")
    for row in rows:
        _validate_row(row)

    hard_failures: list[str] = []
    for row in rows:
        hard_failures.extend(row.hard_failures)
    return ConsolidatedRecallReport(
        report_version="recall.consolidated.v1",
        families=tuple(sorted(rows, key=lambda row: row.family_id)),
        total_expected_positive=sum(row.expected_positive for row in rows),
        total_validated_positive=sum(row.validated_positive for row in rows),
        total_missed_positive=sum(row.missed_positive for row in rows),
        total_false_finding=sum(row.false_finding for row in rows),
        hard_failures=tuple(dict.fromkeys(hard_failures)),
    )


def _validate_row(row: RecallFamilyRow) -> None:
    for field_name in ("expected_positive", "validated_positive", "missed_positive", "false_finding"):
        value = getattr(row, field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RecallReportError(f"{field_name} must be a non-negative int")
    if row.expected_positive == 0:
        raise RecallReportError(f"{row.family_id} has no positive recall target")
    if row.validated_positive + row.missed_positive != row.expected_positive:
        raise RecallReportError(f"{row.family_id} recall counts do not balance")
