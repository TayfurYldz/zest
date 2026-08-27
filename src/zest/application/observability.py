"""Authoritative operational observability primitives.

These projections describe control/runtime truth only. They never create or
upgrade WorkerResult, Observation, Evidence, Candidate, Finding, or belief.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Mapping

from zest.application.identity import new_opaque_id
from zest.data.records import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    RunFaultClass,
    RunFaultComponent,
    RunFaultPhase,
    RunFaultRecord,
    RuntimeInstanceRecord,
    ResearchOrchestrationRecord,
    TargetContactStatus,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.safe_data import sanitize_exception


class SemanticPlane(Enum):
    CONTROL = "CONTROL"
    RESEARCH = "RESEARCH"
    EXECUTION = "EXECUTION"
    EPISTEMIC = "EPISTEMIC"
    SAFETY = "SAFETY"
    RUNTIME = "RUNTIME"


class EffectiveOperationalState(Enum):
    HEALTHY = "HEALTHY"
    RUNNING = "RUNNING"
    RUNTIME_FAULT = "RUNTIME_FAULT"
    OPERATIONAL_FAULT = "OPERATIONAL_FAULT"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EffectiveRunState:
    persisted_state: str
    effective_state: str
    reason_codes: tuple[str, ...]
    runtime_liveness: str


@dataclass(frozen=True)
class SemanticActivity:
    """Small deterministic activity primitive projected from typed facts."""

    activity_id: str
    research_run_id: str
    plane: str
    kind: str
    occurred_at: datetime
    summary: str
    source_type: str
    source_id: str
    attempt_id: str | None = None
    experiment_id: str | None = None
    runtime_instance_id: str | None = None


def run_fault_for_invocation(
    attempt: ExecutionAttemptRecord,
    outcome: WorkerInvocationOutcome,
    *,
    occurred_at: datetime,
    hypothesis_id: str | None = None,
) -> RunFaultRecord | None:
    """Create a typed fault only for a non-admitted invocation outcome."""

    if outcome.invocation_status is InvocationStatus.COMPLETED and outcome.worker_result is not None:
        return None
    diagnostic = outcome.reason or outcome.stderr_diagnostics or outcome.invocation_status.value
    return RunFaultRecord(
        fault_id=f"rf:attempt:{attempt.attempt_id}:{outcome.invocation_status.value.lower()}",
        research_run_id=attempt.research_run_id,
        hypothesis_id=hypothesis_id,
        experiment_id=attempt.experiment_id,
        attempt_id=attempt.attempt_id,
        request_id=attempt.request_id,
        capability=attempt.worker_capability,
        action=attempt.action,
        correlation_id=attempt.correlation_id,
        component=RunFaultComponent.EXECUTION.value,
        phase=RunFaultPhase.INVOCATION.value,
        fault_class=RunFaultClass.EXECUTION.value,
        fault_code=f"INVOCATION_{outcome.invocation_status.value}",
        fatal=False,
        occurred_at=occurred_at,
        diagnostic_summary=diagnostic,
    )


def supervisor_fault(
    research_run_id: str,
    exc: BaseException,
    *,
    runtime_instance_id: str | None,
    occurred_at: datetime,
) -> RunFaultRecord:
    details = sanitize_exception(exc)
    return RunFaultRecord(
        fault_id=new_opaque_id(),
        research_run_id=research_run_id,
        runtime_instance_id=runtime_instance_id,
        component=RunFaultComponent.SUPERVISOR.value,
        phase=RunFaultPhase.TICK.value,
        fault_class=RunFaultClass.SUPERVISOR.value,
        fault_code="SUPERVISOR_TICK_FAILED",
        fatal=True,
        occurred_at=occurred_at,
        diagnostic_summary=str(details["message"]),
    )


def project_effective_run_state(
    orchestration: ResearchOrchestrationRecord | None,
    runtime: RuntimeInstanceRecord | None,
    faults: Iterable[RunFaultRecord],
    *,
    now: datetime,
    liveness_timeout: timedelta = timedelta(seconds=30),
) -> EffectiveRunState:
    if orchestration is None:
        return EffectiveRunState("UNKNOWN", EffectiveOperationalState.UNKNOWN.value, ("ORCHESTRATION_MISSING",), "UNKNOWN")
    persisted = orchestration.state
    reasons: list[str] = []
    runtime_liveness = "NOT_REQUIRED"
    if runtime is not None:
        runtime_liveness = "LIVE"
        if runtime.status == "STOPPED" or runtime.stopped_at is not None:
            runtime_liveness = "STOPPED"
        elif runtime.last_seen_at + liveness_timeout < now:
            runtime_liveness = "STALE"
            reasons.append("RUNTIME_HEARTBEAT_STALE")
    elif orchestration.owner_runtime_instance_id is not None:
        runtime_liveness = "MISSING"
        reasons.append("RUNTIME_INSTANCE_MISSING")
    if orchestration.owner_runtime_instance_id is not None and (
        orchestration.lease_expires_at is not None and orchestration.lease_expires_at < now
    ):
        reasons.append("RUN_LEASE_EXPIRED")
    unresolved = [fault for fault in faults if fault.resolved_at is None]
    if any(fault.fatal for fault in unresolved):
        reasons.extend(f"UNRESOLVED_{fault.fault_code}" for fault in unresolved if fault.fatal)
    if reasons and persisted not in {"COMPLETED", "BUDGET_EXHAUSTED"}:
        effective = (
            EffectiveOperationalState.RUNTIME_FAULT.value
            if runtime_liveness in {"STALE", "STOPPED", "MISSING"}
            or any(fault.fault_class in {RunFaultClass.RUNTIME.value, RunFaultClass.SUPERVISOR.value} for fault in unresolved if fault.fatal)
            else EffectiveOperationalState.OPERATIONAL_FAULT.value
        )
    elif persisted == "RUNNING":
        effective = EffectiveOperationalState.RUNNING.value
    elif persisted in {"COMPLETED", "BUDGET_EXHAUSTED", "FAILED_OPERATIONAL"}:
        effective = EffectiveOperationalState.STOPPED.value
    else:
        effective = EffectiveOperationalState.HEALTHY.value
    return EffectiveRunState(persisted, effective, tuple(dict.fromkeys(reasons)), runtime_liveness)


def activity_from_fault(fault: RunFaultRecord) -> SemanticActivity:
    if fault.component in {RunFaultComponent.RUNTIME.value, RunFaultComponent.SUPERVISOR.value}:
        plane = SemanticPlane.RUNTIME.value
    elif fault.component == RunFaultComponent.EXECUTION.value:
        plane = SemanticPlane.EXECUTION.value
    elif fault.component == RunFaultComponent.PERSISTENCE.value:
        plane = SemanticPlane.SAFETY.value
    else:
        plane = SemanticPlane.CONTROL.value
    severity = "fatal" if fault.fatal else "material"
    return SemanticActivity(
        activity_id=f"activity:{fault.fault_id}",
        research_run_id=fault.research_run_id,
        plane=plane,
        kind="RUN_FAULT",
        occurred_at=fault.occurred_at,
        summary=f"{severity} {fault.component.lower()} fault: {fault.fault_code}",
        source_type="run_fault",
        source_id=fault.fault_id,
        attempt_id=fault.attempt_id,
        experiment_id=fault.experiment_id,
        runtime_instance_id=fault.runtime_instance_id,
    )
