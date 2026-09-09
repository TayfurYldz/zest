"""Pack bounded multi-engine ResearchReasoningContext. Does not invoke a model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.program_research_context import load_program_research_context
from zest.research.context import (
    ChainContextSource,
    ChangeEventContextSource,
    DifferentialContextSource,
    EngineSignalSource,
    ExperimentSource,
    HypothesisSource,
    InferenceSource,
    InvariantContextSource,
    ObservationSource,
    OpportunityContextSource,
)
from zest.research.model_context_census import CONNECTED_MODEL_CONTEXT_ENGINES
from zest.safe_data import redact_secret_keys

ENGINE_SIGNAL_BOUND = 8
MODEL_CONTEXT_NOT_CONNECTED = 0


@dataclass(frozen=True)
class PackedResearchReasoningContext:
    observations: tuple[ObservationSource, ...]
    prior_hypotheses: tuple[HypothesisSource, ...]
    experiments: tuple[ExperimentSource, ...]
    inferences: tuple[InferenceSource, ...]
    differentials: tuple[DifferentialContextSource, ...]
    invariant_hypotheses: tuple[InvariantContextSource, ...]
    chain_hypotheses: tuple[ChainContextSource, ...]
    research_opportunities: tuple[OpportunityContextSource, ...]
    change_events: tuple[ChangeEventContextSource, ...]
    engine_signals: tuple[EngineSignalSource, ...]
    unresolved_questions: tuple[str, ...]
    connected_engines: tuple[str, ...]
    not_connected: int


def pack_research_reasoning_context(
    uow,
    *,
    research_run_id: str,
    research_question: str,
    extra_unresolved: tuple[str, ...] = (),
    opportunity_id: str | None = None,
    differential_id: str | None = None,
    invariant_id: str | None = None,
    chain_id: str | None = None,
    change_event_id: str | None = None,
) -> PackedResearchReasoningContext:
    """Load ranked, redacted engine summaries. Does not dump SoR."""

    del research_question
    observations = tuple(
        ObservationSource(
            observation_id=record.observation_id,
            observation_kind=record.observation_kind,
            payload=dict(redact_secret_keys(record.payload, "observation_payload")),
        )
        for record in _list(uow, "observations", research_run_id)
    )
    prior_hypotheses = tuple(
        HypothesisSource(hypothesis_id=record.hypothesis_id, claim=record.claim)
        for record in _list(uow, "hypotheses", research_run_id)
    )
    experiments = tuple(
        ExperimentSource(
            experiment_id=record.experiment_id,
            hypothesis_id=record.hypothesis_id,
            execution_state=record.execution_state,
        )
        for record in _list(uow, "experiments", research_run_id)
    )
    inferences = tuple(
        InferenceSource(
            inference_id=record.inference_id,
            statement=record.statement,
            source_references=tuple(record.source_refs),
        )
        for record in _list(uow, "target_inferences", research_run_id)
    )
    differentials = _differentials(uow, research_run_id, differential_id)
    invariants = _invariants(uow, research_run_id, invariant_id)
    chains = _chains(uow, research_run_id, chain_id)
    opportunities = _opportunities(uow, research_run_id, opportunity_id)
    changes = _changes(uow, research_run_id, change_event_id)
    signals, unresolved = _engine_signals(uow, research_run_id)
    return PackedResearchReasoningContext(
        observations=observations,
        prior_hypotheses=prior_hypotheses,
        experiments=experiments,
        inferences=inferences,
        differentials=differentials,
        invariant_hypotheses=invariants,
        chain_hypotheses=chains,
        research_opportunities=opportunities,
        change_events=changes,
        engine_signals=signals,
        unresolved_questions=tuple(extra_unresolved) + unresolved,
        connected_engines=CONNECTED_MODEL_CONTEXT_ENGINES,
        not_connected=MODEL_CONTEXT_NOT_CONNECTED,
    )


def _list(uow, attr: str, research_run_id: str) -> list[Any]:
    repo = getattr(uow, attr, None)
    if repo is None or not hasattr(repo, "list_for_research_run"):
        return []
    return list(repo.list_for_research_run(research_run_id))


def _bound(records: list[Any], identity, limit: int = ENGINE_SIGNAL_BOUND) -> list[Any]:
    return sorted(records, key=identity)[:limit]


def _differentials(uow, research_run_id: str, pinned: str | None):
    records = _list(uow, "differential_observations", research_run_id)
    if pinned:
        match = uow.differential_observations.get(pinned)
        if match is not None and match.research_run_id == research_run_id:
            records = [match] + [item for item in records if item.differential_id != pinned]
    return tuple(
        DifferentialContextSource(
            differential_id=item.differential_id,
            statement=(
                "Controlled or diagnostic differential. Difference is not a "
                "vulnerability and not Evidence."
            ),
            source_references=tuple(item.source_refs),
            interpretation=item.interpretation,
            payload={
                "changed_dimensions": list(item.changed_dimensions),
                "common_dimensions": list(getattr(item, "common_dimensions", ())),
                "strategy_version": getattr(item, "strategy_version", ""),
            },
        )
        for item in _bound(records, lambda row: row.differential_id)
    )


def _invariants(uow, research_run_id: str, pinned: str | None):
    records = _list(uow, "invariant_hypotheses", research_run_id)
    if pinned:
        match = uow.invariant_hypotheses.get(pinned)
        if match is not None and match.research_run_id == research_run_id:
            records = [match] + [item for item in records if item.invariant_id != pinned]
    return tuple(
        InvariantContextSource(
            invariant_id=item.invariant_id,
            statement=item.expected_behavior,
            source_references=tuple(item.source_refs),
            payload={
                "status": item.status,
                "kind": item.invariant_kind,
                "counterexample_refs": list(item.counterexample_refs),
                "unknown_is_not_safe": item.status == "UNKNOWN",
            },
        )
        for item in _bound(records, lambda row: row.invariant_id)
    )


def _chains(uow, research_run_id: str, pinned: str | None):
    records = _list(uow, "chain_hypotheses", research_run_id)
    if pinned:
        match = uow.chain_hypotheses.get(pinned)
        if match is not None and match.research_run_id == research_run_id:
            records = [match] + [item for item in records if item.chain_id != pinned]
    return tuple(
        ChainContextSource(
            chain_id=item.chain_id,
            statement=(
                "Chain hypothesis. Sequence is not proven causality and not an exploit."
            ),
            source_references=tuple(item.source_refs),
            payload={
                "depth": max(0, len(item.steps) - 1),
                "structural_identity": item.structural_identity,
            },
        )
        for item in _bound(records, lambda row: row.chain_id)
    )


def _opportunities(uow, research_run_id: str, pinned: str | None):
    records = _list(uow, "research_opportunities", research_run_id)
    pending = [
        item
        for item in _list(uow, "opportunity_selection_candidates", research_run_id)
        if item.outcome == "PENDING"
    ]
    if pinned:
        match = uow.research_opportunities.get(pinned)
        if match is not None and match.research_run_id == research_run_id:
            records = [match] + [item for item in records if item.opportunity_id != pinned]
    sources = [
        OpportunityContextSource(
            opportunity_id=item.opportunity_id,
            statement=(
                "Research opportunity. Selection is not Hypothesis truth and not "
                "Core authorization."
            ),
            source_references=tuple(item.source_refs),
            payload={
                "opportunity_kind": item.opportunity_kind,
                "mode": item.mode,
                "structural_identity": item.structural_identity,
            },
        )
        for item in _bound(records, lambda row: row.opportunity_id)
    ]
    for item in _bound(pending, lambda row: row.candidate_id):
        sources.append(
            OpportunityContextSource(
                opportunity_id=item.candidate_id,
                statement="Pending scheduler candidate. Not yet selected and not executed.",
                source_references=tuple(item.source_refs),
                payload={
                    "opportunity_kind": item.opportunity_kind,
                    "source_system": item.source_system,
                    "outcome": item.outcome,
                    "pending_global_scheduler": True,
                },
            )
        )
    return tuple(sources)


def _changes(uow, research_run_id: str, pinned: str | None):
    records = _list(uow, "change_events", research_run_id)
    if pinned:
        match = uow.change_events.get(pinned)
        if match is not None and match.research_run_id == research_run_id:
            records = [match] + [item for item in records if item.change_event_id != pinned]
    return tuple(
        ChangeEventContextSource(
            change_event_id=item.change_event_id,
            statement=item.statement,
            source_references=tuple(item.source_refs),
            payload={"category": item.category},
        )
        for item in _bound(records, lambda row: row.change_event_id)
    )


def _engine_signals(uow, research_run_id: str) -> tuple[tuple[EngineSignalSource, ...], tuple[str, ...]]:
    run = uow.research_runs.get(research_run_id)
    program_id = run.program_id if run is not None else research_run_id
    inventory = global_research_work_audit(uow, research_run_id)
    orchestration = uow.research_orchestrations.get(research_run_id)
    ceiling = orchestration.side_effect_ceiling if orchestration is not None else None
    program_ctx = load_program_research_context(uow, program_id) if run is not None else None
    facts = _list(uow, "discovery_facts", research_run_id)
    frontiers = _list(uow, "frontier_items", research_run_id)
    sensors = _list(uow, "sensor_observations", research_run_id)
    observations = _list(uow, "observations", research_run_id)
    sessions = _list(uow, "session_contexts", research_run_id)
    families = []
    if hasattr(uow, "hunter_families") and hasattr(uow.hunter_families, "list_enabled"):
        families = uow.hunter_families.list_enabled()
    coverage = _list(uow, "coverage_debt_snapshots", research_run_id)
    tokens = _list(uow, "oast_tokens", research_run_id)
    evidence = _list(uow, "evidence", research_run_id)
    candidates = _list(uow, "candidates", research_run_id)
    verifications = _list(uow, "verifications", research_run_id)
    proposals = _list(uow, "finding_proposals", research_run_id)
    pending_work = [
        item
        for item in _list(uow, "opportunity_selection_candidates", research_run_id)
        if item.outcome == "PENDING"
    ]
    http_obs = [item for item in observations if "http" in item.observation_kind.lower()]
    browser_obs = [item for item in sensors if "browser" in item.sensor_id.lower()]
    signals: list[EngineSignalSource] = []

    def add(engine: str, statement: str, refs: tuple[str, ...], payload: dict[str, Any]) -> None:
        signals.append(
            EngineSignalSource(
                signal_id=f"eng:{engine.lower()}",
                engine=engine,
                statement=statement,
                source_references=refs or (research_run_id,),
                payload=payload,
            )
        )

    allow = []
    deny = []
    if program_ctx is not None:
        for rule in program_ctx.compiled_scope.rules:
            effect = getattr(rule.effect, "value", rule.effect)
            host = rule.host or rule.host_pattern or "*"
            if str(effect).upper() == "DENY":
                deny.append(host)
            else:
                allow.append(host)
    add(
        "TARGET_SCOPE",
        "Program, authorization source, and compiled scope boundaries.",
        (program_id, research_run_id),
        {
            "program_id": program_id,
            "authorization_source_id": (
                run.authorization_source_id if run is not None else None
            ),
            "allowed_hosts": allow[:ENGINE_SIGNAL_BOUND],
            "denied_hosts": deny[:ENGINE_SIGNAL_BOUND],
        },
    )
    add(
        "AUTHORITY",
        "Side-effect ceiling and Core deny status. Core DENY is not coverage.",
        (research_run_id,),
        {
            "side_effect_ceiling": ceiling,
            "protocol_authority_blocked": inventory.protocol_authority_blocked,
            "human_pending": inventory.human_pending,
            "unknown_outcome": inventory.unknown_outcome,
            "core_deny_is_not_coverage": True,
        },
    )
    add(
        "DISCOVERY",
        "Observed discovery facts. Absence from this pack is not absence from SoR.",
        tuple(item.fact_id for item in _bound(facts, lambda row: row.fact_id)) or (research_run_id,),
        {
            "fact_count": len(facts),
            "paths": [
                item.normalized_path
                for item in _bound(facts, lambda row: row.fact_id)
                if item.normalized_path
            ],
            "methods": [
                item.http_method
                for item in _bound(facts, lambda row: row.fact_id)
                if item.http_method
            ],
        },
    )
    add(
        "FRONTIER",
        "Frontier dispositions. Deferred work is not automatically authorized.",
        tuple(item.frontier_id for item in _bound(frontiers, lambda row: row.frontier_id))
        or (research_run_id,),
        {"frontier_count": len(frontiers), "discovery_handoff": inventory.discovery_handoff},
    )
    add(
        "HTTP",
        "HTTP observations present in this run. Payloads are redacted.",
        tuple(item.observation_id for item in _bound(http_obs, lambda row: row.observation_id))
        or (research_run_id,),
        {"http_observation_count": len(http_obs)},
    )
    add(
        "BROWSER",
        "Browser/sensor observations. Network events are not Evidence by themselves.",
        tuple(item.observation_id for item in _bound(browser_obs, lambda row: row.observation_id))
        or (research_run_id,),
        {"browser_sensor_count": len(browser_obs), "sensor_observation_count": len(sensors)},
    )
    add(
        "HUNTER",
        "Hunter family registry and pending hunter work.",
        tuple(f"{item.family_id}:{item.version}" for item in families[:ENGINE_SIGNAL_BOUND])
        or (research_run_id,),
        {
            "enabled_family_count": len(families),
            "hunter_pending": inventory.hunter_pending,
            "hunter_selected": inventory.hunter_selected,
        },
    )
    latest_coverage = _bound(coverage, lambda row: row.snapshot_id, 1)
    add(
        "COVERAGE",
        "Coverage debt. Actionable cells are not Findings. UNKNOWN is not safe.",
        tuple(item.snapshot_id for item in latest_coverage) or (research_run_id,),
        {
            "coverage_actionable": inventory.coverage_actionable,
            "coverage_blocked": inventory.coverage_blocked,
            "coverage_missing_precondition": inventory.coverage_missing_precondition,
            "coverage_resolved": inventory.coverage_resolved,
            "engine_dependency_pending": inventory.engine_dependency_pending,
            "cell_counts": dict(latest_coverage[0].cell_counts) if latest_coverage else {},
        },
    )
    add(
        "IDENTITY",
        "Identity catalog references only. No plaintext secrets.",
        tuple(item.identity_id for item in _bound(sessions, lambda row: row.session_context_id))
        or (research_run_id,),
        {
            "identity_ids": sorted({item.identity_id for item in sessions}),
            "identity_dependency_pending": inventory.identity_dependency_pending,
        },
    )
    add(
        "AUTHENTICATION",
        "Session availability. Missing identity is a precondition, not a skip.",
        tuple(item.session_context_id for item in _bound(sessions, lambda row: row.session_context_id))
        or (research_run_id,),
        {
            "session_states": sorted({item.state for item in sessions}),
            "auth_pending": inventory.auth_pending,
            "auth_failed": inventory.auth_failed,
            "session_refresh_required": inventory.session_refresh_required,
            "secret_references": [
                {"scheme": item.secret_scheme, "name": item.secret_name}
                for item in _bound(sessions, lambda row: row.session_context_id)
            ],
        },
    )
    add(
        "AUTHORIZATION",
        "Authorization differential work. Correct deny is not Evidence.",
        (research_run_id,),
        {
            "authz_pending": inventory.authz_pending,
            "authz_selected": inventory.authz_selected,
            "authz_blocked": inventory.authz_blocked,
            "authz_resolved": inventory.authz_resolved,
        },
    )
    add(
        "WORKFLOW",
        "Workflow / state-transition work. Correct reject is not Evidence.",
        (research_run_id,),
        {
            "workflow_pending": inventory.workflow_pending,
            "workflow_selected": inventory.workflow_selected,
            "workflow_blocked": inventory.workflow_blocked,
            "workflow_resolved": inventory.workflow_resolved,
        },
    )
    add(
        "MUTATION",
        "Mutation outcomes. Positive assessment is not automatically a Finding.",
        (research_run_id,),
        {
            "mutation_pending": inventory.mutation_pending,
            "mutation_selected": inventory.mutation_selected,
            "mutation_executed": inventory.mutation_executed,
            "mutation_resolved": inventory.mutation_resolved,
            "mutation_blocked": inventory.mutation_blocked,
        },
    )
    add(
        "PROTOCOL",
        "Protocol opportunities. SE3 Core deny is CONNECTED_BLOCKED_BY_AUTHORITY, not coverage.",
        (research_run_id,),
        {
            "protocol_pending": inventory.protocol_pending,
            "protocol_selected": inventory.protocol_selected,
            "protocol_executed": inventory.protocol_executed,
            "protocol_authority_blocked": inventory.protocol_authority_blocked,
            "core_deny_is_not_coverage": True,
            "not_worker_executed": inventory.protocol_executed == 0
            and inventory.protocol_authority_blocked > 0,
        },
    )
    add(
        "OAST",
        "OAST arms and callbacks. No callback is not global proof of no vulnerability.",
        tuple(item.token_id for item in _bound(tokens, lambda row: row.token_id))
        or (research_run_id,),
        {
            "oast_armed": inventory.oast_armed,
            "oast_waiting_callback": inventory.oast_waiting_callback,
            "oast_correlated": inventory.oast_correlated,
            "oast_no_callback": inventory.oast_no_callback,
            "oast_expired": inventory.oast_expired,
            "oast_ambiguous": inventory.oast_ambiguous,
            "oast_evidence": inventory.oast_evidence,
            "no_callback_is_not_global_safe": True,
        },
    )
    add(
        "DIFFERENTIAL",
        "Controlled differentials. Noise-only is not Evidence.",
        (research_run_id,),
        {
            "differential_pending": inventory.differential_pending,
            "differential_resolved": inventory.differential_resolved,
            "differential_ambiguous": inventory.differential_ambiguous,
            "differential_blocked": inventory.differential_blocked,
        },
    )
    add(
        "INVARIANT",
        "Invariant checks. UNKNOWN is not VIOLATED and not safe.",
        (research_run_id,),
        {
            "invariant_holds": inventory.invariant_holds,
            "invariant_violated": inventory.invariant_violated,
            "invariant_unknown": inventory.invariant_unknown,
            "invariant_blocked": inventory.invariant_blocked,
            "unknown_is_not_safe": True,
        },
    )
    add(
        "CHAIN",
        "Chain linkage. Hypothesis impact is not a verified chain.",
        (research_run_id,),
        {
            "chain_supported": inventory.chain_supported,
            "chain_rejected": inventory.chain_rejected,
            "chain_unknown": inventory.chain_unknown,
            "chain_blocked": inventory.chain_blocked,
        },
    )
    add(
        "EVIDENCE",
        "Admitted evidence records. Model claims cannot become Evidence.",
        tuple(item.evidence_id for item in _bound(evidence, lambda row: row.evidence_id))
        or (research_run_id,),
        {"evidence_count": len(evidence)},
    )
    add(
        "CANDIDATE",
        "Vulnerability candidates. VALIDATED is not a Finding.",
        tuple(item.candidate_id for item in _bound(candidates, lambda row: row.candidate_id))
        or (research_run_id,),
        {
            "candidate_count": len(candidates),
            "states": sorted({item.state for item in candidates}),
        },
    )
    add(
        "VERIFICATION",
        "Verification records. Pending verification blocks false completion.",
        tuple(item.verification_id for item in _bound(verifications, lambda row: row.verification_id))
        or (research_run_id,),
        {
            "verification_count": len(verifications),
            "verification_pending": inventory.verification_pending,
            "outcomes": sorted({item.outcome for item in verifications}),
        },
    )
    add(
        "FINDING",
        "Finding proposals. Human review remains required where policy requires it.",
        tuple(item.proposal_id for item in _bound(proposals, lambda row: row.proposal_id))
        or (research_run_id,),
        {
            "finding_proposal_count": len(proposals),
            "human_pending": inventory.human_pending,
        },
    )
    add(
        "COMPLETION",
        "Global completion inventory. Model statement cannot set terminal run status.",
        (research_run_id,),
        {
            "completion_allowed": inventory.completion_allowed,
            "completion_block_reasons": list(inventory.completion_block_reasons),
            "research_pending": inventory.research_pending,
            "model_pending": inventory.model_pending,
            "orphan_research_work": inventory.orphan_research_work,
            "unknown_outcome": inventory.unknown_outcome,
            "pending_scheduler_candidates": len(pending_work),
            "model_cannot_force_completion": True,
        },
    )
    present = {item.engine for item in signals}
    missing = [engine for engine in CONNECTED_MODEL_CONTEXT_ENGINES if engine not in present]
    unresolved = tuple(
        f"Engine {engine} context is missing from this pack." for engine in missing
    )
    if inventory.unknown_outcome:
        unresolved = unresolved + (
            "UNKNOWN Worker/attempt outcomes are fail-closed and not safe.",
        )
    if inventory.coverage_missing_precondition or inventory.identity_dependency_pending:
        unresolved = unresolved + (
            "Missing preconditions remain; they are not converted to safe.",
        )
    if inventory.oast_no_callback:
        unresolved = unresolved + (
            "OAST no-callback is terminal for that arm only, not a global safe claim.",
        )
    if inventory.protocol_authority_blocked:
        unresolved = unresolved + (
            "Protocol SE3 remains Core-denied and is not coverage.",
        )
    return tuple(signals), unresolved
