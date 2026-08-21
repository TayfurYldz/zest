"""GATE 17 harness. Drives research selection. Hidden ground truth stays out.

Execution and promotion read only observable harness inputs and pipeline state.
Hidden evaluation is grading-only and must not be imported here.
"""

from __future__ import annotations

import sys
from datetime import timedelta

from e2e.lab.http_research_lab import ResearchSelectionLab
from integration.harness import NOW
from research_os.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from research_os.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
)
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from research_os.application.identity import new_opaque_id
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from research_os.application.record_human_review import (
    RecordHumanReview,
    RecordHumanReviewCommand,
)
from research_os.application.run_research_selection import (
    RunResearchSelection,
    StartResearchSelectionCommand,
)
from research_os.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from research_os.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from research_os.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from research_os.core.enums import ActorType, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import CompiledScope, ScopeRuleDefinition, compile_scope_rules
from research_os.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from research_os.research.candidate import CandidateState
from research_os.research.finding_proposal import HumanReviewDecision
from research_os.research.orchestration import OrchestrationBounds, OrchestrationState
from research_os.research.planning import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
    plan_authorization_differential,
    plan_state_transition,
)
from research_os.research.selection import (
    HypothesisFamily,
    ObjectProbeContext,
    WorkflowProbeContext,
    family_for_claim,
    origin_binds_object_context,
    origin_binds_workflow_context,
)
from research_os.security_benchmark.scenarios import SecurityGroundTruthScenario
from research_os.security_benchmark.scorecard import ObservedScenarioResult
from support.recording_worker import RecordingWorkerPort
from support.sd_g10_validator import seed_validator_pass

GATE17_HUMAN = "gate17-human-reviewer"
MODEL_MODULE_MARKERS = (
    "research_os.integrations.models.cli_session",
    "openai",
    "anthropic",
)
STRIX_MODULE_MARKERS = ("research_os.integrations.strix.adapter",)


def prefix_for(scenario_id: str) -> str:
    """Database identity namespace only. Not a research-decision input."""

    return scenario_id.split("_", 1)[0].lower()


def gate17_promotion_eligible(stop_reason: str | None) -> bool:
    """Observable research-state gate. Hidden ground truth is not an argument."""

    return stop_reason == "SUFFICIENT_EVIDENCE_FOR_VERIFICATION"


class IncrementingClock:
    """Monotonic clock so append-only assessments have a real latest row."""

    def __init__(self) -> None:
        self._current = NOW

    def now(self):
        self._current = self._current + timedelta(milliseconds=1)
        return self._current


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope_for_origin(origin: str) -> CompiledScope:
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix=None,
                source_reference="scope-src",
            ),
        )
    )


def _local_worker() -> RecordingWorkerPort:
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                module=PACKAGED_WORKER_MODULE,
                default_timeout_ms=5_000,
            )
        )
    )


def _bounds(scenario: SecurityGroundTruthScenario) -> OrchestrationBounds:
    harness = scenario.harness
    return OrchestrationBounds(
        max_cycles=harness.max_cycles if harness.max_cycles is not None else 8,
        max_experiments=harness.max_experiments if harness.max_experiments is not None else 6,
        max_model_calls=8,
        max_worker_invocations=harness.max_experiments if harness.max_experiments is not None else 6,
        max_elapsed_ms=60_000,
        max_selected_opportunities=8,
        max_runtime_fallback=0,
        side_effect_ceiling=1,
        allow_repeated_control_experiments=False,
    )


def seed_run(uow, scenario: SecurityGroundTruthScenario) -> None:
    prefix = prefix_for(scenario.scenario_id)
    uow.programs.insert(
        ProgramRecord(program_id=f"{prefix}-prog", created_at=NOW, name=f"gate17-{prefix}")
    )
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id=f"{prefix}-as",
            program_id=f"{prefix}-prog",
            state="ACTIVE",
            provenance_reference="written-local-lab-auth-gate17",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id=f"{prefix}-run",
            program_id=f"{prefix}-prog",
            authorization_source_id=f"{prefix}-as",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id=f"{prefix}-budget",
            research_run_id=f"{prefix}-run",
            max_requests=80,
            max_tool_calls=40,
            max_runtime_ms=60_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )


