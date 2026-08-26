"""Build a CoverageHypothesisView from the append-only ledger.

No LLM. No raw secrets. Identity-agnostic by design (SD-G8 boundary):
HypothesisRecord does not carry identity, so the resulting view has
identity_id=None and the coverage core spreads that state to all identity
cells of the matching (node, family) pair.
"""

from __future__ import annotations

from zest.data.records import AuditEventRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.coverage.types import CoverageHypothesisView


# Event types emitted by hunt_validation.py for tier decisions.
_TIER_PASS_EVENTS = {
    "HYPOTHESIS_TIER_V1_PASSED": "V1",
    "HYPOTHESIS_TIER_V2_PASSED": "V2",
    "HYPOTHESIS_TIER_V3_QUEUED": "V3_QUEUED",
}
_REJECT_EVENTS = {
    "HUNT_TIER_V1_REJECTED",
    "HUNT_TIER_V2_REJECTED",
}


def build_coverage_hypothesis_view(
    uow: UnitOfWork,
    research_run_id: str,
) -> tuple[CoverageHypothesisView, ...]:
    """Return coverage hypothesis views for the run.

    SD-G9: HypothesisRecord carries identity_id. If the record has an identity,
    the view is bound to that identity cell. If the record is NULL (legacy or
    ANONYMOUS without identity binding), the view is identity-agnostic and the
    coverage core spreads it to all identity cells of the (node, family) pair.
    """

    hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
    if not hypotheses:
        return ()

    # Audit events are globally ordered; filter locally by run correlation.
    all_audit_events = uow.audit_events.list_for_subject_type("hypothesis")
    audit_by_hypothesis: dict[str, list[AuditEventRecord]] = {}
    for event in all_audit_events:
        correlation = event.correlation_id
        if correlation is not None and correlation != research_run_id:
            continue
        payload = event.payload or {}
        if payload.get("research_run_id") not in {research_run_id, None}:
            continue
        hypothesis_id = event.subject_id
        audit_by_hypothesis.setdefault(hypothesis_id, []).append(event)

    views: list[CoverageHypothesisView] = []
    for hypothesis in hypotheses:
        events = audit_by_hypothesis.get(hypothesis.hypothesis_id, [])
        tier = _highest_tier_from_events(events)
        node_key, family_id, audit_identity_id = _node_family_identity_from_events(events)
        if node_key is None or family_id is None:
            # No audit metadata yet: still hypothesized but unbound to a cell.
            continue
        # SD-G9 precedence: explicit HypothesisRecord identity wins over audit
        # payload identity (which may be missing for older records).
        identity_id = hypothesis.identity_id if hypothesis.identity_id is not None else audit_identity_id
        views.append(
            CoverageHypothesisView(
                hypothesis_id=hypothesis.hypothesis_id,
                family_id=family_id,
                node_canonical_key=node_key,
                identity_id=identity_id,
                highest_tier=tier,
            )
        )
    return tuple(views)


def _highest_tier_from_events(events: list[AuditEventRecord]) -> str:
    """Highest passed tier reached, or UNTESTED if no pass event exists."""

    highest = "UNTESTED"
    rank = {"UNTESTED": 0, "V1": 1, "V2": 2, "V3_QUEUED": 3, "COVERED": 4}
    for event in sorted(events, key=lambda item: item.occurred_at):
        tier = _TIER_PASS_EVENTS.get(event.event_type)
        if tier is not None and rank.get(tier, 0) > rank.get(highest, 0):
            highest = tier
    return highest


def _node_family_identity_from_events(
    events: list[AuditEventRecord],
) -> tuple[str | None, str | None, str | None]:
    """Extract node canonical key, family id, and identity id from the latest tier event."""

    for event in sorted(events, key=lambda item: item.occurred_at, reverse=True):
        payload = event.payload or {}
        node_key = payload.get("node_canonical_key")
        family_id = payload.get("family_id")
        identity_id = payload.get("identity_id")
        if node_key and family_id:
            return str(node_key), str(family_id), str(identity_id) if identity_id else None
    return None, None, None
