"""Bounded autonomous orchestration for one ResearchRun.

Coordinates existing use cases. Does not own Core authority, Worker execution,
or Research-domain admission semantics. Autonomous != unbounded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from zest.application.budget_enforced_model import BudgetEnforcedModelPort
from zest.application.budget_consumption import (
    BudgetConsumptionRejected,
    RecordBudgetConsumption,
)
from zest.application.errors import ApplicationError
from zest.application.orchestration_config import (
    assert_command_matches_configuration,
    configuration_from_record,
    fingerprint_for_start,
    scope_fingerprint,
)
from zest.application.runtime_outcomes import stop_reason_for_runtime_outcome
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.capability_binding import (
    CapabilityBindingError,
    capability_view_for_plan,
)
from zest.application.execute_planned_experiment import (
    AuthorizedDispatch,
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
    _build_worker_request,
)
from zest.application.identity import new_opaque_id
from zest.application.plan_records import experiment_plan_from_record
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.application.promotion_pipeline import (
    AdvancePromotionCommand,
    PromoteOnAssessment,
    PromotionPipeline,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.application.registry_external_anomaly_source import (
    load_identity_anomaly_context,
    record_identity_anomaly_validation_audit,
)
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.application.select_research_runtime import (
    SelectResearchRuntime,
    SelectResearchRuntimeCommand,
)
from zest.core.approval import ApprovalView
from zest.core.enums import ActorType, ExecutionDecisionKind, ReasonCode
from zest.core.scope import ScopeEvaluationInput
from zest.core.scope_compiler import CompiledScope
from zest.data.budget_ledger import ledger_totals
from zest.data.records import (
    AuditEventRecord,
    ExecutionAttemptState,
    ResearchCycleRecord,
    ResearchOrchestrationRecord,
)
from zest.platform.observability import InMemoryObservability, ObservabilityPort, TelemetryEvent
from zest.platform.secrets import CompositeSecretPort
from zest.platform.worker import WorkerPort
from zest.research.admission import AdmissionOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.exploration import ResearchPolicyBudget
from zest.research.model_port import ModelPort
from zest.research.model_runtime import RuntimeOutcome
from zest.research.orchestration import (
    ORCHESTRATION_POLICY_VERSION,
    TERMINAL_ORCHESTRATION_STATES,
    CycleOutcome,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
    OrchestrationUsage,
    StopReason,
    check_orchestration_bounds,
    cycle_outcome_for_stop,
    next_cycle_action,
    orchestration_state_for_stop,
    NextCycleAction,
)
from zest.data.errors import PersistenceConflictError, TerminalOrchestrationStateError
from zest.data.uniqueness import UQ_RESEARCH_CYCLE_RUN_NUMBER, is_uniqueness_conflict
from zest.research.exploration import OpportunityKind
from zest.research.identity_anomaly import (
    compile_identity_anomaly_experiment,
    exploratory_experiment_id,
    is_exploratory_hypothesis_origin,
)
from zest.research.planning import plan_diagnostic_echo
from zest.research.types import ResearchInputError
from zest.research.routing import ROUTING_POLICY_VERSION, RoutingOutcome, RoutingRequest
from zest.application.discovery.runner import (
    SurfaceDiscoveryCycleResult,
    SurfaceDiscoveryRunner,
    SurfaceDiscoveryStart,
)


CONTROL_PLANE_ACTOR_ID = "control-plane"
DIAGNOSTIC_RESEARCH_QUESTION = (
    "Does the diagnostic capability return the submitted value?"
)

# Sentinel distinguishing "caller did not specify this field, carry the
# persisted value forward unchanged" from "caller explicitly wants this field
# set to None". Plain `None` cannot mean both without ambiguity.
_UNSET = object()


@dataclass(frozen=True)
class StartAutonomousResearchCommand:
    research_run_id: str
    budget_id: str
    target_reference: str
    scope: ScopeEvaluationInput
    bounds: OrchestrationBounds
    research_question: str = DIAGNOSTIC_RESEARCH_QUESTION
    approval: ApprovalView | None = None
    routing_request: RoutingRequest | None = None
    selection_budget: ResearchPolicyBudget | None = None
    surface_discovery: SurfaceDiscoveryStart | None = None
    compiled_scope: CompiledScope | None = None


@dataclass(frozen=True)
class OrchestrationTickResult:
    research_run_id: str
    state: str
    cycle_number: int
    outcome: str
    stop_reason: str | None
    last_phase: str
    hypothesis_id: str | None = None
    experiment_id: str | None = None


@dataclass(frozen=True)
class ManagedCycleOutcome:
    """What a `run_managed_cycle` strategy decided. Not itself a persisted write.

    Lets a non-model, deterministic selection strategy (e.g. HTTP
    object-authorization / workflow-state-transition probing) report the
    result of one cycle without touching `research_orchestration` itself.
    `AutonomousResearchController.run_managed_cycle` is the only writer of
    that row; this is its input, not an alternate write path.
    """

    outcome: CycleOutcome
    phase_label: str
    state: OrchestrationState | None = None
    stop_reason_value: str | None = None
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    opportunity_id: str | None = None
    observation_id: str | None = None
    assessment_id: str | None = None
    pause_reason: str | None = None
    increment_cycle: bool = False
    current_phase: OrchestrationPhase | None = None
    extra_audit_events: tuple[AuditEventRecord, ...] = ()


ManagedCycleFn = Callable[
    [
        ResearchOrchestrationRecord,
        PreparePlannedExperiment,
        ExecutePlannedExperiment,
        EvaluateExperimentFeedback,
    ],
    ManagedCycleOutcome,
]


class AutonomousResearchController:
    """One controller manages one ResearchRun. Research cannot become execution authority."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        model: ModelPort,
        *,
        clock: Clock | None = None,
        observability: ObservabilityPort | None = None,
        actor_id: str = CONTROL_PLANE_ACTOR_ID,
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._observability = observability or InMemoryObservability()
        self._actor_id = actor_id
        self._model = model
        self._execute = ExecutePlannedExperiment(
            uow_factory, worker, clock=self._clock, actor_id=actor_id, secret_port=secret_port
        )
        self._propose = ProposeResearchHypothesis(
            uow_factory, model, clock=self._clock
        )
        self._prepare = PreparePlannedExperiment(uow_factory, clock=self._clock)
        evaluate = EvaluateExperimentFeedback(uow_factory, clock=self._clock)
        self._promotion = PromotionPipeline(
            uow_factory,
            clock=self._clock,
            worker=worker,
            actor_id=actor_id,
            secret_port=secret_port,
        )
        self._evaluate = PromoteOnAssessment(evaluate, self._promotion)
        self._select = SelectResearchOpportunities(
            uow_factory, clock=self._clock, actor_id=actor_id
        )
        self._route = SelectResearchRuntime(
            uow_factory, clock=self._clock, actor_id=actor_id
        )
        self._consume = RecordBudgetConsumption(uow_factory, clock=self._clock)
        self._started_at: dict[str, datetime] = {}
        self._discovery = SurfaceDiscoveryRunner(
            uow_factory, worker, clock=self._clock, secret_port=secret_port
        )

    def start(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            existing = uow.research_orchestrations.get(command.research_run_id)
            if existing is not None:
                uow.rollback()
                return _result_from_record(existing, CycleOutcome.CONTINUE)
            zero = command.bounds.max_cycles == 0
            scope_fp = scope_fingerprint(command.scope)
            routing_version = (
                ROUTING_POLICY_VERSION if command.routing_request is not None else None
            )
            fingerprint = fingerprint_for_start(
                research_run_id=command.research_run_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                research_question=command.research_question,
                policy_version=ORCHESTRATION_POLICY_VERSION,
                bounds=command.bounds,
                routing_policy_version=routing_version,
                scope_fp=scope_fp,
            )
            record = ResearchOrchestrationRecord(
                research_run_id=command.research_run_id,
                state=(
                    OrchestrationState.COMPLETED.value
                    if zero
                    else OrchestrationState.READY.value
                ),
                cycle_number=0,
                last_phase="start",
                last_opportunity_id=None,
                last_hypothesis_id=None,
                last_experiment_id=None,
                pause_reason=None,
                stop_reason=StopReason.MAX_CYCLES_REACHED.value if zero else None,
                policy_version=ORCHESTRATION_POLICY_VERSION,
                max_cycles=command.bounds.max_cycles,
                max_experiments=command.bounds.max_experiments,
                max_model_calls=command.bounds.max_model_calls,
                max_worker_invocations=command.bounds.max_worker_invocations,
                max_elapsed_ms=command.bounds.max_elapsed_ms,
                max_selected_opportunities=command.bounds.max_selected_opportunities,
                max_runtime_fallback=command.bounds.max_runtime_fallback,
                side_effect_ceiling=command.bounds.side_effect_ceiling,
                allow_repeated_control_experiments=(
                    command.bounds.allow_repeated_control_experiments
                ),
                created_at=now,
                updated_at=now,
                checkpoint_at=now,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                research_question=command.research_question,
                configuration_fingerprint=fingerprint,
                current_phase=OrchestrationPhase.CYCLE_READY.value,
                routing_policy_version=routing_version,
                scope_fingerprint=scope_fp,
            )
            uow.research_orchestrations.insert(record)
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="ORCHESTRATION_STARTED",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "policy_version": ORCHESTRATION_POLICY_VERSION,
                        "max_cycles": command.bounds.max_cycles,
                        "not_unbounded": True,
                    },
                )
            )
            uow.commit()
        self._started_at[command.research_run_id] = now
        self._observability.emit(
            TelemetryEvent(
                event="orchestration.start",
                outcome=record.state,
                research_run_id=command.research_run_id,
            )
        )
        outcome = CycleOutcome.COMPLETE if zero else CycleOutcome.CONTINUE
        return _result_from_record(record, outcome)

    def pause(self, research_run_id: str) -> OrchestrationTickResult:
        return self._operator_state(
            research_run_id,
            OrchestrationState.PAUSED,
            StopReason.OPERATOR_PAUSED,
            CycleOutcome.PAUSE,
        )

    def resume(self, research_run_id: str) -> OrchestrationTickResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            if current is None:
                raise ApplicationError("orchestration not found")
            if current.state != OrchestrationState.PAUSED.value:
                uow.rollback()
                return _result_from_record(current, CycleOutcome.CONTINUE)
            updated = replace(
                current,
                state=OrchestrationState.READY.value,
                pause_reason=None,
                stop_reason=None,
                last_phase="resume",
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(updated)
            uow.commit()
        return _result_from_record(updated, CycleOutcome.CONTINUE)

    def cancel(self, research_run_id: str) -> OrchestrationTickResult:
        return self._operator_state(
            research_run_id,
            OrchestrationState.COMPLETED,
            StopReason.OPERATOR_CANCELLED,
            CycleOutcome.COMPLETE,
        )

    def stop_for_operational_failure(
        self,
        research_run_id: str,
        *,
        phase: str = "runtime_fault",
    ) -> OrchestrationTickResult:
        """Fail an actively-owned orchestration without creating research truth."""

        now = self._clock.now()
        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            if current is None:
                raise ApplicationError("orchestration not found")

            if current.state not in {
                OrchestrationState.READY.value,
                OrchestrationState.RUNNING.value,
            }:
                uow.rollback()
                return _result_from_record(current, CycleOutcome.CONTINUE)

            updated = replace(
                current,
                state=OrchestrationState.FAILED_OPERATIONAL.value,
                stop_reason=StopReason.OPERATIONAL_FAILURE.value,
                last_phase=phase,
                updated_at=now,
                checkpoint_at=now,
            )

            uow.research_orchestrations.save(updated)
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="ORCHESTRATION_OPERATIONAL_FAILURE",
                    subject_type="research_run",
                    subject_id=research_run_id,
                    payload={
                        "previous_state": current.state,
                        "phase": phase,
                        "not_research_truth": True,
                    },
                )
            )
            uow.commit()

        return _result_from_record(updated, CycleOutcome.BLOCKED)

    def step(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        current = self._reload(command.research_run_id)
        config = configuration_from_record(current)
        assert_command_matches_configuration(
            config=config,
            bounds=command.bounds,
            budget_id=command.budget_id,
            target_reference=command.target_reference,
            research_question=command.research_question,
            scope=command.scope,
        )
        bounds = config.bounds
        if current.state in {
            OrchestrationState.COMPLETED.value,
            OrchestrationState.BUDGET_EXHAUSTED.value,
            OrchestrationState.FAILED_OPERATIONAL.value,
            OrchestrationState.BLOCKED.value,
            OrchestrationState.WAITING_HUMAN.value,
            OrchestrationState.PAUSED.value,
        }:
            return _result_from_record(current, CycleOutcome.CONTINUE)

        if command.surface_discovery is not None:
            return self._step_surface_discovery(command, current)

        phase = current.current_phase
        if phase == OrchestrationPhase.DISPATCHING.value or self._unknown_open(
            command.research_run_id
        ):
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "unknown_outcome")

        resumed = self._resume_authorized(command, current)
        if resumed is not None:
            return resumed

        if phase == OrchestrationPhase.HYPOTHESIS_ADMITTED.value and current.last_hypothesis_id:
            return self._resume_admitted_hypothesis(command, current, config)
        if phase == OrchestrationPhase.OPPORTUNITY_SELECTED.value:
            if self._opportunity_is_exploratory(current.last_opportunity_id):
                if current.last_hypothesis_id and self._hypothesis_is_exploratory(
                    command.research_run_id, current.last_hypothesis_id
                ):
                    current = self._checkpoint(
                        current,
                        phase=OrchestrationPhase.HYPOTHESIS_ADMITTED,
                        hypothesis_id=current.last_hypothesis_id,
                    )
                    return self._resume_admitted_hypothesis(command, current, config)
            else:
                existing_hypothesis = current.last_hypothesis_id or self._latest_hypothesis_id(
                    command.research_run_id
                )
                if existing_hypothesis:
                    current = self._checkpoint(
                        current,
                        phase=OrchestrationPhase.HYPOTHESIS_ADMITTED,
                        hypothesis_id=existing_hypothesis,
                    )
                    return self._resume_admitted_hypothesis(command, current, config)
        if phase in {
            OrchestrationPhase.EXPERIMENT_PLANNED.value,
            OrchestrationPhase.AUTHORIZATION_REQUESTED.value,
        } and current.last_experiment_id:
            return self._resume_planned_experiment(command, current, config)
        if phase == OrchestrationPhase.ATTEMPT_AUTHORIZED.value and current.last_experiment_id:
            return self._resume_planned_experiment(command, current, config)
        if phase in {
            OrchestrationPhase.WORKER_RESULT_RECORDED.value,
            OrchestrationPhase.TRANSITION_A_COMPLETE.value,
            OrchestrationPhase.ASSESSMENT_COMPLETE.value,
            OrchestrationPhase.TRANSITION_B_COMPLETE.value,
        } and current.last_experiment_id:
            return self._resume_after_worker(command, current, config)

        usage = self._usage(config, current)
        bound = check_orchestration_bounds(bounds, usage)
        if not bound.allowed and bound.stop_reason is not None:
            return self._stop(current, bound.stop_reason, "bounds")

        self._mark_running(current)
        current = self._reload(command.research_run_id)
        skip_discovery = phase == OrchestrationPhase.OPPORTUNITY_SELECTED.value

        if not skip_discovery and command.routing_request is not None:
            routed = self._route.execute(
                SelectResearchRuntimeCommand(
                    research_run_id=command.research_run_id,
                    request=command.routing_request,
                )
            )
            if routed.decision.outcome is RoutingOutcome.NO_COMPATIBLE_RUNTIME:
                return self._stop(current, StopReason.NO_COMPATIBLE_RUNTIME, "routing")
            if routed.decision.outcome is RoutingOutcome.BLOCKED_POLICY:
                return self._stop(current, StopReason.CONTENT_POLICY_BLOCKED, "routing")
            if routed.decision.outcome is not RoutingOutcome.SELECT:
                return self._stop(current, StopReason.NO_COMPATIBLE_RUNTIME, "routing")

        if skip_discovery:
            opportunity_id = current.last_opportunity_id
            cycle_id = current.active_cycle_id or new_opaque_id()
        else:
            selected = self._select.execute(
                SelectResearchOpportunitiesCommand(
                    research_run_id=command.research_run_id,
                    budget=command.selection_budget,
                )
            )
            selected_ids = [item.opportunity.opportunity_id for item in selected.selected]
            if len(selected_ids) > bounds.max_selected_opportunities:
                selected_ids = selected_ids[: bounds.max_selected_opportunities]
            opportunity_id = selected_ids[0] if selected_ids else None
            action, stop = next_cycle_action(
                bounds=bounds,
                usage=self._usage(config, current),
                selected_count=len(selected_ids),
                hypothesis_count=self._hypothesis_count(command.research_run_id),
                unknown_outcome_open=False,
            )
            if action is NextCycleAction.STOP:
                reason = stop or StopReason.COMPLETED_NO_MORE_OPPORTUNITIES
                return self._stop(current, reason, "no_more_opportunities")

            cycle_id = current.active_cycle_id or new_opaque_id()
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.OPPORTUNITY_SELECTED,
                opportunity_id=opportunity_id,
                active_cycle_id=cycle_id,
            )

        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(config.research_run_id)
            program_id = run.program_id if run is not None else None
            uow.rollback()

        bound_model = BudgetEnforcedModelPort(
            self._model,
            self._uow_factory,
            budget_id=config.budget_id,
            research_run_id=config.research_run_id,
            cycle_id=cycle_id,
            program_id=program_id,
            clock=self._clock,
        )
        proposer = ProposeResearchHypothesis(
            self._uow_factory, bound_model, clock=self._clock
        )
        correlation_id = new_opaque_id()

        def _persist_hypothesis(uow, *, hypothesis_id: str | None) -> None:
            nonlocal current
            if hypothesis_id is None:
                return
            now = self._clock.now()
            current = replace(
                current,
                state=OrchestrationState.RUNNING.value,
                current_phase=OrchestrationPhase.HYPOTHESIS_ADMITTED.value,
                last_phase=OrchestrationPhase.HYPOTHESIS_ADMITTED.value,
                last_opportunity_id=opportunity_id or current.last_opportunity_id,
                last_hypothesis_id=hypothesis_id,
                active_cycle_id=cycle_id,
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(current)

        try:
            proposed = proposer.execute(
                ProposeResearchHypothesisCommand(
                    research_run_id=command.research_run_id,
                    research_question=config.research_question,
                    budget_id=config.budget_id,
                    target_reference=config.target_reference,
                    correlation_id=correlation_id,
                    opportunity_id=opportunity_id,
                    echo_message=f"ping-{current.cycle_number + 1}",
                ),
                persist_hook=_persist_hypothesis,
            )
        except BudgetConsumptionRejected:
            return self._stop(current, StopReason.BUDGET_EXHAUSTED, "model_budget")
        if bound_model.reserved_invocations:
            self._observability.increment("model_calls", len(bound_model.reserved_invocations))

        if proposed.outcome is AdmissionOutcome.MODEL_INVOCATION_FAILED:
            outcome = proposed.runtime_outcome or RuntimeOutcome.PROCESS_FAILED
            return self._stop(
                current,
                stop_reason_for_runtime_outcome(outcome),
                "model_runtime_outcome",
                hypothesis_id=proposed.hypothesis_id,
            )
        if not proposed.admission.admitted or proposed.experiment_plan is None:
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "hypothesis_not_admitted",
                opportunity_id=opportunity_id,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )

        plan = proposed.experiment_plan
        if plan.side_effect_level > bounds.side_effect_ceiling:
            return self._stop(current, StopReason.CORE_BLOCKED, "side_effect_ceiling")

        existing_experiment_id = None
        if proposed.hypothesis_id:
            existing_experiment_id = self._existing_experiment_id(
                command.research_run_id, proposed.hypothesis_id
            )
        if existing_experiment_id:
            experiment_id = existing_experiment_id
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.EXPERIMENT_PLANNED,
                experiment_id=experiment_id,
                hypothesis_id=proposed.hypothesis_id,
                opportunity_id=opportunity_id,
            )
            return self._resume_planned_experiment(command, current, config)

        experiment_id = (
            exploratory_experiment_id(command.research_run_id, proposed.hypothesis_id)
            if proposed.hypothesis_id and self._opportunity_is_exploratory(opportunity_id)
            else new_opaque_id()
        )
        with self._uow_factory.open() as uow:
            try:
                self._prepare.execute(
                    PreparePlannedExperimentCommand(
                        experiment_id=experiment_id,
                        research_run_id=command.research_run_id,
                        plan=plan,
                    ),
                    unit_of_work=uow,
                )
            except PersistenceConflictError:
                reused = self._existing_experiment_id(
                    command.research_run_id, proposed.hypothesis_id or ""
                )
                if reused is None:
                    raise
                uow.rollback()
                current = self._checkpoint(
                    current,
                    phase=OrchestrationPhase.EXPERIMENT_PLANNED,
                    experiment_id=reused,
                    hypothesis_id=proposed.hypothesis_id,
                    opportunity_id=opportunity_id,
                )
                return self._resume_planned_experiment(command, current, config)
            current = replace(
                current,
                current_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                last_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                last_experiment_id=experiment_id,
                last_hypothesis_id=proposed.hypothesis_id or current.last_hypothesis_id,
                updated_at=self._clock.now(),
                checkpoint_at=self._clock.now(),
            )
            uow.research_orchestrations.save(current)
            uow.commit()

        current = self._checkpoint(
            current,
            phase=OrchestrationPhase.AUTHORIZATION_REQUESTED,
            experiment_id=experiment_id,
            hypothesis_id=proposed.hypothesis_id,
        )

        def _persist_attempt(uow, *, attempt_id: str, experiment_id: str) -> None:
            nonlocal current
            now = self._clock.now()
            current = replace(
                current,
                state=OrchestrationState.RUNNING.value,
                current_phase=OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
                last_phase=OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
                last_experiment_id=experiment_id,
                last_attempt_id=attempt_id,
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(current)

        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
                approval=command.approval,
                compiled_scope=command.compiled_scope,
            ),
            persist_hook=_persist_attempt,
        )
        self._observability.increment("experiments_executed")
        if loop.status is ResearchLoopStatus.DISPATCH_DENIED:
            return self._stop(
                current,
                StopReason.CORE_BLOCKED,
                "core_deny",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
            )
        if loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "human_review",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
            )
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unknown_outcome",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
            )
        if loop.status in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.NO_OBSERVATION,
            ResearchLoopStatus.INVOCATION_FAILED,
        } and loop.experiment_id:
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
                experiment_id=experiment_id,
                hypothesis_id=proposed.hypothesis_id,
                attempt_id=loop.attempt_id,
                worker_result_id=loop.worker_result_id,
                observation_id=loop.observation_ids[0] if loop.observation_ids else None,
            )
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.TRANSITION_A_COMPLETE,
                experiment_id=experiment_id,
                hypothesis_id=proposed.hypothesis_id,
                observation_id=loop.observation_ids[0] if loop.observation_ids else None,
            )
            feedback = self._evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=loop.experiment_id)
            )
            self._continue_promotion(command, feedback)
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.ASSESSMENT_COMPLETE,
                experiment_id=experiment_id,
                hypothesis_id=proposed.hypothesis_id,
                assessment_id=feedback.assessment_id,
            )
        self._observability.increment("orchestration_cycles")
        next_usage = self._usage(config, current)
        if next_usage.cycles_completed + 1 >= bounds.max_cycles:
            return self._stop(
                current,
                StopReason.MAX_CYCLES_REACHED,
                "execute",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                opportunity_id=opportunity_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "execute",
            opportunity_id=opportunity_id,
            hypothesis_id=proposed.hypothesis_id,
            experiment_id=experiment_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def run_bounded(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        started = self.start(command)
        if started.state != OrchestrationState.READY.value:
            return started
        last = started
        ticks = command.bounds.max_cycles if command.bounds.max_cycles > 0 else 0
        for _ in range(ticks):
            last = self.step(command)
            if last.state != OrchestrationState.RUNNING.value and last.state != OrchestrationState.READY.value:
                return last
        return last

    def run_managed_cycle(
        self, research_run_id: str, cycle_fn: ManagedCycleFn
    ) -> OrchestrationTickResult:
        """Run one cycle of a caller-supplied, non-model decision strategy.

        This is the delegation seam for selection strategies that cannot use
        the model-driven `step()` path (e.g. a deterministic HTTP
        object-authorization / workflow-state-transition prober) but must
        still not become a second component that independently owns
        `research_orchestration` progression or independently dispatches a
        Worker. `cycle_fn` only receives this controller's own single
        `PreparePlannedExperiment` / `ExecutePlannedExperiment` /
        `EvaluateExperimentFeedback` instances -- the same ones `step()`
        uses -- so there is exactly one Worker dispatch path regardless of
        which strategy decided to use it. `cycle_fn`'s returned
        `ManagedCycleOutcome` is persisted through the same terminal-state
        guard and cycle bookkeeping (`_complete_cycle`) as `step()`, so this
        controller remains the sole writer of the orchestration row.
        """
        current = self._reload(research_run_id)
        if current.state in {
            OrchestrationState.COMPLETED.value,
            OrchestrationState.BUDGET_EXHAUSTED.value,
            OrchestrationState.FAILED_OPERATIONAL.value,
            OrchestrationState.BLOCKED.value,
            OrchestrationState.WAITING_HUMAN.value,
            OrchestrationState.PAUSED.value,
        }:
            return _result_from_record(current, CycleOutcome.CONTINUE)
        if self._unknown_open(research_run_id):
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "unknown_outcome")
        result = cycle_fn(current, self._prepare, self._execute, self._evaluate)
        return self._complete_cycle(
            current,
            result.outcome,
            result.phase_label,
            stop_reason=result.stop_reason_value,
            state=result.state,
            hypothesis_id=result.hypothesis_id,
            experiment_id=result.experiment_id,
            opportunity_id=result.opportunity_id,
            observation_id=result.observation_id,
            assessment_id=result.assessment_id,
            pause_reason=result.pause_reason,
            increment_cycle=result.increment_cycle,
            current_phase=result.current_phase,
            extra_audit_events=result.extra_audit_events,
        )

    def _step_surface_discovery(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
    ) -> OrchestrationTickResult:
        if self._unknown_open(command.research_run_id):
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "unknown_outcome")
        start = command.surface_discovery
        if start is None:
            raise ApplicationError("surface discovery start is required")
        result = self._discovery.run_cycle(
            start,
            budget_id=command.budget_id,
            target_reference=command.target_reference,
            scope=command.scope,
            approval=command.approval,
        )
        if result.stop_reason == "UNKNOWN_OUTCOME":
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unknown_outcome",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        if result.stop_reason == "INVOCATION_START_FAILED":
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "discovery_invocation_start_failed",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        if result.stop_reason == "INVOCATION_FAILED":
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "discovery_invocation_failed",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        if result.stop_reason == "BLOCKED_SCOPE":
            return self._stop(
                current,
                StopReason.CORE_BLOCKED,
                "blocked_scope",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        if result.stop_reason in {
            "MAX_DISCOVERY_CYCLES",
            "MAX_FRONTIER_ITEMS",
            "MAX_BROWSER_ACTIONS",
            "MAX_HTTP_TRANSACTIONS",
        }:
            return self._stop(
                current,
                StopReason.MAX_CYCLES_REACHED,
                "discovery_bounds",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        if result.stop_reason == "NO_ELIGIBLE_FRONTIER":
            return self._stop(
                current,
                StopReason.COMPLETED_NO_MORE_OPPORTUNITIES,
                "no_eligible_frontier",
                experiment_id=result.experiment_id,
                increment_cycle=True,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "surface_discovery",
            hypothesis_id=current.last_hypothesis_id,
            experiment_id=result.experiment_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def _resume_authorized(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
    ) -> OrchestrationTickResult | None:
        with self._uow_factory.open() as uow:
            attempts = uow.execution_attempts.list_for_research_run(command.research_run_id)
            authorized = [
                item
                for item in attempts
                if item.state == ExecutionAttemptState.AUTHORIZED.value
            ]
            dispatching = [
                item
                for item in attempts
                if item.state
                in {
                    ExecutionAttemptState.DISPATCHING.value,
                    ExecutionAttemptState.UNKNOWN_OUTCOME.value,
                }
            ]
            if dispatching:
                uow.rollback()
                return self._stop(current, StopReason.OPERATIONAL_FAILURE, "unknown_dispatch")
            if not authorized:
                uow.rollback()
                return None
            attempt = authorized[0]
            experiment = uow.experiments.get(attempt.experiment_id)
            plan_record = uow.experiment_plans.get(attempt.experiment_id)
            issued = uow.issued_budgets.get(attempt.budget_id)
            run = uow.research_runs.get(command.research_run_id)
            policy = uow.program_policies.get(run.program_id) if run is not None else None
            required_user_agent = (
                policy.action_policy.get("required_user_agent")
                if policy is not None and policy.action_policy
                else None
            )
            required_headers = (
                policy.action_policy.get("required_headers")
                if policy is not None and policy.action_policy
                else None
            )
            uow.rollback()
        if experiment is None or plan_record is None or issued is None:
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "resume_missing")
        plan = experiment_plan_from_record(plan_record)
        try:
            capability_view = capability_view_for_plan(plan)
        except CapabilityBindingError:
            return self._stop(current, StopReason.CORE_BLOCKED, "capability_binding")
        dispatch = AuthorizedDispatch(
            experiment_id=experiment.experiment_id,
            hypothesis_id=experiment.hypothesis_id,
            request_id=attempt.request_id,
            attempt_id=attempt.attempt_id,
            correlation_id=attempt.correlation_id,
            authorization_decision_reference=attempt.authorization_decision_reference,
                worker_request=_build_worker_request(
                experiment=experiment,
                plan=plan,
                capability_view=capability_view,
                issued=issued,
                request_id=attempt.request_id,
                correlation_id=attempt.correlation_id,
                    authorization_decision_reference=attempt.authorization_decision_reference,
                    required_user_agent=required_user_agent,
                    required_headers=required_headers,
                ),
            timeout_ms=issued.max_runtime_ms,
            core_decision=ExecutionDecisionKind.ALLOW,
            core_reason_code=ReasonCode.ALLOWED,
        )
        loop = self._execute.dispatch(dispatch)
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unknown_outcome",
                experiment_id=experiment.experiment_id,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "resume_authorized",
            hypothesis_id=experiment.hypothesis_id,
            experiment_id=experiment.experiment_id,
            increment_cycle=True,
        )

    def _operator_state(
        self,
        research_run_id: str,
        state: OrchestrationState,
        reason: StopReason,
        outcome: CycleOutcome,
    ) -> OrchestrationTickResult:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            if current is None:
                raise ApplicationError("orchestration not found")
            if current.state in TERMINAL_ORCHESTRATION_STATES:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="ORCHESTRATION_OPERATOR_COMMAND_REJECTED",
                        subject_type="research_run",
                        subject_id=research_run_id,
                        payload={
                            "requested_state": state.value,
                            "requested_stop_reason": reason.value,
                            "current_state": current.state,
                            "current_stop_reason": current.stop_reason,
                            "rejection_reason": "terminal_state_immutable",
                        },
                    )
                )
                uow.commit()
                return _result_from_record(current, CycleOutcome.CONTINUE)
            updated = replace(
                current,
                state=state.value,
                stop_reason=reason.value,
                pause_reason=reason.value if state is OrchestrationState.PAUSED else current.pause_reason,
                last_phase="operator",
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(updated)
            uow.commit()
        return _result_from_record(updated, outcome)

    def mark_operational_failure(
        self, research_run_id: str, *, reason: str
    ) -> OrchestrationTickResult:
        """Transition a RUNNING checkpoint to FAILED_OPERATIONAL from external
        reconciliation evidence.

        Callers must have already established, outside of this controller
        (e.g. via `ReconcileResearchRun` and the local supervisor registry),
        that the persisted RUNNING checkpoint has no active owner in this
        process. This method re-validates state itself and is a safe no-op
        both when the run is not RUNNING and when it is already terminal, so
        it can be called speculatively without risk of double transition.

        The actual write additionally requires (at the repository/SoR level)
        that the row is currently unowned or its lease has expired, so a
        live owner in a *different* process (undetectable by the
        process-local supervisor registry alone) cannot be overwritten by a
        reconciler that only checked local state.
        """
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            if current is None:
                raise ApplicationError("orchestration not found")
            if current.state != OrchestrationState.RUNNING.value:
                uow.rollback()
                return _result_from_record(current, CycleOutcome.CONTINUE)
            updated = replace(
                current,
                state=OrchestrationState.FAILED_OPERATIONAL.value,
                stop_reason=StopReason.OPERATIONAL_FAILURE.value,
                last_phase="reconciliation",
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(updated, require_unowned_or_expired=True)
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="ORCHESTRATION_RECONCILED_OPERATIONAL_FAILURE",
                    subject_type="research_run",
                    subject_id=research_run_id,
                    payload={
                        "previous_state": current.state,
                        "reason": reason,
                    },
                )
            )
            uow.commit()
        return _result_from_record(updated, CycleOutcome.BLOCKED)

    def _reload(self, research_run_id: str) -> ResearchOrchestrationRecord:
        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            uow.rollback()
        if current is None:
            raise ApplicationError("orchestration not found")
        return current

    def _mark_running(self, current: ResearchOrchestrationRecord) -> None:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            uow.research_orchestrations.save(
                replace(
                    current,
                    state=OrchestrationState.RUNNING.value,
                    last_phase="running",
                    updated_at=now,
                    checkpoint_at=now,
                )
            )
            uow.commit()

    def _stop(
        self,
        current: ResearchOrchestrationRecord,
        reason: StopReason,
        phase: str,
        *,
        hypothesis_id: str | None = None,
        experiment_id: str | None = None,
        opportunity_id: str | None = None,
        increment_cycle: bool = False,
        current_phase: OrchestrationPhase | None = None,
    ) -> OrchestrationTickResult:
        outcome = cycle_outcome_for_stop(reason)
        state = orchestration_state_for_stop(reason)
        return self._complete_cycle(
            current,
            outcome,
            phase,
            stop_reason=reason,
            state=state,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment_id,
            opportunity_id=opportunity_id,
            increment_cycle=increment_cycle,
            current_phase=current_phase,
        )

    def _complete_cycle(
        self,
        current: ResearchOrchestrationRecord,
        outcome: CycleOutcome,
        phase: str,
        *,
        stop_reason: StopReason | str | None = None,
        state: OrchestrationState | None = None,
        hypothesis_id: str | None = None,
        experiment_id: str | None = None,
        opportunity_id: str | None = None,
        observation_id: str | None = None,
        assessment_id: str | None = None,
        pause_reason: object = _UNSET,
        increment_cycle: bool = False,
        current_phase: OrchestrationPhase | None = None,
        extra_audit_events: tuple[AuditEventRecord, ...] = (),
    ) -> OrchestrationTickResult:
        now = self._clock.now()
        inserting = increment_cycle or outcome is not CycleOutcome.CONTINUE
        cycle_number = current.cycle_number + (1 if inserting else 0)
        next_state = (
            state.value
            if state is not None
            else (
                OrchestrationState.READY.value
                if outcome is CycleOutcome.CONTINUE
                else current.state
            )
        )
        if outcome is CycleOutcome.COMPLETE and state is None:
            next_state = OrchestrationState.COMPLETED.value
        resolved_stop_reason = (
            stop_reason.value if isinstance(stop_reason, StopReason) else stop_reason
        )
        updated = replace(
            current,
            state=next_state,
            cycle_number=cycle_number,
            last_phase=phase,
            current_phase=(
                current_phase.value
                if current_phase is not None
                else (
                    OrchestrationPhase.CYCLE_COMPLETE.value
                    if inserting
                    else current.current_phase
                )
            ),
            last_opportunity_id=opportunity_id or current.last_opportunity_id,
            last_hypothesis_id=hypothesis_id or current.last_hypothesis_id,
            last_experiment_id=experiment_id or current.last_experiment_id,
            last_observation_id=observation_id or current.last_observation_id,
            last_assessment_id=assessment_id or current.last_assessment_id,
            pause_reason=(
                current.pause_reason if pause_reason is _UNSET else pause_reason
            ),
            stop_reason=resolved_stop_reason if resolved_stop_reason else current.stop_reason,
            updated_at=now,
            checkpoint_at=now,
        )
        with self._uow_factory.open() as uow:
            try:
                uow.research_orchestrations.save(updated)
                if inserting:
                    try:
                        uow.research_cycles.insert(
                            ResearchCycleRecord(
                                cycle_id=new_opaque_id(),
                                research_run_id=current.research_run_id,
                                cycle_number=cycle_number,
                                phase_completed=phase,
                                outcome=outcome.value,
                                created_at=now,
                                stop_reason=resolved_stop_reason or None,
                                opportunity_id=opportunity_id,
                                hypothesis_id=hypothesis_id,
                                experiment_id=experiment_id,
                            )
                        )
                    except PersistenceConflictError as exc:
                        if not is_uniqueness_conflict(exc, UQ_RESEARCH_CYCLE_RUN_NUMBER):
                            raise
                for event in extra_audit_events:
                    uow.audit_events.insert(event)
                uow.commit()
            except TerminalOrchestrationStateError:
                uow.rollback()
                durable = self._reload(current.research_run_id)
                return _result_from_record(durable, outcome)
        self._observability.emit(
            TelemetryEvent(
                event="orchestration.cycle",
                outcome=outcome.value,
                research_run_id=current.research_run_id,
                experiment_id=experiment_id,
                orchestration_cycle=cycle_number,
            )
        )
        return _result_from_record(updated, outcome)

    def _usage(
        self,
        config,
        current: ResearchOrchestrationRecord,
    ) -> OrchestrationUsage:
        started = self._started_at.get(config.research_run_id, current.created_at)
        elapsed = int((self._clock.now() - started).total_seconds() * 1000)
        if elapsed < 0:
            elapsed = 0
        with self._uow_factory.open() as uow:
            experiments = uow.experiments.list_for_research_run(config.research_run_id)
            promotions = uow.promotion_runs.list_for_research_run(config.research_run_id)
            opportunities = uow.research_opportunities.list_for_research_run(
                config.research_run_id
            )
            consumption = uow.budget_consumptions.list_for_budget(config.budget_id)
            uow.rollback()
        reproduction_ids = {
            item.reproduction_experiment_id
            for item in promotions
            if item.reproduction_experiment_id is not None
        }
        research_experiments = [
            item
            for item in experiments
            if item.experiment_id not in reproduction_ids
        ]
        totals = ledger_totals(consumption)
        return OrchestrationUsage(
            cycles_completed=current.cycle_number,
            experiments_executed=len(research_experiments),
            model_calls=totals.model_calls,
            worker_invocations=totals.worker_invocations,
            elapsed_ms=elapsed,
            opportunities_selected=len(opportunities),
            runtime_fallbacks=0,
            worker_requests=totals.worker_requests,
            execution_time_ms=totals.execution_time_ms,
            artifact_bytes=totals.artifact_bytes,
        )

    def _continue_promotion(
        self, command: StartAutonomousResearchCommand, feedback
    ) -> None:
        """Continue durable promotion after assessment. Does not create Finding.

        ARC remains the caller. PromotionPipeline does not select opportunities
        or own research_orchestration.
        """
        if (
            feedback.assessment_outcome is AssessmentOutcome.CONSISTENT_WITH_PREDICTION
            and self._hypothesis_is_exploratory(
                feedback.research_run_id, feedback.hypothesis_id
            )
        ):
            with self._uow_factory.open() as uow:
                opportunity = None
                if command.research_run_id:
                    current = uow.research_orchestrations.get(command.research_run_id)
                    if current is not None and current.last_opportunity_id:
                        opportunity = uow.research_opportunities.get(
                            current.last_opportunity_id
                        )
                source_id = (
                    opportunity.source_refs[0]
                    if opportunity is not None and opportunity.source_refs
                    else feedback.experiment_id
                )
                record_identity_anomaly_validation_audit(
                    uow,
                    research_run_id=feedback.research_run_id,
                    hypothesis_id=feedback.hypothesis_id,
                    now=self._clock.now(),
                    source_id=source_id,
                )
                uow.commit()
        self._promotion.advance(
            AdvancePromotionCommand(
                research_run_id=feedback.research_run_id,
                scope=command.scope,
                approval=command.approval,
                assessment_id=feedback.assessment_id,
                compiled_scope=command.compiled_scope,
            )
        )

    def _checkpoint(
        self,
        current: ResearchOrchestrationRecord,
        *,
        phase: OrchestrationPhase,
        opportunity_id: str | None = None,
        hypothesis_id: str | None = None,
        experiment_id: str | None = None,
        active_cycle_id: str | None = None,
        attempt_id: str | None = None,
        observation_id: str | None = None,
        assessment_id: str | None = None,
        worker_result_id: str | None = None,
    ) -> ResearchOrchestrationRecord:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            updated = replace(
                current,
                state=OrchestrationState.RUNNING.value,
                current_phase=phase.value,
                last_phase=phase.value,
                last_opportunity_id=opportunity_id or current.last_opportunity_id,
                last_hypothesis_id=hypothesis_id or current.last_hypothesis_id,
                last_experiment_id=experiment_id or current.last_experiment_id,
                last_attempt_id=attempt_id or current.last_attempt_id,
                last_observation_id=observation_id or current.last_observation_id,
                last_assessment_id=assessment_id or current.last_assessment_id,
                last_worker_result_id=worker_result_id or current.last_worker_result_id,
                active_cycle_id=active_cycle_id or current.active_cycle_id,
                updated_at=now,
                checkpoint_at=now,
            )
            try:
                uow.research_orchestrations.save(updated)
                uow.commit()
            except TerminalOrchestrationStateError:
                uow.rollback()
                return self._reload(current.research_run_id)
        return updated

    def _resume_admitted_hypothesis(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        config,
    ) -> OrchestrationTickResult:
        hypothesis_id = current.last_hypothesis_id
        if hypothesis_id is None:
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "missing_hypothesis")
        experiment_id = current.last_experiment_id or self._existing_experiment_id(
            command.research_run_id, hypothesis_id
        )
        if experiment_id is None:
            if self._opportunity_is_exploratory(
                current.last_opportunity_id
            ) or self._hypothesis_is_exploratory(command.research_run_id, hypothesis_id):
                experiment_id = exploratory_experiment_id(
                    command.research_run_id, hypothesis_id
                )
            else:
                experiment_id = new_opaque_id()
            plan = self._plan_for_admitted_hypothesis(
                command.research_run_id,
                hypothesis_id,
                current.last_opportunity_id,
                config,
                message=f"ping-{current.cycle_number + 1}",
            )
            with self._uow_factory.open() as uow:
                self._prepare.execute(
                    PreparePlannedExperimentCommand(
                        experiment_id=experiment_id,
                        research_run_id=command.research_run_id,
                        plan=plan,
                    ),
                    unit_of_work=uow,
                )
                current = replace(
                    current,
                    current_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                    last_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                    last_experiment_id=experiment_id,
                    updated_at=self._clock.now(),
                    checkpoint_at=self._clock.now(),
                )
                uow.research_orchestrations.save(current)
                uow.commit()
        elif current.current_phase != OrchestrationPhase.EXPERIMENT_PLANNED.value:
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.EXPERIMENT_PLANNED,
                experiment_id=experiment_id,
                hypothesis_id=hypothesis_id,
            )
        return self._resume_planned_experiment(command, current, config)

    def _resume_planned_experiment(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        config,
    ) -> OrchestrationTickResult:
        experiment_id = current.last_experiment_id
        if experiment_id is None:
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "missing_experiment")
        with self._uow_factory.open() as uow:
            plan_record = uow.experiment_plans.get(experiment_id)
            uow.rollback()
        if plan_record is None:
            return self._stop(current, StopReason.OPERATIONAL_FAILURE, "missing_plan")
        plan = experiment_plan_from_record(plan_record)

        def _persist_attempt(uow, *, attempt_id: str, experiment_id: str) -> None:
            nonlocal current
            now = self._clock.now()
            current = replace(
                current,
                state=OrchestrationState.RUNNING.value,
                current_phase=OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
                last_phase=OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
                last_experiment_id=experiment_id,
                last_attempt_id=attempt_id,
                updated_at=now,
                checkpoint_at=now,
            )
            uow.research_orchestrations.save(current)

        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
                approval=command.approval,
                compiled_scope=command.compiled_scope,
            ),
            persist_hook=_persist_attempt,
        )
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unknown_outcome",
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
            )
        if loop.status is ResearchLoopStatus.DISPATCH_DENIED:
            return self._stop(current, StopReason.CORE_BLOCKED, "core_deny", experiment_id=experiment_id)
        if loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
            return self._stop(
                current, StopReason.REQUIRE_HUMAN_REVIEW, "human_review", experiment_id=experiment_id
            )
        if loop.experiment_id and loop.status is not ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            feedback = self._evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=loop.experiment_id)
            )
            self._continue_promotion(command, feedback)
        usage = self._usage(config, current)
        if usage.cycles_completed + 1 >= config.bounds.max_cycles:
            return self._stop(
                current,
                StopReason.MAX_CYCLES_REACHED,
                "resume_planned",
                experiment_id=experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "resume_planned",
            experiment_id=experiment_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def _resume_after_worker(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        config,
    ) -> OrchestrationTickResult:
        if current.last_experiment_id and current.current_phase in {
            OrchestrationPhase.WORKER_RESULT_RECORDED.value,
            OrchestrationPhase.TRANSITION_A_COMPLETE.value,
        }:
            feedback = self._evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=current.last_experiment_id)
            )
            self._continue_promotion(command, feedback)
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.ASSESSMENT_COMPLETE,
                experiment_id=current.last_experiment_id,
                assessment_id=feedback.assessment_id,
            )
        usage = self._usage(config, current)
        if usage.cycles_completed + 1 >= config.bounds.max_cycles:
            return self._stop(
                current,
                StopReason.MAX_CYCLES_REACHED,
                "resume_after_worker",
                experiment_id=current.last_experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "resume_after_worker",
            experiment_id=current.last_experiment_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def _hypothesis_count(self, research_run_id: str) -> int:
        with self._uow_factory.open() as uow:
            count = len(uow.hypotheses.list_for_research_run(research_run_id))
            uow.rollback()
        return count

    def _latest_hypothesis_id(self, research_run_id: str) -> str | None:
        with self._uow_factory.open() as uow:
            records = uow.hypotheses.list_for_research_run(research_run_id)
            uow.rollback()
        if not records:
            return None
        return records[-1].hypothesis_id

    def _opportunity_is_exploratory(self, opportunity_id: str | None) -> bool:
        if not opportunity_id:
            return False
        with self._uow_factory.open() as uow:
            record = uow.research_opportunities.get(opportunity_id)
            uow.rollback()
        return (
            record is not None
            and record.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
        )

    def _hypothesis_is_exploratory(self, research_run_id: str, hypothesis_id: str) -> bool:
        with self._uow_factory.open() as uow:
            record = uow.hypotheses.get(hypothesis_id)
            uow.rollback()
        return record is not None and is_exploratory_hypothesis_origin(record.origin_reference)

    def _plan_for_admitted_hypothesis(
        self,
        research_run_id: str,
        hypothesis_id: str,
        opportunity_id: str | None,
        config,
        *,
        message: str,
    ):
        if not self._opportunity_is_exploratory(
            opportunity_id
        ) and not self._hypothesis_is_exploratory(research_run_id, hypothesis_id):
            return plan_diagnostic_echo(
                hypothesis_id,
                budget_id=config.budget_id,
                target_reference=config.target_reference,
                message=message,
            )
        with self._uow_factory.open() as uow:
            opportunity = (
                uow.research_opportunities.get(opportunity_id) if opportunity_id else None
            )
            source_id = opportunity.source_refs[0] if opportunity and opportunity.source_refs else None
            if source_id is None:
                uow.rollback()
                raise ApplicationError("exploratory resume is missing source_refs")
            anomaly = load_identity_anomaly_context(
                uow, research_run_id=research_run_id, source_id=source_id
            )
            uow.rollback()
        try:
            return compile_identity_anomaly_experiment(
                anomaly,
                hypothesis_id=hypothesis_id,
                budget_id=config.budget_id,
                target_reference=config.target_reference,
            )
        except ResearchInputError as exc:
            raise ApplicationError(str(exc)) from exc

    def _existing_experiment_id(self, research_run_id: str, hypothesis_id: str) -> str | None:
        with self._uow_factory.open() as uow:
            records = uow.experiments.list_for_research_run(research_run_id)
            uow.rollback()
        matching = [item for item in records if item.hypothesis_id == hypothesis_id]
        if not matching:
            return None
        return matching[-1].experiment_id

    def _unknown_open(self, research_run_id: str) -> bool:
        with self._uow_factory.open() as uow:
            attempts = uow.execution_attempts.list_for_research_run(research_run_id)
            uow.rollback()
        return any(
            item.state
            in {
                ExecutionAttemptState.DISPATCHING.value,
                ExecutionAttemptState.UNKNOWN_OUTCOME.value,
            }
            for item in attempts
        )


def _result_from_record(
    record: ResearchOrchestrationRecord, outcome: CycleOutcome
) -> OrchestrationTickResult:
    mapped_outcome = outcome.value
    if record.state == OrchestrationState.PAUSED.value:
        mapped_outcome = CycleOutcome.PAUSE.value
    return OrchestrationTickResult(
        research_run_id=record.research_run_id,
        state=record.state,
        cycle_number=record.cycle_number,
        outcome=mapped_outcome,
        stop_reason=record.stop_reason,
        last_phase=record.last_phase,
        hypothesis_id=record.last_hypothesis_id,
        experiment_id=record.last_experiment_id,
    )
