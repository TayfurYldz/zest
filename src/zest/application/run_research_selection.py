"""Bounded multi-hypothesis research selection loop.

Research ranks experiments. Application coordinates. Core authorizes. Worker executes.
Does not invoke a model. Does not create Findings.

`RunResearchSelection` is a compatibility/delegation layer, not an independent
orchestrator. It owns none of the `research_orchestration` row's lifecycle
itself: every read/write of that row goes through one internally-held
`AutonomousResearchController` (ARC), via `ARC.start()` / `ARC.resume()` /
`ARC.run_managed_cycle()`. ARC remains the single component that decides and
records the next research action for a `research_run_id`; this class only
supplies ARC with a deterministic, non-model decision strategy (HTTP
object-authorization / workflow-state-transition probing) for the one cycle
ARC asks it to decide, and ARC dispatches the Worker through its own single
`PreparePlannedExperiment` / `ExecutePlannedExperiment` /
`EvaluateExperimentFeedback` instances -- never a second, independently
constructed set.

History: earlier revisions of this module directly `insert`/`save`d
`ResearchOrchestrationRecord` rows and called `PreparePlannedExperiment` /
`ExecutePlannedExperiment` through their own instances, making this a second,
structurally independent scheduler alongside `AutonomousResearchController`
(see `docs/plans/audit/SLICE_3_COMPLETION_RECORD.md`, "Second-scheduler
remediation"). That duplicate authority has been removed; the deterministic
selection *policy* (which experiment to run next for HTTP
object-authorization / workflow-state-transition hypotheses) is unchanged and
fully preserved below -- only WHERE it is recorded and WHO dispatches the
Worker changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    ManagedCycleOutcome,
    StartAutonomousResearchCommand,
)
from zest.application.errors import ApplicationError
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.identity import new_opaque_id
from zest.application.plan_records import experiment_plan_from_record
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.enums import ActorType, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.records import (
    AuditEventRecord,
    HypothesisRecord,
    ResearchOpportunityRecord,
    ResearchOrchestrationRecord,
    ResearchSelectionRecord,
)
from zest.platform.worker import WorkerPort
from zest.research.model_port import ModelCallRequest, ModelCallResult
from zest.research.exploration import (
    OpportunityDimensions,
    OrdinalLevel,
    SelectionOutcome,
)
from zest.research.orchestration import (
    ORCHESTRATION_POLICY_VERSION,
    CycleOutcome,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
    OrchestrationUsage,
    StopReason,
    check_orchestration_bounds,
)
from zest.research.planning import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
)
from zest.research.selection import (
    RESEARCH_SELECTION_STRATEGY_VERSION,
    ExperimentOption,
    HypothesisFamily,
    HypothesisLifecycle,
    ObjectProbeContext,
    ObservedResearchFact,
    ResearchPortfolio,
    ResearchStopReason,
    WorkflowProbeContext,
    build_portfolio,
    identity_from_plan_arguments,
    object_context_is_observed,
    object_origin_reference,
    opportunity_kind_for,
    opportunity_mode_for,
    origin_binds_object_context,
    origin_binds_workflow_context,
    plan_from_option,
    propose_experiment_options,
    select_next_experiment,
    stop_reason_for_portfolio,
    workflow_context_is_observed,
    workflow_origin_reference,
)

CONTROL_PLANE_ACTOR_ID = "control-plane"
RESEARCH_SELECTION_QUESTION = (
    "Which authorized object-authorization or workflow-authorization experiment "
    "should run next given current observations and competing hypotheses?"
)


class _NeverInvokedModelPort:
    """Placeholder satisfying ARC's `model: ModelPort` constructor argument.

    `RunResearchSelection` never lets ARC take its model-driven `step()` path
    (it only ever drives ARC through `run_managed_cycle`, which does not touch
    `self._model`); this exists solely so ARC's constructor is satisfied
    without importing any real model/provider adapter. If `.complete()` is
    ever actually called, that is a programming error, not a provider
    condition.
    """

    def complete(self, request: ModelCallRequest) -> ModelCallResult:  # pragma: no cover
        raise ApplicationError(
            "RunResearchSelection is a deterministic, non-model selector; its "
            "placeholder ModelPort must never be invoked"
        )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-deny-origin", ScopeRuleEffect.OUT_OF_SCOPE, True, "scope-src"),
        ),
        ambiguous=False,
    )


@dataclass(frozen=True)
class StartResearchSelectionCommand:
    research_run_id: str
    budget_id: str
    authorized_origin: str
    scope: ScopeEvaluationInput
    bounds: OrchestrationBounds
    object_contexts: tuple[ObjectProbeContext, ...]
    workflow_contexts: tuple[WorkflowProbeContext, ...]
    candidate_origins: tuple[str, ...] = ()
    pause_after_cycles: int | None = None


@dataclass(frozen=True)
class ResearchSelectionStepResult:
    research_run_id: str
    state: str
    cycle_number: int
    outcome: str
    stop_reason: str | None
    selected_purpose: str | None
    experiment_id: str | None
    hypothesis_id: str | None
    observation_ids: tuple[str, ...]
    assessment_id: str | None
    core_decision: str | None
    options_considered: int
    selection_reason_codes: tuple[str, ...]


class RunResearchSelection:
    """Closed-loop selector. Not a model runtime and not Finding authority.

    Delegation layer over `AutonomousResearchController`: see module
    docstring. Public API is unchanged from the original independent
    implementation so existing callers (`tests/e2e/gate17_harness.py`) do not
    need to change.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        *,
        clock: Clock | None = None,
        actor_id: str = CONTROL_PLANE_ACTOR_ID,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id
        self._arc = AutonomousResearchController(
            uow_factory,
            worker,
            _NeverInvokedModelPort(),
            clock=self._clock,
            actor_id=actor_id,
        )
        self._surfaces: dict[str, StartResearchSelectionCommand] = {}

    def start(self, command: StartResearchSelectionCommand) -> ResearchSelectionStepResult:
        now = self._clock.now()
        self._surfaces[command.research_run_id] = command
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                uow.rollback()
                raise ApplicationError("research run not found")
            existing = uow.research_orchestrations.get(command.research_run_id)
            if existing is None:
                hypotheses = uow.hypotheses.list_for_research_run(command.research_run_id)
                if not hypotheses:
                    for context in command.object_contexts:
                        uow.hypotheses.insert(
                            HypothesisRecord(
                                hypothesis_id=new_opaque_id(),
                                research_run_id=command.research_run_id,
                                claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
                                origin_reference=object_origin_reference(context),
                                created_at=now,
                            )
                        )
                    for context in command.workflow_contexts:
                        uow.hypotheses.insert(
                            HypothesisRecord(
                                hypothesis_id=new_opaque_id(),
                                research_run_id=command.research_run_id,
                                claim=HTTP_STATE_TRANSITION_CLAIM,
                                origin_reference=workflow_origin_reference(context),
                                created_at=now,
                            )
                        )
                uow.commit()
            else:
                uow.rollback()
        tick = self._arc.start(self._arc_command(command))
        if existing is None:
            self._record_started_audit(command)
        return self._result_from_tick(tick)

    def bind_surface(self, command: StartResearchSelectionCommand) -> None:
        """Re-attach operator surface after process reconstruction. Does not mutate Core."""

        self._surfaces[command.research_run_id] = command

    def step(self, research_run_id: str) -> ResearchSelectionStepResult:
        command = self._surfaces.get(research_run_id)
        if command is None:
            raise ApplicationError("research selection surface is not bound for this run")
        extra: dict[str, Any] = {}

        def _cycle(
            current: ResearchOrchestrationRecord,
            prepare: PreparePlannedExperiment,
            execute: ExecutePlannedExperiment,
            evaluate: EvaluateExperimentFeedback,
        ) -> ManagedCycleOutcome:
            return self._run_selection_cycle(command, current, prepare, execute, evaluate, extra)

        tick = self._arc.run_managed_cycle(research_run_id, _cycle)
        return self._result_from_tick(tick, **extra)

    def resume(self, command: StartResearchSelectionCommand) -> ResearchSelectionStepResult:
        self.bind_surface(command)
        tick = self._arc.resume(command.research_run_id)
        return self._result_from_tick(tick)

    def run_bounded(
        self, command: StartResearchSelectionCommand
    ) -> ResearchSelectionStepResult:
        started = self.start(command)
        if started.state in {
            OrchestrationState.COMPLETED.value,
            OrchestrationState.BUDGET_EXHAUSTED.value,
        }:
            return started
        result = started
        for _ in range(command.bounds.max_cycles + 1):
            result = self.step(command.research_run_id)
            if result.state in {
                OrchestrationState.COMPLETED.value,
                OrchestrationState.BUDGET_EXHAUSTED.value,
                OrchestrationState.PAUSED.value,
                OrchestrationState.BLOCKED.value,
                OrchestrationState.FAILED_OPERATIONAL.value,
            }:
                return result
        return result

    def _arc_command(
        self, command: StartResearchSelectionCommand
    ) -> StartAutonomousResearchCommand:
        return StartAutonomousResearchCommand(
            research_run_id=command.research_run_id,
            budget_id=command.budget_id,
            target_reference=command.authorized_origin,
            scope=command.scope,
            bounds=command.bounds,
            research_question=RESEARCH_SELECTION_QUESTION,
        )

    def _record_started_audit(self, command: StartResearchSelectionCommand) -> None:
        with self._uow_factory.open() as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RESEARCH_SELECTION_STARTED",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "policy_version": ORCHESTRATION_POLICY_VERSION,
                        "strategy_version": RESEARCH_SELECTION_STRATEGY_VERSION,
                        "max_cycles": command.bounds.max_cycles,
                        "max_experiments": command.bounds.max_experiments,
                        "not_a_model": True,
                        "not_a_finding": True,
                    },
                )
            )
            uow.commit()

    def _run_selection_cycle(
        self,
        command: StartResearchSelectionCommand,
        current: ResearchOrchestrationRecord,
        prepare: PreparePlannedExperiment,
        execute: ExecutePlannedExperiment,
        evaluate: EvaluateExperimentFeedback,
        extra: dict[str, Any],
    ) -> ManagedCycleOutcome:
        research_run_id = command.research_run_id
        with self._uow_factory.open() as uow:
            hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
            assessments = uow.hypothesis_assessments.list_for_research_run(research_run_id)
            experiments = uow.experiments.list_for_research_run(research_run_id)
            observations = uow.observations.list_for_research_run(research_run_id)
            opportunities = uow.research_opportunities.list_for_research_run(research_run_id)
            cycles = uow.research_cycles.list_for_research_run(research_run_id)
            usage = OrchestrationUsage(
                cycles_completed=current.cycle_number,
                experiments_executed=len(
                    [
                        item
                        for item in experiments
                        if item.execution_state
                        in {"EXECUTION_SUCCEEDED", "EXECUTION_FAILED", "BLOCKED"}
                    ]
                ),
                model_calls=0,
                worker_invocations=len(
                    [
                        item
                        for item in experiments
                        if item.execution_state == "EXECUTION_SUCCEEDED"
                    ]
                ),
                elapsed_ms=0,
                opportunities_selected=len(opportunities),
                runtime_fallbacks=0,
            )
            bounds = OrchestrationBounds(
                max_cycles=current.max_cycles,
                max_experiments=current.max_experiments,
                max_model_calls=current.max_model_calls,
                max_worker_invocations=current.max_worker_invocations,
                max_elapsed_ms=current.max_elapsed_ms,
                max_selected_opportunities=current.max_selected_opportunities,
                max_runtime_fallback=current.max_runtime_fallback,
                side_effect_ceiling=current.side_effect_ceiling,
                allow_repeated_control_experiments=(
                    current.allow_repeated_control_experiments
                ),
            )
            bound = check_orchestration_bounds(bounds, usage)
            facts = tuple(
                ObservedResearchFact(
                    observation_id=item.observation_id,
                    observation_kind=item.observation_kind,
                    payload=dict(item.payload),
                )
                for item in observations
            )
            portfolio = self._portfolio(
                hypotheses,
                assessments,
                remaining_untested=self._remaining_untested(command, facts, hypotheses),
                cycle_order={
                    item.experiment_id: item.cycle_number
                    for item in cycles
                    if item.experiment_id
                },
            )
            executed_identities = self._executed_identities(
                uow, research_run_id, experiments
            )
            negative_contexts = self._negative_contexts(portfolio, uow, experiments)
            origins = tuple(
                dict.fromkeys(
                    (command.authorized_origin, *command.candidate_origins)
                )
            )
            options = propose_experiment_options(
                portfolio=portfolio,
                observations=facts,
                authorized_origin=command.authorized_origin,
                candidate_origins=origins,
                object_contexts=command.object_contexts,
                workflow_contexts=command.workflow_contexts,
                id_prefix=new_opaque_id(),
            )
            decisions = select_next_experiment(
                options,
                executed_identities=executed_identities,
                negative_context_signatures=negative_contexts,
            )
            selected = next((item for item in decisions if item.selected), None)
            budget_exhausted = (
                not bound.allowed and bound.stop_reason is StopReason.BUDGET_EXHAUSTED
            )
            stop = None
            if not bound.allowed:
                stop = (
                    ResearchStopReason.BUDGET_EXHAUSTED
                    if budget_exhausted
                    else ResearchStopReason.MAX_CYCLES_REACHED
                )
            elif selected is None:
                stop = stop_reason_for_portfolio(
                    portfolio,
                    selected=None,
                    budget_exhausted=False,
                    max_cycles_reached=False,
                    operational=False,
                )
            now = self._clock.now()
            cycle_number = current.cycle_number + 1
            if stop is not None:
                self._persist_decisions(uow, research_run_id, decisions, now)
                trace = self._trace_audit(
                    research_run_id=research_run_id,
                    cycle_number=cycle_number,
                    portfolio=portfolio,
                    decisions=decisions,
                    selected=None,
                    core_decision=None,
                    stop_reason=stop.value,
                    budget_before=bounds.max_experiments - usage.experiments_executed,
                    budget_after=bounds.max_experiments - usage.experiments_executed,
                    now=now,
                )
                uow.commit()
                extra["options_considered"] = len(decisions)
                state = (
                    OrchestrationState.BUDGET_EXHAUSTED
                    if stop is ResearchStopReason.BUDGET_EXHAUSTED
                    else OrchestrationState.COMPLETED
                )
                return ManagedCycleOutcome(
                    outcome=CycleOutcome.COMPLETE,
                    phase_label=OrchestrationPhase.CYCLE_COMPLETE.value,
                    state=state,
                    stop_reason_value=stop.value,
                    increment_cycle=True,
                    current_phase=OrchestrationPhase.CYCLE_COMPLETE,
                    extra_audit_events=(trace,),
                )
            assert selected is not None
            option = selected.option
            self._persist_decisions(uow, research_run_id, decisions, now)
            uow.commit()

        experiment_id = new_opaque_id()
        plan = plan_from_option(option, budget_id=command.budget_id)
        prepare.execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id=research_run_id,
                plan=plan,
            )
        )
        scope = (
            command.scope
            if option.in_authorized_origin
            else _deny_scope()
        )
        from urllib.parse import urlsplit

        parsed_origin = urlsplit(command.authorized_origin)
        compiled_scope = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme=parsed_origin.scheme or "http",
                    host=parsed_origin.hostname or "127.0.0.1",
                    port=parsed_origin.port,
                    path_prefix=None,
                    source_reference="scope-src",
                ),
            )
        )
        executed = execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=scope,
                compiled_scope=compiled_scope,
            )
        )
        assessment_id = None
        observation_ids = executed.observation_ids
        if executed.status is ResearchLoopStatus.OBSERVATION_PRODUCED:
            feedback = evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
            )
            assessment_id = feedback.assessment_id
            observation_ids = feedback.observation_ids
        elif executed.status is ResearchLoopStatus.DISPATCH_DENIED:
            # Core denied. Do not mutate scope. Record and continue next step.
            pass

        pause = (
            command.pause_after_cycles is not None
            and cycle_number >= command.pause_after_cycles
        )
        stop_reason_value = ResearchStopReason.OPERATOR_PAUSED.value if pause else None
        core_decision = (
            executed.core_decision.value
            if executed.core_decision is not None
            else executed.status.value
        )
        # Reuses the same pre-execution `portfolio`/`decisions` snapshot the
        # decision uow above already computed and persisted -- this is a pure
        # audit-trail projection, not a second read of authoritative state.
        trace = self._trace_audit(
            research_run_id=research_run_id,
            cycle_number=cycle_number,
            portfolio=portfolio,
            decisions=decisions,
            selected=option,
            core_decision=core_decision,
            stop_reason=stop_reason_value,
            budget_before=command.bounds.max_experiments - usage.experiments_executed,
            budget_after=command.bounds.max_experiments
            - usage.experiments_executed
            - (1 if executed.status is ResearchLoopStatus.OBSERVATION_PRODUCED else 0),
            now=self._clock.now(),
            experiment_id=experiment_id,
            assessment_id=assessment_id,
            observation_ids=observation_ids,
        )

        extra["options_considered"] = len(decisions)
        extra["selected_purpose"] = option.purpose.value
        extra["observation_ids"] = observation_ids
        extra["assessment_id"] = assessment_id
        extra["core_decision"] = core_decision
        extra["selection_reason_codes"] = selected.reason_codes

        return ManagedCycleOutcome(
            outcome=CycleOutcome.PAUSE if pause else CycleOutcome.CONTINUE,
            phase_label=OrchestrationPhase.ASSESSMENT_COMPLETE.value,
            state=OrchestrationState.PAUSED if pause else OrchestrationState.RUNNING,
            stop_reason_value=stop_reason_value,
            hypothesis_id=option.hypothesis_id,
            experiment_id=experiment_id,
            opportunity_id=option.option_id,
            observation_id=observation_ids[0] if observation_ids else None,
            assessment_id=assessment_id,
            pause_reason=stop_reason_value,
            increment_cycle=True,
            current_phase=OrchestrationPhase.ASSESSMENT_COMPLETE,
            extra_audit_events=(trace,),
        )

    def _portfolio(
        self,
        hypotheses,
        assessments,
        remaining_untested: dict[str, bool] | None = None,
        cycle_order: dict[str, int] | None = None,
    ) -> ResearchPortfolio:
        by_hyp: dict[str, list[str]] = {item.hypothesis_id: [] for item in hypotheses}
        obs_by_hyp: dict[str, list[str]] = {item.hypothesis_id: [] for item in hypotheses}
        order = cycle_order or {}
        ordered = sorted(
            assessments,
            key=lambda item: (
                order.get(item.experiment_id, 10**9),
                item.created_at.isoformat(),
                item.assessment_id,
            ),
        )
        for assessment in ordered:
            by_hyp.setdefault(assessment.hypothesis_id, []).append(
                assessment.assessment_outcome
            )
            obs_by_hyp.setdefault(assessment.hypothesis_id, []).extend(
                assessment.observation_ids
            )
        return build_portfolio(
            hypotheses=tuple((item.hypothesis_id, item.claim) for item in hypotheses),
            assessments_by_hypothesis={
                key: tuple(value) for key, value in by_hyp.items()
            },
            observation_ids_by_hypothesis={
                key: tuple(dict.fromkeys(value)) for key, value in obs_by_hyp.items()
            },
            remaining_untested_by_hypothesis=remaining_untested,
            origin_reference_by_hypothesis={
                item.hypothesis_id: item.origin_reference for item in hypotheses
            },
        )

    def _remaining_untested(self, command, facts, hypotheses) -> dict[str, bool]:
        remaining: dict[str, bool] = {}
        from zest.research.selection import family_for_claim

        for hypothesis in hypotheses:
            family = family_for_claim(hypothesis.claim)
            if family is HypothesisFamily.OBJECT_AUTHORIZATION:
                remaining[hypothesis.hypothesis_id] = any(
                    not object_context_is_observed(
                        facts, context, command.authorized_origin
                    )
                    for context in command.object_contexts
                    if origin_binds_object_context(hypothesis.origin_reference, context)
                )
            elif family is HypothesisFamily.WORKFLOW_STATE_TRANSITION:
                remaining[hypothesis.hypothesis_id] = any(
                    not workflow_context_is_observed(
                        facts, context, command.authorized_origin
                    )
                    for context in command.workflow_contexts
                    if origin_binds_workflow_context(hypothesis.origin_reference, context)
                )
            else:
                remaining[hypothesis.hypothesis_id] = False
        return remaining

    def _executed_identities(self, uow, research_run_id: str, experiments) -> frozenset[str]:
        identities: set[str] = set()
        for experiment in experiments:
            plan_record = uow.experiment_plans.get(experiment.experiment_id)
            if plan_record is None:
                continue
            plan = experiment_plan_from_record(plan_record)
            identity = identity_from_plan_arguments(
                capability=plan.required_capability,
                arguments=plan.arguments,
                target_reference=plan.target_reference,
            )
            if identity is not None:
                identities.add(identity)
        for opportunity in uow.research_opportunities.list_for_research_run(research_run_id):
            identities.add(opportunity.structural_identity)
        return frozenset(identities)

    def _negative_contexts(self, portfolio: ResearchPortfolio, uow, experiments) -> frozenset[str]:
        falsified = {
            item.hypothesis_id
            for item in portfolio.hypotheses
            if item.lifecycle is HypothesisLifecycle.FALSIFIED
        }
        signatures: set[str] = set()
        for experiment in experiments:
            if experiment.hypothesis_id not in falsified:
                continue
            plan_record = uow.experiment_plans.get(experiment.experiment_id)
            if plan_record is None:
                continue
            plan = experiment_plan_from_record(plan_record)
            identity = identity_from_plan_arguments(
                capability=plan.required_capability,
                arguments=plan.arguments,
                target_reference=plan.target_reference,
            )
            if identity is None:
                continue
            from zest.research.selection import context_signature_for

            if plan.required_capability.endswith("authorization.differential"):
                resource = (
                    f"{plan.arguments.get('own_object')}:"
                    f"{plan.arguments.get('cross_object')}"
                )
                family = HypothesisFamily.OBJECT_AUTHORIZATION.value
            else:
                resource = str(plan.arguments.get("resource_id") or "")
                family = HypothesisFamily.WORKFLOW_STATE_TRANSITION.value
            signatures.add(
                context_signature_for(
                    family=family,
                    origin=plan.target_reference,
                    resource=resource,
                )
            )
        return frozenset(signatures)

    def _persist_decisions(self, uow, research_run_id: str, decisions, now) -> None:
        for decision in decisions:
            option = decision.option
            if decision.outcome is SelectionOutcome.SELECT:
                uow.research_opportunities.insert(
                    ResearchOpportunityRecord(
                        opportunity_id=option.option_id,
                        research_run_id=research_run_id,
                        opportunity_kind=opportunity_kind_for(option.purpose).value,
                        mode=opportunity_mode_for(option.purpose).value,
                        source_refs=option.hypothesis_ids,
                        proposed_direction=option.requested_observation,
                        unresolved_question=";".join(option.unresolved_facts)
                        or "no unresolved fact recorded",
                        expected_information_value_description=option.discrimination.value,
                        assumptions=("not_authorization", "not_a_finding"),
                        dimensions=_dimensions_for(option),
                        context_signature=option.context_signature,
                        novelty_composition_marker=False,
                        prior_attempt_refs=option.observation_ids,
                        structural_identity=option.structural_identity,
                        strategy_version=RESEARCH_SELECTION_STRATEGY_VERSION,
                        created_at=now,
                    )
                )
            uow.research_selections.insert(
                ResearchSelectionRecord(
                    selection_id=new_opaque_id(),
                    research_run_id=research_run_id,
                    opportunity_id=option.option_id,
                    outcome=decision.outcome.value,
                    reason_codes=decision.reason_codes,
                    structural_identity=option.structural_identity,
                    created_at=now,
                )
            )

    def _trace_audit(
        self,
        *,
        research_run_id: str,
        cycle_number: int,
        portfolio: ResearchPortfolio,
        decisions,
        selected: ExperimentOption | None,
        core_decision: str | None,
        stop_reason: str | None,
        budget_before: int,
        budget_after: int,
        now,
        experiment_id: str | None = None,
        assessment_id: str | None = None,
        observation_ids: tuple[str, ...] = (),
    ) -> AuditEventRecord:
        return AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id=self._actor_id,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type="RESEARCH_SELECTION_CYCLE",
            subject_type="research_run",
            subject_id=research_run_id,
            payload={
                "iteration_id": cycle_number,
                "active_hypothesis_ids": [
                    item.hypothesis_id for item in portfolio.live()
                ],
                "hypothesis_lifecycles": {
                    item.hypothesis_id: item.lifecycle.value
                    for item in portfolio.hypotheses
                },
                "experiment_options_considered": [
                    item.option.to_public_mapping() for item in decisions
                ],
                "selected_experiment": (
                    selected.to_public_mapping() if selected is not None else None
                ),
                "selection_reason": list(decisions[0].reason_codes)
                if decisions and any(item.selected for item in decisions)
                else [item.reason_codes[0] for item in decisions[:1]],
                "core_decision": core_decision,
                "execution_attempt_id": experiment_id,
                "observation_ids": list(observation_ids),
                "assessment_ids": [assessment_id] if assessment_id else [],
                "budget_before": budget_before,
                "budget_after": budget_after,
                "stop_reason": stop_reason,
                "not_a_model": True,
                "not_a_finding": True,
            },
        )

    def _result_from_tick(
        self,
        tick,
        *,
        selected_purpose: str | None = None,
        observation_ids: tuple[str, ...] = (),
        assessment_id: str | None = None,
        core_decision: str | None = None,
        options_considered: int = 0,
        selection_reason_codes: tuple[str, ...] = (),
    ) -> ResearchSelectionStepResult:
        return ResearchSelectionStepResult(
            research_run_id=tick.research_run_id,
            state=tick.state,
            cycle_number=tick.cycle_number,
            outcome=tick.outcome,
            stop_reason=tick.stop_reason,
            selected_purpose=selected_purpose,
            experiment_id=tick.experiment_id,
            hypothesis_id=tick.hypothesis_id,
            observation_ids=observation_ids,
            assessment_id=assessment_id,
            core_decision=core_decision,
            options_considered=options_considered,
            selection_reason_codes=selection_reason_codes,
        )