def _object_contexts(scenario: SecurityGroundTruthScenario) -> tuple[ObjectProbeContext, ...]:
    harness = scenario.harness
    primary = ObjectProbeContext(
        actor=harness.actor,
        own_object=harness.own_object,
        cross_object=harness.cross_object,
        verification_actor=harness.verification_actor,
        verification_own_object=harness.verification_own_object,
        verification_cross_object=harness.verification_cross_object,
    )
    if harness.second_actor and harness.second_own_object and harness.second_cross_object:
        return (
            primary,
            ObjectProbeContext(
                actor=harness.second_actor,
                own_object=harness.second_own_object,
                cross_object=harness.second_cross_object,
            ),
        )
    return (primary,)


def _workflow_contexts(scenario: SecurityGroundTruthScenario) -> tuple[WorkflowProbeContext, ...]:
    harness = scenario.harness
    assert harness.resource_id is not None
    return (
        WorkflowProbeContext(
            actor=harness.actor,
            resource_id=harness.resource_id,
            transition=harness.transition or "approve",
            verification_actor=harness.verification_actor,
            verification_resource_id=harness.verification_resource_id,
        ),
    )


def _promote_supported(
    factory,
    scenario: SecurityGroundTruthScenario,
    origin: str,
    worker,
    clock: IncrementingClock,
) -> bool:
    """Exercise Evidence → Candidate → Verification → Finding on SUPPORTED state.

    Uniform Gate17 operator policy: only observable research state decides
    whether this path runs. Validators still admit or reject. False promotions
    must remain visible to grading.
    """
    prefix = prefix_for(scenario.scenario_id)
    run_id = f"{prefix}-run"
    finding_before = False
    with factory.open() as uow:
        hypotheses = uow.hypotheses.list_for_research_run(run_id)
        assessments = uow.hypothesis_assessments.list_for_research_run(run_id)
        cycles = uow.research_cycles.list_for_research_run(run_id)
        uow.rollback()
    cycle_order = {
        item.experiment_id: item.cycle_number for item in cycles if item.experiment_id
    }
    ordered = sorted(
        assessments,
        key=lambda row: (
            cycle_order.get(row.experiment_id, 10**9),
            row.created_at.isoformat(),
            row.assessment_id,
        ),
    )
    by_hyp: dict[str, list] = {}
    for item in ordered:
        by_hyp.setdefault(item.hypothesis_id, []).append(item)
    for hypothesis in hypotheses:
        rows = by_hyp.get(hypothesis.hypothesis_id, [])
        if not rows or rows[-1].assessment_outcome != "CONSISTENT_WITH_PREDICTION":
            continue
        consistent = [
            row
            for row in rows
            if row.assessment_outcome == "CONSISTENT_WITH_PREDICTION"
        ]
        original_id = consistent[0].experiment_id
        admitted = AdmitDiagnosticEvidence(factory, clock=clock).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=original_id)
        )
        if admitted.evidence_id is None:
            continue
        proposed = ProposeCandidateFromEvidence(factory, clock=clock).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=admitted.evidence_id)
        )
        if proposed.candidate_id is None:
            continue
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        repro_id = next(
            (
                row.experiment_id
                for row in reversed(consistent)
                if row.experiment_id != original_id
            ),
            None,
        )
        if repro_id is None:
            repro_id = new_opaque_id()
            family = family_for_claim(hypothesis.claim)
            harness = scenario.harness
            object_contexts = _object_contexts(scenario)
            workflow_contexts = _workflow_contexts(scenario)
            if family is HypothesisFamily.OBJECT_AUTHORIZATION:
                context = next(
                    (
                        item
                        for item in object_contexts
                        if origin_binds_object_context(hypothesis.origin_reference, item)
                    ),
                    object_contexts[0],
                )
                plan = plan_authorization_differential(
                    hypothesis.hypothesis_id,
                    budget_id=f"{prefix}-budget",
                    target_reference=origin,
                    authorized_origin=origin,
                    actor=context.verification_actor or context.cross_object,
                    own_object=context.verification_own_object or context.cross_object,
                    cross_object=context.verification_cross_object or context.own_object,
                )
            else:
                context = next(
                    (
                        item
                        for item in workflow_contexts
                        if origin_binds_workflow_context(hypothesis.origin_reference, item)
                    ),
                    workflow_contexts[0],
                )
                plan = plan_state_transition(
                    hypothesis.hypothesis_id,
                    budget_id=f"{prefix}-budget",
                    target_reference=origin,
                    authorized_origin=origin,
                    actor=context.verification_actor or context.actor,
                    resource_id=context.verification_resource_id or context.resource_id,
                    transition=context.transition,
                )
            PreparePlannedExperiment(factory, clock=clock).execute(
                PreparePlannedExperimentCommand(
                    experiment_id=repro_id,
                    research_run_id=run_id,
                    plan=plan,
                )
            )
            ExecutePlannedExperiment(factory, worker, clock=clock).execute(
                ExecutePlannedExperimentCommand(
                    experiment_id=repro_id,
                    plan=plan,
                    scope=_allow_scope(),
                    compiled_scope=_compiled_scope_for_origin(origin),
                )
            )
            EvaluateExperimentFeedback(factory, clock=clock).execute(
                EvaluateExperimentFeedbackCommand(experiment_id=repro_id)
            )
        AdmitDiagnosticEvidence(factory, clock=clock).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=repro_id)
        )
        completed = CompleteCandidateVerification(factory, clock=clock).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id=repro_id,
            )
        )
        if completed.state != CandidateState.VALIDATED:
            continue
        with factory.open() as uow:
            candidate = uow.candidates.get(proposed.candidate_id)
            assert candidate is not None
            family = family_for_claim(hypothesis.claim)
            seed_validator_pass(
                uow,
                research_run_id=candidate.research_run_id,
                hypothesis_id=candidate.hypothesis_id,
                created_at=clock.now(),
                family_id=(
                    "hf-object-authz"
                    if family is HypothesisFamily.OBJECT_AUTHORIZATION
                    else "hf-state-transition"
                ),
                marker=proposed.candidate_id,
            )
            uow.commit()
        submitted = SubmitFindingProposal(factory, clock=clock).execute(
            SubmitFindingProposalCommand(candidate_id=proposed.candidate_id)
        )
        if submitted.proposal_id is None:
            continue
        with factory.open() as uow:
            existing = [
                item
                for item in uow.findings.list_for_research_run(run_id)
                if item.candidate_id == proposed.candidate_id
            ]
            finding_before = finding_before or bool(existing)
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=clock).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=GATE17_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
                note="gate17 explicit human review",
            )
        )
        with factory.open() as uow:
            existing = [
                item
                for item in uow.findings.list_for_research_run(run_id)
                if item.candidate_id == proposed.candidate_id
            ]
            finding_before = finding_before or bool(existing)
        FinalizeFinding(factory, clock=clock).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=GATE17_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
    return finding_before


