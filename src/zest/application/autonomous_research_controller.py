"""Bounded autonomous orchestration for one ResearchRun.

Coordinates existing use cases. Does not own Core authority, Worker execution,
or Research-domain admission semantics. Autonomous != unbounded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from zest.application.budget_enforced_model import BudgetEnforcedModelPort
from zest.application.bounded_model_failover import (
    BoundedRateLimitFailoverModelPort,
)
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
from zest.application.orchestration_obligations import (
    unresolved_control_obligations,
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
from zest.application.http_transaction_authorization import (
    HTTP_SCOPE_CAPABILITIES,
)
from zest.application.identity import new_opaque_id
from zest.application.plan_records import experiment_plan_from_record
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.program_research_context import ProgramPolicyView
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
from zest.application.research_identity_catalog import (
    ResearchIdentityCatalog,
    persist_research_identities,
    resolve_identity_for_plan,
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
    ExperimentExecutionState,
    HypothesisRecord,
    ResearchCycleRecord,
    ResearchOrchestrationRecord,
    WorkerResultStatus,
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
from zest.application.discovery.lifecycle import (
    count_runnable_discovery_frontier,
    discovery_can_exit,
    discovery_exit_audit,
    surface_discovery_start_from_persisted,
)
from zest.application.dic_feedback import apply_dic_coverage_feedback
from zest.application.evaluate_dic import evaluate_selected_dic
from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.hunter_execution_feedback import apply_hunter_execution_feedback
from zest.application.identity_engine_feedback import apply_identity_engine_coverage_feedback
from zest.application.mutation_protocol_feedback import apply_mutation_protocol_coverage_feedback
from zest.application.oast_feedback import apply_oast_coverage_feedback
from zest.application.oast_source import OAST_CALLBACK_EVALUATION_STRATEGY
from zest.application.oast_timeout import close_expired_oast_arms
from zest.application.research_work_planners import (
    ResearchCompileStatus,
    default_research_work_planner_registry,
)


CONTROL_PLANE_ACTOR_ID = "control-plane"

MODEL_CHECKPOINT_RATE_LIMIT_RETRY_BOUND = 1
MODEL_CHECKPOINT_RATE_LIMIT_RETRY_EVENT = (
    "MODEL_RUNTIME_RATE_LIMIT_CHECKPOINT_RETRY"
)

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
    program_policy: ProgramPolicyView | None = None
    identities: tuple = ()
    authentication_profiles: tuple = ()


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
        fallback_models: tuple[ModelPort, ...] = (),
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
        self._fallback_models = tuple(fallback_models)
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
        self._work_planners = default_research_work_planner_registry()

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
            persist_research_identities(
                uow,
                research_run_id=command.research_run_id,
                catalog=ResearchIdentityCatalog(
                    identities=tuple(command.identities),
                    profiles=tuple(command.authentication_profiles),
                ),
                now=now,
                actor_id=self._actor_id,
            )
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


    def deny_reauthorization(
        self,
        research_run_id: str,
        *,
        worker_result_id: str,
        operator_id: str,
    ) -> OrchestrationTickResult:
        """Resolve one reauthorization obligation without granting authority.

        Human DENY closes only the blocked execution branch. It does not
        mutate scope, authorization sources, budgets, or Worker authority.
        """

        if not isinstance(worker_result_id, str) or not worker_result_id.strip():
            raise ApplicationError("worker_result_id is required")
        if not isinstance(operator_id, str) or not operator_id.strip():
            raise ApplicationError("operator_id is required")

        worker_result_id = worker_result_id.strip()
        operator_id = operator_id.strip()
        now = self._clock.now()

        with self._uow_factory.open() as uow:
            current = uow.research_orchestrations.get(research_run_id)
            if current is None:
                raise ApplicationError("orchestration not found")

            worker_result = uow.worker_results.get(worker_result_id)
            if (
                worker_result is None
                or worker_result.research_run_id != research_run_id
                or worker_result.status
                != WorkerResultStatus.REAUTHORIZATION_REQUIRED.value
            ):
                uow.rollback()
                raise ApplicationError(
                    "worker result is not a reauthorization result for this run"
                )

            experiment = uow.experiments.get(worker_result.experiment_id)
            if (
                experiment is None
                or experiment.research_run_id != research_run_id
            ):
                uow.rollback()
                raise ApplicationError(
                    "reauthorization experiment does not belong to this run"
                )

            prior_decisions = uow.audit_events.list_for_subject(
                "research_run",
                research_run_id,
            )
            already_resolved = any(
                event.event_type == "REAUTHORIZATION_DENIED_BY_HUMAN"
                and event.payload.get("worker_result_id") == worker_result_id
                for event in prior_decisions
            )
            if already_resolved:
                uow.rollback()
                return _result_from_record(
                    current,
                    CycleOutcome.CONTINUE,
                )

            if (
                current.state != OrchestrationState.WAITING_HUMAN.value
                or current.stop_reason
                != StopReason.REQUIRE_HUMAN_REVIEW.value
            ):
                uow.rollback()
                raise ApplicationError(
                    "run is not waiting for a reauthorization decision"
                )

            if uow.observations.list_for_worker_result(worker_result_id):
                uow.rollback()
                raise ApplicationError(
                    "reauthorization result unexpectedly produced observations"
                )

            attempts = uow.execution_attempts.list_for_research_run(
                research_run_id
            )
            experiments = uow.experiments.list_for_research_run(
                research_run_id
            )
            worker_results = uow.worker_results.list_for_research_run(
                research_run_id
            )

            obligations = unresolved_control_obligations(
                attempts=attempts,
                experiments=experiments,
                worker_results=worker_results,
            )

            active_reauthorization = any(
                item.code == "REAUTHORIZATION_REQUIRED"
                and item.subject_id == worker_result_id
                for item in obligations
            )
            if not active_reauthorization:
                uow.rollback()
                raise ApplicationError(
                    "reauthorization obligation is no longer active"
                )

            # DENY means this branch cannot cross the requested authority
            # boundary. It is terminal for this Experiment only.
            uow.experiments.set_execution_state(
                experiment.experiment_id,
                ExperimentExecutionState.BLOCKED.value,
            )

            remaining = unresolved_control_obligations(
                attempts=attempts,
                experiments=uow.experiments.list_for_research_run(
                    research_run_id
                ),
                worker_results=worker_results,
            )

            if any(item.code == "UNKNOWN_OUTCOME" for item in remaining):
                next_state = OrchestrationState.FAILED_OPERATIONAL.value
                next_stop_reason = StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW.value
            elif remaining:
                next_state = OrchestrationState.WAITING_HUMAN.value
                next_stop_reason = StopReason.REQUIRE_HUMAN_REVIEW.value
            else:
                next_state = OrchestrationState.READY.value
                next_stop_reason = None

            updated = replace(
                current,
                state=next_state,
                pause_reason=None,
                stop_reason=next_stop_reason,
                last_phase="reauthorization_denied",
                current_phase=OrchestrationPhase.CYCLE_COMPLETE.value,
                updated_at=now,
                checkpoint_at=now,
            )

            uow.research_orchestrations.save(updated)

            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=operator_id,
                    actor_type=ActorType.HUMAN_OPERATOR.value,
                    event_type="REAUTHORIZATION_DENIED_BY_HUMAN",
                    subject_type="research_run",
                    subject_id=research_run_id,
                    payload={
                        "worker_result_id": worker_result_id,
                        "experiment_id": experiment.experiment_id,
                        "hypothesis_id": experiment.hypothesis_id,
                        "decision": "DENY",
                        "scope_changed": False,
                        "new_authority_granted": False,
                        "redispatch_authorized": False,
                        "remaining_control_obligations": len(remaining),
                    },
                )
            )

            uow.commit()

        return _result_from_record(
            updated,
            CycleOutcome.CONTINUE,
        )

    def cancel(self, research_run_id: str) -> OrchestrationTickResult:
        return self._operator_state(
            research_run_id,
            OrchestrationState.COMPLETED,
            StopReason.OPERATOR_CANCELLED,
            CycleOutcome.COMPLETE,
        )

    def stop_for_budget_exhaustion(
        self,
        research_run_id: str,
        *,
        phase: str = "runtime_recovery_budget",
    ) -> OrchestrationTickResult:
        """Terminalize a non-terminal run whose issued Core budget is exhausted.

        This does not allocate budget, extend authority, or retry work.
        """

        current = self._reload(research_run_id)

        if current.state in TERMINAL_ORCHESTRATION_STATES:
            return _result_from_record(current, CycleOutcome.CONTINUE)

        if current.state not in {
            OrchestrationState.READY.value,
            OrchestrationState.RUNNING.value,
        }:
            return _result_from_record(current, CycleOutcome.CONTINUE)

        return self._stop(
            current,
            StopReason.BUDGET_EXHAUSTED,
            phase,
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
        close_expired_oast_arms(
            self._uow_factory,
            research_run_id=command.research_run_id,
            clock=self._clock,
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

        discovery_start = self._discovery_start_for_tick(command)
        if discovery_start is not None:
            return self._step_surface_discovery(
                replace(command, surface_discovery=discovery_start), current
            )

        phase = current.current_phase
        if phase == OrchestrationPhase.DISPATCHING.value or self._unknown_open(
            command.research_run_id
        ):
            return self._stop(
                current, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW, "unknown_outcome"
            )

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
                unknown_outcome_open=self._unknown_open(command.research_run_id),
                runnable_discovery_frontier_count=self._runnable_discovery_count(
                    command.research_run_id
                ),
            )
            if action is NextCycleAction.CONTINUE_SURFACE_DISCOVERY:
                discovery_start = self._discovery_start_for_tick(command)
                if discovery_start is not None:
                    return self._step_surface_discovery(
                        replace(command, surface_discovery=discovery_start), current
                    )
            if action is NextCycleAction.STOP:
                reason = stop or StopReason.COMPLETED_NO_MORE_OPPORTUNITIES
                return self._stop(current, reason, "no_more_opportunities")
            if action is NextCycleAction.NO_SELECTION_THIS_TICK:
                return self._finish_empty_scheduler_tick(command, current)

            cycle_id = current.active_cycle_id or new_opaque_id()
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.OPPORTUNITY_SELECTED,
                opportunity_id=opportunity_id,
                active_cycle_id=cycle_id,
            )

        compiled = self._apply_selected_work_compile(
            command, current, config, opportunity_id
        )
        if compiled is not None:
            return compiled

        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(config.research_run_id)
            program_id = run.program_id if run is not None else None
            uow.rollback()

        raw_models = (
            self._model,
            *self._fallback_models,
        )

        allowed_model_count = min(
            len(raw_models),
            1 + bounds.max_runtime_fallback,
        )

        selected_models = raw_models[
            :allowed_model_count
        ]

        (
            checkpoint_retry_admission_id,
            checkpoint_retry_ordinal,
        ) = self._model_checkpoint_retry_grant(
            command.research_run_id,
            cycle_id=cycle_id,
            opportunity_id=opportunity_id,
        )

        def _model_invocation_namespace(
            index: int,
        ) -> str | None:
            parts: list[str] = []

            if len(selected_models) != 1:
                parts.append(
                    f"slot-{index}"
                )

            if checkpoint_retry_ordinal:
                parts.append(
                    "checkpoint-retry-"
                    f"{checkpoint_retry_ordinal}"
                )

            return (
                ":".join(parts)
                if parts
                else None
            )

        budgeted_models = tuple(
            BudgetEnforcedModelPort(
                model_port,
                self._uow_factory,
                budget_id=config.budget_id,
                research_run_id=config.research_run_id,
                cycle_id=cycle_id,
                program_id=program_id,
                invocation_namespace=(
                    _model_invocation_namespace(index)
                ),
                clock=self._clock,
            )
            for index, model_port
            in enumerate(selected_models)
        )

        bound_model = (
            BoundedRateLimitFailoverModelPort(
                budgeted_models,
                max_fallback_attempts=(
                    bounds.max_runtime_fallback
                ),
                max_rate_limit_retries_per_runtime=1,
                retry_delay_seconds=1.0,
            )
        )

        proposer = ProposeResearchHypothesis(
            self._uow_factory,
            bound_model,
            clock=self._clock,
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
                    retry_admission_record_id=(
                        checkpoint_retry_admission_id
                    ),
                ),
                persist_hook=_persist_hypothesis,
            )
        except BudgetConsumptionRejected:
            return self._stop(current, StopReason.BUDGET_EXHAUSTED, "model_budget")
        if bound_model.reserved_invocations:
            self._observability.increment(
                "model_calls",
                len(
                    bound_model.reserved_invocations
                ),
            )

        fallbacks_used = int(
            getattr(
                bound_model,
                "fallbacks_used",
                0,
            )
        )

        rate_limit_retries_used = int(
            getattr(
                bound_model,
                "rate_limit_retries_used",
                0,
            )
        )

        if rate_limit_retries_used:
            self._observability.increment(
                "model_rate_limit_retries",
                rate_limit_retries_used,
            )

            with self._uow_factory.open() as uow:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=(
                            "ae:model-rate-limit-retry:"
                            + new_opaque_id()
                        ),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=(
                            ActorType.CONTROL_PLANE.value
                        ),
                        event_type=(
                            "MODEL_RUNTIME_RATE_LIMIT_RETRY"
                        ),
                        subject_type="research_run",
                        subject_id=(
                            command.research_run_id
                        ),
                        payload={
                            "retries_used": (
                                rate_limit_retries_used
                            ),
                            "retry_bound_per_runtime": 1,
                            "retry_delay_seconds": 1.0,
                            "physical_attempts_budgeted": True,
                            "authority_expanded": False,
                            "not_research_truth": True,
                        },
                    )
                )
                uow.commit()

        if fallbacks_used:
            self._observability.increment(
                "runtime_fallbacks",
                fallbacks_used,
            )

            with self._uow_factory.open() as uow:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=(
                            "ae:model-fallback:"
                            + new_opaque_id()
                        ),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=(
                            ActorType.CONTROL_PLANE.value
                        ),
                        event_type=(
                            "MODEL_RUNTIME_RATE_LIMIT_FALLBACK"
                        ),
                        subject_type="research_run",
                        subject_id=(
                            command.research_run_id
                        ),
                        payload={
                            "trigger": "RATE_LIMITED",
                            "fallbacks_used": (
                                fallbacks_used
                            ),
                            "active_slot": int(
                                getattr(
                                    bound_model,
                                    "active_index",
                                    0,
                                )
                            ),
                            "max_runtime_fallback": (
                                bounds.max_runtime_fallback
                            ),
                            "not_authorization": True,
                            "not_research_truth": True,
                        },
                    )
                )
                uow.commit()

        if proposed.outcome is AdmissionOutcome.MODEL_INVOCATION_FAILED:
            outcome = (
                proposed.runtime_outcome
                or RuntimeOutcome.PROCESS_FAILED
            )

            # Generic/transient provider throttling receives exactly one
            # durable whole-checkpoint retry. The selected opportunity and
            # active cycle identity remain unchanged; no research cycle is
            # completed and no completion path becomes reachable here.
            #
            # Explicit account/session usage exhaustion is intentionally
            # excluded: MODEL_USAGE_LIMITED remains a truthful immediate
            # RATE_LIMITED block after bounded model fallback.
            if (
                outcome is RuntimeOutcome.RATE_LIMITED
                and proposed.reason_code
                == "MODEL_RATE_LIMITED"
                and checkpoint_retry_ordinal == 0
            ):
                if (
                    proposed.admission_record_id is None
                    or current.active_cycle_id is None
                ):
                    return self._stop(
                        current,
                        StopReason.OPERATIONAL_FAILURE,
                        "model_checkpoint_retry_missing_identity",
                        hypothesis_id=proposed.hypothesis_id,
                    )

                retry_event = AuditEventRecord(
                    audit_event_id=(
                        "ae:model-checkpoint-rate-limit-retry:"
                        + new_opaque_id()
                    ),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=(
                        MODEL_CHECKPOINT_RATE_LIMIT_RETRY_EVENT
                    ),
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "active_cycle_id": (
                            current.active_cycle_id
                        ),
                        "opportunity_id": opportunity_id,
                        "admission_record_id": (
                            proposed.admission_record_id
                        ),
                        "retry_ordinal": 1,
                        "retry_bound": (
                            MODEL_CHECKPOINT_RATE_LIMIT_RETRY_BOUND
                        ),
                        "trigger": "TRANSIENT_RATE_LIMIT",
                        "cycle_incremented": False,
                        "authority_expanded": False,
                        "not_authorization": True,
                        "not_research_truth": True,
                        "next_physical_attempt_budgeted": True,
                    },
                )

                self._observability.increment(
                    "model_checkpoint_rate_limit_retries",
                    1,
                )

                return self._complete_cycle(
                    current,
                    CycleOutcome.CONTINUE,
                    "model_checkpoint_rate_limit_retry",
                    state=OrchestrationState.READY,
                    opportunity_id=opportunity_id,
                    increment_cycle=False,
                    current_phase=(
                        OrchestrationPhase.OPPORTUNITY_SELECTED
                    ),
                    extra_audit_events=(retry_event,),
                )

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
            self._arm_oast_on_attempt(uow, plan, current, attempt_id, now)

        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
                approval=command.approval,
                compiled_scope=command.compiled_scope,
                program_policy=command.program_policy,
            ),
            persist_hook=_persist_attempt,
        )
        self._observability.increment("experiments_executed")
        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "reauthorization_required",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
            )
        if loop.status is ResearchLoopStatus.DISPATCH_DENIED:
            return self._stop(
                current,
                StopReason.CORE_BLOCKED,
                "core_deny",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
            )
        if loop.status is ResearchLoopStatus.INPUT_REJECTED:
            return self._stop(
                current,
                StopReason.CORE_BLOCKED,
                "input_rejected",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
            )
        if loop.status is ResearchLoopStatus.ALREADY_TERMINAL:
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "already_terminal",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
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
                StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
                "unknown_outcome",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
            )
        if loop.status in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.NO_OBSERVATION,
            ResearchLoopStatus.INVOCATION_FAILED,
        }:
            if not loop.experiment_id:
                return self._stop(
                    current,
                    StopReason.OPERATIONAL_FAILURE,
                    "missing_experiment_id",
                    hypothesis_id=proposed.hypothesis_id,
                    experiment_id=experiment_id,
                    current_phase=OrchestrationPhase.DISPATCHING,
                )
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
            self._apply_hunter_feedback(
                command,
                opportunity_id=opportunity_id,
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=loop.experiment_id,
                observation_id=loop.observation_ids[0] if loop.observation_ids else None,
                feedback=feedback,
            )
            self._continue_promotion(command, feedback)
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.ASSESSMENT_COMPLETE,
                experiment_id=experiment_id,
                hypothesis_id=proposed.hypothesis_id,
                assessment_id=feedback.assessment_id,
            )
        else:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unhandled_research_loop_status",
                hypothesis_id=proposed.hypothesis_id,
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
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
            return self._stop(
                current, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW, "unknown_outcome"
            )
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
            return self._stop(
                current, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW, "unknown_outcome"
            )
        start = command.surface_discovery
        if start is None:
            raise ApplicationError("surface discovery start is required")
        result = self._discovery.run_cycle(
            start,
            budget_id=command.budget_id,
            target_reference=command.target_reference,
            scope=command.scope,
            approval=command.approval,
            program_policy=command.program_policy,
        )
        self._record_surface_discovery_cycle(command, current, start, result)
        if result.stop_reason == "UNKNOWN_OUTCOME":
            return self._stop(
                current,
                StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
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
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "surface_discovery_scope_denied",
                experiment_id=result.experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        if result.stop_reason == "REAUTHORIZATION_REQUIRED":
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "reauthorization_required",
                experiment_id=result.experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
            )
        if result.stop_reason == "HUMAN_REVIEW_REQUIRED":
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "human_review",
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
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "surface_discovery_exhausted",
                experiment_id=result.experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        if result.stop_reason == "DISCOVERY_EXIT_BLOCKED":
            if self._discovery_waiting_human(command.research_run_id):
                return self._stop(
                    current,
                    StopReason.REQUIRE_HUMAN_REVIEW,
                    "discovery_waiting_human",
                    experiment_id=result.experiment_id,
                    increment_cycle=True,
                )
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "surface_discovery",
                experiment_id=result.experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
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
                return self._stop(
                    current, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW, "unknown_dispatch"
                )
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
        # The original network envelope is dispatch-time authority
        # derived from the exact authorization/scope decision. ExecutionAttempt
        # does not durably persist that envelope. Reconstructing it from current
        # mutable scope/policy could silently widen an old authorization after a
        # crash. Network-capable AUTHORIZED attempts therefore fail closed
        # instead of being redispatched without the exact original envelope.
        if plan.required_capability in HTTP_SCOPE_CAPABILITIES:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "resume_network_envelope_not_durable",
                experiment_id=experiment.experiment_id,
            )

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
        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "reauthorization_required",
                experiment_id=experiment.experiment_id,
                current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
            )
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return self._stop(
                current,
                StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
                "unknown_outcome",
                experiment_id=experiment.experiment_id,
            )
        if loop.status not in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.NO_OBSERVATION,
            ResearchLoopStatus.INVOCATION_FAILED,
        }:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unhandled_research_loop_status",
                experiment_id=experiment.experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
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
        natural_completion = next_state == OrchestrationState.COMPLETED.value and (
            resolved_stop_reason
            in {
                StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value,
                StopReason.MAX_CYCLES_REACHED.value,
            }
            or (
                outcome is CycleOutcome.COMPLETE
                and resolved_stop_reason != StopReason.OPERATOR_CANCELLED.value
            )
        )
        if natural_completion:
            obligations = self._control_obligations(current.research_run_id, current)
            if obligations:
                first = obligations[0]
                reason = (
                    StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW
                    if first.code == "UNKNOWN_OUTCOME"
                    else StopReason.REQUIRE_HUMAN_REVIEW
                )
                return self._stop(
                    current,
                    reason,
                    f"completion_guard:{first.code.lower()}",
                    current_phase=current_phase,
                )
            if (
                resolved_stop_reason
                == StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
                and not self._discovery_may_release_run(current.research_run_id)
            ):
                if self._discovery_waiting_human(current.research_run_id):
                    return self._stop(
                        current,
                        StopReason.REQUIRE_HUMAN_REVIEW,
                        "completion_guard:discovery_waiting_human",
                        current_phase=current_phase,
                    )
                outcome = CycleOutcome.CONTINUE
                next_state = OrchestrationState.READY.value
                resolved_stop_reason = None
                phase = "completion_guard:discovery_exit_contract"
                extra_audit_events = extra_audit_events + (
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="COMPLETION_BLOCKED",
                        subject_type="research_run",
                        subject_id=current.research_run_id,
                        payload={
                            "completion_considered": True,
                            "completion_allowed": False,
                            "completion_block_reason": "DISCOVERY_EXIT_CONTRACT",
                            "stop_reason_blocked": (
                                StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
                            ),
                        },
                    ),
                )
            inventory = self._research_inventory(current.research_run_id)
            if (
                resolved_stop_reason
                == StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
                and not inventory.completion_allowed
            ):
                outcome = CycleOutcome.CONTINUE
                next_state = OrchestrationState.READY.value
                resolved_stop_reason = None
                phase = "completion_guard:global_research_work"
                extra_audit_events = extra_audit_events + (
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="GLOBAL_RESEARCH_WORK_AUDIT",
                        subject_type="research_run",
                        subject_id=current.research_run_id,
                        payload={
                            **inventory.as_payload(),
                            "completion_considered": True,
                            "stop_reason_blocked": (
                                StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
                            ),
                        },
                    ),
                )
        resolved_current_phase = (
            current_phase.value
            if current_phase is not None
            else (
                OrchestrationPhase.CYCLE_COMPLETE.value
                if inserting
                else current.current_phase
            )
        )

        updated = replace(
            current,
            state=next_state,
            cycle_number=cycle_number,
            last_phase=phase,
            current_phase=resolved_current_phase,
            last_opportunity_id=opportunity_id or current.last_opportunity_id,
            last_hypothesis_id=hypothesis_id or current.last_hypothesis_id,
            last_experiment_id=experiment_id or current.last_experiment_id,
            last_observation_id=observation_id or current.last_observation_id,
            last_assessment_id=assessment_id or current.last_assessment_id,
            active_cycle_id=(
                None
                if resolved_current_phase
                == OrchestrationPhase.CYCLE_COMPLETE.value
                else current.active_cycle_id
            ),
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

    def _model_checkpoint_retry_grant(
        self,
        research_run_id: str,
        *,
        cycle_id: str,
        opportunity_id: str | None,
    ) -> tuple[str | None, int]:
        """Load the single durable transient-rate-limit retry grant.

        Audit is append-only and survives supervisor/process restart. More
        than one grant for one active cycle is an integrity error rather than
        permission to retry unboundedly.
        """
        with self._uow_factory.open() as uow:
            events = uow.audit_events.list_for_subject(
                "research_run",
                research_run_id,
            )

            matches = [
                event
                for event in events
                if (
                    event.event_type
                    == MODEL_CHECKPOINT_RATE_LIMIT_RETRY_EVENT
                    and event.payload.get(
                        "active_cycle_id"
                    ) == cycle_id
                )
            ]

            if len(matches) > 1:
                uow.rollback()
                raise ApplicationError(
                    "multiple model checkpoint retry grants "
                    "for one active cycle"
                )

            if not matches:
                uow.rollback()
                return None, 0

            event = matches[0]
            payload = event.payload

            admission_id = payload.get(
                "admission_record_id"
            )
            retry_ordinal = payload.get(
                "retry_ordinal"
            )
            event_opportunity_id = payload.get(
                "opportunity_id"
            )

            if (
                not isinstance(admission_id, str)
                or not admission_id.strip()
                or retry_ordinal != 1
                or payload.get("retry_bound")
                != MODEL_CHECKPOINT_RATE_LIMIT_RETRY_BOUND
                or event_opportunity_id != opportunity_id
            ):
                uow.rollback()
                raise ApplicationError(
                    "invalid model checkpoint retry grant"
                )

            admission = uow.research_admissions.get(
                admission_id
            )

            if (
                admission is None
                or admission.research_run_id
                != research_run_id
                or admission.outcome
                != AdmissionOutcome.MODEL_INVOCATION_FAILED.value
                or admission.reason_code
                != "MODEL_RATE_LIMITED"
            ):
                uow.rollback()
                raise ApplicationError(
                    "model checkpoint retry grant does not "
                    "reference an exact transient rate-limit admission"
                )

            uow.rollback()

        return admission_id, retry_ordinal


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
        identity = None
        profile = None
        with self._uow_factory.open() as uow:
            identity, profile = resolve_identity_for_plan(
                uow, command.research_run_id, plan
            )
            uow.rollback()

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
            self._arm_oast_on_attempt(uow, plan, current, attempt_id, now)

        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
                approval=command.approval,
                compiled_scope=command.compiled_scope,
                program_policy=command.program_policy,
                identity_id=identity.identity_id if identity is not None else None,
                identity=identity,
                authentication_profile=profile,
            ),
            persist_hook=_persist_attempt,
        )
        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "reauthorization_required",
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
            )
        if loop.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return self._stop(
                current,
                StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
                "unknown_outcome",
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
            )
        if loop.status is ResearchLoopStatus.DISPATCH_DENIED:
            self._record_core_denied(
                command,
                current,
                experiment_id=experiment_id,
                loop=loop,
            )
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "core_deny_authority_terminal",
                experiment_id=experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        if loop.status is ResearchLoopStatus.INPUT_REJECTED:
            return self._stop(
                current,
                StopReason.CORE_BLOCKED,
                "input_rejected",
                experiment_id=experiment_id,
            )
        if loop.status is ResearchLoopStatus.ALREADY_TERMINAL:
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "already_terminal",
                experiment_id=experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        if loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
            return self._stop(
                current, StopReason.REQUIRE_HUMAN_REVIEW, "human_review", experiment_id=experiment_id
            )
        if loop.status not in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.NO_OBSERVATION,
            ResearchLoopStatus.INVOCATION_FAILED,
        }:
            return self._stop(
                current,
                StopReason.OPERATIONAL_FAILURE,
                "unhandled_research_loop_status",
                experiment_id=experiment_id,
                current_phase=OrchestrationPhase.DISPATCHING,
            )
        if loop.experiment_id:
            if self._oast_should_wait(plan, current.last_attempt_id):
                current = self._checkpoint(
                    current,
                    phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
                    experiment_id=loop.experiment_id,
                    hypothesis_id=current.last_hypothesis_id,
                    opportunity_id=current.last_opportunity_id,
                    attempt_id=current.last_attempt_id,
                )
                return self._complete_cycle(
                    current,
                    CycleOutcome.CONTINUE,
                    "oast_waiting_callback",
                    experiment_id=loop.experiment_id,
                    opportunity_id=current.last_opportunity_id,
                    hypothesis_id=current.last_hypothesis_id,
                    increment_cycle=True,
                    current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
                )
            feedback = self._evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=loop.experiment_id)
            )
            self._apply_hunter_feedback(
                command,
                opportunity_id=current.last_opportunity_id,
                hypothesis_id=current.last_hypothesis_id,
                experiment_id=loop.experiment_id,
                observation_id=loop.observation_ids[0] if loop.observation_ids else None,
                feedback=feedback,
            )
            apply_identity_engine_coverage_feedback(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=current.last_opportunity_id,
                experiment_id=loop.experiment_id,
                assessment_outcome=(
                    feedback.assessment_outcome.value
                    if hasattr(feedback.assessment_outcome, "value")
                    else str(feedback.assessment_outcome)
                ),
                clock=self._clock,
            )
            apply_mutation_protocol_coverage_feedback(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=current.last_opportunity_id,
                experiment_id=loop.experiment_id,
                assessment_outcome=(
                    feedback.assessment_outcome.value
                    if hasattr(feedback.assessment_outcome, "value")
                    else str(feedback.assessment_outcome)
                ),
                evaluation_strategy=feedback.evaluation_strategy,
                clock=self._clock,
            )
            apply_oast_coverage_feedback(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=current.last_opportunity_id,
                experiment_id=loop.experiment_id,
                assessment_outcome=(
                    feedback.assessment_outcome.value
                    if hasattr(feedback.assessment_outcome, "value")
                    else str(feedback.assessment_outcome)
                ),
                evaluation_strategy=feedback.evaluation_strategy,
                clock=self._clock,
            )
            if current.last_opportunity_id:
                evaluation = evaluate_selected_dic(
                    self._uow_factory,
                    research_run_id=command.research_run_id,
                    opportunity_id=current.last_opportunity_id,
                    clock=self._clock,
                )
                if evaluation:
                    apply_dic_coverage_feedback(
                        self._uow_factory,
                        research_run_id=command.research_run_id,
                        opportunity_id=current.last_opportunity_id,
                        evaluation=evaluation,
                        clock=self._clock,
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
            with self._uow_factory.open() as uow:
                plan_record = uow.experiment_plans.get(current.last_experiment_id)
                uow.rollback()
            plan = experiment_plan_from_record(plan_record) if plan_record is not None else None
            if plan is not None and self._oast_should_wait(plan, current.last_attempt_id):
                return self._complete_cycle(
                    current,
                    CycleOutcome.CONTINUE,
                    "oast_waiting_callback",
                    experiment_id=current.last_experiment_id,
                    increment_cycle=True,
                    current_phase=OrchestrationPhase.WORKER_RESULT_RECORDED,
                )
            feedback = self._evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=current.last_experiment_id)
            )
            self._apply_hunter_feedback(
                command,
                opportunity_id=current.last_opportunity_id,
                hypothesis_id=current.last_hypothesis_id,
                experiment_id=current.last_experiment_id,
                observation_id=current.last_observation_id,
                feedback=feedback,
            )
            apply_oast_coverage_feedback(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=current.last_opportunity_id,
                experiment_id=current.last_experiment_id,
                assessment_outcome=(
                    feedback.assessment_outcome.value
                    if hasattr(feedback.assessment_outcome, "value")
                    else str(feedback.assessment_outcome)
                ),
                evaluation_strategy=feedback.evaluation_strategy,
                clock=self._clock,
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
        terminal = {
            ExperimentExecutionState.EXECUTION_SUCCEEDED.value,
            ExperimentExecutionState.EXECUTION_FAILED.value,
            ExperimentExecutionState.BLOCKED.value,
            ExperimentExecutionState.CANCELLED.value,
            ExperimentExecutionState.BUDGET_EXHAUSTED.value,
        }
        open_matching = [
            item for item in matching if item.execution_state not in terminal
        ]
        if not open_matching:
            return None
        return open_matching[-1].experiment_id

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

    def _research_inventory(self, research_run_id: str):
        with self._uow_factory.open() as uow:
            audit = global_research_work_audit(uow, research_run_id)
            uow.rollback()
        return audit

    def _finish_empty_scheduler_tick(
        self, command: StartAutonomousResearchCommand, current: ResearchOrchestrationRecord
    ) -> OrchestrationTickResult:
        inventory = self._research_inventory(command.research_run_id)
        extra = (
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=self._clock.now(),
                actor_id=self._actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type="GLOBAL_RESEARCH_WORK_AUDIT",
                subject_type="research_run",
                subject_id=command.research_run_id,
                payload={
                    **inventory.as_payload(),
                    "completion_considered": True,
                    "no_selection_this_tick": True,
                },
            ),
        )
        if inventory.completion_allowed:
            stop = StopReason.COMPLETED_NO_MORE_OPPORTUNITIES
            if inventory.protocol_authority_blocked:
                stop = StopReason.COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK
            return self._complete_cycle(
                current,
                cycle_outcome_for_stop(stop),
                "global_inventory_empty",
                stop_reason=stop,
                state=orchestration_state_for_stop(stop),
                extra_audit_events=extra,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "no_selection_this_tick",
            extra_audit_events=extra,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def _apply_hunter_feedback(
        self,
        command: StartAutonomousResearchCommand,
        *,
        opportunity_id: str | None,
        hypothesis_id: str | None,
        experiment_id: str | None,
        observation_id: str | None,
        feedback,
    ) -> None:
        outcome = feedback.assessment_outcome
        apply_hunter_execution_feedback(
            self._uow_factory,
            research_run_id=command.research_run_id,
            opportunity_id=opportunity_id,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment_id,
            observation_id=observation_id,
            assessment_outcome=outcome.value if hasattr(outcome, "value") else str(outcome),
            clock=self._clock,
        )

    def _apply_selected_work_compile(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        config,
        opportunity_id: str | None,
    ) -> OrchestrationTickResult | None:
        if not opportunity_id:
            return None
        with self._uow_factory.open() as uow:
            opportunity = uow.research_opportunities.get(opportunity_id)
            if opportunity is None:
                uow.rollback()
                return None
            hypotheses = uow.hypotheses.list_for_research_run(command.research_run_id)
            origin_reference = f"research-work-fabric.v1:{opportunity_id}"
            tagged = [
                item
                for item in hypotheses
                if item.origin_reference == origin_reference
            ]
            if tagged:
                hypothesis_id = tagged[-1].hypothesis_id
            else:
                hypothesis_id = new_opaque_id()
                identity_id = None
                if (
                    opportunity.opportunity_kind == OpportunityKind.OAST_INTERACTION.value
                    and len(opportunity.source_refs) > 2
                ):
                    identity_id = opportunity.source_refs[2]
                uow.hypotheses.insert(
                    HypothesisRecord(
                        hypothesis_id=hypothesis_id,
                        research_run_id=command.research_run_id,
                        claim=(
                            "Execute selected research work under Core authorization."
                        ),
                        created_at=self._clock.now(),
                        origin_reference=origin_reference,
                        identity_id=identity_id,
                    )
                )
            decision = self._work_planners.plan(
                uow,
                opportunity,
                hypothesis_id=hypothesis_id,
                budget_id=config.budget_id,
                target_reference=config.target_reference,
                bounds=config.bounds,
                compiled_scope=command.compiled_scope,
                program_policy=command.program_policy,
            )
            event_type = {
                ResearchCompileStatus.DEFERRED_ENGINE_WIRING: (
                    "RESEARCH_WORK_DEFERRED_ENGINE_WIRING"
                ),
                ResearchCompileStatus.MISSING_PRECONDITION: (
                    "RESEARCH_WORK_MISSING_PRECONDITION"
                ),
                ResearchCompileStatus.APPROVAL_REQUIRED: "RESEARCH_WORK_APPROVAL_REQUIRED",
                ResearchCompileStatus.BLOCKED_SCOPE: "RESEARCH_WORK_BLOCKED_SCOPE",
                ResearchCompileStatus.BLOCKED_POLICY: "RESEARCH_WORK_BLOCKED_POLICY",
                ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING: (
                    "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING"
                ),
                ResearchCompileStatus.ENGINE_DEPENDENCY_PENDING: (
                    "RESEARCH_WORK_ENGINE_DEPENDENCY_PENDING"
                ),
                ResearchCompileStatus.EXECUTE_PLAN: "RESEARCH_WORK_COMPILED",
                ResearchCompileStatus.EVALUATE_EXISTING: "RESEARCH_WORK_COMPILED",
                ResearchCompileStatus.USE_MODEL: "RESEARCH_WORK_USE_MODEL",
            }[decision.status]
            payload = {
                "selected_work_id": opportunity_id,
                "selected_engine": decision.source_engine,
                "compile_status": decision.status.value,
                "reason_codes": list(decision.reason_codes),
                "compiled_capability": decision.required_capability,
                "side_effect": decision.side_effect_class,
                "compiler": decision.compiler_id,
                "owner_before": "RESEARCH",
                "owner_after": "ARC",
                "not_authorization": True,
            }
            if decision.forensic:
                payload.update(decision.forensic)
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=event_type,
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=payload,
                )
            )
            uow.commit()
        if decision.hypothesis_id:
            hypothesis_id = decision.hypothesis_id
        if decision.status is ResearchCompileStatus.USE_MODEL:
            return None
        if decision.status is ResearchCompileStatus.EVALUATE_EXISTING:
            evaluation = evaluate_selected_dic(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=opportunity_id,
                clock=self._clock,
            )
            apply_dic_coverage_feedback(
                self._uow_factory,
                research_run_id=command.research_run_id,
                opportunity_id=opportunity_id,
                evaluation=evaluation,
                clock=self._clock,
            )
            return self._complete_cycle(
                current,
                CycleOutcome.CONTINUE,
                "research_work_evaluate_existing",
                opportunity_id=opportunity_id,
                hypothesis_id=hypothesis_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )
        if decision.status is ResearchCompileStatus.EXECUTE_PLAN:
            if decision.plan is None:
                return self._complete_cycle(
                    current,
                    CycleOutcome.CONTINUE,
                    "compile_missing_plan",
                    opportunity_id=opportunity_id,
                    current_phase=OrchestrationPhase.CYCLE_COMPLETE,
                )
            return self._dispatch_experiment_plan(
                command,
                current,
                config,
                decision.plan,
                opportunity_id,
                hypothesis_id,
            )
        if decision.status is ResearchCompileStatus.APPROVAL_REQUIRED:
            return self._stop(
                current,
                StopReason.REQUIRE_HUMAN_REVIEW,
                "research_work_approval_required",
                opportunity_id=opportunity_id,
                hypothesis_id=hypothesis_id,
            )
        return self._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            f"research_work_{decision.status.value.lower()}",
            opportunity_id=opportunity_id,
            hypothesis_id=hypothesis_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

    def _record_core_denied(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        *,
        experiment_id: str | None,
        loop,
    ) -> None:
        with self._uow_factory.open() as uow:
            opportunity = None
            if current.last_opportunity_id:
                opportunity = uow.research_opportunities.get(current.last_opportunity_id)
            payload = {
                "selected_work_id": current.last_opportunity_id,
                "experiment_id": experiment_id,
                "core_decision": (
                    loop.core_decision.value if loop.core_decision is not None else None
                ),
                "reason_codes": [
                    loop.core_reason_code.value if loop.core_reason_code is not None else "CORE_DENIED"
                ],
                "not_authorization_override": True,
                "not_covered": True,
            }
            if opportunity is not None:
                payload["opportunity_kind"] = opportunity.opportunity_kind
                if len(opportunity.source_refs) >= 3 and opportunity.opportunity_kind in {
                    OpportunityKind.PROTOCOL_STEP.value,
                    OpportunityKind.HUNTER_COVERAGE_GAP.value,
                }:
                    family_id, node_key, identity_id = opportunity.source_refs[:3]
                    payload["family_id"] = family_id
                    payload["node_canonical_key"] = node_key
                    payload["identity_id"] = identity_id
                    payload["coverage_cell_id"] = f"{node_key}:{identity_id}:{family_id}"
                payload["selected_engine"] = {
                    OpportunityKind.PROTOCOL_STEP.value: "PROTOCOL",
                    OpportunityKind.MUTATION_VARIANT.value: "MUTATION",
                    OpportunityKind.OAST_INTERACTION.value: "OAST",
                    OpportunityKind.HUNTER_COVERAGE_GAP.value: "HUNTER",
                }.get(opportunity.opportunity_kind, "RESEARCH")
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RESEARCH_WORK_CORE_DENIED",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=payload,
                )
            )
            uow.commit()

    def _arm_oast_on_attempt(self, uow, plan, current, attempt_id: str, now) -> None:
        if plan.evaluation_strategy != OAST_CALLBACK_EVALUATION_STRATEGY:
            return
        from zest.application.arm_oast_correlation import (
            DEFAULT_OAST_TTL,
            ArmOastCorrelation,
            OastCorrelationArmError,
        )

        opportunity = None
        if current.last_opportunity_id:
            opportunity = uow.research_opportunities.get(current.last_opportunity_id)
        callback_id = None
        expires = now + DEFAULT_OAST_TTL
        if opportunity is not None:
            for item in opportunity.assumptions:
                if item.startswith("callback_id:"):
                    callback_id = item.split(":", 1)[1]
                if item.startswith("expires_at:"):
                    raw = item.split(":", 1)[1]
                    try:
                        parsed = datetime.fromisoformat(raw)
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=now.tzinfo)
                        expires = parsed
                    except ValueError:
                        expires = now + DEFAULT_OAST_TTL
            if not callback_id:
                callback_id = (opportunity.dimensions or {}).get("oast_callback_id")
        try:
            ArmOastCorrelation(self._uow_factory, clock=self._clock.now).arm_in_uow(
                uow,
                attempt_id,
                arm_time=now,
                expiry=expires,
                correlation_id=str(callback_id) if callback_id else None,
            )
        except OastCorrelationArmError:
            return
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=now,
                actor_id=self._actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type="OAST_ARMED",
                subject_type="research_run",
                subject_id=current.research_run_id,
                correlation_id=str(callback_id) if callback_id else attempt_id,
                payload={
                    "attempt_id": attempt_id,
                    "callback_id": callback_id,
                    "expires_at": expires.isoformat(),
                    "not_evidence": True,
                },
            )
        )

    def _oast_should_wait(self, plan, attempt_id: str | None) -> bool:
        if plan.evaluation_strategy != OAST_CALLBACK_EVALUATION_STRATEGY:
            return False
        if not attempt_id:
            return False
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            correlation = uow.oast_correlations.get_by_attempt_id(attempt_id)
            if correlation is None:
                uow.rollback()
                return False
            admission = uow.oast_admissions.get_by_correlation(correlation.correlation_id)
            waiting = admission is None and now < correlation.expires_at
            uow.rollback()
        return waiting

    def _dispatch_experiment_plan(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        config,
        plan,
        opportunity_id: str | None,
        hypothesis_id: str | None,
    ) -> OrchestrationTickResult:
        bounds = config.bounds
        if plan.side_effect_level > bounds.side_effect_ceiling:
            return self._stop(current, StopReason.CORE_BLOCKED, "side_effect_ceiling")
        existing_experiment_id = None
        if hypothesis_id:
            existing_experiment_id = self._existing_experiment_id(
                command.research_run_id, hypothesis_id
            )
        if existing_experiment_id:
            current = self._checkpoint(
                current,
                phase=OrchestrationPhase.EXPERIMENT_PLANNED,
                experiment_id=existing_experiment_id,
                hypothesis_id=hypothesis_id,
                opportunity_id=opportunity_id,
            )
            return self._resume_planned_experiment(command, current, config)
        experiment_id = (
            exploratory_experiment_id(command.research_run_id, hypothesis_id)
            if hypothesis_id and self._opportunity_is_exploratory(opportunity_id)
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
                    command.research_run_id, hypothesis_id or ""
                )
                if reused is None:
                    raise
                uow.rollback()
                current = self._checkpoint(
                    current,
                    phase=OrchestrationPhase.EXPERIMENT_PLANNED,
                    experiment_id=reused,
                    hypothesis_id=hypothesis_id,
                    opportunity_id=opportunity_id,
                )
                return self._resume_planned_experiment(command, current, config)
            current = replace(
                current,
                current_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                last_phase=OrchestrationPhase.EXPERIMENT_PLANNED.value,
                last_experiment_id=experiment_id,
                last_hypothesis_id=hypothesis_id or current.last_hypothesis_id,
                last_opportunity_id=opportunity_id or current.last_opportunity_id,
                updated_at=self._clock.now(),
                checkpoint_at=self._clock.now(),
            )
            uow.research_orchestrations.save(current)
            uow.commit()
        current = self._checkpoint(
            current,
            phase=OrchestrationPhase.AUTHORIZATION_REQUESTED,
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            opportunity_id=opportunity_id,
        )
        return self._resume_planned_experiment(command, current, config)

    def _runnable_discovery_count(self, research_run_id: str) -> int:
        with self._uow_factory.open() as uow:
            count = count_runnable_discovery_frontier(uow, research_run_id)
            uow.rollback()
        return count

    def _discovery_may_release_run(self, research_run_id: str) -> bool:
        with self._uow_factory.open() as uow:
            if uow.discovery_run_configs.get(research_run_id) is None:
                uow.rollback()
                return True
            allowed = discovery_can_exit(uow, research_run_id)
            uow.rollback()
        return allowed

    def _discovery_waiting_human(self, research_run_id: str) -> bool:
        with self._uow_factory.open() as uow:
            audit = discovery_exit_audit(uow, research_run_id, considered=True)
            uow.rollback()
        return audit.waiting_human > 0

    def _discovery_start_for_tick(
        self, command: StartAutonomousResearchCommand
    ) -> SurfaceDiscoveryStart | None:
        """SoR decides whether this tick is still owned by surface discovery."""

        with self._uow_factory.open() as uow:
            persisted = uow.discovery_run_configs.get(command.research_run_id)
            can_exit = discovery_can_exit(uow, command.research_run_id)
            reconstructed = surface_discovery_start_from_persisted(
                uow,
                command.research_run_id,
                compiled_scope=command.compiled_scope,
            )
            uow.rollback()
        if persisted is None:
            return command.surface_discovery
        if can_exit:
            return None
        if command.surface_discovery is not None:
            return command.surface_discovery
        return reconstructed

    def _record_surface_discovery_cycle(
        self,
        command: StartAutonomousResearchCommand,
        current: ResearchOrchestrationRecord,
        start: SurfaceDiscoveryStart,
        result: SurfaceDiscoveryCycleResult,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            facts = uow.discovery_facts.list_for_research_run(command.research_run_id)
            frontiers = uow.frontier_items.list_for_research_run(command.research_run_id)
            audit = discovery_exit_audit(
                uow,
                command.research_run_id,
                considered=result.stop_reason
                in {"NO_ELIGIBLE_FRONTIER", "DISCOVERY_EXIT_BLOCKED"}
                or result.eligible_after == 0,
            )
            exhausted = bool(audit.discovery_exit_allowed and result.discovery_exhausted)
            if result.stop_reason == "DISCOVERY_EXIT_BLOCKED":
                exhausted = False
            completion_block_reason = None
            if exhausted:
                completion_block_reason = "DISCOVERY_EXHAUSTED_NOT_RUN_COMPLETE"
            payload = {
                "cycle": current.cycle_number + 1,
                "phase": current.current_phase,
                "surface_discovery_active": True,
                "eligible_before": result.eligible_before,
                "selected_frontier_id": result.frontier_id,
                "selected_goal_kind": result.selected_goal_kind,
                "selected_path": result.selected_path,
                "compiled_capability": result.compiled_capability,
                "authorization_decision": (
                    "DENY"
                    if result.stop_reason == "BLOCKED_SCOPE"
                    else "ALLOW"
                    if result.worker_invoked
                    else None
                ),
                "worker_status": result.worker_status,
                "observation_id": result.observation_id,
                "new_facts": len(facts),
                "new_frontier": len(frontiers),
                "fact_count": len(facts),
                "frontier_count": len(frontiers),
                "eligible_after": result.eligible_after,
                "discovery_exhausted": exhausted,
                "normal_research_transition": exhausted,
                "completion_considered": exhausted,
                "completion_allowed": False if exhausted else None,
                "completion_block_reason": completion_block_reason,
                "stop_reason": result.stop_reason,
                "normalized_origin": start.config.normalized_origin,
                "discovery_exit_considered": audit.discovery_exit_considered,
                "discovery_exit_allowed": audit.discovery_exit_allowed,
                "discovery_exit_block_reasons": list(audit.discovery_exit_block_reasons),
                "frontier_total": audit.frontier_total,
                "runnable_discovery": audit.runnable_discovery,
                "owned_by_research": audit.owned_by_research,
                "waiting_human": audit.waiting_human,
                "terminal_dispositions": audit.terminal_dispositions,
                "unsupported_count": audit.unsupported,
                "failed_count": audit.failed,
                "unexplained_count": audit.unexplained,
            }
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="SURFACE_DISCOVERY_CYCLE",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=payload,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="DISCOVERY_EXIT_AUDIT",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=audit.as_payload(),
                )
            )
            uow.commit()

    def _control_obligations(self, research_run_id: str, current=None):
        with self._uow_factory.open() as uow:
            obligations = unresolved_control_obligations(
                attempts=uow.execution_attempts.list_for_research_run(research_run_id),
                experiments=uow.experiments.list_for_research_run(research_run_id),
                worker_results=uow.worker_results.list_for_research_run(research_run_id),
                active_experiment_ids=(
                    frozenset({current.last_experiment_id})
                    if current is not None and current.last_experiment_id is not None
                    else frozenset()
                ),
            )
            uow.rollback()
        return obligations


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
