"""Execute a planned Experiment through Core, durable attempt, Worker, Transition A.

This is the A7-lite control-loop skeleton. It is not a Research Brain.
It does not call a model, create Evidence, or update Hypothesis truth.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from research_os.application.capability_binding import (
    CapabilityBindingError,
    capability_view_for_plan,
)
from research_os.application.http_transaction_authorization import (
    HTTP_SCOPE_CAPABILITIES,
    authorize_http_transaction_plan,
    scope_evaluation_from_compiled_check,
)
from research_os.application.authorized_network_envelope import AuthorizedNetworkEnvelope
from research_os.application.program_research_context import ProgramPolicyView
from research_os.application.identity import (
    attempt_id_for,
    execution_decision_audit_id,
    new_opaque_id,
)
from research_os.application.session_binding import bind_identity_session
from research_os.application.scope_reauthorization import reauthorization_request_from_worker_result
from research_os.application.session_lifecycle import authenticating_session_record
from research_os.platform.secrets import CompositeSecretPort
from research_os.application.ingest_worker_invocation import (
    CONTROL_PLANE_ACTOR_ID,
    IngestCompletedWorkerInvocation,
    IngestionOutcome,
    IngestionStatus,
)
from research_os.application.plan_records import (
    durable_plan_matches,
    experiment_plan_from_record,
    experiment_plan_record_for,
)
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.core.approval import ApprovalView
from research_os.core.authorization import AuthorizationSourceView
from research_os.core.budget import BudgetUsage, IssuedBudget
from research_os.core.enums import (
    ActorType,
    AuthorizationSourceState,
    ExecutionDecisionKind,
    ReasonCode,
)
from research_os.core.execution import ExecutionDecision, ExecutionRequest, evaluate_execution
from research_os.core.rate_limit import RateLimitProfile, check_rate_limit
from research_os.core.scope import ScopeEvaluationInput
from research_os.core.scope_compiler import CompiledScope
from research_os.data.budget_ledger import remaining_for_resource, usage_from_consumptions
from research_os.data.errors import BudgetOverspendError, PersistenceError
from research_os.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    BudgetConsumptionRecord,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExperimentExecutionState,
    ExperimentRecord,
    IssuedBudgetRecord,
    RateLimitProfileRecord,
)
from research_os.data.unit_of_work import UnitOfWork
from research_os.platform.contract_validation import ContractValidator
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome, WorkerPort
from research_os.research.identity_session import HttpFormLoginProfile, Identity
from research_os.research.types import ExperimentPlan
from research_os.tools.browser_page_policy import BROWSER_PAGE_MAX_NETWORK_REQUESTS
from research_os.tools.capabilities import BROWSER_PAGE_CAPABILITY, HTTP_RAW_EXCHANGE_CAPABILITY

WORKER_CONTRACT_VERSION = "v1"
AUDIT_EXECUTION_DECISION = "EXECUTION_DECISION"
BUDGET_CONSUMPTION_LEDGER_IMPLEMENTED = True

TERMINAL_EXPERIMENT_STATES = frozenset(
    {
        ExperimentExecutionState.EXECUTION_SUCCEEDED.value,
        ExperimentExecutionState.EXECUTION_FAILED.value,
        ExperimentExecutionState.BLOCKED.value,
        ExperimentExecutionState.CANCELLED.value,
        ExperimentExecutionState.BUDGET_EXHAUSTED.value,
    }
)

FAILED_INVOCATION_STATUSES = frozenset(
    {
        InvocationStatus.START_FAILED,
        InvocationStatus.PROCESS_FAILED,
        InvocationStatus.PROTOCOL_ERROR,
        InvocationStatus.CONTRACT_INVALID,
    }
)


class ResearchLoopStatus(Enum):
    """Control-loop outcome. Not a vulnerability verdict. Not Hypothesis belief."""

    OBSERVATION_PRODUCED = "OBSERVATION_PRODUCED"
    NO_OBSERVATION = "NO_OBSERVATION"
    DISPATCH_DENIED = "DISPATCH_DENIED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    INVOCATION_FAILED = "INVOCATION_FAILED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    AUTHORIZED_NOT_DISPATCHED = "AUTHORIZED_NOT_DISPATCHED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    INPUT_REJECTED = "INPUT_REJECTED"
    REAUTHORIZATION_REQUIRED = "REAUTHORIZATION_REQUIRED"


@dataclass(frozen=True)
class ExecutePlannedExperimentCommand:
    experiment_id: str
    plan: ExperimentPlan
    scope: ScopeEvaluationInput
    approval: ApprovalView | None = None
    budget_usage: BudgetUsage | None = None
    compiled_scope: CompiledScope | None = None
    program_policy: ProgramPolicyView | None = None
    identity_id: str | None = None
    identity: Identity | None = None
    authentication_profile: HttpFormLoginProfile | None = None


@dataclass(frozen=True)
class AuthorizedDispatch:
    """In-process handle after TX1 AUTHORIZED intent. Not a WorkerResult."""

    experiment_id: str
    hypothesis_id: str
    request_id: str
    attempt_id: str
    correlation_id: str
    authorization_decision_reference: str
    worker_request: Mapping[str, Any]
    timeout_ms: int
    core_decision: ExecutionDecisionKind
    core_reason_code: ReasonCode
    resolved_secret_values: Mapping[str, str] | None = None
    network_envelope: AuthorizedNetworkEnvelope | None = None


@dataclass(frozen=True)
class ResearchLoopOutcome:
    status: ResearchLoopStatus
    hypothesis_id: str
    experiment_id: str
    experiment_execution_state: str
    core_decision: ExecutionDecisionKind | None = None
    core_reason_code: ReasonCode | None = None
    authorization_decision_reference: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None
    attempt_state: str | None = None
    invocation_status: InvocationStatus | None = None
    worker_result_id: str | None = None
    observation_ids: tuple[str, ...] = ()
    ingestion_status: IngestionStatus | None = None
    hypothesis_claim_unchanged: bool = True
    reauthorization_request: Mapping[str, Any] | None = None
    network_envelope: AuthorizedNetworkEnvelope | None = None


class ExecutePlannedExperiment:
    """Hypothesis/Experiment → Core → durable attempt → WorkerPort → Transition A."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        *,
        ingest: IngestCompletedWorkerInvocation | None = None,
        clock: Clock | None = None,
        validator: ContractValidator | None = None,
        actor_id: str = CONTROL_PLANE_ACTOR_ID,
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._worker = worker
        self._clock = clock or SystemClock()
        self._validator = validator or ContractValidator()
        self._actor_id = actor_id
        self._secret_port = secret_port
        self._ingest = ingest or IngestCompletedWorkerInvocation(
            uow_factory,
            validator=self._validator,
            clock=self._clock,
            actor_id=actor_id,
            secret_port=secret_port,
        )

    def execute(
        self,
        command: ExecutePlannedExperimentCommand,
        *,
        persist_hook: Callable[..., None] | None = None,
    ) -> ResearchLoopOutcome:
        existing = self._fail_closed_existing(command.experiment_id)
        if existing is not None:
            return existing
        authorized = self.authorize(command, persist_hook=persist_hook)
        if isinstance(authorized, ResearchLoopOutcome):
            return authorized
        return self.dispatch(authorized)

    def authorize(
        self,
        command: ExecutePlannedExperimentCommand,
        *,
        persist_hook: Callable[..., None] | None = None,
    ) -> AuthorizedDispatch | ResearchLoopOutcome:
        request_id = new_opaque_id()
        correlation_id = new_opaque_id()
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            loaded = self._load_context(uow, command)
            if isinstance(loaded, ResearchLoopOutcome):
                return loaded
            experiment, hypothesis_id, source, issued = loaded
            plan_error = self._ensure_plan(uow, experiment, command.plan)
            if plan_error is not None:
                uow.rollback()
                return plan_error
            persisted = uow.experiment_plans.get(experiment.experiment_id)
            if persisted is None:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                )
            bound_plan = experiment_plan_from_record(persisted)
            existing_attempts = uow.execution_attempts.list_for_experiment(
                experiment.experiment_id
            )
            if existing_attempts:
                uow.rollback()
                return self._outcome_for_existing(experiment, hypothesis_id, existing_attempts[0])
            uow.experiments.set_execution_state(
                experiment.experiment_id,
                ExperimentExecutionState.AUTHORIZATION_CHECK.value,
            )
            try:
                capability_view = capability_view_for_plan(bound_plan)
            except CapabilityBindingError as exc:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.DISPATCH_DENIED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                    core_decision=ExecutionDecisionKind.DENY,
                    core_reason_code=_binding_reason(exc.reason_code),
                )
            http_decision = authorize_http_transaction_plan(
                bound_plan,
                command.compiled_scope,
                program_policy=command.program_policy,
            )
            if http_decision.input_rejected:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                    core_decision=ExecutionDecisionKind.DENY,
                    core_reason_code=http_decision.reason_code,
                )
            rate_limit_outcome = self._check_rate_limit(
                uow,
                command.program_policy,
                experiment,
                hypothesis_id,
                now,
            )
            if rate_limit_outcome is not None:
                uow.experiments.set_execution_state(
                    experiment.experiment_id,
                    ExperimentExecutionState.BLOCKED.value,
                )
                uow.commit()
                return rate_limit_outcome
            if bound_plan.required_capability in HTTP_SCOPE_CAPABILITIES:
                if command.compiled_scope is None or http_decision.scope_check is None:
                    core_scope: ScopeEvaluationInput = ScopeEvaluationInput(
                        matches=(),
                        ambiguous=False,
                    )
                else:
                    core_scope = scope_evaluation_from_compiled_check(
                        http_decision.scope_check,
                        command.compiled_scope,
                    )
            else:
                core_scope = command.scope
            decision = evaluate_execution(
                ExecutionRequest(
                    authorization_source=source,
                    scope=core_scope,
                    issued_budget=_issued_budget_from_record(issued),
                    budget_usage=_usage_or_ledger(command.budget_usage, uow, issued.budget_id),
                    requested_budget_id=bound_plan.requested_budget_id,
                    side_effect_level=bound_plan.side_effect_level,
                    requested_subject=bound_plan.target_reference,
                    capability=capability_view,
                    approval=command.approval,
                )
            )
            if (
                bound_plan.required_capability in HTTP_SCOPE_CAPABILITIES
                and not http_decision.accepted
            ):
                matched_ids = (
                    http_decision.scope_check.matched_rule_ids
                    if http_decision.scope_check is not None
                    else ()
                )
                decision = ExecutionDecision(
                    decision=ExecutionDecisionKind.DENY,
                    reason_code=http_decision.reason_code or ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED,
                    authorization_source_id=decision.authorization_source_id,
                    matched_scope_rule_ids=matched_ids,
                    budget_id=decision.budget_id,
                    side_effect_level=decision.side_effect_level,
                    approval_id=decision.approval_id,
                )
            session_ref = bound_plan.arguments.get("session_context_reference")
            loaded_session = None
            if isinstance(session_ref, str) and session_ref.strip():
                loaded_session = uow.session_contexts.get(session_ref)
            session_decision = bind_identity_session(
                bound_plan,
                identity_id=command.identity_id,
                identity=command.identity,
                profile=command.authentication_profile,
                session=loaded_session,
                secret_port=self._secret_port,
                now=now,
                research_run_id=experiment.research_run_id,
            )
            if session_decision.input_rejected:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                    core_decision=ExecutionDecisionKind.DENY,
                    core_reason_code=session_decision.reason_code,
                )
            if decision.decision is ExecutionDecisionKind.ALLOW and not session_decision.accepted:
                decision = ExecutionDecision(
                    decision=ExecutionDecisionKind.DENY,
                    reason_code=session_decision.reason_code or ReasonCode.SCHEMA_MISMATCH,
                    authorization_source_id=decision.authorization_source_id,
                    matched_scope_rule_ids=(),
                    budget_id=decision.budget_id,
                    side_effect_level=decision.side_effect_level,
                    approval_id=decision.approval_id,
                )
            audit_id = execution_decision_audit_id(request_id)
            uow.audit_events.insert(
                _execution_decision_audit(
                    audit_id=audit_id,
                    occurred_at=now,
                    actor_id=self._actor_id,
                    experiment_id=experiment.experiment_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    decision=decision,
                )
            )
            if decision.decision is ExecutionDecisionKind.DENY:
                terminal = _deny_experiment_state(decision.reason_code)
                uow.experiments.set_execution_state(experiment.experiment_id, terminal)
                uow.commit()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.DISPATCH_DENIED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=terminal,
                    core_decision=decision.decision,
                    core_reason_code=decision.reason_code,
                    authorization_decision_reference=audit_id,
                    request_id=None,
                    attempt_id=None,
                    attempt_state=None,
                )
            if decision.decision is ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW:
                uow.commit()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.HUMAN_REVIEW_REQUIRED,
                    hypothesis_id=hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=ExperimentExecutionState.AUTHORIZATION_CHECK.value,
                    core_decision=decision.decision,
                    core_reason_code=decision.reason_code,
                    authorization_decision_reference=audit_id,
                    request_id=None,
                    attempt_id=None,
                    attempt_state=None,
                )
            worker_request = _build_worker_request(
                experiment=experiment,
                plan=bound_plan,
                capability_view=capability_view,
                issued=issued,
                request_id=request_id,
                correlation_id=correlation_id,
                authorization_decision_reference=audit_id,
                network_envelope=http_decision.envelope,
                identity_id=(
                    session_decision.session.identity_id
                    if (
                        session_decision.session is not None
                        and bound_plan.required_capability == BROWSER_PAGE_CAPABILITY
                    )
                    else None
                ),
                required_user_agent=_required_user_agent(
                    uow,
                    experiment.research_run_id,
                ),
                required_headers=_required_program_headers(
                    uow,
                    experiment.research_run_id,
                ),
            )
            self._validator.validate_worker_request(worker_request)
            if (
                bound_plan.required_capability == "http.authentication"
                and command.identity is not None
                and command.authentication_profile is not None
            ):
                uow.session_contexts.insert(
                    authenticating_session_record(
                        bound_plan,
                        identity=command.identity,
                        profile=command.authentication_profile,
                        research_run_id=experiment.research_run_id,
                        now=now,
                    )
                )
            attempt_id = attempt_id_for(request_id)
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id=attempt_id,
                    request_id=request_id,
                    experiment_id=experiment.experiment_id,
                    research_run_id=experiment.research_run_id,
                    correlation_id=correlation_id,
                    worker_capability=bound_plan.required_capability,
                    action=bound_plan.action,
                    target_reference=bound_plan.target_reference,
                    budget_id=issued.budget_id,
                    side_effect_level=bound_plan.side_effect_level,
                    authorization_decision_reference=audit_id,
                    state=ExecutionAttemptState.AUTHORIZED.value,
                    created_at=now,
                    authorized_at=now,
                )
            )
            uow.experiments.set_execution_state(
                experiment.experiment_id,
                ExperimentExecutionState.READY.value,
            )
            if persist_hook is not None:
                persist_hook(
                    uow,
                    attempt_id=attempt_id,
                    experiment_id=experiment.experiment_id,
                )
            uow.commit()
        envelope = http_decision.envelope
        if envelope is not None:
            envelope = replace(
                envelope,
                authorization_decision_reference=audit_id,
            )
            worker_request = dict(worker_request)
            worker_request["network_envelope"] = envelope.to_mapping()
        return AuthorizedDispatch(
            experiment_id=experiment.experiment_id,
            hypothesis_id=hypothesis_id,
            request_id=request_id,
            attempt_id=attempt_id,
            correlation_id=correlation_id,
            authorization_decision_reference=audit_id,
            worker_request=worker_request,
            timeout_ms=issued.max_runtime_ms,
            core_decision=decision.decision,
            core_reason_code=decision.reason_code,
            resolved_secret_values=session_decision.resolved_secrets,
            network_envelope=envelope,
        )

    def dispatch(self, authorized: AuthorizedDispatch) -> ResearchLoopOutcome:
        now = self._clock.now()
        with self._uow_factory.open() as uow:
            attempt = uow.execution_attempts.get(authorized.attempt_id)
            experiment = uow.experiments.get(authorized.experiment_id)
            if attempt is None or experiment is None:
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=authorized.hypothesis_id,
                    experiment_id=authorized.experiment_id,
                    experiment_execution_state=(
                        experiment.execution_state if experiment is not None else "MISSING"
                    ),
                    request_id=authorized.request_id,
                    attempt_id=authorized.attempt_id,
                )
            if attempt.state != ExecutionAttemptState.AUTHORIZED.value:
                if attempt.state == ExecutionAttemptState.DISPATCHING.value:
                    return self._mark_unknown(uow, experiment, attempt, authorized.hypothesis_id)
                return self._outcome_for_existing(experiment, authorized.hypothesis_id, attempt)
            issued = uow.issued_budgets.get(attempt.budget_id)
            if issued is None:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=authorized.hypothesis_id,
                    experiment_id=authorized.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                    request_id=authorized.request_id,
                    attempt_id=authorized.attempt_id,
                )
            uow.execution_attempts.set_state(
                attempt.attempt_id,
                ExecutionAttemptState.DISPATCHING.value,
                dispatch_started_at=now,
            )
            uow.experiments.set_execution_state(
                experiment.experiment_id,
                ExperimentExecutionState.RUNNING.value,
            )
            try:
                request_amount = _request_consumption_amount(uow, attempt, issued)
                _record_dispatch_consumption(
                    uow, attempt, issued, occurred_at=now, request_amount=request_amount
                )
            except BudgetOverspendError:
                uow.rollback()
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.DISPATCH_DENIED,
                    hypothesis_id=authorized.hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=ExperimentExecutionState.BUDGET_EXHAUSTED.value,
                    request_id=authorized.request_id,
                    attempt_id=authorized.attempt_id,
                    attempt_state=ExecutionAttemptState.AUTHORIZED.value,
                    core_reason_code=ReasonCode.BUDGET_EXHAUSTED,
                )
            reserved_requests = request_amount
            worker_capability = attempt.worker_capability
            uow.commit()
        invocation = self._worker.invoke(
            _request_with_resolved_secrets(
                authorized.worker_request,
                authorized.resolved_secret_values,
                max_attempted_requests=(
                    reserved_requests if worker_capability == BROWSER_PAGE_CAPABILITY else None
                ),
            ),
            timeout_ms=authorized.timeout_ms,
        )
        return self._record_outcome(authorized, invocation)

    def _fail_closed_existing(self, experiment_id: str) -> ResearchLoopOutcome | None:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(experiment_id)
            if experiment is None:
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id="",
                    experiment_id=experiment_id,
                    experiment_execution_state="MISSING",
                )
            attempts = uow.execution_attempts.list_for_experiment(experiment_id)
            if not attempts:
                if experiment.execution_state in TERMINAL_EXPERIMENT_STATES:
                    return ResearchLoopOutcome(
                        status=ResearchLoopStatus.ALREADY_TERMINAL,
                        hypothesis_id=experiment.hypothesis_id,
                        experiment_id=experiment.experiment_id,
                        experiment_execution_state=experiment.execution_state,
                    )
                return None
            attempt = attempts[0]
            if attempt.state == ExecutionAttemptState.DISPATCHING.value:
                return self._mark_unknown(uow, experiment, attempt, experiment.hypothesis_id)
            results = uow.worker_results.list_for_experiment(experiment_id)
            if (
                attempt.state == ExecutionAttemptState.COMPLETED.value
                and any(item.status == "REAUTHORIZATION_REQUIRED" for item in results)
            ):
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.REAUTHORIZATION_REQUIRED,
                    hypothesis_id=experiment.hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                    authorization_decision_reference=attempt.authorization_decision_reference,
                    request_id=attempt.request_id,
                    attempt_id=attempt.attempt_id,
                    attempt_state=attempt.state,
                )
            return self._outcome_for_existing(experiment, experiment.hypothesis_id, attempt)

    def _mark_unknown(
        self,
        uow: UnitOfWork,
        experiment: ExperimentRecord,
        attempt: ExecutionAttemptRecord,
        hypothesis_id: str,
    ) -> ResearchLoopOutcome:
        now = self._clock.now()
        uow.execution_attempts.set_state(
            attempt.attempt_id,
            ExecutionAttemptState.UNKNOWN_OUTCOME.value,
            completed_at=now,
        )
        uow.commit()
        return ResearchLoopOutcome(
            status=ResearchLoopStatus.UNKNOWN_OUTCOME,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment.experiment_id,
            experiment_execution_state=experiment.execution_state,
            authorization_decision_reference=attempt.authorization_decision_reference,
            request_id=attempt.request_id,
            attempt_id=attempt.attempt_id,
            attempt_state=ExecutionAttemptState.UNKNOWN_OUTCOME.value,
        )

    def _record_outcome(
        self,
        authorized: AuthorizedDispatch,
        invocation: WorkerInvocationOutcome,
    ) -> ResearchLoopOutcome:
        now = self._clock.now()
        attempt_state, experiment_state, loop_status = _classify_invocation(invocation)
        ingestion: IngestionOutcome | None = None
        persist_failed = False
        try:
            with self._uow_factory.open() as uow:
                uow.execution_attempts.set_state(
                    authorized.attempt_id,
                    attempt_state,
                    completed_at=now,
                )
                uow.experiments.set_execution_state(
                    authorized.experiment_id,
                    experiment_state,
                )
                uow.commit()
        except PersistenceError:
            persist_failed = True
            loop_status = ResearchLoopStatus.INVOCATION_FAILED
            experiment_state = ExperimentExecutionState.RUNNING.value
            attempt_state = ExecutionAttemptState.DISPATCHING.value
        if (
            not persist_failed
            and invocation.invocation_status is InvocationStatus.COMPLETED
            and invocation.worker_result is not None
        ):
            ingestion = self._ingest.execute(authorized.worker_request, invocation)
            if ingestion.status is IngestionStatus.REJECTED_INVALID_INVOCATION:
                loop_status = ResearchLoopStatus.INVOCATION_FAILED
                experiment_state = ExperimentExecutionState.EXECUTION_FAILED.value
                attempt_state = ExecutionAttemptState.FAILED.value
                with self._uow_factory.open() as uow:
                    uow.execution_attempts.set_state(
                        authorized.attempt_id,
                        attempt_state,
                        completed_at=now,
                    )
                    uow.experiments.set_execution_state(
                        authorized.experiment_id,
                        experiment_state,
                    )
                    uow.commit()
            elif ingestion.status is IngestionStatus.NO_OBSERVATION:
                if loop_status is not ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
                    loop_status = ResearchLoopStatus.NO_OBSERVATION
            elif ingestion.status in {
                IngestionStatus.INGESTED,
                IngestionStatus.ALREADY_INGESTED,
            }:
                loop_status = ResearchLoopStatus.OBSERVATION_PRODUCED
        reauthorization_request = None
        if (
            loop_status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
            and invocation.worker_result is not None
        ):
            reauthorization_request = reauthorization_request_from_worker_result(
                authorized.worker_request,
                invocation.worker_result,
            )
        return ResearchLoopOutcome(
            status=loop_status,
            hypothesis_id=authorized.hypothesis_id,
            experiment_id=authorized.experiment_id,
            experiment_execution_state=experiment_state,
            core_decision=authorized.core_decision,
            core_reason_code=authorized.core_reason_code,
            authorization_decision_reference=authorized.authorization_decision_reference,
            request_id=authorized.request_id,
            attempt_id=authorized.attempt_id,
            attempt_state=attempt_state,
            invocation_status=invocation.invocation_status,
            worker_result_id=None if ingestion is None else ingestion.worker_result_id,
            observation_ids=() if ingestion is None else ingestion.observation_ids,
            ingestion_status=None if ingestion is None else ingestion.status,
            reauthorization_request=reauthorization_request,
            network_envelope=authorized.network_envelope,
        )

    def _load_context(
        self,
        uow: UnitOfWork,
        command: ExecutePlannedExperimentCommand,
    ) -> (
        tuple[
            ExperimentRecord,
            str,
            AuthorizationSourceView | None,
            IssuedBudgetRecord,
        ]
        | ResearchLoopOutcome
    ):
        experiment = uow.experiments.get(command.experiment_id)
        if experiment is None:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=command.plan.hypothesis_id,
                experiment_id=command.experiment_id,
                experiment_execution_state="MISSING",
            )
        if experiment.execution_state in TERMINAL_EXPERIMENT_STATES:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.ALREADY_TERMINAL,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        if experiment.hypothesis_id != command.plan.hypothesis_id:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        if experiment.budget_id != command.plan.requested_budget_id:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        hypothesis = uow.hypotheses.get(experiment.hypothesis_id)
        if hypothesis is None:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        run = uow.research_runs.get(experiment.research_run_id)
        if run is None:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        issued = uow.issued_budgets.get(experiment.budget_id)
        if issued is None:
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        source_record = uow.authorization_sources.get(run.authorization_source_id)
        return (
            experiment,
            hypothesis.hypothesis_id,
            _authorization_view(source_record),
            issued,
        )

    def _ensure_plan(
        self,
        uow: UnitOfWork,
        experiment: ExperimentRecord,
        plan,
    ) -> ResearchLoopOutcome | None:
        existing = uow.experiment_plans.get(experiment.experiment_id)
        if existing is None:
            if not plan.capability_version or not plan.capability_definition_fingerprint:
                return ResearchLoopOutcome(
                    status=ResearchLoopStatus.INPUT_REJECTED,
                    hypothesis_id=experiment.hypothesis_id,
                    experiment_id=experiment.experiment_id,
                    experiment_execution_state=experiment.execution_state,
                )
            uow.experiment_plans.insert(
                experiment_plan_record_for(experiment, plan, created_at=self._clock.now())
            )
            return None
        if not durable_plan_matches(existing, plan):
            return ResearchLoopOutcome(
                status=ResearchLoopStatus.INPUT_REJECTED,
                hypothesis_id=experiment.hypothesis_id,
                experiment_id=experiment.experiment_id,
                experiment_execution_state=experiment.execution_state,
            )
        return None

    def _outcome_for_existing(
        self,
        experiment: ExperimentRecord,
        hypothesis_id: str,
        attempt: ExecutionAttemptRecord,
    ) -> ResearchLoopOutcome:
        if attempt.state == ExecutionAttemptState.AUTHORIZED.value:
            status = ResearchLoopStatus.AUTHORIZED_NOT_DISPATCHED
        elif attempt.state == ExecutionAttemptState.UNKNOWN_OUTCOME.value:
            status = ResearchLoopStatus.UNKNOWN_OUTCOME
        elif experiment.execution_state in TERMINAL_EXPERIMENT_STATES:
            status = ResearchLoopStatus.ALREADY_TERMINAL
        else:
            status = ResearchLoopStatus.ALREADY_TERMINAL
        return ResearchLoopOutcome(
            status=status,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment.experiment_id,
            experiment_execution_state=experiment.execution_state,
            authorization_decision_reference=attempt.authorization_decision_reference,
            request_id=attempt.request_id,
            attempt_id=attempt.attempt_id,
            attempt_state=attempt.state,
        )

    def _check_rate_limit(
        self,
        uow: UnitOfWork,
        program_policy: ProgramPolicyView | None,
        experiment: ExperimentRecord,
        hypothesis_id: str,
        now: datetime,
    ) -> ResearchLoopOutcome | None:
        """Fail-closed rate-limit gate before Core execution. D4 clock injection."""
        if program_policy is None or program_policy.rate_limit_profile is None:
            return None
        profile_record = program_policy.rate_limit_profile
        profile = _rate_limit_profile_from_record(profile_record)
        attempts = uow.execution_attempts.list_for_research_run(experiment.research_run_id)
        authorized_times = tuple(
            attempt.authorized_at
            for attempt in attempts
            if attempt.authorized_at is not None
        )
        check = check_rate_limit(profile, authorized_times, now)
        if check.allowed:
            return None
        audit_id = execution_decision_audit_id(new_opaque_id())
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=audit_id,
                occurred_at=now,
                actor_id=self._actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type="RATE_LIMIT_DENIED",
                subject_type="experiment",
                subject_id=experiment.experiment_id,
                correlation_id=experiment.research_run_id,
                payload={
                    "profile_id": profile_record.profile_id,
                    "max_requests_per_window": profile.max_requests_per_window,
                    "window_seconds": profile.window_seconds,
                    "reason_code": check.reason_code.value,
                    "next_allowed_at": (
                        check.next_allowed_at.isoformat()
                        if check.next_allowed_at is not None
                        else None
                    ),
                },
            )
        )
        return ResearchLoopOutcome(
            status=ResearchLoopStatus.DISPATCH_DENIED,
            hypothesis_id=hypothesis_id,
            experiment_id=experiment.experiment_id,
            experiment_execution_state=ExperimentExecutionState.BLOCKED.value,
            core_decision=ExecutionDecisionKind.DENY,
            core_reason_code=check.reason_code,
            authorization_decision_reference=audit_id,
        )


