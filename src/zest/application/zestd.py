"""Persistent zestd runtime owner.

Owns process lifetime, RuntimeInstance identity, lease acquire/renew/release,
supervisor threads, Preflight invocation, Operator API hosting, and recovery
classification coordination.

Does not own opportunity selection, hypothesis generation, Evidence/Candidate/
Verification/Finding decisions, scope, budget policy, authorization, or
arbitrary Worker dispatch. Asks AutonomousResearchController and Application
use cases. PostgreSQL remains the sole authoritative state.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    CONTROL_PLANE_ACTOR_ID,
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from zest.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
    RuntimeRecoveryDecision,
)
from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.lease_fencing import (
    LeaseFencedWorkerPort,
    SingleRunFencedUowFactory,
)
from zest.application.local_run_supervisor import LocalRunSupervisorRegistry
from zest.application.operator_errors import OperatorError, OperatorErrorCode
from zest.application.operator_run_read_model import (
    build_program_list,
    build_run_detail,
    build_run_list,
)
from zest.application.operator_hq_read_model import build_hq_run_analysis
from zest.application.observer import ObserverService
from zest.application.orchestration_lease import LeaseConfig
from zest.application.persist_preflight import (
    configuration_fingerprint_for_command,
    persist_preflight_report,
    preflight_record_to_mapping,
)
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.preflight import (
    ModelReadinessInput,
    Preflight,
    PreflightCommand,
    PreflightCheckName,
    PreflightReport,
    PreflightStatus,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.application.reconstruct_run_command import (
    allocate_daily_budget_if_required,
    reconstruct_start_command,
)
from zest.application.runtime_instance import (
    ENGINE_VERSION,
    heartbeat_runtime_instance,
    mark_runtime_status,
    register_runtime_instance,
)
from zest.core.enums import ActorType, ReasonCode
from zest.data.errors import (
    DatabaseUnavailableError,
    LeaseFencingError,
    PersistenceConflictError,
    PersistenceError,
)
from zest.data.records import AuditEventRecord, RuntimeInstanceRecord
from zest.platform.worker import WorkerPort
from zest.research.model_port import ModelPort
from zest.tools.capabilities import BROWSER_PAGE_CAPABILITY
from zest.research.orchestration import OrchestrationState
from zest.safe_data import redact_secret_keys

LOGGER = logging.getLogger("zest.zestd")

ReadinessProbe = Callable[[], object]


def _log(event: str, **fields: object) -> None:
    payload = redact_secret_keys({"event": event, **fields})
    LOGGER.info("%s", payload)


class ZestdRuntime:
    """One OS process that owns local supervisors for leased runs."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        model: ModelPort,
        *,
        fallback_models: tuple[ModelPort, ...] = (),
        clock: Clock | None = None,
        lease_config: LeaseConfig | None = None,
        cadence_seconds: float = 0.25,
        probe_schema: Callable[[], SchemaHealthInput],
        probe_worker: Callable[[], WorkerReadinessInput],
        probe_model: Callable[[], ModelReadinessInput],
        required_worker_capabilities: frozenset[str] = frozenset(),
        engine_version: str = ENGINE_VERSION,
        host_identity: str = "zestd",
        process_id: str = "0",
        environment_name: str = "local",
        observer_service: ObserverService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._worker = worker
        self._model = model
        self._fallback_models = tuple(fallback_models)
        self._clock = clock or SystemClock()
        self._lease_config = lease_config or LeaseConfig()
        self._cadence_seconds = cadence_seconds
        self._probe_schema = probe_schema
        self._probe_worker = probe_worker
        self._probe_model = probe_model
        self._required_worker_capabilities = required_worker_capabilities
        self._engine_version = engine_version
        self._host_identity = host_identity
        self._process_id = process_id
        self._environment_name = environment_name
        self._observer = observer_service or ObserverService()
        self._instance: RuntimeInstanceRecord | None = None
        self._registry: LocalRunSupervisorRegistry | None = None
        self._unfenced_controller = AutonomousResearchController(
            uow_factory,
            worker,
            model,
            fallback_models=self._fallback_models,
            clock=self._clock,
        )
        self._preflight = Preflight(uow_factory, clock=self._clock)
        self._classifier = ClassifyRuntimeRecovery(uow_factory)
        self._stop = threading.Event()
        self._pg_unavailable = False
        self._lock = threading.Lock()
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def runtime_instance_id(self) -> str:
        if self._instance is None:
            raise ApplicationError("zestd has not registered a runtime instance")
        return self._instance.runtime_instance_id

    @property
    def pg_unavailable(self) -> bool:
        return self._pg_unavailable

    def is_supervising(self, research_run_id: str) -> bool:
        return self._registry is not None and self._registry.is_active(research_run_id)

    def start_process(self) -> RuntimeInstanceRecord:
        self._instance = register_runtime_instance(
            self._uow_factory,
            host_identity=self._host_identity,
            process_id=self._process_id,
            clock=self._clock,
            engine_version=self._engine_version,
            capabilities_summary={"operator_api": True, "local_supervisor": True},
        )
        self._registry = LocalRunSupervisorRegistry(
            owner_runtime_instance_id=self._instance.runtime_instance_id,
            lease_config=self._lease_config,
        )
        self._instance = mark_runtime_status(
            self._uow_factory,
            self._instance.runtime_instance_id,
            "RUNNING",
            clock=self._clock,
        )
        self._stop.clear()
        self._pg_unavailable = False
        _log(
            "runtime.start",
            runtime_instance_id=self._instance.runtime_instance_id,
            engine_version=self._engine_version,
        )
        self.recover_runs()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="zestd-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        return self._instance

    def recover_runs(self) -> tuple[RuntimeRecoveryDecision, ...]:
        if self._pg_unavailable:
            return ()
        try:
            with self._uow_factory.open() as uow:
                recoverable = uow.research_orchestrations.list_recoverable()
                uow.rollback()
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)
            return ()
        decisions: list[RuntimeRecoveryDecision] = []
        for record in recoverable:
            if self._lease_held_by_other(record):
                continue
            try:
                decision = self._classifier.execute(record.research_run_id)
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
                break
            decisions.append(decision)
            _log(
                "runtime.recovery",
                runtime_instance_id=self.runtime_instance_id,
                research_run_id=record.research_run_id,
                recovery_classification=decision.action.value,
                reason=decision.reason,
                lease_epoch=record.lease_epoch,
            )
            if decision.action in {
                RuntimeRecoveryAction.SAFE_RESUME,
                RuntimeRecoveryAction.SAFE_RETRY_AFTER_REAUTHORIZATION,
            }:
                self._attach_supervisor(record.research_run_id, recovery=True)
            elif decision.action in {
                RuntimeRecoveryAction.RECONCILIATION_REQUIRED,
                RuntimeRecoveryAction.HUMAN_REQUIRED,
            }:
                self._hold_for_human(record.research_run_id, decision)
        return tuple(decisions)

    def start_run(self, research_run_id: str) -> OrchestrationTickResult:
        self._require_pg()
        if self.is_supervising(research_run_id):
            return self._status_from_sor(research_run_id)
        try:
            return self._start_run_locked(research_run_id)
        except PersistenceConflictError:
            self._attach_supervisor(research_run_id, recovery=True)
            return self._status_from_sor(research_run_id)
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)
            raise OperatorError(
                OperatorErrorCode.DATABASE_UNAVAILABLE,
                "postgresql unavailable; refusing new authoritative work",
            ) from exc

    def _start_run_locked(self, research_run_id: str) -> OrchestrationTickResult:
        with self._uow_factory.open() as uow:
            existing = uow.research_orchestrations.get(research_run_id)
            uow.rollback()
        if existing is not None:
            if existing.state in {
                OrchestrationState.COMPLETED.value,
                OrchestrationState.BUDGET_EXHAUSTED.value,
                OrchestrationState.FAILED_OPERATIONAL.value,
            }:
                return self._status_from_sor(research_run_id)
            self._attach_supervisor(research_run_id, recovery=True)
            return self._status_from_sor(research_run_id)
        allocate_daily_budget_if_required(self._uow_factory, research_run_id)
        try:
            command = reconstruct_start_command(
                self._uow_factory, research_run_id, recovery=False
            )
        except ApplicationError as exc:
            if "not found" in str(exc).lower():
                raise OperatorError(OperatorErrorCode.RUN_NOT_FOUND, str(exc)) from exc
            raise
        report = self._run_preflight(command)
        if report.status is not PreflightStatus.READY_TO_START:
            if self._preflight_is_lease_conflict_only(report):
                return self._status_from_sor(research_run_id)
            raise self._operator_error_from_preflight(report)
        result = self._unfenced_controller.start(command)
        if result.state == OrchestrationState.READY.value:
            attached = self._attach_supervisor(
                research_run_id, recovery=False, command=command
            )
            if attached is None and not self.is_supervising(research_run_id):
                _log(
                    "runtime.start_denied_held",
                    runtime_instance_id=self.runtime_instance_id,
                    research_run_id=research_run_id,
                )
        return result

    def _preflight_is_lease_conflict_only(self, report: PreflightReport) -> bool:
        failing = [check for check in report.checks if not check.passed]
        return bool(failing) and all(
            check.name is PreflightCheckName.NO_CONFLICTING_LEASE for check in failing
        )

    def pause_run(
        self,
        research_run_id: str,
    ) -> OrchestrationTickResult:
        self._require_pg()

        supervisor = (
            None
            if self._registry is None
            else self._registry.supervisor(
                research_run_id
            )
        )

        if (
            supervisor is not None
            and supervisor.is_running
        ):
            try:
                return supervisor.run_serialized_control(
                    lambda: supervisor.controller.pause(
                        research_run_id
                    ),
                    timeout_seconds=(
                        self._lease_config.lease_ttl_seconds
                    ),
                )
            except TimeoutError as exc:
                raise OperatorError(
                    OperatorErrorCode.RECONCILIATION_REQUIRED,
                    "local supervisor did not reach a "
                    "safe pause boundary before its lease "
                    "TTL; PAUSED was not claimed",
                ) from exc
            except LeaseFencingError as exc:
                raise OperatorError(
                    OperatorErrorCode.LEASE_CONFLICT,
                    "local supervisor ownership changed "
                    "before pause; PAUSED was not claimed",
                ) from exc
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
                raise OperatorError(
                    OperatorErrorCode.DATABASE_UNAVAILABLE,
                    "postgresql became unavailable while "
                    "establishing the pause boundary",
                ) from exc

        with self._uow_factory.open() as uow:
            current = (
                uow.research_orchestrations.get(
                    research_run_id
                )
            )
            uow.rollback()

        if current is None:
            raise OperatorError(
                OperatorErrorCode.RUN_NOT_FOUND,
                "orchestration not found",
            )

        if current.state in {
            OrchestrationState.PAUSED.value,
            OrchestrationState.COMPLETED.value,
            OrchestrationState.BUDGET_EXHAUSTED.value,
            OrchestrationState.FAILED_OPERATIONAL.value,
        }:
            return self._status_from_sor(
                research_run_id
            )

        owner = current.owner_runtime_instance_id

        if owner is not None:
            if owner != self.runtime_instance_id:
                raise OperatorError(
                    OperatorErrorCode.LEASE_CONFLICT,
                    "run is owned by another runtime; "
                    "refusing unfenced pause",
                )

            raise OperatorError(
                OperatorErrorCode.RECONCILIATION_REQUIRED,
                "run is leased by this runtime but has no "
                "live local supervisor; reconcile before pause",
            )

        return self._unfenced_controller.pause(
            research_run_id
        )

    def resume_run(
        self,
        research_run_id: str,
    ) -> OrchestrationTickResult:
        self._require_pg()

        command = reconstruct_start_command(
            self._uow_factory,
            research_run_id,
            recovery=True,
        )

        supervisor = (
            None
            if self._registry is None
            else self._registry.supervisor(
                research_run_id
            )
        )

        if (
            supervisor is not None
            and supervisor.is_running
        ):
            def _resume_under_barrier():
                # Preflight while still PAUSED. No autonomous tick can
                # race this decision because the supervisor execution
                # barrier is held.
                report = self._run_preflight(
                    command,
                    check_reconciliation=True,
                )

                if (
                    report.status
                    is not PreflightStatus.READY_TO_START
                ):
                    failing = [
                        check
                        for check in report.checks
                        if not check.passed
                    ]

                    budget_only = (
                        len(failing) == 1
                        and failing[0].name
                        is PreflightCheckName.BUDGET_AVAILABLE
                        and failing[0].detail
                        == ReasonCode.BUDGET_EXHAUSTED.value
                    )

                    if budget_only:
                        resumed = (
                            supervisor.controller.resume(
                                research_run_id
                            )
                        )

                        if (
                            resumed.state
                            == OrchestrationState.READY.value
                        ):
                            terminal = (
                                supervisor.controller
                                .stop_for_budget_exhaustion(
                                    research_run_id,
                                    phase=(
                                        "runtime_recovery_budget"
                                    ),
                                )
                            )
                            supervisor.request_stop()
                            return terminal

                        return resumed

                    # Other recovery blockers must not turn PAUSED into
                    # a misleading READY state.
                    raise self._operator_error_from_preflight(
                        report
                    )

                return supervisor.controller.resume(
                    research_run_id
                )

            try:
                return supervisor.run_serialized_control(
                    _resume_under_barrier,
                    timeout_seconds=(
                        self._lease_config.lease_ttl_seconds
                    ),
                )
            except TimeoutError as exc:
                raise OperatorError(
                    OperatorErrorCode.RECONCILIATION_REQUIRED,
                    "local supervisor did not reach a "
                    "safe resume boundary before its lease TTL; "
                    "READY was not claimed",
                ) from exc
            except LeaseFencingError as exc:
                raise OperatorError(
                    OperatorErrorCode.LEASE_CONFLICT,
                    "local supervisor ownership changed "
                    "before resume; READY was not claimed",
                ) from exc
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
                raise OperatorError(
                    OperatorErrorCode.DATABASE_UNAVAILABLE,
                    "postgresql became unavailable while "
                    "establishing the resume boundary",
                ) from exc

        # No live local supervisor: preserve the existing recovery path.
        # _attach_supervisor performs fresh recovery preflight and can
        # terminalize exhausted budget. Return SoR after it, never the
        # pre-recovery READY snapshot.
        result = self._unfenced_controller.resume(
            research_run_id
        )

        if result.state == OrchestrationState.READY.value:
            self._attach_supervisor(
                research_run_id,
                recovery=True,
                command=command,
            )
            return self._status_from_sor(
                research_run_id
            )

        return result


    def deny_reauthorization(
        self,
        research_run_id: str,
        *,
        worker_result_id: str,
        operator_id: str,
    ) -> OrchestrationTickResult:
        self._require_pg()

        result = self._unfenced_controller.deny_reauthorization(
            research_run_id,
            worker_result_id=worker_result_id,
            operator_id=operator_id,
        )

        if (
            result.state == OrchestrationState.READY.value
            and not self.is_supervising(research_run_id)
        ):
            command = reconstruct_start_command(
                self._uow_factory,
                research_run_id,
                recovery=True,
            )
            self._attach_supervisor(
                research_run_id,
                recovery=True,
                command=command,
            )

        return result

    def cancel_run(
        self,
        research_run_id: str,
    ) -> OrchestrationTickResult:
        self._require_pg()

        supervisor = (
            None
            if self._registry is None
            else self._registry.supervisor(
                research_run_id
            )
        )

        if (
            supervisor is not None
            and supervisor.is_running
        ):
            def _cancel_under_barrier():
                result = supervisor.controller.cancel(
                    research_run_id
                )
                # Stop while the barrier is still held so no new
                # autonomous tick can start after cancellation.
                supervisor.request_stop()
                return result

            try:
                return supervisor.run_serialized_control(
                    _cancel_under_barrier,
                    timeout_seconds=(
                        self._lease_config.lease_ttl_seconds
                    ),
                )
            except TimeoutError as exc:
                raise OperatorError(
                    OperatorErrorCode.RECONCILIATION_REQUIRED,
                    "local supervisor did not reach a "
                    "safe cancel boundary before its lease TTL; "
                    "cancellation was not claimed",
                ) from exc
            except LeaseFencingError as exc:
                raise OperatorError(
                    OperatorErrorCode.LEASE_CONFLICT,
                    "local supervisor ownership changed "
                    "before cancel; cancellation was not claimed",
                ) from exc
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
                raise OperatorError(
                    OperatorErrorCode.DATABASE_UNAVAILABLE,
                    "postgresql became unavailable while "
                    "establishing the cancel boundary",
                ) from exc

        result = self._unfenced_controller.cancel(
            research_run_id
        )

        if self._registry is not None:
            self._registry.stop(
                research_run_id
            )

        return result


    def run_status(self, research_run_id: str) -> dict[str, object]:
        self._require_pg()
        with self._uow_factory.open() as uow:
            orchestration = uow.research_orchestrations.get(research_run_id)
            run = uow.research_runs.get(research_run_id)
            uow.rollback()
        if run is None:
            raise OperatorError(OperatorErrorCode.RUN_NOT_FOUND, "research run not found")
        payload: dict[str, object] = {
            "research_run_id": research_run_id,
            "runtime_instance_id": self.runtime_instance_id,
            "locally_supervised": self.is_supervising(research_run_id),
        }
        if orchestration is None:
            payload["state"] = None
            return redact_secret_keys(payload)
        payload.update(
            {
                "state": orchestration.state,
                "cycle_number": orchestration.cycle_number,
                "stop_reason": orchestration.stop_reason,
                "last_phase": orchestration.last_phase,
                "lease_epoch": orchestration.lease_epoch,
                "owner_runtime_instance_id": orchestration.owner_runtime_instance_id,
                "hypothesis_id": orchestration.last_hypothesis_id,
                "experiment_id": orchestration.last_experiment_id,
            }
        )
        return redact_secret_keys(payload)

    def execute_preflight(self, research_run_id: str) -> dict[str, object]:
        self._require_pg()
        try:
            command = reconstruct_start_command(
                self._uow_factory, research_run_id, recovery=False
            )
        except ApplicationError as exc:
            if "not found" in str(exc).lower():
                raise OperatorError(OperatorErrorCode.RUN_NOT_FOUND, str(exc)) from exc
            raise OperatorError(OperatorErrorCode.PREFLIGHT_FAILED, str(exc)) from exc
        report = self._run_preflight(command)
        latest = self.latest_preflight(research_run_id)
        payload = {
            "ok": True,
            "status": report.status.value,
            "result": report.status.value,
            "authorizes_start": False,
            "reasons": list(report.reasons),
            "checks": [
                {"name": check.name.value, "passed": check.passed, "detail": check.detail}
                for check in report.checks
            ],
            "latest": latest,
        }
        return redact_secret_keys(payload)

    def latest_preflight(self, research_run_id: str) -> dict[str, object] | None:
        self._require_pg()
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(research_run_id)
            record = uow.preflight_reports.latest_for_research_run(research_run_id)
            uow.rollback()
        if run is None:
            raise OperatorError(OperatorErrorCode.RUN_NOT_FOUND, "research run not found")
        if record is None:
            return None
        return preflight_record_to_mapping(record)

    def run_detail(self, research_run_id: str) -> dict[str, object]:
        self._require_pg()
        return build_run_detail(
            self._uow_factory,
            research_run_id,
            runtime_instance_id=None if self._instance is None else self.runtime_instance_id,
            locally_supervised=self.is_supervising(research_run_id),
        )

    def run_analysis(self, research_run_id: str, *, include_observer: bool = True) -> dict[str, object]:
        self._require_pg()
        payload = build_hq_run_analysis(
            self._uow_factory,
            research_run_id,
            locally_supervised=self.is_supervising(research_run_id),
        )
        if include_observer:
            payload["observer"] = self._observer.observe(payload)
        return payload

    def list_runs(self) -> list[dict[str, object]]:
        self._require_pg()
        supervised = (
            frozenset()
            if self._registry is None
            else frozenset(self._registry.owned_run_ids())
        )
        return build_run_list(
            self._uow_factory,
            runtime_instance_id=None if self._instance is None else self.runtime_instance_id,
            supervised_ids=supervised,
        )

    def list_programs(self) -> list[dict[str, object]]:
        self._require_pg()
        return build_program_list(self._uow_factory)

    def console_snapshot(self) -> dict[str, object]:
        runs = self.list_runs()
        details = [self.run_detail(str(row["research_run_id"])) for row in runs]
        return redact_secret_keys(
            {
                "health": self.health(),
                "programs": self.list_programs(),
                "runs": runs,
                "run_details": details,
                "live_update": "rest_poll",
                "sse_deferred": True,
            }
        )

    def health(self) -> dict[str, object]:
        from zest.maturity import GATE_04B_STATUS
        from zest.platform.health import ComponentHealth

        db_available = self._refresh_pg_availability()
        worker = self._probe_worker()
        model = self._probe_model()
        schema = SchemaHealthInput(at_expected_head=False, detail="unavailable")
        if db_available:
            try:
                schema = self._probe_schema()
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
                db_available = False
        worker_health = worker.health.health
        model_health = model.health.health
        candidate = model.candidate
        model_installed = True
        model_configured = candidate is not None
        model_authenticated = bool(candidate is not None and candidate.authenticated)
        model_structured = bool(
            candidate is not None and candidate.structured_output_compatible
        )
        model_available = (
            model_configured
            and model_authenticated
            and model_structured
            and model_health is ComponentHealth.HEALTHY
            and bool(candidate is not None and candidate.available)
        )
        worker_available = worker_health is ComponentHealth.HEALTHY
        payload: dict[str, object] = {
            "ok": db_available and self._instance is not None,
            "pg_unavailable": not db_available,
            "engine_version": self._engine_version,
            "environment_name": self._environment_name,
            "not_research_truth": True,
            "ready_for_start": db_available
            and worker_available
            and model_available
            and schema.at_expected_head,
            "database": {
                "installed": True,
                "configured": True,
                "available_now": db_available,
                "schema_at_expected_head": schema.at_expected_head,
                "health": (
                    ComponentHealth.HEALTHY.value
                    if db_available
                    else ComponentHealth.UNAVAILABLE.value
                ),
                "detail": "reachable" if db_available else "unavailable",
            },
            "worker": {
                "installed": True,
                "configured": True,
                "available_now": worker_available,
                "health": worker_health.value,
                "detail": worker.health.detail,
                "capabilities": sorted(worker.available_capabilities),
            },
            "model": {
                "installed": model_installed,
                "configured": model_configured,
                "authenticated": model_authenticated,
                "structured_output_compatible": model_structured,
                "available_now": model_available,
                "health": model_health.value,
                "detail": model.health.detail,
                "rate_limited": model_health is ComponentHealth.RATE_LIMITED,
                "gate_04b": GATE_04B_STATUS,
                "gate_04b_is_not_availability": True,
            },
        }
        if self._instance is not None:
            payload["runtime_instance_id"] = self._instance.runtime_instance_id
            payload["status"] = self._instance.status
        return redact_secret_keys(payload)

    def drain(self, *, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._instance is not None and self._instance.status == "STOPPED":
            return
        if self._instance is not None:
            try:
                mark_runtime_status(
                    self._uow_factory,
                    self._instance.runtime_instance_id,
                    "DRAINING",
                    clock=self._clock,
                )
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
        if self._registry is not None:
            for run_id in self._registry.owned_run_ids():
                self._registry.stop(run_id)
            for run_id in self._registry.owned_run_ids():
                supervisor = self._registry.supervisor(run_id)
                if supervisor is not None:
                    supervisor.join(join_timeout)
        if self._instance is not None:
            try:
                self._instance = mark_runtime_status(
                    self._uow_factory,
                    self._instance.runtime_instance_id,
                    "STOPPED",
                    clock=self._clock,
                )
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)
        _log(
            "runtime.drain",
            runtime_instance_id=None if self._instance is None else self._instance.runtime_instance_id,
        )

    def _attach_supervisor(
        self,
        research_run_id: str,
        *,
        recovery: bool,
        command: StartAutonomousResearchCommand | None = None,
    ):
        self._require_pg()
        if self._registry is None:
            raise ApplicationError("zestd has not registered a runtime instance")
        if self.is_supervising(research_run_id):
            return self._registry.supervisor(research_run_id)
        try:
            command = command or reconstruct_start_command(
                self._uow_factory, research_run_id, recovery=recovery
            )
            report = self._run_preflight(command, check_reconciliation=recovery)
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)
            return None
        if report.status is not PreflightStatus.READY_TO_START:
            failing = [check for check in report.checks if not check.passed]

            recovery_budget_exhausted = (
                recovery
                and len(failing) == 1
                and failing[0].name is PreflightCheckName.BUDGET_AVAILABLE
                and failing[0].detail == ReasonCode.BUDGET_EXHAUSTED.value
            )

            if recovery_budget_exhausted:
                result = self._unfenced_controller.stop_for_budget_exhaustion(
                    research_run_id,
                    phase="runtime_recovery_budget",
                )
                _log(
                    "runtime.recovery_budget_exhausted",
                    runtime_instance_id=self.runtime_instance_id,
                    research_run_id=research_run_id,
                    state=result.state,
                    stop_reason=result.stop_reason,
                    authority_expanded=False,
                )
                return None

            recovery_integrity_only = recovery and bool(failing) and all(
                check.name is PreflightCheckName.ORCHESTRATION_RECOVERABLE
                for check in failing
            )
            if not recovery_integrity_only:
                _log(
                    "runtime.preflight_blocked",
                    runtime_instance_id=self.runtime_instance_id,
                    research_run_id=research_run_id,
                    reasons=list(report.reasons),
                )
                return None
        owner_id = self.runtime_instance_id

        def _controller_factory(lease_epoch: int) -> AutonomousResearchController:
            fenced_factory = SingleRunFencedUowFactory(
                self._uow_factory,
                owner_runtime_instance_id=owner_id,
                lease_epoch=lease_epoch,
            )
            fenced_worker = LeaseFencedWorkerPort(
                self._worker,
                self._uow_factory,
                research_run_id=research_run_id,
                owner_runtime_instance_id=owner_id,
                lease_epoch=lease_epoch,
            )
            return AutonomousResearchController(
                fenced_factory,
                fenced_worker,
                self._model,
                fallback_models=self._fallback_models,
                clock=self._clock,
            )

        try:
            supervisor = self._registry.start(
                research_run_id=research_run_id,
                controller=self._unfenced_controller,
                command=command,
                uow_factory=self._uow_factory,
                cadence_seconds=self._cadence_seconds,
                clock=self._clock,
                controller_factory=_controller_factory,
            )
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)
            return None
        if supervisor is not None:
            _log(
                "runtime.supervisor_attached",
                runtime_instance_id=owner_id,
                research_run_id=research_run_id,
                lease_epoch=supervisor.lease_epoch,
            )
        return supervisor

    def _run_preflight(
        self,
        command: StartAutonomousResearchCommand,
        *,
        check_reconciliation: bool = True,
    ) -> PreflightReport:
        report = self._preflight.execute(
            PreflightCommand(
                research_run_id=command.research_run_id,
                target_reference=command.target_reference,
                schema=self._probe_schema(),
                worker=self._probe_worker(),
                model=self._probe_model(),
                required_worker_capabilities=(
                    self._required_worker_capabilities
                    | (
                        frozenset({BROWSER_PAGE_CAPABILITY})
                        if command.surface_discovery is not None
                        else frozenset()
                    )
                ),
                requesting_owner_runtime_instance_id=self.runtime_instance_id,
                check_reconciliation=check_reconciliation,
            )
        )
        persist_preflight_report(
            self._uow_factory,
            report,
            runtime_instance_id=self.runtime_instance_id,
            release_version=self._engine_version,
            configuration_fingerprint=configuration_fingerprint_for_command(command),
            actor_id=CONTROL_PLANE_ACTOR_ID,
        )
        return report

    def _operator_error_from_preflight(self, report: PreflightReport) -> OperatorError:
        detail = "; ".join(report.reasons) or "preflight not ready"
        failing = [check for check in report.checks if not check.passed]
        if not failing:
            return OperatorError(OperatorErrorCode.PREFLIGHT_FAILED, detail)
        first = failing[0]
        if first.name is PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE:
            return OperatorError(OperatorErrorCode.AUTHORIZATION_UNAVAILABLE, detail)
        if first.name is PreflightCheckName.BUDGET_AVAILABLE:
            return OperatorError(OperatorErrorCode.BUDGET_EXHAUSTED, detail)
        if first.name is PreflightCheckName.NO_CONFLICTING_LEASE:
            return OperatorError(OperatorErrorCode.LEASE_CONFLICT, detail)
        if first.name is PreflightCheckName.WORKER_RUNTIME_HEALTHY:
            return OperatorError(OperatorErrorCode.WORKER_UNAVAILABLE, detail)
        if first.name is PreflightCheckName.WORKER_CAPABILITIES_PRESENT:
            return OperatorError(OperatorErrorCode.WORKER_UNAVAILABLE, detail)
        if first.name is PreflightCheckName.MODEL_RUNTIME_READY:
            joined = " ".join(check.detail for check in failing)
            if "AUTH_REQUIRED" in joined:
                return OperatorError(OperatorErrorCode.MODEL_AUTH_REQUIRED, detail)
            if "RATE_LIMITED" in joined:
                return OperatorError(OperatorErrorCode.MODEL_RATE_LIMITED, detail)
            return OperatorError(OperatorErrorCode.PREFLIGHT_FAILED, detail)
        if first.name is PreflightCheckName.ORCHESTRATION_RECOVERABLE:
            return OperatorError(OperatorErrorCode.RECONCILIATION_REQUIRED, detail)
        if first.name is PreflightCheckName.DATABASE_REACHABLE:
            return OperatorError(OperatorErrorCode.DATABASE_UNAVAILABLE, detail)
        return OperatorError(OperatorErrorCode.PREFLIGHT_FAILED, detail)

    def _lease_held_by_other(self, record) -> bool:
        owner = record.owner_runtime_instance_id
        if owner in (None, self.runtime_instance_id):
            return False
        expires = record.lease_expires_at
        if expires is None:
            return True
        return expires >= datetime.now(timezone.utc)

    def _hold_for_human(self, research_run_id: str, decision: RuntimeRecoveryDecision) -> None:
        now = self._clock.now()
        try:
            with self._uow_factory.open() as uow:
                current = uow.research_orchestrations.get(research_run_id)
                if current is None or current.state not in {
                    OrchestrationState.READY.value,
                    OrchestrationState.RUNNING.value,
                }:
                    uow.rollback()
                    return
                updated = replace(
                    current,
                    state=OrchestrationState.WAITING_HUMAN.value,
                    pause_reason=decision.action.value,
                    last_phase="runtime_recovery",
                    updated_at=now,
                    checkpoint_at=now,
                )
                uow.research_orchestrations.save(updated)
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id=CONTROL_PLANE_ACTOR_ID,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="RUNTIME_RECOVERY_HELD_FOR_HUMAN",
                        subject_type="research_run",
                        subject_id=research_run_id,
                        payload={
                            "recovery_classification": decision.action.value,
                            "reason": decision.reason,
                            "not_research_truth": True,
                        },
                    )
                )
                uow.commit()
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)

    def _status_from_sor(self, research_run_id: str) -> OrchestrationTickResult:
        with self._uow_factory.open() as uow:
            record = uow.research_orchestrations.get(research_run_id)
            uow.rollback()
        if record is None:
            raise ApplicationError("orchestration not found")
        from zest.research.orchestration import CycleOutcome

        return OrchestrationTickResult(
            research_run_id=record.research_run_id,
            state=record.state,
            cycle_number=record.cycle_number,
            outcome=CycleOutcome.CONTINUE.value,
            stop_reason=record.stop_reason,
            last_phase=record.last_phase,
            hypothesis_id=record.last_hypothesis_id,
            experiment_id=record.last_experiment_id,
        )

    def _heartbeat_loop(self) -> None:
        interval = self._lease_config.heartbeat_interval_seconds
        while not self._stop.wait(interval):
            if self._instance is None:
                continue
            try:
                heartbeat_runtime_instance(
                    self._uow_factory,
                    self._instance.runtime_instance_id,
                    clock=self._clock,
                )
                self._pg_unavailable = False
            except DatabaseUnavailableError as exc:
                self._mark_pg_unavailable(exc)

    def _refresh_pg_availability(self) -> bool:
        try:
            with self._uow_factory.open() as uow:
                uow.rollback()
        except DatabaseUnavailableError as exc:
            self._mark_pg_unavailable(exc)
            return False
        self._pg_unavailable = False
        return True

    def _mark_pg_unavailable(self, exc: PersistenceError) -> None:
        self._pg_unavailable = True
        _log(
            "runtime.pg_unavailable",
            runtime_instance_id=None if self._instance is None else self._instance.runtime_instance_id,
            error=exc.__class__.__name__,
        )

    def _require_pg(self) -> None:
        if not self._refresh_pg_availability():
            raise OperatorError(
                OperatorErrorCode.DATABASE_UNAVAILABLE,
                "postgresql unavailable; refusing new authoritative work",
            )
