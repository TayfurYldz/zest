"""Deterministic OAST arm timeout closer. Does not invent callbacks or Evidence."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord

OAST_NO_CALLBACK_TIMEOUT = "OAST_NO_CALLBACK_TIMEOUT"
OAST_ARM_EXPIRED = "OAST_ARM_EXPIRED"


def close_expired_oast_arms(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    clock: Clock | None = None,
) -> int:
    clock = clock or SystemClock()
    now = clock.now()
    closed = 0
    with uow_factory.open() as uow:
        audits = uow.audit_events.list_for_subject("research_run", research_run_id)
        already = {
            (item.payload or {}).get("correlation_id")
            for item in audits
            if item.event_type in {OAST_NO_CALLBACK_TIMEOUT, OAST_ARM_EXPIRED}
        }
        for correlation in uow.oast_correlations.list_for_research_run(research_run_id):
            if correlation.expires_at > now:
                continue
            if correlation.correlation_id in already:
                continue
            admission = uow.oast_admissions.get_by_correlation(correlation.correlation_id)
            event_type = OAST_ARM_EXPIRED if admission is not None else OAST_NO_CALLBACK_TIMEOUT
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id="control-plane:oast-timeout",
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=event_type,
                    subject_type="research_run",
                    subject_id=research_run_id,
                    correlation_id=correlation.correlation_id,
                    payload={
                        "correlation_id": correlation.correlation_id,
                        "attempt_id": correlation.attempt_id,
                        "experiment_id": correlation.experiment_id,
                        "expired_at": correlation.expires_at.isoformat(),
                        "had_admission": admission is not None,
                        "not_evidence": True,
                    },
                )
            )
            closed += 1
        if closed:
            uow.commit()
        else:
            uow.rollback()
    return closed