def run_scenario(factory, scenario: SecurityGroundTruthScenario) -> ObservedScenarioResult:
    prefix = prefix_for(scenario.scenario_id)
    harness = scenario.harness
    with factory.open() as uow:
        seed_run(uow, scenario)
        uow.commit()
    object_kind = harness.object_fixture or "TRUE_BOLA"
    workflow_kind = harness.workflow_fixture or "SECURE_ROLE_ENFORCEMENT"
    object_kind_b = "TRUE_BOLA" if harness.second_actor else None
    lab = ResearchSelectionLab(
        object_kind,
        workflow_kind,
        object_kind_b=object_kind_b,
    )
    origin = lab.start()
    host, _port = lab._server.server_address[:2]
    if host != "127.0.0.1":
        lab.stop()
        raise AssertionError(f"lab bound {host!r}, not 127.0.0.1")
    worker = _local_worker()
    finding_before = False
    try:
        command = StartResearchSelectionCommand(
            research_run_id=f"{prefix}-run",
            budget_id=f"{prefix}-budget",
            authorized_origin=origin,
            scope=_allow_scope(),
            bounds=_bounds(scenario),
            object_contexts=_object_contexts(scenario),
            workflow_contexts=_workflow_contexts(scenario),
            candidate_origins=(
                (harness.candidate_origin,) if harness.candidate_origin else ()
            ),
            pause_after_cycles=harness.pause_after_cycles,
        )
        clock = IncrementingClock()
        controller = RunResearchSelection(factory, worker, clock=clock)
        result = controller.start(command)
        while result.state in {
            OrchestrationState.READY.value,
            OrchestrationState.RUNNING.value,
        }:
            result = controller.step(f"{prefix}-run")
            if result.state == OrchestrationState.PAUSED.value:
                result = controller.resume(command)
        if gate17_promotion_eligible(result.stop_reason):
            finding_before = _promote_supported(factory, scenario, origin, worker, clock)
        return _snapshot(factory, scenario, prefix, worker, origin, finding_before, lab)
    finally:
        lab.stop()


