"""Preflight readiness aggregation for starting a research run.

Aggregates existing Core/data checks (authorization, scope, budget,
orchestration recoverability, lease conflict) -- all read fresh from the
authoritative PostgreSQL SoR on every call -- with runtime signals the
caller must obtain immediately before calling `execute()` (worker health,
model runtime readiness, schema-head status), since those are backend- and
provider-specific probes this application-layer use case must not itself
depend on.

Preflight is readiness, not research truth:
- it never authorizes anything by itself,
- a READY_TO_START report is not permanent and does not replace the fresh
  Core authorization every Worker dispatch still performs independently,
- ambiguous or missing input always resolves to NOT_READY, never to a
  silent "proceed" (master-plan authorization-boundary invariant).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from zest.application.errors import ApplicationError
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.program_research_context import (
    ProgramResearchContext,
    load_program_research_context,
)
from zest.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationResolution,
)
from zest.core.authorization import AuthorizationSourceView, check_authorization
from zest.core.budget import IssuedBudget, check_budget
from zest.core.enums import AuthorizationSourceState, ScopeDecision
from zest.core.scope_compiler import evaluate_scope_candidate
from zest.data.budget_ledger import ledger_totals
from zest.data.records import (
    AuthorizationSourceRecord,
    BudgetConsumptionRecord,
    IssuedBudgetRecord,
    ResearchOrchestrationRecord,
    ResearchRunRecord,
)
from zest.platform.health import ComponentHealth, HealthCheck
from zest.platform.url_normalize import normalize_url
from zest.research.orchestration import TERMINAL_ORCHESTRATION_STATES
from zest.research.routing import RuntimeCandidate

_BLOCKING_RECONCILIATION_RESOLUTIONS = frozenset(
    {
        ReconciliationResolution.UNKNOWN_OUTCOME,
        ReconciliationResolution.REQUIRE_HUMAN_REVIEW,
        ReconciliationResolution.INTEGRITY_ERROR,
    }
)


class PreflightStatus(Enum):
    READY_TO_START = "READY_TO_START"
    NOT_READY = "NOT_READY"


class PreflightCheckName(Enum):
    DATABASE_REACHABLE = "DATABASE_REACHABLE"
    SCHEMA_AT_EXPECTED_HEAD = "SCHEMA_AT_EXPECTED_HEAD"
    RUN_CONFIGURATION_EXISTS = "RUN_CONFIGURATION_EXISTS"
    AUTHORIZATION_SOURCE_ACTIVE = "AUTHORIZATION_SOURCE_ACTIVE"
    SCOPE_COMPILES = "SCOPE_COMPILES"
    TARGET_IN_SCOPE = "TARGET_IN_SCOPE"
    BUDGET_AVAILABLE = "BUDGET_AVAILABLE"
    ORCHESTRATION_RECOVERABLE = "ORCHESTRATION_RECOVERABLE"
    NO_CONFLICTING_LEASE = "NO_CONFLICTING_LEASE"
    WORKER_CAPABILITIES_PRESENT = "WORKER_CAPABILITIES_PRESENT"
    WORKER_RUNTIME_HEALTHY = "WORKER_RUNTIME_HEALTHY"
    MODEL_RUNTIME_READY = "MODEL_RUNTIME_READY"


@dataclass(frozen=True)
class PreflightCheckResult:
    name: PreflightCheckName
    passed: bool
    detail: str


@dataclass(frozen=True)
class SchemaHealthInput:
    """Freshly-computed schema-head comparison, obtained by the caller.

    Alembic revision tracking is a PostgreSQL-backend detail; the
    application layer must not depend on it directly, so this use case
    consumes an already-computed result rather than importing Alembic
    itself.
    """

    at_expected_head: bool
    detail: str = ""


@dataclass(frozen=True)
class WorkerReadinessInput:
    """Freshly-probed worker health, obtained by the caller immediately
    before this call (e.g. via `platform.worker_health.probe_local_python_worker`).
    A cached/stale health snapshot must not be reused here."""

    health: HealthCheck
    available_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ModelReadinessInput:
    """Freshly-obtained model runtime readiness, obtained by the caller.

    `candidate=None` means no runtime is currently selected/available and
    fails closed rather than being silently skipped.
    """

    candidate: RuntimeCandidate | None
    health: HealthCheck


@dataclass(frozen=True)
class PreflightCommand:
    research_run_id: str
    target_reference: str
    schema: SchemaHealthInput
    worker: WorkerReadinessInput
    model: ModelReadinessInput
    required_worker_capabilities: frozenset[str] = frozenset()
    requesting_owner_runtime_instance_id: str | None = None
    check_reconciliation: bool = True


@dataclass(frozen=True)
class PreflightReport:
    research_run_id: str
    status: PreflightStatus
    checks: tuple[PreflightCheckResult, ...]
    generated_at: datetime

    @property
    def is_ready(self) -> bool:
        return self.status is PreflightStatus.READY_TO_START

    @property
    def reasons(self) -> tuple[str, ...]:
        """Human-readable reasons for every failing check. Empty when ready."""

        return tuple(
            f"{check.name.value}: {check.detail}" for check in self.checks if not check.passed
        )


class Preflight:
    """Aggregates existing readiness signals into one go/no-go report.

    Reads the authoritative SoR fresh on every call. Never mutates state.
    Never authorizes anything; `start()` and every subsequent Worker
    dispatch still perform their own independent Core authorization.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: PreflightCommand) -> PreflightReport:
        now = self._clock.now()
        checks: list[PreflightCheckResult] = []

        try:
            with self._uow_factory.open() as uow:
                run = uow.research_runs.get(command.research_run_id)
                source_record = (
                    uow.authorization_sources.get(run.authorization_source_id)
                    if run is not None
                    else None
                )
                context = (
                    load_program_research_context(uow, run.program_id, now=now)
                    if run is not None
                    else None
                )
                issued_budgets = (
                    uow.issued_budgets.list_for_research_run(run.research_run_id)
                    if run is not None
                    else []
                )
                consumptions = (
                    uow.budget_consumptions.list_for_research_run(run.research_run_id)
                    if run is not None
                    else []
                )
                orchestration = (
                    uow.research_orchestrations.get(run.research_run_id)
                    if run is not None
                    else None
                )
                uow.rollback()
        except ApplicationError:
            raise
        except Exception as exc:  # the reachability probe itself, not a business rule
            checks.append(
                PreflightCheckResult(
                    PreflightCheckName.DATABASE_REACHABLE,
                    False,
                    f"database read failed during preflight: {exc}",
                )
            )
            return PreflightReport(
                research_run_id=command.research_run_id,
                status=PreflightStatus.NOT_READY,
                checks=tuple(checks),
                generated_at=now,
            )

        checks.append(
            PreflightCheckResult(
                PreflightCheckName.DATABASE_REACHABLE, True, "system of record reachable"
            )
        )
        checks.append(_schema_head_check(command.schema))
        checks.append(_run_configuration_check(run))
        if run is not None:
            checks.append(_authorization_check(source_record, run, now=now))
            checks.extend(_scope_checks(context, command.target_reference))
            checks.append(_budget_check(issued_budgets, consumptions))
            checks.append(_orchestration_terminal_check(orchestration))
            checks.append(
                _lease_conflict_check(
                    orchestration, now, command.requesting_owner_runtime_instance_id
                )
            )
            if command.check_reconciliation:
                checks.append(self._reconciliation_check(run.research_run_id))
        checks.append(_worker_capability_check(command))
        checks.append(_worker_health_check(command.worker))
        checks.append(_model_readiness_check(command.model))

        status = (
            PreflightStatus.READY_TO_START
            if all(check.passed for check in checks)
            else PreflightStatus.NOT_READY
        )
        return PreflightReport(
            research_run_id=command.research_run_id,
            status=status,
            checks=tuple(checks),
            generated_at=now,
        )

    def _reconciliation_check(self, research_run_id: str) -> PreflightCheckResult:
        try:
            result = ReconcileResearchRun(self._uow_factory, clock=self._clock).execute(
                ReconcileResearchRunCommand(research_run_id=research_run_id)
            )
        except ApplicationError as exc:
            return PreflightCheckResult(
                PreflightCheckName.ORCHESTRATION_RECOVERABLE,
                False,
                f"reconciliation could not run: {exc}",
            )
        blockers = [
            item for item in result.items if item.resolution in _BLOCKING_RECONCILIATION_RESOLUTIONS
        ]
        if blockers:
            detail = "; ".join(
                f"{item.subject_type}:{item.subject_id} {item.resolution.value}"
                for item in blockers
            )
            return PreflightCheckResult(
                PreflightCheckName.ORCHESTRATION_RECOVERABLE,
                False,
                f"reconciliation requires attention before start: {detail}",
            )
        return PreflightCheckResult(
            PreflightCheckName.ORCHESTRATION_RECOVERABLE,
            True,
            "no blocking reconciliation items",
        )


