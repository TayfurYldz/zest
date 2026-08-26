"""Load the run-scoped exploratory draft bound to a HypothesisRecord.

Application-level binding only. Not a TemporaryFamilyInstance type and not a
permanent HunterFamily row.
"""

from __future__ import annotations

from zest.application.errors import ApplicationError
from zest.data.records import AuditEventRecord, HypothesisRecord
from zest.research.exploratory import (
    EXPLORATORY_DRAFTED_EVENT,
    EXPLORATORY_SUBJECT_TYPE,
    ExploratoryHypothesisDraft,
    exploratory_draft_from_audit,
)
from zest.research.types import ResearchInputError


def load_exploratory_binding(
    uow,
    *,
    research_run_id: str,
    hypothesis_id: str,
) -> tuple[HypothesisRecord, ExploratoryHypothesisDraft, AuditEventRecord]:
    hypothesis = uow.hypotheses.get(hypothesis_id)
    if hypothesis is None:
        raise ApplicationError("hypothesis not found")
    if hypothesis.research_run_id != research_run_id:
        raise ApplicationError("hypothesis does not belong to research run")
    if not hypothesis.origin_reference:
        raise ApplicationError("hypothesis is not an exploratory draft")
    audit = uow.audit_events.get(hypothesis.origin_reference)
    if audit is None:
        raise ApplicationError("exploratory draft audit not found")
    if audit.event_type != EXPLORATORY_DRAFTED_EVENT:
        raise ApplicationError("hypothesis is not an exploratory draft")
    if audit.subject_type != EXPLORATORY_SUBJECT_TYPE:
        raise ApplicationError("hypothesis is not an exploratory draft")
    payload_hypothesis = audit.payload.get("hypothesis_id")
    if payload_hypothesis != hypothesis.hypothesis_id:
        raise ApplicationError("exploratory draft is not bound to this hypothesis")
    try:
        draft = exploratory_draft_from_audit(
            draft_id=audit.subject_id,
            research_run_id=research_run_id,
            payload=audit.payload,
        )
    except ResearchInputError as exc:
        raise ApplicationError(str(exc)) from exc
    if draft.research_run_id != research_run_id:
        raise ApplicationError("exploratory draft is cross-run")
    return hypothesis, draft, audit
