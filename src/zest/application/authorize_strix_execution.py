"""Authorize then invoke Strix. Denied requests never reach the Integration."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.capability_binding import integration_capability_view_for
from zest.application.errors import ApplicationError
from zest.application.identity import execution_decision_audit_id, new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.approval import ApprovalView
from zest.core.authorization import AuthorizationSourceView
from zest.core.budget import BudgetUsage, IssuedBudget
from zest.core.enums import (
    ActorType,
    AuthorizationSourceState,
    ExecutionDecisionKind,
)
from zest.core.execution import ExecutionRequest, evaluate_execution
from zest.core.scope import ScopeEvaluationInput
from zest.data.records import AuditEventRecord
from zest.platform.strix import (
    ALLOWED_STRIX_CAPABILITIES,
    STRIX_DISABLED_REASON,
    STRIX_RUNTIME_ENABLED,
    UNRESTRICTED_CAPABILITY_MARKERS,
    StrixExecutionOutcome,
    StrixExecutionRequest,
    StrixIntegration,
    StrixRuntimeStatus,
)
from zest.tools.capabilities import STRIX_DIAGNOSTIC_PING_CAPABILITY


@dataclass(frozen=True)
class AuthorizeStrixExecutionCommand:
    research_run_id: str
    experiment_id: str
    capability: str
    target_reference: str
    budget_id: str
    side_effect_level: int
    scope: ScopeEvaluationInput
    allowed_capabilities: tuple[str, ...] = (STRIX_DIAGNOSTIC_PING_CAPABILITY,)
    approval: ApprovalView | None = None
    redirect_or_new_asset: bool = False
    budget_usage: BudgetUsage | None = None


@dataclass(frozen=True)
class AuthorizeStrixExecutionResult:
    core_decision: ExecutionDecisionKind
    core_reason_code: object | None
    authorization_decision_reference: str | None
    reached_strix: bool
    outcome: StrixExecutionOutcome | None


class AuthorizeStrixExecution:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        strix: StrixIntegration,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
    ) -> None:
        self._uow_factory = uow_factory
        self._strix = strix
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(self, command: AuthorizeStrixExecutionCommand) -> AuthorizeStrixExecutionResult:
        if command.redirect_or_new_asset:
            return AuthorizeStrixExecutionResult(
                core_decision=ExecutionDecisionKind.DENY,
                core_reason_code="SCOPE_RECHECK_REQUIRED",
                authorization_decision_reference=None,
                reached_strix=False,
                outcome=StrixExecutionOutcome(
                    status=StrixRuntimeStatus.SCOPE_RECHECK_REQUIRED,
                    untrusted=True,
                    capability=command.capability,
                    reason_codes=("REDIRECT_OR_NEW_ASSET_REQUIRES_CORE_REEVALUATION",),
                    payload={"not_observation": True},
                ),
            )
        lowered = {item.lower() for item in command.allowed_capabilities}
        if lowered & UNRESTRICTED_CAPABILITY_MARKERS:
            return AuthorizeStrixExecutionResult(
                core_decision=ExecutionDecisionKind.DENY,
                core_reason_code="UNRESTRICTED_CAPABILITY",
                authorization_decision_reference=None,
                reached_strix=False,
                outcome=StrixExecutionOutcome(
                    status=StrixRuntimeStatus.DENIED,
                    untrusted=True,
                    capability=command.capability,
                    reason_codes=("UNRESTRICTED_CAPABILITY_REJECTED",),
                    payload={"not_observation": True},
                ),
            )
        if command.capability not in command.allowed_capabilities:
            return AuthorizeStrixExecutionResult(
                core_decision=ExecutionDecisionKind.DENY,
                core_reason_code="CAPABILITY_NOT_ALLOWLISTED",
                authorization_decision_reference=None,
                reached_strix=False,
                outcome=StrixExecutionOutcome(
                    status=StrixRuntimeStatus.DENIED,
                    untrusted=True,
                    capability=command.capability,
                    reason_codes=("CAPABILITY_NOT_ALLOWLISTED",),
                    payload={"not_observation": True},
                ),
            )
        if command.capability not in ALLOWED_STRIX_CAPABILITIES:
            return AuthorizeStrixExecutionResult(
                core_decision=ExecutionDecisionKind.DENY,
                core_reason_code="NON_DIAGNOSTIC_CAPABILITY",
                authorization_decision_reference=None,
                reached_strix=False,
                outcome=StrixExecutionOutcome(
                    status=StrixRuntimeStatus.DENIED,
                    untrusted=True,
                    capability=command.capability,
                    reason_codes=("NON_DIAGNOSTIC_STRIX_CAPABILITY_DEFERRED",),
                    payload={"not_observation": True},
                ),
            )
        if not STRIX_RUNTIME_ENABLED:
            return AuthorizeStrixExecutionResult(
                core_decision=ExecutionDecisionKind.DENY,
                core_reason_code=STRIX_DISABLED_REASON,
                authorization_decision_reference=None,
                reached_strix=False,
                outcome=StrixExecutionOutcome(
                    status=StrixRuntimeStatus.DENIED,
                    untrusted=True,
                    capability=command.capability,
                    reason_codes=(STRIX_DISABLED_REASON,),
                    payload={
                        "not_observation": True,
                        "not_evidence": True,
                        "not_candidate": True,
                        "not_finding": True,
                        "runtime_disabled": True,
                    },
                ),
            )

        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None or experiment.research_run_id != command.research_run_id:
                raise ApplicationError("experiment not found for research run")
            issued = uow.issued_budgets.get(command.budget_id)
            if issued is None or issued.research_run_id != command.research_run_id:
                raise ApplicationError("issued budget not found for research run")
            source_record = uow.authorization_sources.get(run.authorization_source_id)
            if source_record is None:
                raise ApplicationError("authorization source not found")
            source = AuthorizationSourceView(
                source_record.authorization_source_id,
                source_record.program_id,
                AuthorizationSourceState(source_record.state),
            )
            decision = evaluate_execution(
                ExecutionRequest(
                    authorization_source=source,
                    scope=command.scope,
                    issued_budget=IssuedBudget(
                        issued.budget_id,
                        issued.max_requests,
                        issued.max_tool_calls,
                        issued.max_runtime_ms,
                        issued.max_concurrency,
                    ),
                    budget_usage=command.budget_usage or BudgetUsage(0, 0, 0, 0),
                    requested_budget_id=command.budget_id,
                    side_effect_level=command.side_effect_level,
                    requested_subject=command.target_reference,
                    capability=integration_capability_view_for(
                        command.capability,
                        "ping",
                        effective_side_effect=command.side_effect_level,
                    ),
                    approval=command.approval,
                )
            )
            audit_id = execution_decision_audit_id(new_opaque_id())
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=audit_id,
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="STRIX_AUTHORIZATION_DECISION",
                    subject_type="experiment",
                    subject_id=command.experiment_id,
                    payload={
                        "decision": decision.decision.value,
                        "reason_code": getattr(decision.reason_code, "value", str(decision.reason_code)),
                        "not_observation": True,
                    },
                )
            )
            if decision.decision is not ExecutionDecisionKind.ALLOW:
                uow.commit()
                return AuthorizeStrixExecutionResult(
                    core_decision=decision.decision,
                    core_reason_code=decision.reason_code,
                    authorization_decision_reference=audit_id,
                    reached_strix=False,
                    outcome=StrixExecutionOutcome(
                        status=StrixRuntimeStatus.DENIED,
                        untrusted=True,
                        capability=command.capability,
                        reason_codes=("CORE_DID_NOT_ALLOW", decision.decision.value),
                        payload={"not_observation": True},
                    ),
                )
            request = StrixExecutionRequest(
                research_run_id=command.research_run_id,
                experiment_id=command.experiment_id,
                correlation_id=new_opaque_id(),
                request_id=new_opaque_id(),
                capability=command.capability,
                authorized_target_reference=command.target_reference,
                budget_id=command.budget_id,
                side_effect_level=command.side_effect_level,
                authorization_decision_reference=audit_id,
                allowed_capabilities=command.allowed_capabilities,
            )
            uow.commit()
        outcome = self._strix.execute(request)
        if outcome.status is not StrixRuntimeStatus.COMPLETED:
            return AuthorizeStrixExecutionResult(
                core_decision=decision.decision,
                core_reason_code=decision.reason_code,
                authorization_decision_reference=audit_id,
                reached_strix=True,
                outcome=outcome,
            )
        return AuthorizeStrixExecutionResult(
            core_decision=decision.decision,
            core_reason_code=decision.reason_code,
            authorization_decision_reference=audit_id,
            reached_strix=True,
            outcome=outcome,
        )