def _authorization_view(
    record: AuthorizationSourceRecord | None,
) -> AuthorizationSourceView | None:
    if record is None:
        return None
    return AuthorizationSourceView(
        record.authorization_source_id,
        record.program_id,
        AuthorizationSourceState(record.state),
    )


def _issued_budget_from_record(record: IssuedBudgetRecord) -> IssuedBudget:
    return IssuedBudget(
        record.budget_id,
        record.max_requests,
        record.max_tool_calls,
        record.max_runtime_ms,
        record.max_concurrency,
    )


def _deny_experiment_state(reason: ReasonCode) -> str:
    if reason is ReasonCode.BUDGET_EXHAUSTED:
        return ExperimentExecutionState.BUDGET_EXHAUSTED.value
    return ExperimentExecutionState.BLOCKED.value


def _rate_limit_profile_from_record(record: RateLimitProfileRecord) -> RateLimitProfile:
    return RateLimitProfile(
        profile_id=record.profile_id,
        program_id=record.program_id,
        max_requests_per_window=record.max_requests_per_window,
        window_seconds=record.window_seconds,
    )


def _execution_decision_audit(
    *,
    audit_id: str,
    occurred_at,
    actor_id: str,
    experiment_id: str,
    correlation_id: str,
    request_id: str,
    decision: ExecutionDecision,
) -> AuditEventRecord:
    return AuditEventRecord(
        audit_event_id=audit_id,
        occurred_at=occurred_at,
        actor_id=actor_id,
        actor_type=ActorType.CONTROL_PLANE.value,
        event_type=AUDIT_EXECUTION_DECISION,
        subject_type="experiment",
        subject_id=experiment_id,
        payload={
            "decision": decision.decision.value,
            "reason_code": decision.reason_code.value,
            "authorization_source_id": decision.authorization_source_id,
            "matched_scope_rule_ids": list(decision.matched_scope_rule_ids),
            "budget_id": decision.budget_id,
            "side_effect_level": int(decision.side_effect_level),
            "approval_id": decision.approval_id,
            "request_id": request_id,
            "dispatched": decision.decision is ExecutionDecisionKind.ALLOW,
        },
        correlation_id=correlation_id,
    )