def _dimensions_for(option: ExperimentOption) -> dict[str, Any]:
    information = (
        OrdinalLevel.HIGH
        if option.discrimination.value.startswith("HIGH")
        else OrdinalLevel.MEDIUM
        if option.discrimination.value.startswith("MEDIUM")
        else OrdinalLevel.LOW
    )
    mapping = OpportunityDimensions(
        expected_information_value=information,
        security_relevance_potential=OrdinalLevel.MEDIUM,
        novelty_composition=OrdinalLevel.MEDIUM,
        unresolved_uncertainty=OrdinalLevel.HIGH
        if option.resolves_missing_fact
        else OrdinalLevel.LOW,
        chain_potential=OrdinalLevel.LOW,
        evidence_coverage=OrdinalLevel.MEDIUM,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=option.side_effect_level,
        duplicate_risk=OrdinalLevel.LOW,
        previous_failed_attempts=0,
    ).to_mapping()
    mapping.update(
        {
            "purpose": option.purpose.value,
            "can_falsify_live": option.can_falsify_live,
            "distinguishes_competing_count": option.distinguishes_competing_count,
            "resolves_missing_fact": option.resolves_missing_fact,
            "provides_missing_negative_control": option.provides_missing_negative_control,
            "estimated_request_cost": option.estimated_request_cost,
            "not_a_priority_score": True,
            "not_confidence": True,
        }
    )
    return mapping
