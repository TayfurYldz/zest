"""Coverage debt domain types. No ledger authority; pure projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from zest.research.types import ResearchInputError


class CoverageState(Enum):
    """Lifecycle of one (node × identity × family) coverage cell.

    ORDER matters: higher values mean more coverage. The matrix picks the
    highest tier reached for that cell.
    """

    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNTESTED = "UNTESTED"
    HYPOTHESIZED = "HYPOTHESIZED"
    V1_PASSED = "V1_PASSED"
    V2_PASSED = "V2_PASSED"
    V3_QUEUED = "V3_QUEUED"
    COVERED = "COVERED"


@dataclass(frozen=True)
class CoverageHypothesisView:
    """Read-only projection of hypothesis progress for one (node, family) pair.

    identity_id is None when the underlying hypothesis is identity-agnostic
    (G5 HypothesisRecord does not carry identity). In that case the hypothesis
    state is spread to every identity cell of the node×family pair (SD-G8
    boundary; per-identity binding is scheduled for SD-G9).
    """

    hypothesis_id: str | None
    family_id: str
    node_canonical_key: str
    identity_id: str | None
    highest_tier: str

    def __post_init__(self) -> None:
        if self.hypothesis_id is not None and (
            not isinstance(self.hypothesis_id, str) or not self.hypothesis_id.strip()
        ):
            raise ResearchInputError("hypothesis_id must be a non-empty string or None")
        if not isinstance(self.family_id, str) or not self.family_id.strip():
            raise ResearchInputError("family_id must be a non-empty string")
        if not isinstance(self.node_canonical_key, str) or not self.node_canonical_key.strip():
            raise ResearchInputError("node_canonical_key must be a non-empty string")
        if self.identity_id is not None and (
            not isinstance(self.identity_id, str) or not self.identity_id.strip()
        ):
            raise ResearchInputError("identity_id must be a non-empty string or None")
        if self.highest_tier not in {
            "UNTESTED",
            "V1",
            "V2",
            "V3_QUEUED",
            "COVERED",
            "REJECTED",
        }:
            raise ResearchInputError("highest_tier is not a recognized coverage tier")


@dataclass(frozen=True)
class CoverageCell:
    """One cell of the coverage-debt matrix."""

    node_canonical_key: str
    identity_id: str
    family_id: str
    state: CoverageState
    missing_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.node_canonical_key, str) or not self.node_canonical_key.strip():
            raise ResearchInputError("node_canonical_key must be a non-empty string")
        if not isinstance(self.identity_id, str) or not self.identity_id.strip():
            raise ResearchInputError("identity_id must be a non-empty string")
        if not isinstance(self.family_id, str) or not self.family_id.strip():
            raise ResearchInputError("family_id must be a non-empty string")
        if not isinstance(self.state, CoverageState):
            raise ResearchInputError("state must be a CoverageState")
        if not isinstance(self.missing_evidence, tuple):
            raise ResearchInputError("missing_evidence must be a tuple")


@dataclass(frozen=True)
class CoverageMatrix:
    """Deterministic coverage-debt matrix for one research run."""

    research_run_id: str
    strategy_version: str
    cells: tuple[CoverageCell, ...]
    cell_counts: dict[str, int]
    total_debt: int
    matrix_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.research_run_id, str) or not self.research_run_id.strip():
            raise ResearchInputError("research_run_id must be a non-empty string")
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise ResearchInputError("strategy_version must be a non-empty string")
        if not isinstance(self.cells, tuple):
            raise ResearchInputError("cells must be a tuple")
        if not isinstance(self.cell_counts, dict):
            raise ResearchInputError("cell_counts must be a dict")
        if not isinstance(self.total_debt, int) or isinstance(self.total_debt, bool) or self.total_debt < 0:
            raise ResearchInputError("total_debt must be a non-negative int")
        if not isinstance(self.matrix_hash, str) or len(self.matrix_hash) != 64:
            raise ResearchInputError("matrix_hash must be a SHA-256 hex digest")
