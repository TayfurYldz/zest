"""Causal chain composition. Untested steps are not proven. No invented edges."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

CHAIN_EVALUATION_STRATEGY = "chain.causal.v1"


class ChainStepStatus(Enum):
    PROVEN = "PROVEN"
    DISPROVEN = "DISPROVEN"
    UNTESTED = "UNTESTED"
    BLOCKED_BY_AUTHORITY = "BLOCKED_BY_AUTHORITY"
    MISSING_PRECONDITION = "MISSING_PRECONDITION"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


class ChainLinkage(Enum):
    SUPPORTED = "SUPPORTED"
    INSUFFICIENT_LINKAGE = "INSUFFICIENT_LINKAGE"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class ImpactStatus(Enum):
    HYPOTHESIS = "HYPOTHESIS"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ChainNodeView:
    node_id: str
    engine: str
    resource_key: str
    assessment_outcome: str | None
    authority_status: str
    step_status: ChainStepStatus


@dataclass(frozen=True)
class ChainCheckResult:
    linkage: ChainLinkage
    reason_codes: tuple[str, ...]
    nodes: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]
    impact_status: ImpactStatus
    may_support_evidence: bool


def evaluate_chain_linkage(
    left: ChainNodeView,
    right: ChainNodeView,
    *,
    claimed_edge: str,
) -> ChainCheckResult:
    left_map = _node_map(left)
    right_map = _node_map(right)
    if left.resource_key != right.resource_key or not left.resource_key:
        return ChainCheckResult(
            linkage=ChainLinkage.INSUFFICIENT_LINKAGE,
            reason_codes=("INSUFFICIENT_LINKAGE", "NO_SHARED_RESOURCE"),
            nodes=(left_map, right_map),
            edges=(),
            impact_status=ImpactStatus.UNKNOWN,
            may_support_evidence=False,
        )
    if claimed_edge != "ENABLES":
        return ChainCheckResult(
            linkage=ChainLinkage.REJECTED,
            reason_codes=("UNSUPPORTED_EDGE",),
            nodes=(left_map, right_map),
            edges=(),
            impact_status=ImpactStatus.UNKNOWN,
            may_support_evidence=False,
        )
    if (
        left.step_status is ChainStepStatus.UNTESTED
        or right.step_status is ChainStepStatus.UNTESTED
    ):
        return ChainCheckResult(
            linkage=ChainLinkage.REJECTED,
            reason_codes=("UNTESTED_IS_NOT_PROVEN",),
            nodes=(left_map, right_map),
            edges=(),
            impact_status=ImpactStatus.UNKNOWN,
            may_support_evidence=False,
        )
    if (
        left.step_status is ChainStepStatus.BLOCKED_BY_AUTHORITY
        or right.step_status is ChainStepStatus.BLOCKED_BY_AUTHORITY
    ):
        return ChainCheckResult(
            linkage=ChainLinkage.BLOCKED,
            reason_codes=("BLOCKED_BY_AUTHORITY",),
            nodes=(left_map, right_map),
            edges=(),
            impact_status=ImpactStatus.BLOCKED,
            may_support_evidence=False,
        )
    if left.step_status is not ChainStepStatus.PROVEN or right.step_status is not ChainStepStatus.PROVEN:
        return ChainCheckResult(
            linkage=ChainLinkage.REJECTED,
            reason_codes=("STEP_NOT_PROVEN", left.step_status.value, right.step_status.value),
            nodes=(left_map, right_map),
            edges=(),
            impact_status=ImpactStatus.UNKNOWN,
            may_support_evidence=False,
        )
    edge = {
        "from": left.node_id,
        "to": right.node_id,
        "kind": "ENABLES",
        "precondition": f"resource:{left.resource_key}",
        "postcondition": f"resource:{right.resource_key}",
        "supported_by": (left.node_id, right.node_id),
    }
    return ChainCheckResult(
        linkage=ChainLinkage.SUPPORTED,
        reason_codes=("SHARED_RESOURCE_ENABLES", "IMPACT_HYPOTHESIS_ONLY"),
        nodes=(left_map, right_map),
        edges=(edge,),
        impact_status=ImpactStatus.HYPOTHESIS,
        may_support_evidence=True,
    )


def step_status_from_assessment(outcome: str | None, *, authority_blocked: bool) -> ChainStepStatus:
    if authority_blocked:
        return ChainStepStatus.BLOCKED_BY_AUTHORITY
    if outcome is None:
        return ChainStepStatus.UNTESTED
    if outcome == "CONSISTENT_WITH_PREDICTION":
        return ChainStepStatus.PROVEN
    if outcome == "CONTRADICTS_PREDICTION":
        return ChainStepStatus.DISPROVEN
    if outcome in {"EXECUTION_UNUSABLE", "NEEDS_MORE_CONTEXT"}:
        return ChainStepStatus.UNKNOWN_OUTCOME
    if outcome == "INCONCLUSIVE":
        return ChainStepStatus.UNKNOWN_OUTCOME
    return ChainStepStatus.UNTESTED


def _node_map(node: ChainNodeView) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "engine": node.engine,
        "resource_key": node.resource_key,
        "assessment_outcome": node.assessment_outcome,
        "authority_status": node.authority_status,
        "step_status": node.step_status.value,
    }
