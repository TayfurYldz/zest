"""Dimension-aware Mutation/Protocol coverage feedback. Not full-endpoint coverage."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.evidence import SUPPORTED_EVIDENCE_STRATEGIES
from zest.research.exploration import OpportunityKind

MUTATION_PROTOCOL_COVERAGE_UPDATED = "MUTATION_PROTOCOL_COVERAGE_UPDATED"
EVIDENCE_PIPELINE_PENDING = "EVIDENCE_PIPELINE_PENDING"


def apply_mutation_protocol_coverage_feedback(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    opportunity_id: str | None,
    experiment_id: str | None,
    assessment_outcome: str | None,
    evaluation_strategy: str | None,
    clock: Clock | None = None,
) -> None:
    if not opportunity_id:
        return
    clock = clock or SystemClock()
    with uow_factory.open() as uow:
        opportunity = uow.research_opportunities.get(opportunity_id)
        if opportunity is None:
            uow.rollback()
            return
        if opportunity.opportunity_kind not in {
            OpportunityKind.MUTATION_VARIANT.value,
            OpportunityKind.PROTOCOL_STEP.value,
            OpportunityKind.HUNTER_COVERAGE_GAP.value,
        }:
            uow.rollback()
            return
        if opportunity.opportunity_kind == OpportunityKind.HUNTER_COVERAGE_GAP.value:
            family_hint = " ".join(opportunity.assumptions)
            if "HTTP_REQUEST_SMUGGLING" not in family_hint and "HTTP_CACHE_POISONING" not in family_hint:
                if "SQL_INJECTION" not in family_hint and "mutation" not in family_hint.lower():
                    uow.rollback()
                    return
        evidence_status = (
            "CONNECTED"
            if evaluation_strategy in SUPPORTED_EVIDENCE_STRATEGIES
            else EVIDENCE_PIPELINE_PENDING
        )
        dimensions = {
            "mutation_family": (
                opportunity.source_refs[2] if opportunity.opportunity_kind == OpportunityKind.MUTATION_VARIANT.value and len(opportunity.source_refs) > 2 else None
            ),
            "mutation_rule": (
                opportunity.source_refs[3] if opportunity.opportunity_kind == OpportunityKind.MUTATION_VARIANT.value and len(opportunity.source_refs) > 3 else None
            ),
            "protocol_family": (
                opportunity.source_refs[0] if opportunity.opportunity_kind == OpportunityKind.PROTOCOL_STEP.value else None
            ),
            "endpoint_fully_mutation_covered": False,
            "endpoint_fully_protocol_covered": False,
            "dimension_tested": True,
        }
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:mutation-protocol-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=MUTATION_PROTOCOL_COVERAGE_UPDATED,
                subject_type="research_run",
                subject_id=research_run_id,
                payload={
                    "opportunity_id": opportunity_id,
                    "opportunity_kind": opportunity.opportunity_kind,
                    "experiment_id": experiment_id,
                    "assessment_outcome": assessment_outcome,
                    "evaluation_strategy": evaluation_strategy,
                    "evidence_pipeline": evidence_status,
                    "dimensions": dimensions,
                    "not_full_coverage": True,
                    "not_a_finding": True,
                },
            )
        )
        uow.commit()