def _snapshot(
    factory,
    scenario: SecurityGroundTruthScenario,
    prefix: str,
    worker,
    origin: str,
    finding_before_approval: bool,
    lab: ResearchSelectionLab,
) -> ObservedScenarioResult:
    run_id = f"{prefix}-run"
    with factory.open() as uow:
        observations = uow.observations.list_for_research_run(run_id)
        evidence = [
            item
            for item in uow.evidence.list_for_research_run(run_id)
            if item.polarity == "SUPPORTING"
        ]
        candidates = uow.candidates.list_for_research_run(run_id)
        findings = uow.findings.list_for_research_run(run_id)
        hypotheses = uow.hypotheses.list_for_research_run(run_id)
        assessments = uow.hypothesis_assessments.list_for_research_run(run_id)
        experiments = uow.experiments.list_for_research_run(run_id)
        cycles = uow.research_cycles.list_for_research_run(run_id)
        selections = uow.research_selections.list_for_research_run(run_id)
        orchestration = uow.research_orchestrations.get(run_id)
        cycle_by_experiment = {
            item.experiment_id: item.cycle_number
            for item in cycles
            if item.experiment_id
        }
        experiments = sorted(
            experiments,
            key=lambda item: (
                cycle_by_experiment.get(item.experiment_id, 10**9),
                item.created_at.isoformat(),
                item.experiment_id,
            ),
        )
        plans = {
            item.experiment_id: uow.experiment_plans.get(item.experiment_id)
            for item in experiments
        }
        verifications = []
        for candidate in candidates:
            verifications.extend(uow.verifications.list_for_candidate(candidate.candidate_id))
        observation_payload = observations[0].payload if observations else None
        worker_request = worker.calls[0]["request"] if worker.calls else None
        human_approved = bool(findings)
        candidate = None
        if candidates:
            rank = {
                "VALIDATED": 4,
                "VERIFYING": 3,
                "OPEN": 2,
                "INCONCLUSIVE": 1,
                "REJECTED": 0,
            }
            candidate = max(
                candidates,
                key=lambda item: (
                    rank.get(item.state, -1),
                    item.created_at.isoformat(),
                    item.candidate_id,
                ),
            )
        verification_outcome = verifications[-1].outcome if verifications else None
        ordered = sorted(
            assessments,
            key=lambda item: (
                cycle_by_experiment.get(item.experiment_id, 10**9),
                item.created_at.isoformat(),
                item.assessment_id,
            ),
        )
        latest: dict[str, str] = {}
        for item in ordered:
            latest[item.hypothesis_id] = item.assessment_outcome
        lifecycles = []
        from research_os.research.selection import (
            HypothesisFamily,
            ObjectProbeContext,
            ObservedResearchFact,
            WorkflowProbeContext,
            build_portfolio,
            family_for_claim,
            object_context_is_observed,
            origin_binds_object_context,
            origin_binds_workflow_context,
            workflow_context_is_observed,
        )

        facts = tuple(
            ObservedResearchFact(
                observation_id=item.observation_id,
                observation_kind=item.observation_kind,
                payload=dict(item.payload),
            )
            for item in observations
        )
        remaining = {}
        object_contexts = _object_contexts(scenario)
        workflow_contexts = _workflow_contexts(scenario)
        for item in hypotheses:
            family = family_for_claim(item.claim)
            if family is HypothesisFamily.OBJECT_AUTHORIZATION:
                remaining[item.hypothesis_id] = any(
                    not object_context_is_observed(facts, context, origin)
                    for context in object_contexts
                    if origin_binds_object_context(item.origin_reference, context)
                )
            elif family is HypothesisFamily.WORKFLOW_STATE_TRANSITION:
                remaining[item.hypothesis_id] = any(
                    not workflow_context_is_observed(facts, context, origin)
                    for context in workflow_contexts
                    if origin_binds_workflow_context(item.origin_reference, context)
                )
            else:
                remaining[item.hypothesis_id] = False
        portfolio = build_portfolio(
            hypotheses=tuple((item.hypothesis_id, item.claim) for item in hypotheses),
            assessments_by_hypothesis={
                item.hypothesis_id: tuple(
                    row.assessment_outcome
                    for row in ordered
                    if row.hypothesis_id == item.hypothesis_id
                )
                for item in hypotheses
            },
            observation_ids_by_hypothesis={
                item.hypothesis_id: tuple(
                    obs_id
                    for row in ordered
                    if row.hypothesis_id == item.hypothesis_id
                    for obs_id in row.observation_ids
                )
                for item in hypotheses
            },
            remaining_untested_by_hypothesis=remaining,
            origin_reference_by_hypothesis={
                item.hypothesis_id: item.origin_reference for item in hypotheses
            },
        )
        for item in portfolio.hypotheses:
            family = (
                "HTTP_AUTHORIZATION_DIFFERENTIAL"
                if item.claim == HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM
                else "HTTP_STATE_TRANSITION_AUTHORIZATION"
                if item.claim == HTTP_STATE_TRANSITION_CLAIM
                else "UNKNOWN"
            )
            lifecycles.append((family, item.lifecycle.value))
        harness = scenario.harness
        selected_purposes = []
        reason_codes: list[str] = []
        for selection in selections:
            if selection.outcome == "SELECT":
                reason_codes.extend(selection.reason_codes)
        for experiment in experiments:
            plan = plans.get(experiment.experiment_id)
            if plan is None:
                continue
            if plan.required_capability == "http.authorization.differential":
                mode = (plan.arguments or {}).get("mode")
                actor = (plan.arguments or {}).get("actor")
                if mode == "secure_only":
                    selected_purposes.append("OBJECT_CONTROL_PROBE")
                elif actor and actor == harness.verification_actor:
                    selected_purposes.append("OBJECT_INDEPENDENT_REPRODUCTION")
                else:
                    selected_purposes.append("OBJECT_CROSS_PROBE")
            elif plan.required_capability == "http.state_transition":
                area = (plan.arguments or {}).get("area")
                actor = (plan.arguments or {}).get("actor")
                if area == "control":
                    selected_purposes.append("WORKFLOW_CONTROL_PROBE")
                elif actor and actor == harness.verification_actor:
                    selected_purposes.append("WORKFLOW_INDEPENDENT_REPRODUCTION")
                else:
                    selected_purposes.append("WORKFLOW_TRANSITION_PROBE")
        identities = [
            (plan.required_capability, json_identity(plan.arguments), plan.target_reference)
            for plan in plans.values()
            if plan is not None
        ]
        redundant = len(identities) != len(set(identities))
        out_of_scope_worker = 0
        for call in worker.calls:
            request = call.get("request") or {}
            target = request.get("target_reference")
            if isinstance(target, str) and target != origin:
                out_of_scope_worker += 1
        candidate_classes = tuple(
            item.classification for item in candidates if item.classification
        )
        finding_classes = tuple(item.classification for item in findings if item.classification)
        observed_class = candidate_classes[0] if candidate_classes else None
        finding_class = finding_classes[0] if finding_classes else None
        original = experiments[0].experiment_id if experiments else None
        repro = None
        if len(experiments) > 1:
            repro = experiments[-1].experiment_id
        attempts = uow.execution_attempts.list_for_research_run(run_id)
        original_request = attempts[0].request_id if attempts else None
        repro_request = attempts[-1].request_id if len(attempts) > 1 else None
        results = uow.worker_results.list_for_research_run(run_id)
        worker_status = results[0].status if results else None
        admissions = uow.evidence_admissions.list_for_research_run(run_id)
        evidence_rationale = None
        if admissions:
            evidence_rationale = {"reason_codes": list(admissions[0].reason_codes)}
        assessment_reason = None
        assessment_reason = None
        if ordered:
            assessment_reason = str((ordered[-1].rationale or {}).get("reason_code") or "") or None
        stop_reason = None if orchestration is None else orchestration.stop_reason
        original = experiments[0].experiment_id if experiments else None
        repro = experiments[-1].experiment_id if len(experiments) > 1 else None
        attempts = uow.execution_attempts.list_for_research_run(run_id)
        original_request = attempts[0].request_id if attempts else None
        repro_request = attempts[-1].request_id if len(attempts) > 1 else None
        results = uow.worker_results.list_for_research_run(run_id)
        worker_status = results[0].status if results else None
        admissions = uow.evidence_admissions.list_for_research_run(run_id)
        evidence_rationale = None
        if admissions:
            evidence_rationale = {"reason_codes": list(admissions[0].reason_codes)}
    return ObservedScenarioResult(
        scenario_id=scenario.scenario_id,
        version=scenario.version,
        observation_count=len(observations),
        evidence_admitted=bool(evidence),
        candidate_state=None if candidate is None else candidate.state,
        verification_outcome=verification_outcome,
        finding_count=len(findings),
        finding_before_human_approval=finding_before_approval,
        human_approved=human_approved,
        worker_invocation_count=len(worker.calls),
        http_request_count=lab.object_get_count + lab.workflow_request_count,
        redirect_followed=False,
        original_experiment_id=original,
        reproduction_experiment_id=repro,
        original_request_id=original_request,
        reproduction_request_id=repro_request,
        worker_out_of_process=(
            isinstance(worker._inner, LocalProcessWorkerAdapter)
            and worker._inner._config.module == PACKAGED_WORKER_MODULE
        ),
        worker_request=worker_request,
        observation_payload=observation_payload,
        evidence_rationale=evidence_rationale,
        assessment_reason_code=assessment_reason,
        worker_result_status=worker_status,
        core_reason_code=None,
        model_modules_loaded=tuple(
            name for name in MODEL_MODULE_MARKERS if name in sys.modules
        ),
        strix_modules_loaded=tuple(
            name for name in STRIX_MODULE_MARKERS if name in sys.modules
        ),
        observed_classification=observed_class,
        finding_classification=finding_class,
        research_stop_reason=stop_reason,
        hypothesis_lifecycles=tuple(lifecycles),
        selected_purposes=tuple(selected_purposes),
        selection_reason_codes=tuple(reason_codes),
        adaptive_depth=len(cycles),
        redundant_experiment_executed=redundant,
        worker_out_of_scope_count=out_of_scope_worker,
        candidate_classifications=candidate_classes,
        finding_classifications=finding_classes,
    )


def json_identity(arguments) -> str:
    actor = str((arguments or {}).get("actor") or "")
    resource = str(
        (arguments or {}).get("cross_object")
        or (arguments or {}).get("resource_id")
        or ""
    )
    mode = str(
        (arguments or {}).get("mode") or (arguments or {}).get("area") or ""
    )
    return f"{actor}:{resource}:{mode}"
