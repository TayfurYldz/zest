"""Coverage dimension updates from Authz/Workflow execution. Not full-type coverage."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.exploration import OpportunityKind

IDENTITY_ENGINE_COVERAGE_UPDATED = "IDENTITY_ENGINE_COVERAGE_UPDATED"


def apply_identity_engine_coverage_feedback(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    opportunity_id: str | None,
    experiment_id: str | None,
    assessment_outcome: str | None,
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
            OpportunityKind.AUTHORIZATION_DIFFERENTIAL.value,
            OpportunityKind.WORKFLOW_STATE_TRANSITION.value,
        }:
            uow.rollback()
            return
        dimensions = {
            "identity_tested": opportunity.source_refs[1] if len(opportunity.source_refs) > 1 else None,
            "object_relationship_tested": (
                opportunity.opportunity_kind == OpportunityKind.AUTHORIZATION_DIFFERENTIAL.value
            ),
            "workflow_edge_tested": (
                opportunity.opportunity_kind == OpportunityKind.WORKFLOW_STATE_TRANSITION.value
            ),
            "authorization_relation_resolved": assessment_outcome
            in {"CONSISTENT_WITH_PREDICTION", "CONTRADICTS_PREDICTION"},
            "full_resource_type_covered": False,
        }
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:identity-engine-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=IDENTITY_ENGINE_COVERAGE_UPDATED,
                subject_type="research_run",
                subject_id=research_run_id,
                payload={
                    "opportunity_id": opportunity_id,
                    "opportunity_kind": opportunity.opportunity_kind,
                    "experiment_id": experiment_id,
                    "assessment_outcome": assessment_outcome,
                    "dimensions": dimensions,
                    "not_full_coverage": True,
                },
            )
        )
        uow.commit()
