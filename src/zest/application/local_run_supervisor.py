"""Small local runner around the existing AutonomousResearchController.

The supervisor owns no research policy. It only schedules bounded controller
ticks and treats the persisted orchestration record as authoritative.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from zest.application.discovery.lifecycle import discovery_is_exhausted
from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.orchestration_lease import LeaseConfig
from zest.application.observability import supervisor_fault
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.errors import (
    LeaseFencingError,
    PersistenceError,
    TerminalOrchestrationStateError,
)
from zest.data.records import LeaseAcquireOutcome
from zest.research.orchestration import (
    CycleOutcome,
    OrchestrationState,
)

_NON_RUNNABLE_STATES = frozenset(
    {
        OrchestrationState.PAUSED.value,
        OrchestrationState.WAITING_HUMAN.value,
        OrchestrationState.BLOCKED.value,
        OrchestrationState.BUDGET_EXHAUSTED.value,
        OrchestrationState.FAILED_OPERATIONAL.value,
    }
)
_TERMINAL_STATES = frozenset(
    {
        OrchestrationState.COMPLETED.value,
        OrchestrationState.BUDGET_EXHAUSTED.value,
        OrchestrationState.FAILED_OPERATIONAL.value,
    }
)


def _result_from_persisted(record) -> OrchestrationTickResult:
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


@dataclass
class LocalRunSupervisor:
    """One bounded cadence loop for one already-started ResearchRun.

    When `owner_runtime_instance_id` is set (the production path, always
    supplied by `LocalRunSupervisorRegistry` after a successful lease
    acquisition), each tick renews the lease at `lease_config`'s heartbeat
    interval and stops ticking immediately if renewal is ever rejected
    (another runtime instance has since acquired a newer epoch). When left
    `None` (legacy/test construction), the supervisor ticks unleased, exactly
    as before this lease mechanism existed.
    """

    research_run_id: str
    controller: AutonomousResearchController
    command: StartAutonomousResearchCommand
    uow_factory: UnitOfWorkFactory
    cadence_seconds: float = 0.25
    clock: Clock | None = None
    owner_runtime_instance_id: str | None = None
    lease_epoch: int = 0
    lease_config: LeaseConfig = field(default_factory=LeaseConfig)

    def __post_init__(self) -> None:
        if self.cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._execution_lock = threading.Lock()
        self._last_result: OrchestrationTickResult | None = None
        self._last_renewed_monotonic = time.monotonic()
        self._lease_lost = False
        self._clock = self.clock or SystemClock()
        self._daily_budget_date: str | None = None

    @property
    def lease_lost(self) -> bool:
        """True once a heartbeat renewal has been rejected by the SoR."""

        return self._lease_lost

    def _ensure_current_daily_budget(self) -> None:
        """Provision the current UTC day's program LLM envelope once.

        This is a lifecycle prerequisite, not budget authority. The
        persisted program policy remains the source of the configured
        limit. An unset limit is still not provisioned, so the model
        layer continues to fail closed.
        """

        budget_date = self._clock.now().date().isoformat()

        if self._daily_budget_date == budget_date:
            return

        # Local import avoids widening the supervisor module's ownership:
        # this calls the existing SoR-backed allocation use case.
        from zest.application.reconstruct_run_command import (
            allocate_daily_budget_if_required,
        )

        allocate_daily_budget_if_required(
            self.uow_factory,
            self.research_run_id,
            clock=self._clock,
        )

        self._daily_budget_date = budget_date

    def _renew_lease_if_due(self) -> bool:
        """Return False only when renewal was attempted and rejected."""

        if self.owner_runtime_instance_id is None:
            return True
        elapsed = time.monotonic() - self._last_renewed_monotonic
        if elapsed < self.lease_config.heartbeat_interval_seconds:
            return True
        with self.uow_factory.open() as uow:
            renewed = uow.research_orchestrations.renew_lease(
                self.research_run_id,
                owner_runtime_instance_id=self.owner_runtime_instance_id,
                expected_lease_epoch=self.lease_epoch,
                ttl_seconds=self.lease_config.lease_ttl_seconds,
            )
            uow.commit()
        if renewed:
            self._last_renewed_monotonic = time.monotonic()
            return True
        return False

    def tick(self) -> OrchestrationTickResult | None:
        """Serialize one autonomous tick against synchronous controls."""
        with self._execution_lock:
            return self._tick_once()

    def run_serialized_control(
        self,
        action: Callable[[], OrchestrationTickResult],
        *,
        timeout_seconds: float,
    ) -> OrchestrationTickResult:
        """Run one operator transition outside any in-flight tick.

        The existing lease is renewed while the execution barrier is held,
        so PAUSED cannot be claimed after silently giving ownership away.
        """
        if timeout_seconds <= 0:
            raise ValueError(
                "timeout_seconds must be positive"
            )

        acquired = self._execution_lock.acquire(
            timeout=timeout_seconds
        )

        if not acquired:
            raise TimeoutError(
                "supervisor execution barrier timed out"
            )

        try:
            # The control may have lost a race to an autonomous tick that
            # terminalized the run immediately before this barrier was
            # acquired. Terminal SoR is immutable and authoritative:
            # return it directly rather than attempting an impossible
            # lease renewal and misreporting a LEASE_CONFLICT.
            with self.uow_factory.open() as uow:
                current = uow.research_orchestrations.get(
                    self.research_run_id
                )
                uow.rollback()

            if current is None:
                raise ApplicationError(
                    "orchestration not found"
                )

            if current.state in _TERMINAL_STATES:
                result = _result_from_persisted(
                    current
                )
                self._last_result = result
                self._stop_event.set()
                return result

            if self.owner_runtime_instance_id is not None:
                with self.uow_factory.open() as uow:
                    renewed = (
                        uow.research_orchestrations.renew_lease(
                            self.research_run_id,
                            owner_runtime_instance_id=(
                                self.owner_runtime_instance_id
                            ),
                            expected_lease_epoch=self.lease_epoch,
                            ttl_seconds=(
                                self.lease_config.lease_ttl_seconds
                            ),
                        )
                    )
                    uow.commit()

                if not renewed:
                    self._lease_lost = True
                    self._stop_event.set()
                    raise LeaseFencingError(
                        "supervisor lost lease before "
                        "operator control transition"
                    )

                self._last_renewed_monotonic = (
                    time.monotonic()
                )

            return action()
        finally:
            self._execution_lock.release()

    def _tick_once(self) -> OrchestrationTickResult | None:
        """Run at most one controller step after reloading durable state."""

        if self._lease_lost:
            return self._last_result
        if not self._renew_lease_if_due():
            self._lease_lost = True
            self._stop_event.set()
            with self.uow_factory.open() as uow:
                record = uow.research_orchestrations.get(self.research_run_id)
                uow.rollback()
            self._last_result = _result_from_persisted(record) if record is not None else None
            return self._last_result
        with self.uow_factory.open() as uow:
            record = uow.research_orchestrations.get(self.research_run_id)
            uow.rollback()
        if record is None:
            raise ApplicationError("orchestration not found")
        if record.state in _NON_RUNNABLE_STATES:
            result = _result_from_persisted(record)
        elif record.state in {
            OrchestrationState.READY.value,
            OrchestrationState.RUNNING.value,
        }:
            try:
                self._ensure_current_daily_budget()
                result = self.controller.step(self.command)
            except LeaseFencingError:
                self._lease_lost = True
                self._stop_event.set()
                with self.uow_factory.open() as uow:
                    record = uow.research_orchestrations.get(self.research_run_id)
                    uow.rollback()
                self._last_result = (
                    _result_from_persisted(record) if record is not None else None
                )
                return self._last_result
            except TerminalOrchestrationStateError:
                # Another authority (operator pause/cancel, or reconciliation)
                # finalized this run terminally while this tick was in
                # flight. The persisted terminal state already wins; reload
                # and stop supervising rather than raising in this thread.
                with self.uow_factory.open() as uow:
                    record = uow.research_orchestrations.get(self.research_run_id)
                    uow.rollback()
                if record is None:
                    raise ApplicationError("orchestration not found")
                result = _result_from_persisted(record)
            except Exception as exc:
                # A supervisor exception must be durable operational truth;
                # it must not be mistaken for a research result.
                try:
                    with self.uow_factory.open() as uow:
                        uow.run_faults.insert(
                            supervisor_fault(
                                self.research_run_id,
                                exc,
                                runtime_instance_id=self.owner_runtime_instance_id,
                                occurred_at=datetime.now(timezone.utc),
                            )
                        )
                        uow.commit()
                except PersistenceError:
                    # The original supervisor fault remains an operational
                    # failure even when its persistence path is unavailable.
                    pass
                try:
                    result = self.controller.stop_for_operational_failure(
                        self.research_run_id,
                        phase="supervisor_fault",
                    )
                except (
                    ApplicationError,
                    PersistenceError,
                    LeaseFencingError,
                    TerminalOrchestrationStateError,
                ):
                    # If PostgreSQL/fencing prevents the authoritative
                    # transition, preserve the original durable RunFault and
                    # stop. Recovery/reconciliation will classify the row.
                    with self.uow_factory.open() as uow:
                        record = uow.research_orchestrations.get(self.research_run_id)
                        uow.rollback()
                    result = (
                        _result_from_persisted(record)
                        if record is not None
                        else None
                    )

                self._stop_event.set()
                self._last_result = result
                return self._last_result
        else:
            result = _result_from_persisted(record)
        self._last_result = result
        if self.command.surface_discovery is not None:
            with self.uow_factory.open() as uow:
                exhausted = discovery_is_exhausted(uow, self.research_run_id)
                uow.rollback()
            if exhausted:
                self.command = replace(self.command, surface_discovery=None)
        if result.state in _TERMINAL_STATES:
            self._stop_event.set()
        return result

    def start(self) -> "LocalRunSupervisor":
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"research-run-{self.research_run_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def request_stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_result(self) -> OrchestrationTickResult | None:
        return self._last_result

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.tick()
            if not self._stop_event.wait(self.cadence_seconds):
                continue
        self._release_lease_best_effort()

    def _release_lease_best_effort(self) -> None:
        """Give up the lease on graceful stop. Never raises: a failed
        release here (e.g. already superseded, already unowned) leaves the
        row exactly as safe as before this call, since acquire/renew are
        independently CAS-guarded regardless of whether release ran."""

        if self.owner_runtime_instance_id is None or self._lease_lost:
            return
        try:
            with self.uow_factory.open() as uow:
                uow.research_orchestrations.release_lease(
                    self.research_run_id,
                    owner_runtime_instance_id=self.owner_runtime_instance_id,
                    expected_lease_epoch=self.lease_epoch,
                )
                uow.commit()
        except PersistenceError:
            # Best-effort: a failed release leaves the row exactly as safe
            # as before this call (acquire/renew are independently
            # CAS-guarded regardless of whether release ever ran).
            pass


class LocalRunSupervisorRegistry:
    """Process-local duplicate-start guard for local supervisors.

    Also owns this process's identity as a lease holder
    (`owner_runtime_instance_id`, one per registry instance, i.e. one per
    process in production) and the lease timing policy applied to every
    supervisor it starts.
    """

    def __init__(
        self,
        *,
        owner_runtime_instance_id: str | None = None,
        lease_config: LeaseConfig | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._supervisors: dict[str, LocalRunSupervisor] = {}
        self.owner_runtime_instance_id = owner_runtime_instance_id or new_opaque_id()
        self.lease_config = lease_config or LeaseConfig()

    def start(
        self,
        *,
        research_run_id: str,
        controller: AutonomousResearchController,
        command: StartAutonomousResearchCommand,
        uow_factory: UnitOfWorkFactory,
        cadence_seconds: float = 0.25,
        clock: Clock | None = None,
        controller_factory=None,
    ) -> LocalRunSupervisor | None:
        """Attach a supervisor for this run, or return None if the lease
        could not be acquired (another runtime instance holds it, or the
        run is terminal). Returning None is not an error: it means this
        process must not drive this run, not that the request was invalid.
        """

        with self._lock:
            existing = self._supervisors.get(research_run_id)
            if existing is not None and existing.is_running:
                return existing
            with uow_factory.open() as uow:
                acquired = uow.research_orchestrations.acquire_lease(
                    research_run_id,
                    owner_runtime_instance_id=self.owner_runtime_instance_id,
                    ttl_seconds=self.lease_config.lease_ttl_seconds,
                )
                uow.commit()
            if acquired.outcome is not LeaseAcquireOutcome.ACQUIRED:
                return None
            if controller_factory is not None:
                controller = controller_factory(acquired.record.lease_epoch)
            supervisor = LocalRunSupervisor(
                research_run_id,
                controller,
                command,
                uow_factory,
                cadence_seconds,
                clock=clock,
                owner_runtime_instance_id=self.owner_runtime_instance_id,
                lease_epoch=acquired.record.lease_epoch,
                lease_config=self.lease_config,
            )
            self._supervisors[research_run_id] = supervisor
            supervisor.start()
            return supervisor

    def stop(self, research_run_id: str) -> None:
        with self._lock:
            supervisor = self._supervisors.get(research_run_id)
        if supervisor is not None:
            supervisor.request_stop()

    def is_active(self, research_run_id: str) -> bool:
        """True if this process already owns a live supervisor thread for the run.

        Process-local only: a run supervised by a different process will
        report False here even though it is genuinely owned elsewhere. Full
        cross-process ownership is the lease/fencing mechanism, not this
        registry.
        """
        with self._lock:
            supervisor = self._supervisors.get(research_run_id)
        return supervisor is not None and supervisor.is_running

    def owned_run_ids(self) -> tuple[str, ...]:
        """Return only runs with a live supervisor thread in this process."""
        with self._lock:
            return tuple(
                research_run_id
                for research_run_id, supervisor in self._supervisors.items()
                if supervisor.is_running
            )

    def supervisor(self, research_run_id: str):
        with self._lock:
            return self._supervisors.get(research_run_id)
