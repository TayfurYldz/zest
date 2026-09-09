"""Read-only Phase 8 acceptance trace from PostgreSQL. Not research truth."""

from __future__ import annotations

from typing import Any

from zest.application.global_research_work_audit import global_research_work_audit


def build_acceptance_trace(uow, research_run_id: str) -> dict[str, Any]:
    run = uow.research_runs.get(research_run_id)
    orchestration = uow.research_orchestrations.get(research_run_id)
    audit = global_research_work_audit(uow, research_run_id)
    proposals = []
    if hasattr(uow, "finding_proposals"):
        proposals = uow.finding_proposals.list_for_research_run(research_run_id)
    reviews = []
    if hasattr(uow, "human_reviews"):
        for proposal in proposals:
            review = uow.human_reviews.get_for_proposal(proposal.proposal_id)
            if review is not None:
                reviews.append(review)
    oast_arms = uow.oast_correlations.list_for_research_run(research_run_id)
    admissions = uow.research_admissions.list_for_research_run(research_run_id)
    attempts = uow.execution_attempts.list_for_research_run(research_run_id)
    start_events = [
        item
        for item in uow.audit_events.list_for_subject("research_run", research_run_id)
        if item.event_type in {"ORCHESTRATION_STARTED", "PREFLIGHT_RECORDED"}
    ]
    core_events = [
        item
        for item in uow.audit_events.list_for_subject("research_run", research_run_id)
        if item.event_type
        in {
            "RESEARCH_WORK_CORE_DENIED",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_COMPILED",
            "RESEARCH_WORK_MISSING_PRECONDITION",
        }
    ]
    selection_traces = [
        item
        for item in uow.audit_events.list_for_subject("research_run", research_run_id)
        if item.event_type == "RESEARCH_SELECTION_TRACE"
    ]
    return {
        "run_id": research_run_id,
        "program_id": None if run is None else run.program_id,
        "target": None if orchestration is None else orchestration.target_reference,
        "START_source": "reconstruct_start_command via ZestdRuntime.start_run",
        "supervisor_owner": (
            None if orchestration is None else orchestration.owner_runtime_instance_id
        ),
        "cycles": None if orchestration is None else orchestration.cycle_number,
        "orchestration_state": None if orchestration is None else orchestration.state,
        "stop_reason": None if orchestration is None else orchestration.stop_reason,
        "last_phase": None if orchestration is None else orchestration.last_phase,
        "start_events": len(start_events),
        "selected_work": [
            {
                "opportunity_id": item.opportunity_id,
                "outcome": item.outcome,
            }
            for item in uow.research_selections.list_for_research_run(research_run_id)
        ],
        "attempts": [
            {
                "attempt_id": item.attempt_id,
                "capability": item.worker_capability,
                "state": item.state,
                "side_effect_level": item.side_effect_level,
            }
            for item in attempts
        ],
        "observation_ids": [
            item.observation_id
            for item in uow.observations.list_for_research_run(research_run_id)
        ],
        "assessment_ids": [
            item.assessment_id
            for item in uow.hypothesis_assessments.list_for_research_run(research_run_id)
        ],
        "evidence_ids": [
            item.evidence_id for item in uow.evidence.list_for_research_run(research_run_id)
        ],
        "candidate_ids": [
            item.candidate_id for item in uow.candidates.list_for_research_run(research_run_id)
        ],
        "verification_ids": [
            item.verification_id
            for item in uow.verifications.list_for_research_run(research_run_id)
        ],
        "finding_proposal_ids": [item.proposal_id for item in proposals],
        "finding_proposal_states": [item.state for item in proposals],
        "human_review_states": [item.decision for item in reviews],
        "oast_arm_ids": [item.correlation_id for item in oast_arms],
        "model_admissions": [
            {"outcome": item.outcome, "reason_code": item.reason_code} for item in admissions
        ],
        "core_and_compile_events": [
            {"event_type": item.event_type, "payload": dict(item.payload or {})}
            for item in core_events
        ],
        "selection_traces": [
            {"event_type": item.event_type, "payload": dict(item.payload or {})}
            for item in selection_traces
        ],
        "completion_allowed": audit.completion_allowed,
        "completion_block_reasons": list(audit.completion_block_reasons),
        "audit": audit.as_payload(),
    }
