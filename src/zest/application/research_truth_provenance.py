"""Research-truth promotion provenance policy.

research-work-fabric hypotheses are execution/control anchors.
They are not research truth and cannot enter Evidence/Candidate/
FindingProposal promotion.
"""

from __future__ import annotations

from zest.application.errors import ApplicationError
from zest.data.records import HypothesisRecord


RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX = (
    "research-work-fabric.v1:"
)

RESEARCH_WORK_FABRIC_PROMOTION_BLOCK_REASON = (
    "RESEARCH_WORK_FABRIC_CONTROL_ANCHOR_NOT_PROMOTABLE"
)


class NonPromotableHypothesisOrigin(
    ApplicationError
):
    """Durable hypothesis provenance forbids truth promotion."""


def is_research_work_fabric_hypothesis(
    hypothesis: HypothesisRecord,
) -> bool:
    return (
        hypothesis.origin_reference or ""
    ).startswith(
        RESEARCH_WORK_FABRIC_HYPOTHESIS_ORIGIN_PREFIX
    )


def require_promotable_hypothesis(
    uow,
    hypothesis_id: str,
) -> HypothesisRecord:
    hypothesis = uow.hypotheses.get(
        hypothesis_id
    )

    if hypothesis is None:
        raise ApplicationError(
            "hypothesis not found"
        )

    if is_research_work_fabric_hypothesis(
        hypothesis
    ):
        raise NonPromotableHypothesisOrigin(
            RESEARCH_WORK_FABRIC_PROMOTION_BLOCK_REASON
        )

    return hypothesis