def _request_with_resolved_secrets(
    request: Mapping[str, Any],
    values: Mapping[str, str] | None,
    *,
    max_attempted_requests: int | None = None,
) -> Mapping[str, Any]:
    if not values and max_attempted_requests is None:
        return request
    payload = dict(request)
    if values:
        payload["resolved_secret_values"] = dict(values)
    if max_attempted_requests is not None:
        payload["max_attempted_requests"] = max_attempted_requests
    return payload


def _build_worker_request(
    *,
    experiment: ExperimentRecord,
    plan: ExperimentPlan,
    capability_view,
    issued: IssuedBudgetRecord,
    request_id: str,
    correlation_id: str,
    authorization_decision_reference: str,
    network_envelope: AuthorizedNetworkEnvelope | None = None,
    identity_id: str | None = None,
    required_user_agent: str | None = None,
    required_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    arguments = dict(plan.arguments)
    if identity_id is not None:
        arguments["identity_id"] = identity_id

    existing_headers = arguments.get("headers") or {}
    if not isinstance(existing_headers, Mapping):
        raise ValueError("worker request headers must be an object")
    headers = dict(existing_headers)

    def set_authoritative_header(name: str, value: str) -> None:
        for existing in list(headers):
            if (
                isinstance(existing, str)
                and existing.lower() == name.lower()
            ):
                del headers[existing]
        headers[name] = value

    if required_user_agent is not None:
        set_authoritative_header("User-Agent", required_user_agent)

    for name, value in _validate_required_program_headers(
        required_headers
    ).items():
        set_authoritative_header(name, value)

    if required_user_agent is not None or required_headers:
        arguments["headers"] = headers
    payload: dict[str, Any] = {
        "contract_version": WORKER_CONTRACT_VERSION,
        "correlation": {
            "correlation_id": correlation_id,
            "research_run_id": experiment.research_run_id,
            "experiment_id": experiment.experiment_id,
            "request_id": request_id,
        },
        "worker_capability": plan.required_capability,
        "action": plan.action,
        "target_reference": plan.target_reference,
        "authorization_decision_reference": authorization_decision_reference,
        "execution_budget": {
            "budget_id": issued.budget_id,
            "max_requests": issued.max_requests,
            "max_tool_calls": issued.max_tool_calls,
            "max_runtime_ms": issued.max_runtime_ms,
            "max_concurrency": issued.max_concurrency,
        },
        "side_effect_level": plan.side_effect_level,
        "capability_version": capability_view.capability_version,
        "capability_definition_fingerprint": capability_view.definition_fingerprint,
        "secret_references": [],
        "arguments": arguments,
    }
    if network_envelope is not None:
        payload["network_envelope"] = network_envelope.to_mapping()
    return payload


def _required_user_agent(uow, research_run_id: str) -> str | None:
    run = uow.research_runs.get(research_run_id)
    if run is None:
        raise ValueError("research run not found while resolving execution policy")
    policy = uow.program_policies.get(run.program_id)
    if policy is None or not policy.action_policy:
        return None
    value = policy.action_policy.get("required_user_agent")
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(marker in value for marker in ("\r", "\n", "\x00"))
        or len(value) > 128
    ):
        raise ValueError("required_user_agent policy is invalid")
    return value.strip()


