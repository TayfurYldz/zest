"""Execute a registry-external exploratory hypothesis through ARC.

Compatibility/delegation layer, not a second research lifecycle owner. Compiles
via the Slice 4 generic planner, then asks ARC to prepare/authorize/dispatch
through the same `PreparePlannedExperiment` / `ExecutePlannedExperiment` /
`EvaluateExperimentFeedback` instances `step()` uses. Does not write
`hunter_family`. Does not create Findings.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from zest.application.exploratory_binding import load_exploratory_binding
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.enums import ActorType
from zest.core.scope import ScopeEvaluationInput
from zest.data.records import AuditEventRecord, ResearchOrchestrationRecord
from zest.platform.worker import WorkerPort
from zest.research.compiler_registry import CompilerOutcome
from zest.research.exploratory import EXPLORATORY_SUBJECT_TYPE, ExploratoryHypothesisDraft
from zest.research.exploratory_compile import (
    EXPLORATORY_COMPILER_ADAPTER_VERSION,
    EXPLORATORY_ECHO_MESSAGE,
    compile_exploratory_hypothesis,
)
from zest.research.model_port import ModelCallRequest, ModelCallResult
from zest.research.orchestration import (
    CycleOutcome,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)
from zest.research.types import ExperimentPlan, ResearchInputError

CONTROL_PLANE_ACTOR_ID = "control-plane:exploratory-execution"
EXPLORATORY_EXECUTED_EVENT = "EXPLORATORY_HYPOTHESIS_EXECUTED"
EXPLORATORY_RESEARCH_QUESTION = (
    "Does the registry-external exploratory hypothesis round-trip through the "
    "normal compiler, Core, and Worker diagnostic path without writing hunter_family?"
)


class _NeverInvokedModelPort:
    """Placeholder so ARC can be constructed without a model for this path."""

    def complete(self, request: ModelCallRequest) -> ModelCallResult:  # pragma: no cover
        raise ApplicationError(
            "ExecuteExploratoryHypothesis is a non-model path; its placeholder "
            "ModelPort must never be invoked"
        )


@dataclass(frozen=True)
class ExecuteExploratoryHypothesisCommand:
    research_run_id: str
    hypothesis_id: str
    budget_id: str
    target_reference: str
    scope: ScopeEvaluationInput
    bounds: OrchestrationBounds
    echo_message: str = EXPLORATORY_ECHO_MESSAGE
    correlation_id: str | None = None


@dataclass(frozen=True)
class ExecuteExploratoryHypothesisResult:
    hypothesis_id: str
    draft_id: str
    compiler_outcome: str
    compiler_reason: str
    orchestration_state: str
    experiment_id: str | None = None
    assessment_id: str | None = None
    observation_id: str | None = None
    core_decision: str | None = None
    stop_reason: str | None = None
    may_write_hunter_registry: bool = False
    registry_written: bool = False


class ExecuteExploratoryHypothesis:
    """Run-scoped exploratory execution. ARC remains the sole next-action owner."""

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

    def execute(
        self, command: ExecuteExploratoryHypothesisCommand
    ) -> ExecuteExploratoryHypothesisResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            hypothesis, draft, _audit = load_exploratory_binding(
                uow,
                research_run_id=command.research_run_id,
                hypothesis_id=command.hypothesis_id,
            )
            existing_assessments = uow.hypothesis_assessments.list_for_hypothesis(
                hypothesis.hypothesis_id
            )
            uow.rollback()

        try:
            compiled = compile_exploratory_hypothesis(
                draft,
                hypothesis_id=hypothesis.hypothesis_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                message=command.echo_message,
            )
        except ResearchInputError as exc:
            raise ApplicationError(str(exc)) from exc

        if not compiled.compiled or compiled.plan is None:
            return ExecuteExploratoryHypothesisResult(
                hypothesis_id=hypothesis.hypothesis_id,
                draft_id=draft.draft_id,
                compiler_outcome=compiled.outcome.value,
                compiler_reason=compiled.reason_code,
                orchestration_state=OrchestrationState.READY.value,
            )

        plan = compiled.plan
        if plan.side_effect_level > command.bounds.side_effect_ceiling:
            return ExecuteExploratoryHypothesisResult(
                hypothesis_id=hypothesis.hypothesis_id,
                draft_id=draft.draft_id,
                compiler_outcome=compiled.outcome.value,
                compiler_reason="SIDE_EFFECT_CEILING_EXCEEDED",
                orchestration_state=OrchestrationState.BLOCKED.value,
                stop_reason=StopReason.CORE_BLOCKED.value,
            )

        if existing_assessments:
            record = existing_assessments[0]
            return ExecuteExploratoryHypothesisResult(
                hypothesis_id=hypothesis.hypothesis_id,
                draft_id=draft.draft_id,
                compiler_outcome=CompilerOutcome.COMPILED.value,
                compiler_reason="ALREADY_ASSESSED",
                orchestration_state=OrchestrationState.RUNNING.value,
                experiment_id=record.experiment_id,
                assessment_id=record.assessment_id,
                observation_id=record.observation_ids[0] if record.observation_ids else None,
            )

        self._arc.start(
            StartAutonomousResearchCommand(
                research_run_id=command.research_run_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                scope=command.scope,
                bounds=command.bounds,
                research_question=EXPLORATORY_RESEARCH_QUESTION,
            )
        )

        extra: dict[str, str | None] = {
            "core_decision": None,
            "assessment_id": None,
            "observation_id": None,
            "experiment_id": None,
        }

        def _cycle(
            current: ResearchOrchestrationRecord,
            prepare: PreparePlannedExperiment,
            execute: ExecutePlannedExperiment,
            evaluate: EvaluateExperimentFeedback,
        ) -> ManagedCycleOutcome:
            return self._run_exploratory_cycle(
                command,
                draft,
                plan,
                current,
                prepare,
                execute,
                evaluate,
                extra,
            )

        tick = self._arc.run_managed_cycle(command.research_run_id, _cycle)
        return ExecuteExploratoryHypothesisResult(
            hypothesis_id=hypothesis.hypothesis_id,
            draft_id=draft.draft_id,
            compiler_outcome=compiled.outcome.value,
            compiler_reason=compiled.reason_code,
            orchestration_state=tick.state,
            experiment_id=extra["experiment_id"],
            assessment_id=extra["assessment_id"],
            observation_id=extra["observation_id"],
            core_decision=extra["core_decision"],
            stop_reason=tick.stop_reason,
        )

    def _run_exploratory_cycle(
        self,
        command: ExecuteExploratoryHypothesisCommand,
        draft: ExploratoryHypothesisDraft,
        plan: ExperimentPlan,
        current: ResearchOrchestrationRecord,
        prepare: PreparePlannedExperiment,
        execute: ExecutePlannedExperiment,
        evaluate: EvaluateExperimentFeedback,
        extra: dict[str, str | None],
    ) -> ManagedCycleOutcome:
        if plan.side_effect_level > current.side_effect_ceiling:
            return ManagedCycleOutcome(
                outcome=CycleOutcome.BLOCKED,
                phase_label=OrchestrationPhase.CYCLE_COMPLETE.value,
                state=OrchestrationState.BLOCKED,
                stop_reason_value=StopReason.CORE_BLOCKED.value,
                hypothesis_id=command.hypothesis_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
            )

        experiment_id = new_opaque_id()
        prepare.execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id=command.research_run_id,
                plan=plan,
            )
        )
        extra["experiment_id"] = experiment_id
        executed = execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
            )
        )
        extra["core_decision"] = (
            executed.core_decision.value
            if executed.core_decision is not None
            else executed.status.value
        )
        extra["observation_id"] = (
            executed.observation_ids[0] if executed.observation_ids else None
        )
        assessment_id = None
        if executed.status is ResearchLoopStatus.OBSERVATION_PRODUCED:
            feedback = evaluate.execute(
                EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
            )
            assessment_id = feedback.assessment_id
            extra["assessment_id"] = assessment_id
            extra["observation_id"] = (
                feedback.observation_ids[0] if feedback.observation_ids else extra["observation_id"]
            )
        elif executed.status is ResearchLoopStatus.UNKNOWN_OUTCOME:
            return ManagedCycleOutcome(
                outcome=CycleOutcome.BLOCKED,
                phase_label=OrchestrationPhase.CYCLE_COMPLETE.value,
                state=OrchestrationState.FAILED_OPERATIONAL,
                stop_reason_value=StopReason.OPERATIONAL_FAILURE.value,
                hypothesis_id=command.hypothesis_id,
                experiment_id=experiment_id,
                increment_cycle=True,
                current_phase=OrchestrationPhase.CYCLE_COMPLETE,
                extra_audit_events=(self._execution_audit(command, draft, experiment_id, executed),),
            )

        denied = executed.status is ResearchLoopStatus.DISPATCH_DENIED
        return ManagedCycleOutcome(
            outcome=CycleOutcome.BLOCKED if denied else CycleOutcome.CONTINUE,
            phase_label=OrchestrationPhase.ASSESSMENT_COMPLETE.value,
            state=OrchestrationState.BLOCKED if denied else OrchestrationState.RUNNING,
            stop_reason_value=StopReason.CORE_BLOCKED.value if denied else None,
            hypothesis_id=command.hypothesis_id,
            experiment_id=experiment_id,
            observation_id=extra["observation_id"],
            assessment_id=assessment_id,
            increment_cycle=True,
            current_phase=OrchestrationPhase.ASSESSMENT_COMPLETE,
            extra_audit_events=(self._execution_audit(command, draft, experiment_id, executed),),
        )

    def _execution_audit(
        self,
        command: ExecuteExploratoryHypothesisCommand,
        draft: ExploratoryHypothesisDraft,
        experiment_id: str,
        executed,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=self._clock.now(),
            actor_id=self._actor_id,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=EXPLORATORY_EXECUTED_EVENT,
            subject_type=EXPLORATORY_SUBJECT_TYPE,
            subject_id=draft.draft_id,
            payload={
                "hypothesis_id": command.hypothesis_id,
                "research_run_id": command.research_run_id,
                "experiment_id": experiment_id,
                "compiler_adapter": EXPLORATORY_COMPILER_ADAPTER_VERSION,
                "may_write_hunter_registry": False,
                "registry_external": True,
                "loop_status": executed.status.value,
                "core_decision": (
                    executed.core_decision.value if executed.core_decision is not None else None
                ),
            },
            correlation_id=command.correlation_id,
        )