def _schema_head_check(schema: SchemaHealthInput) -> PreflightCheckResult:
    if not schema.at_expected_head:
        return PreflightCheckResult(
            PreflightCheckName.SCHEMA_AT_EXPECTED_HEAD,
            False,
            schema.detail or "schema is not at the expected migration head",
        )
    return PreflightCheckResult(
        PreflightCheckName.SCHEMA_AT_EXPECTED_HEAD, True, "schema at expected migration head"
    )


def _run_configuration_check(run: ResearchRunRecord | None) -> PreflightCheckResult:
    if run is None:
        return PreflightCheckResult(
            PreflightCheckName.RUN_CONFIGURATION_EXISTS, False, "research run not found"
        )
    return PreflightCheckResult(
        PreflightCheckName.RUN_CONFIGURATION_EXISTS, True, "research run persisted"
    )


def _authorization_check(
    source_record: AuthorizationSourceRecord | None,
    run: ResearchRunRecord,
    *,
    now: datetime,
) -> PreflightCheckResult:
    if source_record is None:
        return PreflightCheckResult(
            PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE,
            False,
            "authorization source not found",
        )
    source = AuthorizationSourceView(
        source_record.authorization_source_id,
        source_record.program_id,
        AuthorizationSourceState(source_record.state),
    )
    decision = check_authorization(source)
    if not decision.allowed_to_continue:
        return PreflightCheckResult(
            PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE, False, decision.reason_code.value
        )
    if source_record.program_id != run.program_id:
        return PreflightCheckResult(
            PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE,
            False,
            "authorization source program does not match research run program",
        )
    if source_record.effective_from is not None and now < source_record.effective_from:
        return PreflightCheckResult(
            PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE,
            False,
            "authorization source is not yet effective",
        )
    if source_record.effective_until is not None and now > source_record.effective_until:
        return PreflightCheckResult(
            PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE,
            False,
            "authorization source has expired",
        )
    return PreflightCheckResult(
        PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE,
        True,
        "active and within its effective window",
    )


