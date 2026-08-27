"""Sanitized operator run read model. Projection only. Not SoR."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zest.application.operator_errors import OperatorError, OperatorErrorCode
from zest.application.observability import (
    activity_from_fault,
    project_effective_run_state,
)
from zest.application.persist_preflight import preflight_record_to_mapping
from zest.application.ports import UnitOfWorkFactory
from zest.data.budget_ledger import ledger_totals
from zest.data.records import (
    AuditEventRecord,
    PreflightReportRecord,
    ResearchCycleRecord,
    ResearchOrchestrationRecord,
)
from zest.research.orchestration import OrchestrationState
from zest.safe_data import redact_secret_keys

_PENDING_PROPOSAL_STATES = frozenset({"PROPOSED", "HUMAN_REVIEW"})
_RECONCILIATION_STATES = frozenset(
    {
        OrchestrationState.WAITING_HUMAN.value,
        "RECONCILIATION_REQUIRED",
    }
)
_TIMELINE_LABELS = {
    "DASHBOARD_PROGRAM_BOOTSTRAPPED": "RUN_CREATED",
    "PREFLIGHT_COMPLETED": "PREFLIGHT_COMPLETED",
    "RUNTIME_RECOVERY_HELD_FOR_HUMAN": "WAITING_HUMAN",
    "LEASE_ACQUIRED": "LEASE_ACQUIRED",
    "RUN_START_REQUESTED": "RUN_START_REQUESTED",
}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _lease_health(
    orchestration: ResearchOrchestrationRecord | None, *, now: datetime
) -> dict[str, object]:
    if orchestration is None:
        return {
            "held": False,
            "owner_runtime_instance_id": None,
            "lease_epoch": None,
            "expires_at": None,
            "healthy": True,
        }
    owner = orchestration.owner_runtime_instance_id
    expires = orchestration.lease_expires_at
    held = owner is not None and (expires is None or expires >= now)
    return {
        "held": held,
        "owner_runtime_instance_id": owner,
        "lease_epoch": orchestration.lease_epoch,
        "expires_at": _iso(expires),
        "healthy": True if not held or expires is None or expires >= now else False,
    }


def _reconciliation(
    orchestration: ResearchOrchestrationRecord | None,
) -> dict[str, object] | None:
    if orchestration is None:
        return None
    state = orchestration.state
    if state not in _RECONCILIATION_STATES and orchestration.pause_reason is None:
        return None
    if state not in _RECONCILIATION_STATES:
        return None
    return {
        "state": state,
        "classification": orchestration.pause_reason or orchestration.stop_reason,
        "last_known_safe_phase": orchestration.last_phase,
        "last_attempt_id": orchestration.last_attempt_id,
        "side_effect_class": orchestration.side_effect_ceiling,
        "operator_action": "human review required; do not auto-retry UNKNOWN_OUTCOME",
        "auto_retry": False,
    }


def _timeline(
    audits: list[AuditEventRecord], cycles: list[ResearchCycleRecord]
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for item in audits:
        label = _TIMELINE_LABELS.get(item.event_type, item.event_type)
        events.append(
            {
                "kind": "audit",
                "label": label,
                "occurred_at": _iso(item.occurred_at),
                "event_type": item.event_type,
            }
        )
    for cycle in cycles:
        events.append(
            {
                "kind": "cycle",
                "label": "CYCLE_COMPLETE",
                "occurred_at": _iso(cycle.created_at),
                "cycle_number": cycle.cycle_number,
                "phase_completed": cycle.phase_completed,
                "outcome": cycle.outcome,
            }
        )
    events.sort(key=lambda row: str(row.get("occurred_at") or ""))
    return events


def build_run_detail(
    uow_factory: UnitOfWorkFactory,
    research_run_id: str,
    *,
    runtime_instance_id: str | None,
    locally_supervised: bool,
) -> dict[str, Any]:
    with uow_factory.open() as uow:
        run = uow.research_runs.get(research_run_id)
        if run is None:
            uow.rollback()
            raise OperatorError(OperatorErrorCode.RUN_NOT_FOUND, "research run not found")
        program = uow.programs.get(run.program_id)
        auth = uow.authorization_sources.get(run.authorization_source_id)
        orchestration = uow.research_orchestrations.get(research_run_id)
        runtime = None
        if orchestration is not None and orchestration.owner_runtime_instance_id is not None:
            runtime = uow.runtime_instances.get(orchestration.owner_runtime_instance_id)
        elif runtime_instance_id is not None:
            runtime = uow.runtime_instances.get(runtime_instance_id)
        run_faults = uow.run_faults.list_for_research_run(research_run_id)
        latest_preflight: PreflightReportRecord | None = (
            uow.preflight_reports.latest_for_research_run(research_run_id)
        )
        budgets = uow.issued_budgets.list_for_research_run(research_run_id)
        consumptions = uow.budget_consumptions.list_for_research_run(research_run_id)
        hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
        experiments = uow.experiments.list_for_research_run(research_run_id)
        observations = uow.observations.list_for_research_run(research_run_id)
        evidence = uow.evidence.list_for_research_run(research_run_id)
        candidates = uow.candidates.list_for_research_run(research_run_id)
        proposals = uow.finding_proposals.list_for_research_run(research_run_id)
        findings = uow.findings.list_for_research_run(research_run_id)
        pending_v3 = uow.hunt_v3_queue.list_pending_for_research_run(research_run_id)
        cycles = uow.research_cycles.list_for_research_run(research_run_id)
        audits = uow.audit_events.list_for_subject("research_run", research_run_id)
        uow.rollback()
    totals = ledger_totals(consumptions)
    operational = project_effective_run_state(
        orchestration,
        runtime,
        run_faults,
        now=datetime.now(timezone.utc),
    )
    pending_proposals = [
        {
            "kind": "finding_proposal",
            "id": item.proposal_id,
            "state": item.state,
            "title": item.title,
        }
        for item in proposals
        if item.state in _PENDING_PROPOSAL_STATES
    ]
    pending_approvals = pending_proposals + [
        {
            "kind": "hunt_v3",
            "id": item.queue_id,
            "state": item.state,
            "family_id": item.family_id,
        }
        for item in pending_v3
    ]
    issued = budgets[0] if budgets else None
    payload: dict[str, Any] = {
        "research_run_id": research_run_id,
        "program_id": run.program_id,
        "program_name": None if program is None else program.name,
        "state": None if orchestration is None else orchestration.state,
        "operational": {
            "persisted_state": operational.persisted_state,
            "effective_state": operational.effective_state,
            "reason_codes": list(operational.reason_codes),
            "runtime_liveness": operational.runtime_liveness,
        },
        "run_faults": [
            {
                "fault_id": fault.fault_id,
                "component": fault.component,
                "phase": fault.phase,
                "fault_class": fault.fault_class,
                "fault_code": fault.fault_code,
                "fatal": fault.fatal,
                "occurred_at": _iso(fault.occurred_at),
                "diagnostic_summary": fault.diagnostic_summary,
                "attempt_id": fault.attempt_id,
                "experiment_id": fault.experiment_id,
                "runtime_instance_id": fault.runtime_instance_id,
                "resolved_at": _iso(fault.resolved_at),
            }
            for fault in run_faults
        ],
        "semantic_activities": [
            {
                "activity_id": activity.activity_id,
                "plane": activity.plane,
                "kind": activity.kind,
                "occurred_at": _iso(activity.occurred_at),
                "summary": activity.summary,
                "source_type": activity.source_type,
                "source_id": activity.source_id,
                "attempt_id": activity.attempt_id,
                "experiment_id": activity.experiment_id,
                "runtime_instance_id": activity.runtime_instance_id,
            }
            for activity in (activity_from_fault(fault) for fault in run_faults)
        ],
        "desired_action": None,
        "current_phase": None if orchestration is None else orchestration.current_phase,
        "cycle_number": None if orchestration is None else orchestration.cycle_number,
        "max_cycles": None if orchestration is None else orchestration.max_cycles,
        "stop_reason": None if orchestration is None else orchestration.stop_reason,
        "pause_reason": None if orchestration is None else orchestration.pause_reason,
        "last_phase": None if orchestration is None else orchestration.last_phase,
        "authorization_state": None if auth is None else auth.state,
        "runtime_instance_id": runtime_instance_id,
        "locally_supervised": locally_supervised,
        "lease": _lease_health(orchestration, now=datetime.now(timezone.utc)),
        "latest_preflight": (
            None if latest_preflight is None else preflight_record_to_mapping(latest_preflight)
        ),
        "request_count": totals.worker_requests,
        "worker_count": totals.worker_invocations,
        "model_count": totals.model_calls,
        "budget": None
        if issued is None
        else {
            "max_requests": issued.max_requests,
            "max_tool_calls": issued.max_tool_calls,
            "max_runtime_ms": issued.max_runtime_ms,
            "remaining_requests": max(issued.max_requests - totals.worker_requests, 0),
            "remaining_worker": max(issued.max_tool_calls - totals.worker_invocations, 0),
            "remaining_model_calls": None,
        },
        "hypothesis_count": len(hypotheses),
        "experiment_count": len(experiments),
        "observation_count": len(observations),
        "evidence_count": len(evidence),
        "candidate_count": len(candidates),
        "finding_proposal_count": len(proposals),
        "finding_count": len(findings),
        "pending_approval_count": len(pending_approvals),
        "pending_approvals": pending_approvals,
        "reconciliation": _reconciliation(orchestration),
        "timeline": _timeline(audits, cycles),
        "started_at": _iso(run.started_at),
        "updated_at": None if orchestration is None else _iso(orchestration.updated_at),
        "approval_mutations": "dashboard_existing_use_cases",
        "not_research_truth": True,
    }
    return redact_secret_keys(payload)


def build_run_list(
    uow_factory: UnitOfWorkFactory,
    *,
    runtime_instance_id: str | None,
    supervised_ids: frozenset[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    with uow_factory.open() as uow:
        runs = uow.research_runs.list_recent(limit=limit)
        rows: list[dict[str, Any]] = []
        for run in runs:
            program = uow.programs.get(run.program_id)
            orchestration = uow.research_orchestrations.get(run.research_run_id)
            runtime = (
                None
                if orchestration is None or orchestration.owner_runtime_instance_id is None
                else uow.runtime_instances.get(orchestration.owner_runtime_instance_id)
            )
            faults = uow.run_faults.list_for_research_run(run.research_run_id)
            operational = project_effective_run_state(
                orchestration,
                runtime,
                faults,
                now=datetime.now(timezone.utc),
            )
            latest = uow.preflight_reports.latest_for_research_run(run.research_run_id)
            rows.append(
                redact_secret_keys(
                    {
                        "research_run_id": run.research_run_id,
                        "program_id": run.program_id,
                        "program_name": None if program is None else program.name,
                        "state": None if orchestration is None else orchestration.state,
                        "operational": {
                            "persisted_state": operational.persisted_state,
                            "effective_state": operational.effective_state,
                            "reason_codes": list(operational.reason_codes),
                            "runtime_liveness": operational.runtime_liveness,
                        },
                        "current_phase": (
                            None if orchestration is None else orchestration.current_phase
                        ),
                        "cycle_number": (
                            None if orchestration is None else orchestration.cycle_number
                        ),
                        "stop_reason": None if orchestration is None else orchestration.stop_reason,
                        "pause_reason": (
                            None if orchestration is None else orchestration.pause_reason
                        ),
                        "target_reference": (
                            None if orchestration is None else orchestration.target_reference
                        ),
                        "started_at": _iso(run.started_at),
                        "updated_at": (
                            None if orchestration is None else _iso(orchestration.updated_at)
                        ),
                        "locally_supervised": run.research_run_id in supervised_ids,
                        "runtime_instance_id": runtime_instance_id,
                        "latest_preflight_status": None if latest is None else latest.status,
                        "reconciliation": _reconciliation(orchestration),
                    }
                )
            )
        uow.rollback()
    return rows


def build_program_list(uow_factory: UnitOfWorkFactory, *, limit: int = 50) -> list[dict[str, Any]]:
    with uow_factory.open() as uow:
        programs = uow.programs.list_recent(limit=limit)
        rows: list[dict[str, Any]] = []
        for program in programs:
            rules = uow.scope_rules_v2.list_for_program(program.program_id)
            rows.append(
                redact_secret_keys(
                    {
                        "program_id": program.program_id,
                        "name": program.name,
                        "handle": program.handle,
                        "platform": program.platform,
                        "created_at": _iso(program.created_at),
                        "scope_rules": len(rules),
                    }
                )
            )
        uow.rollback()
    return rows
