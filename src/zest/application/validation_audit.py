"""Shared SD-G10 validation audit helpers."""

from __future__ import annotations

from dataclasses import dataclass

from zest.data.records import AuditEventRecord, CandidateRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
)
from zest.research.validation.severity import ScopeState
from zest.research.validation.tier_gate import (
    ValidationAdmissionDecision,
    ValidationTier,
    ValidationTierOutcome,
    validate_required_tiers,
)


SECURITY_CANDIDATE_REQUIRED_TIER = {
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION: ValidationTier.V3,
    HTTP_STATE_TRANSITION_CLASSIFICATION: ValidationTier.V3,
}


@dataclass(frozen=True)
class ValidationAuditView:
    decision: ValidationAdmissionDecision | None
    latest_events: dict[ValidationTier, AuditEventRecord]

    @property
    def scope_state(self) -> ScopeState:
        if self.decision is None or not self.decision.admitted:
            return ScopeState.NOT_IN_SCOPE
        observed = {
            str(
                event.payload.get("scope_state")
                or event.payload.get("scope_classification")
                or ""
            )
            for event in self.latest_events.values()
        }
        return ScopeState.IN_SCOPE if "IN_SCOPE" in observed else ScopeState.NOT_IN_SCOPE


def read_validation_audit_view(
    uow: UnitOfWork,
    candidate: CandidateRecord,
) -> ValidationAuditView:
    if candidate.classification == DIAGNOSTIC_CANDIDATE_CLASSIFICATION:
        return ValidationAuditView(decision=None, latest_events={})

    required_tier = SECURITY_CANDIDATE_REQUIRED_TIER.get(candidate.classification)
    latest_events = latest_tier_events(
        uow,
        research_run_id=candidate.research_run_id,
        hypothesis_id=candidate.hypothesis_id,
    )
    observed = {
        tier: ValidationTierOutcome(str(event.payload["outcome"]))
        for tier, event in latest_events.items()
    }
    if required_tier is None:
        return ValidationAuditView(
            decision=validate_required_tiers(ValidationTier.V3, {}),
            latest_events=latest_events,
        )
    return ValidationAuditView(
        decision=validate_required_tiers(required_tier, observed),
        latest_events=latest_events,
    )


def latest_tier_events(
    uow: UnitOfWork,
    *,
    research_run_id: str,
    hypothesis_id: str,
) -> dict[ValidationTier, AuditEventRecord]:
    events = [
        event
        for event in uow.audit_events.list_for_subject_type("hypothesis")
        if event.subject_id == hypothesis_id
        and event.correlation_id == research_run_id
        and event.payload.get("tier") in {"V1", "V2", "V3"}
        and event.payload.get("outcome") in {"PASSED", "REJECTED", "INCONCLUSIVE", "QUEUED"}
    ]
    latest: dict[ValidationTier, AuditEventRecord] = {}
    for event in sorted(events, key=lambda item: (item.occurred_at, item.audit_event_id)):
        latest[ValidationTier(str(event.payload["tier"]))] = event
    return latest


def latest_tier_outcomes(
    uow: UnitOfWork,
    *,
    research_run_id: str,
    hypothesis_id: str,
) -> dict[ValidationTier, ValidationTierOutcome]:
    return {
        tier: ValidationTierOutcome(str(event.payload["outcome"]))
        for tier, event in latest_tier_events(
            uow,
            research_run_id=research_run_id,
            hypothesis_id=hypothesis_id,
        ).items()
    }
