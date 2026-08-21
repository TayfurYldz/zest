"""Execute registry-external exploratory research through ARC + PromotionPipeline.

Not Slice 7 diagnostic.echo plumbing. Compiles via typed non-diagnostic
compilers, then the same Prepare/Execute/Evaluate path ARC uses. Does not
write hunter_family. Does not create Finding. Model args are not Worker
payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    ManagedCycleOutcome,
    StartAutonomousResearchCommand,
)
from research_os.application.errors import ApplicationError
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from research_os.application.exploratory_binding import load_exploratory_binding
from research_os.application.identity import new_opaque_id
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.promotion_pipeline import (
    AdvancePromotionCommand,
    AdvancePromotionPipeline,
    PromotionResult,
)
from research_os.core.enums import ActorType
from research_os.core.scope import ScopeEvaluationInput
from research_os.core.scope_compiler import CompiledScope
from research_os.data.records import AuditEventRecord, ResearchOpportunityRecord, ResearchOrchestrationRecord
from research_os.platform.worker import WorkerPort
from research_os.research.compiler_registry import CompilerOutcome
from research_os.research.exploratory import EXPLORATORY_SUBJECT_TYPE, ExploratoryHypothesisDraft
from research_os.research.exploratory_research_compile import (
    EXPLORATORY_RESEARCH_COMPILER_VERSION,
    compile_exploratory_research,
)
from research_os.research.model_port import ModelCallRequest, ModelCallResult
from research_os.research.orchestration import (
    CycleOutcome,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)
from research_os.research.types import ExperimentPlan, ResearchInputError
from research_os.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY

CONTROL_PLANE_ACTOR_ID = "control-plane:exploratory-research"
EXPLORATORY_RESEARCH_EXECUTED_EVENT = "EXPLORATORY_RESEARCH_EXECUTED"
EXPLORATORY_RESEARCH_QUESTION = (
    "Does the registry-external exploratory hypothesis reproduce through a "
    "typed non-diagnostic compiler, Core, Worker, and PromotionPipeline?"
)
OPPORTUNITY_STRATEGY_VERSION = "exploratory.research.opportunity.v1"


class _NeverInvokedModelPort:
    def complete(self, request: ModelCallRequest) -> ModelCallResult:  # pragma: no cover
        raise ApplicationError(
            "ExecuteExploratoryResearch is a non-model path; its placeholder "
            "ModelPort must never be invoked"
        )


@dataclass(frozen=True)
class ExecuteExploratoryResearchCommand:
    research_run_id: str
    hypothesis_id: str
    budget_id: str
    target_reference: str
    scope: ScopeEvaluationInput
    bounds: OrchestrationBounds
    compile_arguments: Mapping[str, Any] | None = None
    compiled_scope: CompiledScope | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class ExecuteExploratoryResearchResult:
    hypothesis_id: str
    draft_id: str
    compiler_outcome: str
    compiler_reason: str
    orchestration_state: str
    opportunity_id: str | None = None
    experiment_id: str | None = None
    assessment_id: str | None = None
    observation_id: str | None = None
    core_decision: str | None = None
    stop_reason: str | None = None
    evidence_id: str | None = None
    candidate_id: str | None = None
    verification_id: str | None = None
    finding_proposal_id: str | None = None
    may_write_hunter_registry: bool = False
    registry_written: bool = False
    used_diagnostic_echo: bool = False


class ExecuteExploratoryResearch:
    """Run-scoped exploratory research. ARC remains the sole next-action owner."""

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
        self._worker = worker
        self._arc = AutonomousResearchController(
            uow_factory,
            worker,
            _NeverInvokedModelPort(),
            clock=self._clock,
            actor_id=actor_id,
        )
        self._advance = AdvancePromotionPipeline(
            uow_factory, worker, clock=self._clock, actor_id=actor_id
        )

    def execute(
        self, command: ExecuteExploratoryResearchCommand
    ) -> ExecuteExploratoryResearchResult:
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
            families_before = {record.family_id for record in uow.hunter_families.list_enabled()}
            uow.rollback()

        try:
            compiled = compile_exploratory_research(
                draft,
                hypothesis_id=hypothesis.hypothesis_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                compile_arguments=command.compile_arguments,
            )
        except ResearchInputError as exc:
            raise ApplicationError(str(exc)) from exc

        if not compiled.compiled or compiled.plan is None:
            return ExecuteExploratoryResearchResult(
                hypothesis_id=hypothesis.hypothesis_id,
                draft_id=draft.draft_id,
                compiler_outcome=compiled.outcome.value,
                compiler_reason=compiled.reason_code,
                orchestration_state=OrchestrationState.READY.value,
            )
        plan = compiled.plan
        if plan.required_capability == DIAGNOSTIC_ECHO_CAPABILITY:
            raise ApplicationError("exploratory research cannot execute diagnostic.echo")
        if plan.side_effect_level > command.bounds.side_effect_ceiling:
            return ExecuteExploratoryResearchResult(
                hypothesis_id=hypothesis.hypothesis_id,
                draft_id=draft.draft_id,
                compiler_outcome=compiled.outcome.value,
                compiler_reason="SIDE_EFFECT_CEILING_EXCEEDED",
                orchestration_state=OrchestrationState.BLOCKED.value,
                stop_reason=StopReason.CORE_BLOCKED.value,
            )

        opportunity_id = self._ensure_opportunity(command, draft)
        if existing_assessments:
            record = existing_assessments[0]
            promoted = self._advance.execute(
                AdvancePromotionCommand(
                    research_run_id=command.research_run_id,
                    scope=command.scope,
                    compiled_scope=command.compiled_scope,
                    assessment_id=record.assessment_id,
                )
            )
            promotion = promoted[0] if promoted else None
            return self._result(
                hypothesis.hypothesis_id,
                draft,
                compiled,
                orchestration_state=OrchestrationState.RUNNING.value,
                opportunity_id=opportunity_id,
                experiment_id=record.experiment_id,
                assessment_id=record.assessment_id,
                observation_id=record.observation_ids[0] if record.observation_ids else None,
                promotion=promotion,
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
            return self._run_cycle(
                command, draft, plan, current, prepare, execute, evaluate, extra
            )

        tick = self._arc.run_managed_cycle(command.research_run_id, _cycle)
        promoted = self._advance.execute(
            AdvancePromotionCommand(
                research_run_id=command.research_run_id,
                scope=command.scope,
                compiled_scope=command.compiled_scope,
            )
        )
        promotion = promoted[0] if promoted else None
        with self._uow_factory.open() as uow:
            families_after = {record.family_id for record in uow.hunter_families.list_enabled()}
            uow.rollback()
        if families_after != families_before:
            raise ApplicationError("exploratory research mutated hunter_family")
        return self._result(
            hypothesis.hypothesis_id,
            draft,
            compiled,
            orchestration_state=tick.state,
            opportunity_id=opportunity_id,
            experiment_id=extra["experiment_id"],
            assessment_id=extra["assessment_id"],
            observation_id=extra["observation_id"],
            core_decision=extra["core_decision"],
            stop_reason=tick.stop_reason,
            promotion=promotion,
        )

    def _run_cycle(
        self,
        command: ExecuteExploratoryResearchCommand,
        draft: ExploratoryHypothesisDraft,
        plan: ExperimentPlan,
        current: ResearchOrchestrationRecord,
        prepare: PreparePlannedExperiment,
        execute: ExecutePlannedExperiment,
        evaluate: EvaluateExperimentFeedback,
        extra: dict[str, str | None],
    ) -> ManagedCycleOutcome:
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
                compiled_scope=command.compiled_scope,
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

    def _ensure_opportunity(
        self,
        command: ExecuteExploratoryResearchCommand,
        draft: ExploratoryHypothesisDraft,
    ) -> str:
        with self._uow_factory.open() as uow:
            existing = [
                item
                for item in uow.research_opportunities.list_for_research_run(
                    command.research_run_id
                )
                if item.structural_identity == draft.structural_identity
            ]
            if existing:
                opportunity_id = existing[0].opportunity_id
                uow.rollback()
                return opportunity_id
            opportunity_id = new_opaque_id()
            uow.research_opportunities.insert(
                ResearchOpportunityRecord(
                    opportunity_id=opportunity_id,
                    research_run_id=command.research_run_id,
                    opportunity_kind="OTHER",
                    mode="EXPLORATION",
                    source_refs=draft.source_refs,
                    proposed_direction=draft.hypothesis_claim,
                    unresolved_question=draft.proposed_family_rationale,
                    expected_information_value_description=(
                        "registry-external exploratory hypothesis is not Evidence"
                    ),
                    assumptions=("exploratory draft is not a HunterFamily",),
                    dimensions={
                        "registry_external": True,
                        "exploratory": True,
                        "proposed_family_name": draft.proposed_family_name,
                    },
                    context_signature=draft.structural_identity,
                    novelty_composition_marker=True,
                    prior_attempt_refs=(),
                    structural_identity=draft.structural_identity,
                    strategy_version=OPPORTUNITY_STRATEGY_VERSION,
                    created_at=self._clock.now(),
                )
            )
            uow.commit()
        return opportunity_id

    def _execution_audit(
        self,
        command: ExecuteExploratoryResearchCommand,
        draft: ExploratoryHypothesisDraft,
        experiment_id: str,
        executed,
    ) -> AuditEventRecord:
        return AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=self._clock.now(),
            actor_id=self._actor_id,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=EXPLORATORY_RESEARCH_EXECUTED_EVENT,
            subject_type=EXPLORATORY_SUBJECT_TYPE,
            subject_id=draft.draft_id,
            payload={
                "hypothesis_id": command.hypothesis_id,
                "research_run_id": command.research_run_id,
                "experiment_id": experiment_id,
                "compiler_adapter": EXPLORATORY_RESEARCH_COMPILER_VERSION,
                "may_write_hunter_registry": False,
                "registry_external": True,
                "not_diagnostic_echo": True,
                "loop_status": executed.status.value,
                "core_decision": (
                    executed.core_decision.value if executed.core_decision is not None else None
                ),
            },
            correlation_id=command.correlation_id,
        )

    def _result(
        self,
        hypothesis_id: str,
        draft: ExploratoryHypothesisDraft,
        compiled,
        *,
        orchestration_state: str,
        opportunity_id: str | None,
        experiment_id: str | None,
        assessment_id: str | None,
        observation_id: str | None,
        core_decision: str | None = None,
        stop_reason: str | None = None,
        promotion: PromotionResult | None = None,
    ) -> ExecuteExploratoryResearchResult:
        return ExecuteExploratoryResearchResult(
            hypothesis_id=hypothesis_id,
            draft_id=draft.draft_id,
            compiler_outcome=compiled.outcome.value,
            compiler_reason=compiled.reason_code,
            orchestration_state=orchestration_state,
            opportunity_id=opportunity_id,
            experiment_id=experiment_id,
            assessment_id=assessment_id,
            observation_id=observation_id,
            core_decision=core_decision,
            stop_reason=stop_reason,
            evidence_id=promotion.evidence_id if promotion else None,
            candidate_id=promotion.candidate_id if promotion else None,
            verification_id=promotion.verification_id if promotion else None,
            finding_proposal_id=promotion.finding_proposal_id if promotion else None,
        )
