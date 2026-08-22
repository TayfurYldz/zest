"""Persistent research-osd runtime owner.

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

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    CONTROL_PLANE_ACTOR_ID,
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from research_os.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
    RuntimeRecoveryDecision,
)
from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.lease_fencing import (
    LeaseFencedWorkerPort,
    SingleRunFencedUowFactory,
)
from research_os.application.local_run_supervisor import LocalRunSupervisorRegistry
from research_os.application.orchestration_lease import LeaseConfig
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.application.preflight import (
    ModelReadinessInput,
    Preflight,
    PreflightCommand,
    PreflightCheckName,
    PreflightReport,
    PreflightStatus,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from research_os.application.reconstruct_run_command import (
    allocate_daily_budget_if_required,
    reconstruct_start_command,
)
from research_os.application.runtime_instance import (
    ENGINE_VERSION,
    heartbeat_runtime_instance,
    mark_runtime_status,
    register_runtime_instance,
)
from research_os.core.enums import ActorType
from research_os.data.errors import PersistenceConflictError, PersistenceError
from research_os.data.records import AuditEventRecord, RuntimeInstanceRecord
from research_os.platform.worker import WorkerPort
from research_os.research.model_port import ModelPort
from research_os.research.orchestration import OrchestrationState
from research_os.safe_data import redact_secret_keys

LOGGER = logging.getLogger("research_os.research_osd")

ReadinessProbe = Callable[[], object]


def _log(event: str, **fields: object) -> None:
    payload = redact_secret_keys({"event": event, **fields})
    LOGGER.info("%s", payload)


class ResearchOsdRuntime:
    """One OS process that owns local supervisors for leased runs."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        model: ModelPort,
        *,
        clock: Clock | None = None,
        lease_config: LeaseConfig | None = None,
        cadence_seconds: float = 0.25,
        probe_schema: Callable[[], SchemaHealthInput],
        probe_worker: Callable[[], WorkerReadinessInput],
        probe_model: Callable[[], ModelReadinessInput],
        required_worker_capabilities: frozenset[str] = frozenset(),
        engine_version: str = ENGINE_VERSION,
        host_identity: str = "research-osd",
        process_id: str = "0",
    ) -> None:
        self._uow_factory = uow_factory
        self._worker = worker
        self._model = model
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
        self._instance: RuntimeInstanceRecord | None = None
        self._registry: LocalRunSupervisorRegistry | None = None
        self._unfenced_controller = AutonomousResearchController(
            uow_factory, worker, model, clock=self._clock
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
            raise ApplicationError("research-osd has not registered a runtime instance")
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
            name="research-osd-heartbeat",
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
        except PersistenceError as exc:
            self._mark_pg_unavailable(exc)
            return ()
        decisions: list[RuntimeRecoveryDecision] = []
        for record in recoverable:
            if self._lease_held_by_other(record):
                continue
            try:
                decision = self._classifier.execute(record.research_run_id)
            except PersistenceError as exc:
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
        except PersistenceError as exc:
            self._mark_pg_unavailable(exc)
            raise ApplicationError(
                "postgresql unavailable; refusing new authoritative work"
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
        command = reconstruct_start_command(
            self._uow_factory, research_run_id, recovery=False
        )
        report = self._run_preflight(command)
        if report.status is not PreflightStatus.READY_TO_START:
            if self._preflight_is_lease_conflict_only(report):
                return self._status_from_sor(research_run_id)
            raise ApplicationError("; ".join(report.reasons) or "preflight not ready")
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

    def pause_run(self, research_run_id: str) -> OrchestrationTickResult:
        self._require_pg()
        result = self._unfenced_controller.pause(research_run_id)
        if self._registry is not None:
            self._registry.stop(research_run_id)
        return result

    def resume_run(self, research_run_id: str) -> OrchestrationTickResult:
        self._require_pg()
        command = reconstruct_start_command(
            self._uow_factory, research_run_id, recovery=True
        )
        result = self._unfenced_controller.resume(research_run_id)
        if result.state == OrchestrationState.READY.value:
            self._attach_supervisor(research_run_id, recovery=True, command=command)
        return result

    def cancel_run(self, research_run_id: str) -> OrchestrationTickResult:
        self._require_pg()
        result = self._unfenced_controller.cancel(research_run_id)
        if self._registry is not None:
            self._registry.stop(research_run_id)
        return result

    def run_status(self, research_run_id: str) -> dict[str, object]:
        self._require_pg()
        with self._uow_factory.open() as uow:
            orchestration = uow.research_orchestrations.get(research_run_id)
            run = uow.research_runs.get(research_run_id)
            uow.rollback()
        if run is None:
            raise ApplicationError("research run not found")
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

    def health(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": not self._pg_unavailable and self._instance is not None,
            "pg_unavailable": self._pg_unavailable,
            "engine_version": self._engine_version,
            "not_research_truth": True,
        }
        if self._instance is not None:
            payload["runtime_instance_id"] = self._instance.runtime_instance_id
            payload["status"] = self._instance.status
        return redact_secret_keys(payload)

    def drain(self, *, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._instance is not None:
            try:
                mark_runtime_status(
                    self._uow_factory,
                    self._instance.runtime_instance_id,
                    "DRAINING",
                    clock=self._clock,
                )
            except PersistenceError as exc:
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
            except PersistenceError as exc:
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
            raise ApplicationError("research-osd has not registered a runtime instance")
        if self.is_supervising(research_run_id):
            return self._registry.supervisor(research_run_id)
        try:
            command = command or reconstruct_start_command(
                self._uow_factory, research_run_id, recovery=recovery
            )
            report = self._run_preflight(command)
        except PersistenceError as exc:
            self._mark_pg_unavailable(exc)
            return None
        if report.status is not PreflightStatus.READY_TO_START:
            failing = [check for check in report.checks if not check.passed]
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
                fenced_factory, fenced_worker, self._model, clock=self._clock
            )

        try:
            supervisor = self._registry.start(
                research_run_id=research_run_id,
                controller=self._unfenced_controller,
                command=command,
                uow_factory=self._uow_factory,
                cadence_seconds=self._cadence_seconds,
                controller_factory=_controller_factory,
            )
        except PersistenceError as exc:
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

    def _run_preflight(self, command: StartAutonomousResearchCommand) -> PreflightReport:
        return self._preflight.execute(
            PreflightCommand(
                research_run_id=command.research_run_id,
                target_reference=command.target_reference,
                schema=self._probe_schema(),
                worker=self._probe_worker(),
                model=self._probe_model(),
                required_worker_capabilities=self._required_worker_capabilities,
                requesting_owner_runtime_instance_id=self.runtime_instance_id,
            )
        )

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
        except PersistenceError as exc:
            self._mark_pg_unavailable(exc)

    def _status_from_sor(self, research_run_id: str) -> OrchestrationTickResult:
        with self._uow_factory.open() as uow:
            record = uow.research_orchestrations.get(research_run_id)
            uow.rollback()
        if record is None:
            raise ApplicationError("orchestration not found")
        from research_os.research.orchestration import CycleOutcome

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
            except PersistenceError as exc:
                self._mark_pg_unavailable(exc)

    def _mark_pg_unavailable(self, exc: PersistenceError) -> None:
        self._pg_unavailable = True
        _log(
            "runtime.pg_unavailable",
            runtime_instance_id=None if self._instance is None else self._instance.runtime_instance_id,
            error=exc.__class__.__name__,
        )

    def _require_pg(self) -> None:
        if self._pg_unavailable:
            raise ApplicationError("postgresql unavailable; refusing new authoritative work")
