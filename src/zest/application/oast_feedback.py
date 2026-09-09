"""Dimension-specific OAST coverage feedback. Not full-target coverage."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.evidence import SUPPORTED_EVIDENCE_STRATEGIES
from zest.research.exploration import OpportunityKind

OAST_COVERAGE_UPDATED = "OAST_COVERAGE_UPDATED"
EVIDENCE_PIPELINE_PENDING = "EVIDENCE_PIPELINE_PENDING"


def apply_oast_coverage_feedback(
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
        if opportunity is None or opportunity.opportunity_kind != OpportunityKind.OAST_INTERACTION.value:
            uow.rollback()
            return
        evidence_status = (
            "CONNECTED"
            if evaluation_strategy in SUPPORTED_EVIDENCE_STRATEGIES
            else EVIDENCE_PIPELINE_PENDING
        )
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:oast-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=OAST_COVERAGE_UPDATED,
                subject_type="research_run",
                subject_id=research_run_id,
                payload={
                    "opportunity_id": opportunity_id,
                    "opportunity_kind": opportunity.opportunity_kind,
                    "experiment_id": experiment_id,
                    "assessment_outcome": assessment_outcome,
                    "evaluation_strategy": evaluation_strategy,
                    "evidence_pipeline": evidence_status,
                    "dimensions": {
                        "oast_family": opportunity.source_refs[0] if opportunity.source_refs else None,
                        "sink": opportunity.source_refs[3] if len(opportunity.source_refs) > 3 else None,
                        "endpoint_fully_oast_covered": False,
                        "dimension_tested": True,
                    },
                    "not_full_coverage": True,
                    "not_a_finding": True,
                },
            )
        )
        uow.commit()
