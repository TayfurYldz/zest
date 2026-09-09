"""Record Hunter Worker results onto V3 queue and coverage. Not a dispatcher."""

from __future__ import annotations

from zest.application.coverage.live_debt import RefreshLiveCoverageDebt, RefreshLiveCoverageDebtCommand
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import AuditEventRecord
from zest.research.exploration import OpportunityKind


HUNT_CELL_COVERED = "HUNT_CELL_COVERED"
HUNTER_FEEDBACK_APPLIED = "HUNTER_FEEDBACK_APPLIED"


def apply_hunter_execution_feedback(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    opportunity_id: str | None,
    hypothesis_id: str | None,
    experiment_id: str | None,
    observation_id: str | None,
    assessment_outcome: str | None,
    clock: Clock | None = None,
) -> None:
    """Mark executed V3 work and refresh coverage. Idempotent on queue RUN."""

    if not opportunity_id or not hypothesis_id:
        return
    clock = clock or SystemClock()
    with uow_factory.open() as uow:
        opportunity = uow.research_opportunities.get(opportunity_id)
        if opportunity is None or opportunity.opportunity_kind != OpportunityKind.HUNTER_COVERAGE_GAP.value:
            uow.rollback()
            return
        queues = [
            item
            for item in uow.hunt_v3_queue.list_for_research_run(research_run_id)
            if item.hypothesis_id == hypothesis_id
        ]
        covered_queue_ids: list[str] = []
        for item in queues:
            if item.state == "RUN":
                continue
            try:
                uow.hunt_v3_queue.set_state(item.queue_id, "RUN", from_state=item.state)
            except PersistenceConflictError:
                continue
            covered_queue_ids.append(item.queue_id)
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=clock.now(),
                    actor_id="control-plane:hunter-feedback",
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=HUNT_CELL_COVERED,
                    subject_type="hypothesis",
                    subject_id=hypothesis_id,
                    correlation_id=research_run_id,
                    payload={
                        "research_run_id": research_run_id,
                        "family_id": item.family_id,
                        "node_canonical_key": item.node_canonical_key,
                        "identity_id": item.identity_id,
                        "queue_id": item.queue_id,
                        "experiment_id": experiment_id,
                        "observation_id": observation_id,
                        "assessment_outcome": assessment_outcome,
                        "not_a_finding": True,
                    },
                )
            )
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=clock.now(),
                actor_id="control-plane:hunter-feedback",
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=HUNTER_FEEDBACK_APPLIED,
                subject_type="research_run",
                subject_id=research_run_id,
                payload={
                    "opportunity_id": opportunity_id,
                    "hypothesis_id": hypothesis_id,
                    "experiment_id": experiment_id,
                    "observation_id": observation_id,
                    "assessment_outcome": assessment_outcome,
                    "queue_ids": covered_queue_ids,
                    "not_a_finding": True,
                },
            )
        )
        uow.commit()
    RefreshLiveCoverageDebt(uow_factory, clock=clock).execute(
        RefreshLiveCoverageDebtCommand(research_run_id=research_run_id)
    )