def _scope_checks(
    context: ProgramResearchContext | None, target_reference: str
) -> list[PreflightCheckResult]:
    if context is None:
        return [
            PreflightCheckResult(
                PreflightCheckName.SCOPE_COMPILES, False, "program research context unavailable"
            ),
            PreflightCheckResult(
                PreflightCheckName.TARGET_IN_SCOPE, False, "scope could not be evaluated"
            ),
        ]
    compiled = context.compiled_scope
    scope_compiles = PreflightCheckResult(
        PreflightCheckName.SCOPE_COMPILES,
        True,
        f"{len(compiled.rules)} compiled scope rule(s)",
    )
    candidate = normalize_url(target_reference)
    if candidate.normalization_error is not None:
        return [
            scope_compiles,
            PreflightCheckResult(
                PreflightCheckName.TARGET_IN_SCOPE,
                False,
                f"target does not normalize: {candidate.normalization_error}",
            ),
        ]
    check = evaluate_scope_candidate(candidate, compiled)
    if check.decision is ScopeDecision.ALLOW:
        return [
            scope_compiles,
            PreflightCheckResult(
                PreflightCheckName.TARGET_IN_SCOPE,
                True,
                f"allowed by rule(s) {check.matched_rule_ids}",
            ),
        ]
    return [
        scope_compiles,
        PreflightCheckResult(
            PreflightCheckName.TARGET_IN_SCOPE,
            False,
            f"{check.decision.value}: {check.reason_code.value}",
        ),
    ]


def _budget_check(
    issued_budgets: list[IssuedBudgetRecord],
    consumptions: list[BudgetConsumptionRecord],
) -> PreflightCheckResult:
    if not issued_budgets:
        return PreflightCheckResult(
            PreflightCheckName.BUDGET_AVAILABLE, False, "no issued budget for this research run"
        )
    issued_record = issued_budgets[0]
    issued = IssuedBudget(
        issued_record.budget_id,
        issued_record.max_requests,
        issued_record.max_tool_calls,
        issued_record.max_runtime_ms,
        issued_record.max_concurrency,
    )
    usage = ledger_totals(consumptions).to_budget_usage()
    decision = check_budget(issued, usage, issued_record.budget_id)
    return PreflightCheckResult(
        PreflightCheckName.BUDGET_AVAILABLE, decision.allowed_to_continue, decision.reason_code.value
    )


