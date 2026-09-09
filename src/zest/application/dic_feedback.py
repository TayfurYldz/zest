"""Coverage / hunter / model-context-ready feedback for Differential, Invariant, Chain."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.evidence import SUPPORTED_EVIDENCE_STRATEGIES
from zest.research.exploration import OpportunityKind

DIC_COVERAGE_UPDATED = "DIC_COVERAGE_UPDATED"
DIC_MODEL_CONTEXT_READY = "DIC_MODEL_CONTEXT_READY"
EVIDENCE_PIPELINE_PENDING = "EVIDENCE_PIPELINE_PENDING"
DIC_KINDS = {
    OpportunityKind.DIFFERENTIAL.value,
    OpportunityKind.INVARIANT.value,
    OpportunityKind.CHAIN.value,
}


def apply_dic_coverage_feedback(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    opportunity_id: str | None,
    evaluation: dict | None,
    clock: Clock | None = None,
) -> None:
    if not opportunity_id:
        return
    clock = clock or SystemClock()
    evaluation = evaluation or {}
    with uow_factory.open() as uow:
        opportunity = uow.research_opportunities.get(opportunity_id)
        if opportunity is None or opportunity.opportunity_kind not in DIC_KINDS:
            uow.rollback()
            return
        strategy = evaluation.get("evaluation_strategy")
        evidence_status = (
            "CONNECTED"
            if strategy in SUPPORTED_EVIDENCE_STRATEGIES
            else EVIDENCE_PIPELINE_PENDING
        )
        coverage = {
            "opportunity_id": opportunity_id,
            "opportunity_kind": opportunity.opportunity_kind,
            "assessment_outcome": evaluation.get("assessment")
            or evaluation.get("outcome")
            or evaluation.get("linkage"),
            "evaluation_strategy": strategy,
            "evidence_pipeline": evidence_status,
            "dimensions": {
                "endpoint_fully_differential_covered": False,
                "endpoint_fully_invariant_covered": False,
                "endpoint_fully_chain_covered": False,
                "dimension_tested": True,
            },
            "not_full_coverage": True,
            "not_a_finding": True,
        }
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:dic-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=DIC_COVERAGE_UPDATED,
                subject_type="research_run",
                subject_id=research_run_id,
                payload=coverage,
            )
        )
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:dic-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=DIC_MODEL_CONTEXT_READY,
                subject_type="research_run",
                subject_id=research_run_id,
                payload={
                    "opportunity_id": opportunity_id,
                    "opportunity_kind": opportunity.opportunity_kind,
                    "evaluation": {
                        key: evaluation[key]
                        for key in (
                            "judgement",
                            "outcome",
                            "linkage",
                            "impact_status",
                            "reason_codes",
                            "left_step_status",
                            "right_step_status",
                        )
                        if key in evaluation
                    },
                    "ready_for_model_context": True,
                    "not_a_finding": True,
                },
            )
        )
        uow.commit()
