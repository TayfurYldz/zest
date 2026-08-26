"""Dispatch an APPROVED Hunt V3 queue item through the compiler and Core.

Human approval of a queue item is admission to compile, not execution
authorization. Fresh Core `evaluate_execution()` happens inside
`ExecutePlannedExperiment` for every dispatch. Unsupported families fail closed
to BLOCKED and are never marked covered or RUN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopOutcome,
    ResearchLoopStatus,
)
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.approval import ApprovalView
from zest.core.enums import ActorType
from zest.core.scope import ScopeEvaluationInput
from zest.core.scope_compiler import CompiledScope
from zest.data.errors import PersistenceConflictError
from zest.data.records import AuditEventRecord, HuntV3QueueRecord
from zest.platform.secrets import CompositeSecretPort
from zest.platform.worker import WorkerPort
from zest.research.compiler_registry import (
    MUTATION_MATRIX_FAMILIES,
    PROTOCOL_FAMILIES,
    CompilerOutcome,
    CompilerRequest,
    CompilerResult,
    ExperimentCompilerRegistry,
)

DISPATCH_ACTOR_ID = "control-plane:dispatch-approved-v3-queue"
HUNT_V3_QUEUE_DISPATCHED = "HUNT_V3_QUEUE_DISPATCHED"
HUNT_V3_QUEUE_DISPATCH_BLOCKED = "HUNT_V3_QUEUE_DISPATCH_BLOCKED"
HUNT_V3_QUEUE_DISPATCH_REJECTED = "HUNT_V3_QUEUE_DISPATCH_REJECTED"
HUNT_V3_UNIT_INTENT = "HUNT_V3_UNIT_INTENT"
HUNT_V3_UNIT_OUTCOME = "HUNT_V3_UNIT_OUTCOME"
UNIT_FAMILIES = MUTATION_MATRIX_FAMILIES | PROTOCOL_FAMILIES


class HuntV3DispatchError(ApplicationError):
    """Invalid dispatch inputs. Not a Core DENY and not a compile miss."""


@dataclass(frozen=True)
class DispatchApprovedV3QueueCommand:
    research_run_id: str
    queue_id: str
    budget_id: str
    target_reference: str
    scope: ScopeEvaluationInput
    compiled_scope: CompiledScope | None = None
    approval: ApprovalView | None = None
    compile_arguments: Mapping[str, Any] = field(default_factory=dict)
    selected_cell_id: str | None = None
    selected_step_id: str | None = None


@dataclass(frozen=True)
class DispatchApprovedV3QueueResult:
    research_run_id: str
    queue_id: str
    state: str
    outcome: str
    reason_code: str
    compiler_id: str | None = None
    experiment_id: str | None = None
    hypothesis_id: str | None = None
    attempt_id: str | None = None
    core_decision: str | None = None
    worker_invoked: bool = False
    coverage_recorded: bool = False


class DispatchApprovedV3Queue:
    """APPROVED → compile | BLOCKED_UNSUPPORTED → fresh Core authorize → one attempt."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        *,
        clock: Clock | None = None,
        registry: ExperimentCompilerRegistry | None = None,
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._registry = registry or ExperimentCompilerRegistry()
        self._prepare = PreparePlannedExperiment(uow_factory, clock=self._clock)
        self._execute = ExecutePlannedExperiment(
            uow_factory, worker, clock=self._clock, secret_port=secret_port
        )
        self._evaluate = EvaluateExperimentFeedback(uow_factory, clock=self._clock)

    def execute(self, command: DispatchApprovedV3QueueCommand) -> DispatchApprovedV3QueueResult:
        item = self._load_approved(command)
        if item.state == "RUN":
            return DispatchApprovedV3QueueResult(
                research_run_id=item.research_run_id,
                queue_id=item.queue_id,
                state=item.state,
                outcome="ALREADY_DISPATCHED",
                reason_code="ALREADY_RUN",
                hypothesis_id=item.hypothesis_id,
                worker_invoked=False,
            )
        family_name = _family_name(item)
        unit_id = _unit_id(command, family_name)
        if family_name in UNIT_FAMILIES:
            if unit_id is None:
                compiled = self._compile(command, item)
                self._audit_unit(
                    item,
                    event_type=HUNT_V3_UNIT_OUTCOME,
                    unit_id="",
                    experiment_id=None,
                    outcome="BLOCKED_INVALID_INPUT",
                    reason_code="SELECTED_UNIT_REQUIRED",
                    coverage_eligible=False,
                    compiler_id=compiled.compiler_id,
                )
                return DispatchApprovedV3QueueResult(
                    research_run_id=item.research_run_id,
                    queue_id=item.queue_id,
                    state=item.state,
                    outcome="BLOCKED_INVALID_INPUT",
                    reason_code="SELECTED_UNIT_REQUIRED",
                    compiler_id=compiled.compiler_id,
                    hypothesis_id=item.hypothesis_id,
                    worker_invoked=False,
                )
            prior = self._prior_unit(item.queue_id, unit_id)
            if prior is not None:
                return DispatchApprovedV3QueueResult(
                    research_run_id=item.research_run_id,
                    queue_id=item.queue_id,
                    state=item.state,
                    outcome="ALREADY_DISPATCHED",
                    reason_code="UNIT_ALREADY_ATTEMPTED",
                    compiler_id=None,
                    experiment_id=str(prior.payload.get("experiment_id") or "") or None,
                    hypothesis_id=item.hypothesis_id,
                    worker_invoked=False,
                )

        compiled = self._compile(command, item)
        if not compiled.compiled or compiled.plan is None:
            if family_name in UNIT_FAMILIES:
                self._audit_unit(
                    item,
                    event_type=HUNT_V3_UNIT_OUTCOME,
                    unit_id=unit_id or "",
                    experiment_id=None,
                    outcome=compiled.outcome.value,
                    reason_code=compiled.reason_code,
                    coverage_eligible=False,
                    compiler_id=compiled.compiler_id,
                )
                return DispatchApprovedV3QueueResult(
                    research_run_id=item.research_run_id,
                    queue_id=item.queue_id,
                    state=item.state,
                    outcome=compiled.outcome.value,
                    reason_code=compiled.reason_code,
                    compiler_id=compiled.compiler_id,
                    hypothesis_id=item.hypothesis_id,
                    worker_invoked=False,
                )
            self._block(item, compiled)
            return DispatchApprovedV3QueueResult(
                research_run_id=item.research_run_id,
                queue_id=item.queue_id,
                state="BLOCKED",
                outcome=compiled.outcome.value,
                reason_code=compiled.reason_code,
                compiler_id=compiled.compiler_id,
                hypothesis_id=item.hypothesis_id,
                worker_invoked=False,
            )

        experiment_id = new_opaque_id()
        self._prepare.execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id=command.research_run_id,
                plan=compiled.plan,
            )
        )
        if family_name in UNIT_FAMILIES and unit_id is not None:
            self._audit_unit(
                item,
                event_type=HUNT_V3_UNIT_INTENT,
                unit_id=unit_id,
                experiment_id=experiment_id,
                outcome="INTENT",
                reason_code="PREPARED",
                coverage_eligible=False,
                compiler_id=compiled.compiler_id,
            )
        loop = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=compiled.plan,
                scope=command.scope,
                compiled_scope=command.compiled_scope,
                approval=command.approval,
                identity_id=item.identity_id,
            )
        )
        worker_invoked = loop.status not in {
            ResearchLoopStatus.DISPATCH_DENIED,
            ResearchLoopStatus.HUMAN_REVIEW_REQUIRED,
            ResearchLoopStatus.INPUT_REJECTED,
        }
        if loop.status in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.NO_OBSERVATION,
            ResearchLoopStatus.INVOCATION_FAILED,
        } and loop.experiment_id:
            self._evaluate.execute(EvaluateExperimentFeedbackCommand(experiment_id=loop.experiment_id))

        if loop.status is ResearchLoopStatus.DISPATCH_DENIED:
            self._block(item, compiled, reason_code="CORE_DENIED", experiment_id=experiment_id)
            return self._result(
                item,
                state="BLOCKED",
                outcome="CORE_DENIED",
                reason_code="CORE_DENIED",
                compiled=compiled,
                experiment_id=experiment_id,
                loop=loop,
                worker_invoked=False,
            )
        if loop.status is ResearchLoopStatus.HUMAN_REVIEW_REQUIRED:
            self._block(
                item,
                compiled,
                reason_code="REQUIRE_HUMAN_REVIEW",
                experiment_id=experiment_id,
            )
            return self._result(
                item,
                state="BLOCKED",
                outcome="REQUIRE_HUMAN_REVIEW",
                reason_code="REQUIRE_HUMAN_REVIEW",
                compiled=compiled,
                experiment_id=experiment_id,
                loop=loop,
                worker_invoked=False,
            )

        coverage_recorded = loop.status is ResearchLoopStatus.OBSERVATION_PRODUCED
        if family_name in UNIT_FAMILIES and unit_id is not None:
            self._audit_unit(
                item,
                event_type=HUNT_V3_UNIT_OUTCOME,
                unit_id=unit_id,
                experiment_id=experiment_id,
                outcome=loop.status.value,
                reason_code="DISPATCHED",
                coverage_eligible=coverage_recorded,
                compiler_id=compiled.compiler_id,
                attempt_id=loop.attempt_id,
            )
            return self._result(
                item,
                state=item.state,
                outcome=loop.status.value,
                reason_code="DISPATCHED",
                compiled=compiled,
                experiment_id=experiment_id,
                loop=loop,
                worker_invoked=worker_invoked,
                coverage_recorded=coverage_recorded,
            )

        self._mark_run(item, experiment_id=experiment_id, loop=loop, compiled=compiled)
        return self._result(
            item,
            state="RUN",
            outcome=loop.status.value,
            reason_code="DISPATCHED",
            compiled=compiled,
            experiment_id=experiment_id,
            loop=loop,
            worker_invoked=worker_invoked,
            coverage_recorded=coverage_recorded,
        )

    def _prior_unit(self, queue_id: str, unit_id: str) -> AuditEventRecord | None:
        with self._uow_factory.open() as uow:
            events = uow.audit_events.list_for_subject_type("HUNT_V3_QUEUE")
            uow.rollback()
        for event in events:
            if event.subject_id != queue_id:
                continue
            if event.event_type not in {HUNT_V3_UNIT_INTENT, HUNT_V3_UNIT_OUTCOME}:
                continue
            if str(event.payload.get("unit_id") or "") == unit_id:
                return event
        return None

    def _audit_unit(
        self,
        item: HuntV3QueueRecord,
        *,
        event_type: str,
        unit_id: str,
        experiment_id: str | None,
        outcome: str,
        reason_code: str,
        coverage_eligible: bool,
        compiler_id: str | None,
        attempt_id: str | None = None,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=DISPATCH_ACTOR_ID,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=event_type,
                    subject_type="HUNT_V3_QUEUE",
                    subject_id=item.queue_id,
                    payload={
                        "research_run_id": item.research_run_id,
                        "queue_id": item.queue_id,
                        "unit_id": unit_id,
                        "experiment_id": experiment_id,
                        "attempt_id": attempt_id,
                        "outcome": outcome,
                        "reason_code": reason_code,
                        "compiler_id": compiler_id,
                        "coverage_eligible": coverage_eligible,
                        "compiled_is_not_coverage": True,
                        "approval_is_not_authorization": True,
                    },
                    correlation_id=item.research_run_id,
                )
            )
            uow.commit()

    def _load_approved(self, command: DispatchApprovedV3QueueCommand) -> HuntV3QueueRecord:
        with self._uow_factory.open() as uow:
            item = uow.hunt_v3_queue.get(command.queue_id)
            run = uow.research_runs.get(command.research_run_id)
            uow.rollback()
        if item is None:
            raise HuntV3DispatchError("hunt V3 queue item not found")
        if run is None:
            raise HuntV3DispatchError("research run not found")
        if item.research_run_id != command.research_run_id:
            raise HuntV3DispatchError("hunt V3 queue item does not belong to run")
        if item.state not in {"APPROVED", "RUN"}:
            raise HuntV3DispatchError("hunt V3 queue item is not approved")
        return item

    def _compile(
        self, command: DispatchApprovedV3QueueCommand, item: HuntV3QueueRecord
    ) -> CompilerResult:
        merged = dict(item.arguments)
        merged.update(dict(command.compile_arguments))
        if command.selected_cell_id is not None:
            merged["selected_cell_id"] = command.selected_cell_id
            selected = _cell_by_id(merged.get("cells"), command.selected_cell_id)
            if selected is not None:
                merged.update(selected)
        if command.selected_step_id is not None:
            merged["selected_step_id"] = command.selected_step_id
            selected = _cell_by_id(merged.get("steps"), command.selected_step_id)
            if selected is not None:
                merged.update(selected)
        family_name = merged.get("family_name")
        if not isinstance(family_name, str):
            family_name = None
        return self._registry.compile(
            CompilerRequest(
                hypothesis_id=item.hypothesis_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
                family_id=item.family_id,
                family_name=family_name,
                arguments=merged,
                requested_side_effect=None,
            )
        )

    def _block(
        self,
        item: HuntV3QueueRecord,
        compiled: CompilerResult,
        *,
        reason_code: str | None = None,
        experiment_id: str | None = None,
    ) -> None:
        now = self._clock.now()
        resolved = reason_code or compiled.reason_code
        with self._uow_factory.open() as uow:
            try:
                uow.hunt_v3_queue.set_state(item.queue_id, "BLOCKED", from_state="APPROVED")
            except PersistenceConflictError:
                uow.rollback()
                return
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=DISPATCH_ACTOR_ID,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=HUNT_V3_QUEUE_DISPATCH_BLOCKED,
                    subject_type="HUNT_V3_QUEUE",
                    subject_id=item.queue_id,
                    payload={
                        "research_run_id": item.research_run_id,
                        "queue_id": item.queue_id,
                        "reason_code": resolved,
                        "compiler_id": compiled.compiler_id,
                        "compiler_outcome": compiled.outcome.value,
                        "experiment_id": experiment_id,
                        "approval_is_not_authorization": True,
                        "not_coverage": True,
                    },
                    correlation_id=item.research_run_id,
                )
            )
            uow.commit()

    def _mark_run(
        self,
        item: HuntV3QueueRecord,
        *,
        experiment_id: str,
        loop: ResearchLoopOutcome,
        compiled: CompilerResult,
    ) -> None:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            try:
                uow.hunt_v3_queue.set_state(item.queue_id, "RUN", from_state="APPROVED")
            except PersistenceConflictError:
                uow.rollback()
                return
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=DISPATCH_ACTOR_ID,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=HUNT_V3_QUEUE_DISPATCHED,
                    subject_type="HUNT_V3_QUEUE",
                    subject_id=item.queue_id,
                    payload={
                        "research_run_id": item.research_run_id,
                        "queue_id": item.queue_id,
                        "experiment_id": experiment_id,
                        "attempt_id": loop.attempt_id,
                        "compiler_id": compiled.compiler_id,
                        "loop_status": loop.status.value,
                        "core_decision": (
                            loop.core_decision.value if loop.core_decision is not None else None
                        ),
                        "approval_is_not_authorization": True,
                    },
                    correlation_id=item.research_run_id,
                )
            )
            uow.commit()

    def _result(
        self,
        item: HuntV3QueueRecord,
        *,
        state: str,
        outcome: str,
        reason_code: str,
        compiled: CompilerResult,
        experiment_id: str | None,
        loop: ResearchLoopOutcome | None,
        worker_invoked: bool,
        coverage_recorded: bool = False,
    ) -> DispatchApprovedV3QueueResult:
        return DispatchApprovedV3QueueResult(
            research_run_id=item.research_run_id,
            queue_id=item.queue_id,
            state=state,
            outcome=outcome,
            reason_code=reason_code,
            compiler_id=compiled.compiler_id,
            experiment_id=experiment_id,
            hypothesis_id=item.hypothesis_id,
            attempt_id=loop.attempt_id if loop is not None else None,
            core_decision=(
                loop.core_decision.value if loop is not None and loop.core_decision is not None else None
            ),
            worker_invoked=worker_invoked,
            coverage_recorded=coverage_recorded,
        )


def _cell_by_id(items: object, item_id: str) -> dict[str, Any] | None:
    if not isinstance(items, (list, tuple)):
        return None
    for item in items:
        if isinstance(item, Mapping) and item.get("cell_id") == item_id:
            return dict(item)
        if isinstance(item, Mapping) and item.get("step_id") == item_id:
            return dict(item)
    return None


def _family_name(item: HuntV3QueueRecord) -> str | None:
    value = item.arguments.get("family_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _unit_id(command: DispatchApprovedV3QueueCommand, family_name: str | None) -> str | None:
    if family_name in MUTATION_MATRIX_FAMILIES:
        return command.selected_cell_id
    if family_name in PROTOCOL_FAMILIES:
        return command.selected_step_id
    return None