def _orchestration_terminal_check(
    orchestration: ResearchOrchestrationRecord | None,
) -> PreflightCheckResult:
    if orchestration is None:
        return PreflightCheckResult(
            PreflightCheckName.ORCHESTRATION_RECOVERABLE,
            True,
            "no prior orchestration checkpoint; fresh start",
        )
    if orchestration.state in TERMINAL_ORCHESTRATION_STATES:
        return PreflightCheckResult(
            PreflightCheckName.ORCHESTRATION_RECOVERABLE,
            False,
            f"orchestration is already terminal ({orchestration.state}); "
            "this research_run_id cannot be restarted",
        )
    return PreflightCheckResult(
        PreflightCheckName.ORCHESTRATION_RECOVERABLE,
        True,
        f"orchestration checkpoint is non-terminal ({orchestration.state})",
    )


def _lease_conflict_check(
    orchestration: ResearchOrchestrationRecord | None,
    now: datetime,
    requesting_owner_runtime_instance_id: str | None,
) -> PreflightCheckResult:
    """Advisory only: the authoritative decision is `acquire_lease()`'s own
    PostgreSQL-clock-anchored CAS at actual supervisor-attach time. This
    check exists to fail closed early and visibly, not to replace it."""

    if orchestration is None or orchestration.owner_runtime_instance_id is None:
        return PreflightCheckResult(
            PreflightCheckName.NO_CONFLICTING_LEASE, True, "no active lease"
        )
    if orchestration.lease_expires_at is not None and orchestration.lease_expires_at < now:
        return PreflightCheckResult(
            PreflightCheckName.NO_CONFLICTING_LEASE, True, "existing lease has expired"
        )
    if (
        requesting_owner_runtime_instance_id is not None
        and orchestration.owner_runtime_instance_id == requesting_owner_runtime_instance_id
    ):
        return PreflightCheckResult(
            PreflightCheckName.NO_CONFLICTING_LEASE,
            True,
            "lease is already held by this runtime instance",
        )
    return PreflightCheckResult(
        PreflightCheckName.NO_CONFLICTING_LEASE,
        False,
        f"run is actively leased by runtime instance {orchestration.owner_runtime_instance_id!r}",
    )


def _worker_capability_check(command: PreflightCommand) -> PreflightCheckResult:
    missing = sorted(command.required_worker_capabilities - command.worker.available_capabilities)
    if missing:
        return PreflightCheckResult(
            PreflightCheckName.WORKER_CAPABILITIES_PRESENT,
            False,
            f"missing required worker capabilities: {missing}",
        )
    return PreflightCheckResult(
        PreflightCheckName.WORKER_CAPABILITIES_PRESENT, True, "all required capabilities present"
    )


def _worker_health_check(worker: WorkerReadinessInput) -> PreflightCheckResult:
    healthy = worker.health.health is ComponentHealth.HEALTHY
    return PreflightCheckResult(
        PreflightCheckName.WORKER_RUNTIME_HEALTHY,
        healthy,
        f"{worker.health.component}: {worker.health.health.value} - {worker.health.detail}",
    )


def _model_readiness_check(model: ModelReadinessInput) -> PreflightCheckResult:
    if model.candidate is None:
        return PreflightCheckResult(
            PreflightCheckName.MODEL_RUNTIME_READY,
            False,
            "no model runtime candidate is currently selected/available",
        )
    if model.health.health is not ComponentHealth.HEALTHY:
        return PreflightCheckResult(
            PreflightCheckName.MODEL_RUNTIME_READY,
            False,
            f"{model.health.component}: {model.health.health.value} - {model.health.detail}",
        )
    if not (model.candidate.available and model.candidate.authenticated):
        return PreflightCheckResult(
            PreflightCheckName.MODEL_RUNTIME_READY,
            False,
            "model runtime is not available/authenticated",
        )
    if not model.candidate.structured_output_compatible:
        return PreflightCheckResult(
            PreflightCheckName.MODEL_RUNTIME_READY,
            False,
            "model runtime is not structured-output compatible",
        )
    return PreflightCheckResult(
        PreflightCheckName.MODEL_RUNTIME_READY,
        True,
        "model runtime available, authenticated, structured-output compatible, and healthy",
    )