_PROGRAM_HEADER_BLOCKLIST = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "upgrade",
        "origin",
        "referer",
        "user-agent",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)

_PROGRAM_HEADER_SENSITIVE_MARKERS = (
    "api-key",
    "apikey",
    "password",
    "secret",
    "token",
    "credential",
)


def _validate_required_program_headers(
    value: Mapping[str, str] | None,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("required_headers policy is invalid")
    if len(value) > 8:
        raise ValueError("required_headers policy exceeds limit")

    result: dict[str, str] = {}
    seen: set[str] = set()

    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("required_headers policy is invalid")

        name = raw_name.strip()
        header_value = raw_value.strip()
        lower = name.lower()

        if (
            not name
            or len(name) > 64
            or any(not (char.isalnum() or char == "-") for char in name)
            or lower in _PROGRAM_HEADER_BLOCKLIST
            or any(marker in lower for marker in _PROGRAM_HEADER_SENSITIVE_MARKERS)
            or lower in seen
            or not header_value
            or len(header_value) > 256
            or any(marker in header_value for marker in ("\r", "\n", "\x00"))
        ):
            raise ValueError("required_headers policy is invalid")

        seen.add(lower)
        result[name] = header_value

    return result


def _required_program_headers(
    uow,
    research_run_id: str,
) -> dict[str, str]:
    run = uow.research_runs.get(research_run_id)
    if run is None:
        raise ValueError(
            "research run not found while resolving execution policy"
        )

    policy = uow.program_policies.get(run.program_id)

    if policy is None or not policy.action_policy:
        return {}

    return _validate_required_program_headers(
        policy.action_policy.get("required_headers")
    )


def _classify_invocation(
    invocation: WorkerInvocationOutcome,
) -> tuple[str, str, ResearchLoopStatus]:
    if invocation.invocation_status is InvocationStatus.COMPLETED:
        result = invocation.worker_result if isinstance(invocation.worker_result, Mapping) else {}
        if result.get("status") == "REAUTHORIZATION_REQUIRED":
            return (
                ExecutionAttemptState.COMPLETED.value,
                ExperimentExecutionState.AUTHORIZATION_CHECK.value,
                ResearchLoopStatus.REAUTHORIZATION_REQUIRED,
            )
        if result.get("status") == "BUDGET_EXHAUSTED":
            return (
                ExecutionAttemptState.COMPLETED.value,
                ExperimentExecutionState.BUDGET_EXHAUSTED.value,
                ResearchLoopStatus.DISPATCH_DENIED,
            )
        return (
            ExecutionAttemptState.COMPLETED.value,
            ExperimentExecutionState.EXECUTION_SUCCEEDED.value,
            ResearchLoopStatus.NO_OBSERVATION,
        )
    if invocation.invocation_status is InvocationStatus.TIMED_OUT:
        return (
            ExecutionAttemptState.TIMED_OUT.value,
            ExperimentExecutionState.EXECUTION_FAILED.value,
            ResearchLoopStatus.INVOCATION_FAILED,
        )
    if invocation.invocation_status is InvocationStatus.CANCELLED:
        return (
            ExecutionAttemptState.CANCELLED.value,
            ExperimentExecutionState.CANCELLED.value,
            ResearchLoopStatus.INVOCATION_FAILED,
        )
    if invocation.invocation_status in FAILED_INVOCATION_STATUSES:
        return (
            ExecutionAttemptState.FAILED.value,
            ExperimentExecutionState.EXECUTION_FAILED.value,
            ResearchLoopStatus.INVOCATION_FAILED,
        )
    return (
        ExecutionAttemptState.UNKNOWN_OUTCOME.value,
        ExperimentExecutionState.RUNNING.value,
        ResearchLoopStatus.UNKNOWN_OUTCOME,
    )


def _usage_or_ledger(
    provided: BudgetUsage | None, uow: UnitOfWork, budget_id: str
) -> BudgetUsage:
    if provided is not None:
        return provided
    return usage_from_consumptions(uow.budget_consumptions.list_for_budget(budget_id))


def _request_consumption_amount(
    uow: UnitOfWork,
    attempt: ExecutionAttemptRecord,
    issued: IssuedBudgetRecord,
) -> int:
    if attempt.worker_capability == HTTP_RAW_EXCHANGE_CAPABILITY:
        plan = uow.experiment_plans.get(attempt.experiment_id)
        profile = None
        if plan is not None and isinstance(plan.arguments, Mapping):
            profile = plan.arguments.get("framing_profile")
        return 2 if profile == "http1_connection_reuse" else 1
    if attempt.worker_capability != BROWSER_PAGE_CAPABILITY:
        return 1
    usage = usage_from_consumptions(uow.budget_consumptions.list_for_budget(issued.budget_id))
    remaining = remaining_for_resource(issued, usage, "REQUEST")
    if remaining < 1:
        raise BudgetOverspendError("consumption would exceed issued allowance")
    return min(remaining, BROWSER_PAGE_MAX_NETWORK_REQUESTS)


def _record_dispatch_consumption(
    uow: UnitOfWork,
    attempt: ExecutionAttemptRecord,
    issued: IssuedBudgetRecord,
    *,
    occurred_at,
    request_amount: int = 1,
) -> None:
    for resource_type, unit, amount in (
        ("WORKER_INVOCATION", "count", 1),
        ("REQUEST", "count", request_amount),
    ):
        uow.budget_consumptions.insert_within_allowance(
            BudgetConsumptionRecord(
                consumption_id=new_opaque_id(),
                budget_id=issued.budget_id,
                research_run_id=attempt.research_run_id,
                resource_type=resource_type,
                amount=amount,
                unit=unit,
                occurred_at=occurred_at,
                provenance="execute_planned_experiment.dispatch",
                experiment_id=attempt.experiment_id,
                request_id=attempt.request_id,
            ),
            issued,
        )


def _binding_reason(code: str) -> ReasonCode:
    if code in ReasonCode.__members__:
        return ReasonCode[code]
    if code in {
        "MISSING_REQUIRED_ARGUMENT",
        "UNEXPECTED_ARGUMENT",
        "INVALID_ARGUMENT_TYPE",
    }:
        return ReasonCode.SCHEMA_MISMATCH
    return ReasonCode.UNKNOWN_CAPABILITY
