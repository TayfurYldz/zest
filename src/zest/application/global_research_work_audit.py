"""Authoritative global research work inventory. UNKNOWN is not zero."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zest.application.discovery.lifecycle import discovery_exit_audit
from zest.application.hunter_coverage_exhaustion import hunter_coverage_exhaustion_inventory
from zest.application.lifecycle_orphan_inventory import lifecycle_orphan_inventory
from zest.application.orchestration_obligations import unresolved_control_obligations
from zest.data.records import ExecutionAttemptState
from zest.research.discovery.frontier import FrontierEventKind
from zest.research.exploration import OpportunityKind

NOT_YET_CONNECTED = "NOT_YET_CONNECTED"


@dataclass(frozen=True)
class GlobalResearchWorkAudit:
    discovery_runnable: int
    discovery_handoff: int
    research_pending: int
    research_selected: int
    hunter_pending: int
    hunter_selected: int
    hunter_deferred: int
    coverage_actionable: int
    coverage_blocked: int
    coverage_missing_precondition: int
    coverage_resolved: int
    engine_dependency_pending: int
    auth_pending: object
    auth_selected: int
    auth_missing_precondition: int
    auth_failed: int
    authz_pending: object
    authz_selected: int
    authz_blocked: int
    authz_resolved: int
    workflow_pending: object
    workflow_selected: int
    workflow_blocked: int
    workflow_resolved: int
    identity_dependency_pending: int
    session_refresh_required: int
    mutation_pending: int
    mutation_selected: int
    mutation_executed: int
    mutation_resolved: int
    mutation_blocked: int
    protocol_pending: int
    protocol_selected: int
    protocol_executed: int
    protocol_resolved: int
    protocol_authority_blocked: int
    oast_pending: int
    oast_selected: int
    oast_armed: int
    oast_executed: int
    oast_waiting_callback: int
    oast_callback_received: int
    oast_correlated: int
    oast_no_callback: int
    oast_expired: int
    oast_ambiguous: int
    oast_evidence: int
    oast_blocked: int
    oast_missing_precondition: int
    differential_pending: int
    differential_selected: int
    differential_resolved: int
    differential_blocked: int
    differential_ambiguous: int
    invariant_pending: int
    invariant_selected: int
    invariant_holds: int
    invariant_violated: int
    invariant_unknown: int
    invariant_blocked: int
    chain_pending: int
    chain_selected: int
    chain_supported: int
    chain_rejected: int
    chain_blocked: int
    chain_unknown: int
    model_pending: int
    verification_pending: int
    human_pending: int
    unknown_outcome: int
    dispatching: int
    in_flight: int
    approval_waiting: int
    finding_proposal_pending: int
    finding_review_pending: int
    model_rejected: int
    sensor_uncorrelated: int
    cross_run_rejected: int
    budget_blocked: int
    authority_blocked: int
    unsupported: int
    blocked: int
    missing_precondition: int
    orphan_research_work: int
    unexplained_work: int
    deferred_engine_wiring: int
    hunter_coverage_exhausted_for_connected_engines: bool
    completion_allowed: bool
    completion_block_reasons: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "discovery_runnable": self.discovery_runnable,
            "discovery_handoff": self.discovery_handoff,
            "research_pending": self.research_pending,
            "research_selected": self.research_selected,
            "hunter_pending": self.hunter_pending,
            "hunter_selected": self.hunter_selected,
            "hunter_deferred": self.hunter_deferred,
            "coverage_actionable": self.coverage_actionable,
            "coverage_blocked": self.coverage_blocked,
            "coverage_missing_precondition": self.coverage_missing_precondition,
            "coverage_resolved": self.coverage_resolved,
            "engine_dependency_pending": self.engine_dependency_pending,
            "auth_pending": self.auth_pending,
            "auth_selected": self.auth_selected,
            "auth_missing_precondition": self.auth_missing_precondition,
            "auth_failed": self.auth_failed,
            "authz_pending": self.authz_pending,
            "authz_selected": self.authz_selected,
            "authz_blocked": self.authz_blocked,
            "authz_resolved": self.authz_resolved,
            "workflow_pending": self.workflow_pending,
            "workflow_selected": self.workflow_selected,
            "workflow_blocked": self.workflow_blocked,
            "workflow_resolved": self.workflow_resolved,
            "identity_dependency_pending": self.identity_dependency_pending,
            "session_refresh_required": self.session_refresh_required,
            "mutation_pending": self.mutation_pending,
            "mutation_selected": self.mutation_selected,
            "mutation_executed": self.mutation_executed,
            "mutation_resolved": self.mutation_resolved,
            "mutation_blocked": self.mutation_blocked,
            "protocol_pending": self.protocol_pending,
            "protocol_selected": self.protocol_selected,
            "protocol_executed": self.protocol_executed,
            "protocol_resolved": self.protocol_resolved,
            "protocol_authority_blocked": self.protocol_authority_blocked,
            "oast_pending": self.oast_pending,
            "oast_selected": self.oast_selected,
            "oast_armed": self.oast_armed,
            "oast_executed": self.oast_executed,
            "oast_waiting_callback": self.oast_waiting_callback,
            "oast_callback_received": self.oast_callback_received,
            "oast_correlated": self.oast_correlated,
            "oast_no_callback": self.oast_no_callback,
            "oast_expired": self.oast_expired,
            "oast_ambiguous": self.oast_ambiguous,
            "oast_evidence": self.oast_evidence,
            "oast_blocked": self.oast_blocked,
            "oast_missing_precondition": self.oast_missing_precondition,
            "differential_pending": self.differential_pending,
            "differential_selected": self.differential_selected,
            "differential_resolved": self.differential_resolved,
            "differential_blocked": self.differential_blocked,
            "differential_ambiguous": self.differential_ambiguous,
            "invariant_pending": self.invariant_pending,
            "invariant_selected": self.invariant_selected,
            "invariant_holds": self.invariant_holds,
            "invariant_violated": self.invariant_violated,
            "invariant_unknown": self.invariant_unknown,
            "invariant_blocked": self.invariant_blocked,
            "chain_pending": self.chain_pending,
            "chain_selected": self.chain_selected,
            "chain_supported": self.chain_supported,
            "chain_rejected": self.chain_rejected,
            "chain_blocked": self.chain_blocked,
            "chain_unknown": self.chain_unknown,
            "model_pending": self.model_pending,
            "verification_pending": self.verification_pending,
            "human_pending": self.human_pending,
            "unknown_outcome": self.unknown_outcome,
            "dispatching": self.dispatching,
            "in_flight": self.in_flight,
            "approval_waiting": self.approval_waiting,
            "finding_proposal_pending": self.finding_proposal_pending,
            "finding_review_pending": self.finding_review_pending,
            "model_rejected": self.model_rejected,
            "sensor_uncorrelated": self.sensor_uncorrelated,
            "cross_run_rejected": self.cross_run_rejected,
            "budget_blocked": self.budget_blocked,
            "authority_blocked": self.authority_blocked,
            "unsupported": self.unsupported,
            "blocked": self.blocked,
            "missing_precondition": self.missing_precondition,
            "orphan_research_work": self.orphan_research_work,
            "unexplained_work": self.unexplained_work,
            "deferred_engine_wiring": self.deferred_engine_wiring,
            "hunter_coverage_exhausted_for_connected_engines": (
                self.hunter_coverage_exhausted_for_connected_engines
            ),
            "completion_allowed": self.completion_allowed,
            "completion_block_reasons": list(self.completion_block_reasons),
        }


def global_research_work_audit(uow, research_run_id: str) -> GlobalResearchWorkAudit:
    discovery = discovery_exit_audit(uow, research_run_id, considered=True)
    frontiers = uow.frontier_items.list_for_research_run(research_run_id)
    handed_off_ids: list[str] = []
    for item in frontiers:
        events = uow.frontier_events.list_for_frontier(item.frontier_id)
        if not events:
            continue
        latest = max(events, key=lambda row: row.sequence)
        if latest.event_kind == FrontierEventKind.DEFERRED_TO_RESEARCH.value:
            handed_off_ids.append(item.frontier_id)
    candidates = uow.opportunity_selection_candidates.list_for_research_run(
        research_run_id
    )
    pending = [item for item in candidates if item.outcome == "PENDING"]
    hunter_opportunities = [
        item
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
        if item.opportunity_kind == OpportunityKind.HUNTER_COVERAGE_GAP.value
    ]
    hunter_ids = {item.opportunity_id for item in hunter_opportunities}
    exhaustion = hunter_coverage_exhaustion_inventory(uow, research_run_id)
    hunter_pending = exhaustion.hunter_pending
    coverage_actionable = exhaustion.coverage_actionable
    coverage_resolved = exhaustion.coverage_resolved
    handoff_pending = sum(
        1 for item in pending if item.source_system == "DISCOVERY_HANDOFF"
    )
    covered_frontiers = {
        item.source_refs[0]
        for item in candidates
        if item.source_system == "DISCOVERY_HANDOFF" and item.source_refs
    }
    orphan = sum(1 for frontier_id in handed_off_ids if frontier_id not in covered_frontiers)
    selections = [
        item
        for item in uow.research_selections.list_for_research_run(research_run_id)
        if item.outcome == "SELECT"
    ]
    hunter_selected = sum(1 for item in selections if item.opportunity_id in hunter_ids)
    experiments = uow.experiments.list_for_research_run(research_run_id)
    attempts = uow.execution_attempts.list_for_research_run(research_run_id)
    unknown_outcome = sum(
        1
        for item in attempts
        if item.state
        in {
            ExecutionAttemptState.DISPATCHING.value,
            ExecutionAttemptState.UNKNOWN_OUTCOME.value,
        }
    )
    dispatching = sum(
        1 for item in attempts if item.state == ExecutionAttemptState.DISPATCHING.value
    )
    in_flight = sum(
        1
        for item in attempts
        if item.state
        in {
            ExecutionAttemptState.AUTHORIZED.value,
            ExecutionAttemptState.DISPATCHING.value,
        }
    )
    obligations = unresolved_control_obligations(
        attempts=attempts,
        experiments=experiments,
        worker_results=uow.worker_results.list_for_research_run(research_run_id),
    )
    human_pending = discovery.waiting_human + sum(
        1 for item in obligations if item.code != "UNKNOWN_OUTCOME"
    )
    audits = uow.audit_events.list_for_subject("research_run", research_run_id)
    deferred_wiring = sum(
        1 for item in audits if item.event_type == "RESEARCH_WORK_DEFERRED_ENGINE_WIRING"
    )
    hunter_deferred = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_DEFERRED_ENGINE_WIRING",
            "RESEARCH_WORK_ENGINE_DEPENDENCY_PENDING",
        }
        and (item.payload or {}).get("selected_engine") == "HUNTER"
    )
    coverage_blocked = exhaustion.coverage_blocked
    coverage_missing_precondition = exhaustion.coverage_missing_precondition
    engine_dependency_pending = exhaustion.engine_dependency_pending
    missing_precondition = sum(
        1 for item in audits if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
    )
    verification_pending = 0
    finding_proposal_pending = 0
    finding_review_pending = 0
    if hasattr(uow, "candidates"):
        verification_pending += sum(
            1
            for item in uow.candidates.list_for_research_run(research_run_id)
            if item.state in {"OPEN", "VERIFYING"}
        )
    if hasattr(uow, "finding_proposals"):
        proposals = uow.finding_proposals.list_for_research_run(research_run_id)
        finding_proposal_pending = sum(
            1 for item in proposals if getattr(item, "state", None) == "PROPOSED"
        )
        finding_review_pending = sum(
            1 for item in proposals if getattr(item, "state", None) == "HUMAN_REVIEW"
        )
    admitted_hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
    experimented = {
        item.hypothesis_id for item in uow.experiments.list_for_research_run(research_run_id)
    }
    model_unscheduled = sum(
        1
        for item in admitted_hypotheses
        if item.hypothesis_id not in experimented and item.origin_reference is not None
    )
    model_pending = sum(
        1
        for item in pending
        if item.opportunity_kind
        in {
            OpportunityKind.HYPOTHESIS_FOLLOWUP.value,
            OpportunityKind.CONTROL_EXPERIMENT.value,
        }
    ) + model_unscheduled
    block_reasons: list[str] = []
    if discovery.runnable_discovery:
        block_reasons.append("DISCOVERY_RUNNABLE")
    if not discovery.discovery_exit_allowed and uow.discovery_run_configs.get(
        research_run_id
    ):
        block_reasons.extend(discovery.discovery_exit_block_reasons)
    if pending:
        block_reasons.append("RESEARCH_PENDING")
    if orphan or exhaustion.hunter_orphan:
        block_reasons.append("ORPHAN_RESEARCH_WORK")
    if exhaustion.hunter_selected_ownerless:
        block_reasons.append("SELECTED_OWNERLESS_HUNTER_WORK")
    if handed_off_ids and (handoff_pending or orphan):
        block_reasons.append("DISCOVERY_HANDOFF")
    if deferred_wiring:
        block_reasons.append("DEFERRED_ENGINE_WIRING")
    if unknown_outcome:
        block_reasons.append("UNKNOWN_OUTCOME")
    if human_pending:
        block_reasons.append("HUMAN_PENDING")
    if coverage_actionable:
        block_reasons.append("ACTIONABLE_COVERAGE_DEBT")
    if engine_dependency_pending:
        block_reasons.append("ENGINE_DEPENDENCY_PENDING")
    identity_dependency_pending = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        and "MISSING_IDENTITY_PRECONDITION"
        in list((item.payload or {}).get("reason_codes") or [])
    )
    sessions = uow.session_contexts.list_for_research_run(research_run_id)
    session_refresh_required = sum(1 for item in sessions if item.state == "EXPIRED")
    auth_failed = sum(1 for item in sessions if item.state == "FAILED")
    auth_kind = OpportunityKind.AUTHENTICATION.value
    authz_kind = OpportunityKind.AUTHORIZATION_DIFFERENTIAL.value
    workflow_kind = OpportunityKind.WORKFLOW_STATE_TRANSITION.value
    auth_pending_count = sum(1 for item in pending if item.opportunity_kind == auth_kind)
    authz_pending_count = sum(1 for item in pending if item.opportunity_kind == authz_kind)
    workflow_pending_count = sum(
        1 for item in pending if item.opportunity_kind == workflow_kind
    )
    kind_by_opportunity = {
        item.opportunity_id: item.opportunity_kind
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
    }
    auth_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == auth_kind
    )
    authz_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == authz_kind
    )
    workflow_selected = sum(
        1
        for item in selections
        if kind_by_opportunity.get(item.opportunity_id) == workflow_kind
    )
    auth_missing_precondition = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        and (item.payload or {}).get("selected_engine") == "AUTHENTICATION"
    )
    authz_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
        }
        and (item.payload or {}).get("selected_engine") == "AUTHORIZATION"
    )
    workflow_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
        }
        and (item.payload or {}).get("selected_engine") == "WORKFLOW"
    )
    authz_resolved = sum(
        1
        for item in audits
        if item.event_type == "IDENTITY_ENGINE_COVERAGE_UPDATED"
        and (item.payload or {}).get("opportunity_kind") == authz_kind
    )
    workflow_resolved = sum(
        1
        for item in audits
        if item.event_type == "IDENTITY_ENGINE_COVERAGE_UPDATED"
        and (item.payload or {}).get("opportunity_kind") == workflow_kind
    )
    mutation_kind = OpportunityKind.MUTATION_VARIANT.value
    protocol_kind = OpportunityKind.PROTOCOL_STEP.value
    mutation_pending_count = sum(1 for item in pending if item.opportunity_kind == mutation_kind)
    protocol_pending_count = sum(1 for item in pending if item.opportunity_kind == protocol_kind)
    mutation_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == mutation_kind
    )
    protocol_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == protocol_kind
    )
    mutation_executed = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_COMPILED"
        and (item.payload or {}).get("selected_engine") == "MUTATION"
    )
    protocol_executed = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_COMPILED"
        and (item.payload or {}).get("selected_engine") == "PROTOCOL"
    )
    mutation_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_CORE_DENIED",
        }
        and (item.payload or {}).get("selected_engine") == "MUTATION"
    )
    protocol_authority_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_CORE_DENIED",
        }
        and (
            (item.payload or {}).get("side_effect") == 3
            or (item.payload or {}).get("native_side_effect") == 3
            or (item.payload or {}).get("compiled_capability") == "http.raw_exchange"
            or (item.payload or {}).get("native_capability") == "http.raw_exchange"
            or (item.payload or {}).get("hunter_family")
            in {
                "HTTP_REQUEST_SMUGGLING_DESYNC",
                "HTTP_CACHE_POISONING_DECEPTION",
            }
        )
    )
    mutation_resolved = sum(
        1
        for item in audits
        if item.event_type == "MUTATION_PROTOCOL_COVERAGE_UPDATED"
        and (item.payload or {}).get("opportunity_kind") == mutation_kind
    )
    protocol_resolved = sum(
        1
        for item in audits
        if item.event_type == "MUTATION_PROTOCOL_COVERAGE_UPDATED"
        and (item.payload or {}).get("opportunity_kind") == protocol_kind
    )
    oast_kind = OpportunityKind.OAST_INTERACTION.value
    oast_pending_count = sum(1 for item in pending if item.opportunity_kind == oast_kind)
    oast_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == oast_kind
    )
    oast_armed = sum(1 for item in audits if item.event_type == "OAST_ARMED")
    oast_executed = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_COMPILED"
        and (item.payload or {}).get("selected_engine") == "OAST"
    )
    oast_callback_received = sum(
        1
        for item in audits
        if item.event_type in {"OAST_CALLBACK_ADMITTED", "OAST_CALLBACK_EXPIRED", "OAST_CALLBACK_UNKNOWN_TOKEN"}
    )
    oast_correlated = len(uow.oast_admissions.list_for_research_run(research_run_id)) if hasattr(
        uow.oast_admissions, "list_for_research_run"
    ) else 0
    oast_no_callback = sum(1 for item in audits if item.event_type == "OAST_NO_CALLBACK_TIMEOUT")
    oast_expired = sum(1 for item in audits if item.event_type in {"OAST_ARM_EXPIRED", "OAST_CALLBACK_EXPIRED"})
    oast_ambiguous = 0
    oast_evidence = sum(
        1
        for item in audits
        if item.event_type == "OAST_COVERAGE_UPDATED"
        and (item.payload or {}).get("assessment_outcome") == "CONSISTENT_WITH_PREDICTION"
    )
    oast_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_CORE_DENIED",
        }
        and (item.payload or {}).get("selected_engine") == "OAST"
    )
    oast_missing_precondition = sum(
        1
        for item in audits
        if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        and (item.payload or {}).get("selected_engine") == "OAST"
    )
    now_correlations = uow.oast_correlations.list_for_research_run(research_run_id)
    oast_waiting_callback = 0
    for correlation in now_correlations:
        admission = uow.oast_admissions.get_by_correlation(correlation.correlation_id)
        timed_out = any(
            item.event_type == "OAST_NO_CALLBACK_TIMEOUT"
            and (item.payload or {}).get("correlation_id") == correlation.correlation_id
            for item in audits
        )
        if admission is None and not timed_out:
            oast_waiting_callback += 1
    protocol_pending_actionable = protocol_pending_count + len(exhaustion.protocol_pending_keys)
    if identity_dependency_pending:
        block_reasons.append("IDENTITY_DEPENDENCY_PENDING")
    if auth_pending_count or authz_pending_count or workflow_pending_count:
        block_reasons.append("AUTH_AUTHZ_WORKFLOW_PENDING")
    if mutation_pending_count:
        block_reasons.append("MUTATION_PENDING")
    if protocol_pending_count or exhaustion.protocol_pending_keys:
        block_reasons.append("PROTOCOL_PENDING")
    if oast_pending_count:
        block_reasons.append("OAST_PENDING")
    if oast_waiting_callback:
        block_reasons.append("OAST_WAITING_CALLBACK")
    differential_kind = OpportunityKind.DIFFERENTIAL.value
    invariant_kind = OpportunityKind.INVARIANT.value
    chain_kind = OpportunityKind.CHAIN.value
    differential_pending_count = sum(1 for item in pending if item.opportunity_kind == differential_kind)
    invariant_pending_count = sum(1 for item in pending if item.opportunity_kind == invariant_kind)
    chain_pending_count = sum(1 for item in pending if item.opportunity_kind == chain_kind)
    differential_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == differential_kind
    )
    invariant_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == invariant_kind
    )
    chain_selected = sum(
        1 for item in selections if kind_by_opportunity.get(item.opportunity_id) == chain_kind
    )
    differential_resolved = sum(
        1
        for item in audits
        if item.event_type == "DIFFERENTIAL_EVALUATED"
        and (item.payload or {}).get("judgement")
        in {"EQUIVALENT", "NOISE_ONLY", "CONTROLLED_SIGNAL"}
    )
    differential_ambiguous = sum(
        1
        for item in audits
        if item.event_type == "DIFFERENTIAL_EVALUATED"
        and (item.payload or {}).get("judgement") in {"AMBIGUOUS", "INSUFFICIENT_CONTROL"}
    )
    differential_blocked = sum(
        1
        for item in audits
        if item.event_type
        in {
            "RESEARCH_WORK_BLOCKED_SCOPE",
            "RESEARCH_WORK_BLOCKED_POLICY",
            "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
            "RESEARCH_WORK_CORE_DENIED",
        }
        and (item.payload or {}).get("selected_engine") == "DIFFERENTIAL"
    )
    invariant_holds = sum(
        1
        for item in audits
        if item.event_type == "INVARIANT_EVALUATED"
        and (item.payload or {}).get("outcome") == "HOLDS"
    )
    invariant_violated = sum(
        1
        for item in audits
        if item.event_type == "INVARIANT_EVALUATED"
        and (item.payload or {}).get("outcome") == "VIOLATED"
    )
    invariant_unknown = sum(
        1
        for item in audits
        if item.event_type == "INVARIANT_EVALUATED"
        and (item.payload or {}).get("outcome") in {"UNKNOWN", "INSUFFICIENT_DATA"}
    )
    invariant_blocked = sum(
        1
        for item in audits
        if (
            item.event_type == "INVARIANT_EVALUATED"
            and (item.payload or {}).get("outcome") == "BLOCKED_BY_AUTHORITY"
        )
        or (
            item.event_type
            in {
                "RESEARCH_WORK_BLOCKED_SCOPE",
                "RESEARCH_WORK_BLOCKED_POLICY",
                "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING",
                "RESEARCH_WORK_CORE_DENIED",
            }
            and (item.payload or {}).get("selected_engine") == "INVARIANT"
        )
    )
    chain_supported = sum(
        1
        for item in audits
        if item.event_type == "CHAIN_EVALUATED"
        and (item.payload or {}).get("linkage") == "SUPPORTED"
    )
    chain_rejected = sum(
        1
        for item in audits
        if item.event_type == "CHAIN_EVALUATED"
        and (item.payload or {}).get("linkage") in {"INSUFFICIENT_LINKAGE", "REJECTED"}
    )
    chain_blocked = sum(
        1
        for item in audits
        if item.event_type == "CHAIN_EVALUATED"
        and (item.payload or {}).get("linkage") == "BLOCKED"
    )
    chain_unknown = sum(
        1
        for item in audits
        if item.event_type == "CHAIN_EVALUATED"
        and (item.payload or {}).get("impact_status") == "UNKNOWN"
        and (item.payload or {}).get("linkage") not in {"SUPPORTED", "INSUFFICIENT_LINKAGE", "REJECTED", "BLOCKED"}
    )
    dangling = lifecycle_orphan_inventory(uow, research_run_id)
    model_rejected = 0
    if hasattr(uow, "research_admissions"):
        model_rejected = sum(
            1
            for item in uow.research_admissions.list_for_research_run(research_run_id)
            if item.outcome != "ADMITTED"
        )
    cross_run_rejected = sum(
        1
        for item in audits
        if item.event_type == "OAST_CALLBACK_REJECTED"
        and bool((item.payload or {}).get("cross_run_rejected"))
    )
    callback_audits = uow.audit_events.list_for_subject_type("OAST_CALLBACK")
    sensor_uncorrelated = sum(
        1 for item in audits if item.event_type == "OAST_CALLBACK_UNKNOWN_TOKEN"
    ) + sum(
        1
        for item in callback_audits
        if item.event_type == "OAST_CALLBACK_UNKNOWN_TOKEN"
    )
    budget_blocked = sum(
        1
        for item in audits
        if item.event_type in {"BUDGET_EXHAUSTED", "BUDGET_CONSUMPTION_REJECTED"}
    )
    approval_waiting = sum(
        1 for item in obligations if item.code == "REAUTHORIZATION_REQUIRED"
    )
    authority_blocked = protocol_authority_blocked + sum(
        1 for item in audits if item.event_type == "RESEARCH_WORK_CORE_DENIED"
    )
    if dangling.total:
        block_reasons.append("ORPHAN_RESEARCH_WORK")
    if differential_pending_count:
        block_reasons.append("DIFFERENTIAL_PENDING")
    if invariant_pending_count:
        block_reasons.append("INVARIANT_PENDING")
    if chain_pending_count:
        block_reasons.append("CHAIN_PENDING")
    if verification_pending:
        block_reasons.append("VERIFICATION_PENDING")
    if finding_review_pending or finding_proposal_pending:
        block_reasons.append("FINDING_REVIEW_PENDING")
    if model_pending:
        block_reasons.append("MODEL_PENDING")
    return GlobalResearchWorkAudit(
        discovery_runnable=discovery.runnable_discovery,
        discovery_handoff=len(handed_off_ids),
        research_pending=len(pending),
        research_selected=len(selections),
        hunter_pending=hunter_pending,
        hunter_selected=hunter_selected,
        hunter_deferred=hunter_deferred,
        coverage_actionable=coverage_actionable,
        coverage_blocked=coverage_blocked,
        coverage_missing_precondition=coverage_missing_precondition,
        coverage_resolved=coverage_resolved,
        engine_dependency_pending=engine_dependency_pending,
        auth_pending=auth_pending_count,
        auth_selected=auth_selected,
        auth_missing_precondition=auth_missing_precondition,
        auth_failed=auth_failed,
        authz_pending=authz_pending_count,
        authz_selected=authz_selected,
        authz_blocked=authz_blocked,
        authz_resolved=authz_resolved,
        workflow_pending=workflow_pending_count,
        workflow_selected=workflow_selected,
        workflow_blocked=workflow_blocked,
        workflow_resolved=workflow_resolved,
        identity_dependency_pending=identity_dependency_pending,
        session_refresh_required=session_refresh_required,
        mutation_pending=mutation_pending_count,
        mutation_selected=mutation_selected,
        mutation_executed=mutation_executed,
        mutation_resolved=mutation_resolved,
        mutation_blocked=mutation_blocked,
        protocol_pending=protocol_pending_actionable,
        protocol_selected=protocol_selected,
        protocol_executed=protocol_executed,
        protocol_resolved=protocol_resolved,
        protocol_authority_blocked=protocol_authority_blocked,
        oast_pending=oast_pending_count,
        oast_selected=oast_selected,
        oast_armed=oast_armed,
        oast_executed=oast_executed,
        oast_waiting_callback=oast_waiting_callback,
        oast_callback_received=oast_callback_received,
        oast_correlated=oast_correlated,
        oast_no_callback=oast_no_callback,
        oast_expired=oast_expired,
        oast_ambiguous=oast_ambiguous,
        oast_evidence=oast_evidence,
        oast_blocked=oast_blocked,
        oast_missing_precondition=oast_missing_precondition,
        differential_pending=differential_pending_count,
        differential_selected=differential_selected,
        differential_resolved=differential_resolved,
        differential_blocked=differential_blocked,
        differential_ambiguous=differential_ambiguous,
        invariant_pending=invariant_pending_count,
        invariant_selected=invariant_selected,
        invariant_holds=invariant_holds,
        invariant_violated=invariant_violated,
        invariant_unknown=invariant_unknown,
        invariant_blocked=invariant_blocked,
        chain_pending=chain_pending_count,
        chain_selected=chain_selected,
        chain_supported=chain_supported,
        chain_rejected=chain_rejected,
        chain_blocked=chain_blocked,
        chain_unknown=chain_unknown,
        model_pending=model_pending,
        verification_pending=verification_pending,
        human_pending=human_pending,
        unknown_outcome=unknown_outcome,
        dispatching=dispatching,
        in_flight=in_flight,
        approval_waiting=approval_waiting,
        finding_proposal_pending=finding_proposal_pending,
        finding_review_pending=finding_review_pending,
        model_rejected=model_rejected,
        sensor_uncorrelated=sensor_uncorrelated,
        cross_run_rejected=cross_run_rejected,
        budget_blocked=budget_blocked,
        authority_blocked=authority_blocked,
        unsupported=discovery.unsupported,
        blocked=discovery.blocked_scope + discovery.blocked_policy,
        missing_precondition=missing_precondition,
        orphan_research_work=orphan + exhaustion.hunter_orphan + dangling.total,
        unexplained_work=discovery.unexplained + exhaustion.hunter_orphan,
        deferred_engine_wiring=deferred_wiring,
        hunter_coverage_exhausted_for_connected_engines=(
            exhaustion.hunter_coverage_exhausted_for_connected_engines
        ),
        completion_allowed=not block_reasons,
        completion_block_reasons=tuple(dict.fromkeys(block_reasons)),
    )
