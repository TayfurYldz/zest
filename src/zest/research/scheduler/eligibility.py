"""Select-time eligibility blockers. Not ranking and not Core authorization."""

from __future__ import annotations

from zest.research.exploration import OpportunityKind, ResearchOpportunity


def select_time_blockers(opportunity: ResearchOpportunity) -> tuple[str, ...] | None:
    """Return reason codes when work is not eligible to be ranked/selected.

    Engine-specific missing prerequisites stay blockers. Fairness must not
    promote ineligible work.
    """

    if opportunity.opportunity_kind is OpportunityKind.OAST_INTERACTION:
        if len(opportunity.source_refs) < 4:
            return ("MISSING_PRECONDITION", "OAST_SOURCE_REFS_INCOMPLETE")
        callback_id = ""
        for item in opportunity.assumptions:
            if item.startswith("callback_id:"):
                callback_id = item.split(":", 1)[1]
                break
        if not callback_id:
            return ("MISSING_PRECONDITION", "OAST_CALLBACK_ID_ABSENT")
    return None
