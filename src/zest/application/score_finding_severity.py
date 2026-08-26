"""Score severity for an admitted FindingProposal without mutating proposal content."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.validation_audit import read_validation_audit_view
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.impact.types import ImpactKind
from zest.research.validation.severity import (
    InternalSeverity,
    ScopeState,
    SeverityInput,
    ValidationState,
    bound_severity,
    classify_severity,
)


FINDING_SEVERITY_SCORED = "FINDING_SEVERITY_SCORED"
FINDING_SEVERITY_NOT_SCORED = "FINDING_SEVERITY_NOT_SCORED"
SEVERITY_ACTOR_ID = "control-plane:severity"


@dataclass(frozen=True)
class ScoreFindingSeverityCommand:
    proposal_id: str
    data_sensitivity: str = "NONE"
    affected_scope: str = "SINGLE_USER"


@dataclass(frozen=True)
class ScoreFindingSeverityResult:
    scored: bool
    severity: InternalSeverity | None
    reason_codes: tuple[str, ...]
    audit_event_id: str


class ScoreFindingSeverity:
    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: ScoreFindingSeverityCommand) -> ScoreFindingSeverityResult:
        with self._uow_factory.open() as uow:
            proposal = uow.finding_proposals.get(command.proposal_id)
            if proposal is None:
                raise ApplicationError("finding proposal not found")
            candidate = uow.candidates.get(proposal.candidate_id)
            if candidate is None:
                raise ApplicationError("finding proposal candidate not found")

            validation_view = read_validation_audit_view(uow, candidate)
            validation_passed = (
                validation_view.decision is not None and validation_view.decision.admitted
            )
            impact_kinds = _impact_kinds_for_proposal(uow, proposal.impact_chain_ids)
            demonstrated = classify_severity(
                SeverityInput(
                    validation_state=(
                        ValidationState.PASSED
                        if validation_passed
                        else ValidationState.NOT_PASSED
                    ),
                    scope_state=(
                        validation_view.scope_state
                        if validation_passed
                        else ScopeState.NOT_IN_SCOPE
                    ),
                    impact_kinds=impact_kinds,
                    data_sensitivity="NONE",
                    affected_scope="SINGLE_USER",
                )
            )
            requested = classify_severity(
                SeverityInput(
                    validation_state=(
                        ValidationState.PASSED
                        if validation_passed
                        else ValidationState.NOT_PASSED
                    ),
                    scope_state=(
                        validation_view.scope_state
                        if validation_passed
                        else ScopeState.NOT_IN_SCOPE
                    ),
                    impact_kinds=impact_kinds,
                    data_sensitivity=command.data_sensitivity,
                    affected_scope=command.affected_scope,
                )
            )
            severity = bound_severity(demonstrated, requested)
            event_id = new_opaque_id()
            payload = {
                "proposal_id": proposal.proposal_id,
                "candidate_id": candidate.candidate_id,
                "hypothesis_id": candidate.hypothesis_id,
                "classification": candidate.classification,
                "validation_outcome": (
                    None
                    if validation_view.decision is None
                    else validation_view.decision.outcome.value
                ),
                "scope_state": (
                    validation_view.scope_state.value
                    if validation_passed
                    else ScopeState.NOT_IN_SCOPE.value
                ),
                "impact_kinds": tuple(item.value for item in impact_kinds),
                "scored": severity.scored,
                "severity": None if severity.severity is None else severity.severity.value,
                "platform_mapping": (
                    None
                    if severity.platform_mapping is None
                    else {
                        "bugcrowd_priority": severity.platform_mapping.bugcrowd_priority,
                        "hackerone_severity": severity.platform_mapping.hackerone_severity,
                        "vrt_category": severity.platform_mapping.vrt_category,
                    }
                ),
                "reason_codes": severity.reason_codes,
            }
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=event_id,
                    occurred_at=self._clock.now(),
                    actor_id=SEVERITY_ACTOR_ID,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=(
                        FINDING_SEVERITY_SCORED
                        if severity.scored
                        else FINDING_SEVERITY_NOT_SCORED
                    ),
                    subject_type="finding_proposal",
                    subject_id=proposal.proposal_id,
                    correlation_id=proposal.research_run_id,
                    payload=payload,
                )
            )
            uow.commit()
            return ScoreFindingSeverityResult(
                scored=severity.scored,
                severity=severity.severity,
                reason_codes=severity.reason_codes,
                audit_event_id=event_id,
            )


def _impact_kinds_for_proposal(uow, chain_ids: tuple[str, ...]) -> tuple[ImpactKind, ...]:
    impact_kinds: list[ImpactKind] = []
    for chain_id in chain_ids:
        for node in uow.impact_chains.get_nodes(chain_id):
            impact_kinds.append(ImpactKind(node.impact_kind))
    return tuple(impact_kinds)
