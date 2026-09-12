"""Checkpoint 16 J11 lease/fencing qualification helpers.

Qualification-only. Uses production PostgreSQL lease CAS, fenced UoW, and
fenced WorkerPort primitives. Does not relax Preflight, grant authority,
create Findings, or dispatch external/network Workers.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import socket
import time
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping

from zest.application.lease_fencing import (
    LeaseFencedWorkerPort,
    SingleRunFencedUowFactory,
)
from zest.application.runtime_instance import (
    mark_runtime_status,
    register_runtime_instance,
)
from zest.data.errors import LeaseFencingError
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import LeaseAcquireOutcome
from zest.platform.worker import (
    InvocationStatus,
    WorkerInvocationOutcome,
)
from zest.research.orchestration import OrchestrationState

RUN_ID = "run-1"


class J11QualificationError(Exception):
    """J11 qualification setup or invariant failed."""


@dataclass(frozen=True)
class J11PrepareResult:
    state: str
    owner_runtime_instance_id: str | None
    lease_epoch: int
    side_effect_ceiling: int


@dataclass(frozen=True)
class J11RaceChildResult:
    label: str
    process_id: int
    runtime_instance_id: str
    acquire_outcome: str
    acquired_epoch: int | None
    loser_worker_blocked: bool
    inner_dispatch_count: int
    stopped: bool
    error: str | None = None


@dataclass(frozen=True)
class J11RaceIteration:
    iteration: int
    winner_runtime_instance_id: str
    loser_runtime_instance_id: str
    winner_process_id: int
    loser_process_id: int
    lease_epoch: int
    loser_worker_blocked: bool
    loser_inner_dispatch_count: int
    released_after_iteration: bool


@dataclass(frozen=True)
class J11RaceResult:
    iterations: tuple[J11RaceIteration, ...]
    final_owner_runtime_instance_id: str
    final_lease_epoch: int
    final_owner_count: int


@dataclass(frozen=True)
class J11StaleProofResult:
    stale_owner_runtime_instance_id: str
    current_owner_runtime_instance_id: str
    stale_process_id: int
    current_process_id: int
    stale_epoch: int
    current_epoch: int
    stale_save_blocked: bool
    stale_worker_blocked: bool
    stale_inner_dispatch_count: int
    stopped_runtime_count: int


@dataclass(frozen=True)
class J11CleanupResult:
    cleanup: str
    owner_runtime_instance_id: str | None
    expected_lease_epoch: int | None
    lease_epoch_after: int
    owner_after: str | None
    lease_expires_at_after: str | None
    derived_owner: bool
    owner_status: str | None
    owner_expired: bool
    release_cas_applied: bool


class _CountingDiagnosticWorker:
    def __init__(self) -> None:
        self.invocation_count = 0

    def invoke(
        self,
        request: Mapping[str, object],
        *,
        timeout_ms: int | None = None,
    ) -> WorkerInvocationOutcome:
        self.invocation_count += 1
        now = datetime.now(timezone.utc)
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            worker_result={
                "status": "SUCCEEDED",
                "worker_capability": "diagnostic.echo",
                "raw_result": {"echoed": str(request.get("message", "j11"))},
            },
        )


def prepare_j11_run(engine) -> J11PrepareResult:
    """Prepare run-1 for a lease race without weakening production Preflight.

    J10 leaves run-1 WAITING_HUMAN by design. J11 is a fencing qualification,
    not a recovery/start qualification, so it needs a non-terminal unowned
    orchestration row to exercise lease CAS. A live owner is never cleared.
    """

    now = datetime.now(timezone.utc)
    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        current = uow.research_orchestrations.get(RUN_ID)
        if current is None:
            uow.rollback()
            raise J11QualificationError("run-1 orchestration missing; run seed first")
        if current.owner_runtime_instance_id is not None:
            uow.rollback()
            raise J11QualificationError(
                "run-1 already has an owner; refusing J11 preparation"
            )
        if current.state in {"COMPLETED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}:
            uow.rollback()
            raise J11QualificationError(
                f"run-1 is terminal ({current.state}); refusing J11 preparation"
            )
        updated = replace(
            current,
            state=OrchestrationState.RUNNING.value,
            pause_reason=None,
            last_phase="j11_fencing_prepare",
            updated_at=now,
            checkpoint_at=now,
        )
        uow.research_orchestrations.save(updated)
        uow.commit()
    return J11PrepareResult(
        state=updated.state,
        owner_runtime_instance_id=updated.owner_runtime_instance_id,
        lease_epoch=updated.lease_epoch,
        side_effect_ceiling=updated.side_effect_ceiling,
    )


def run_two_process_owner_race(
    database_url: str,
    *,
    iterations: int = 5,
    lease_ttl_seconds: float = 30.0,
) -> J11RaceResult:
    if iterations < 1:
        raise J11QualificationError("iterations must be >= 1")
    results: list[J11RaceIteration] = []
    for iteration in range(1, iterations + 1):
        pair = _run_one_race_iteration(database_url, lease_ttl_seconds=lease_ttl_seconds)
        acquired = [
            item
            for item in pair
            if item.acquire_outcome == LeaseAcquireOutcome.ACQUIRED.value
        ]
        denied = [
            item
            for item in pair
            if item.acquire_outcome == LeaseAcquireOutcome.DENIED_HELD_BY_OTHER.value
        ]
        if len(acquired) != 1 or len(denied) != 1:
            raise J11QualificationError(f"expected exactly one winner, got {pair!r}")
        winner = acquired[0]
        loser = denied[0]
        if not loser.loser_worker_blocked or loser.inner_dispatch_count != 0:
            raise J11QualificationError("loser Worker dispatch was not fenced")
        if winner.acquired_epoch is None:
            raise J11QualificationError("winner did not report acquired epoch")
        release_after = iteration != iterations
        if release_after:
            _release_owner(
                database_url,
                owner_runtime_instance_id=winner.runtime_instance_id,
                lease_epoch=winner.acquired_epoch,
            )
        results.append(
            J11RaceIteration(
                iteration=iteration,
                winner_runtime_instance_id=winner.runtime_instance_id,
                loser_runtime_instance_id=loser.runtime_instance_id,
                winner_process_id=winner.process_id,
                loser_process_id=loser.process_id,
                lease_epoch=winner.acquired_epoch,
                loser_worker_blocked=loser.loser_worker_blocked,
                loser_inner_dispatch_count=loser.inner_dispatch_count,
                released_after_iteration=release_after,
            )
        )
    final = _load_orchestration(database_url)
    if final.owner_runtime_instance_id is None:
        raise J11QualificationError("final race left run unowned")
    return J11RaceResult(
        iterations=tuple(results),
        final_owner_runtime_instance_id=final.owner_runtime_instance_id,
        final_lease_epoch=final.lease_epoch,
        final_owner_count=1,
    )


def run_stale_epoch_proof(
    database_url: str,
    *,
    expired_ttl_seconds: float = 0.2,
    lease_ttl_seconds: float = 30.0,
    release_current_owner: bool = True,
) -> J11StaleProofResult:
    stale = _run_one_acquire_process(
        database_url,
        label="stale",
        lease_ttl_seconds=expired_ttl_seconds,
    )
    if stale.acquire_outcome != LeaseAcquireOutcome.ACQUIRED.value:
        raise J11QualificationError(f"stale owner could not acquire: {stale.acquire_outcome}")
    if stale.acquired_epoch is None:
        raise J11QualificationError("stale owner did not report an epoch")
    time.sleep(expired_ttl_seconds + 0.35)
    current = _run_one_acquire_process(
        database_url,
        label="current",
        lease_ttl_seconds=lease_ttl_seconds,
    )
    if current.acquire_outcome != LeaseAcquireOutcome.ACQUIRED.value:
        raise J11QualificationError(
            f"current owner could not acquire: {current.acquire_outcome}"
        )
    if current.acquired_epoch is None:
        raise J11QualificationError("current owner did not report an epoch")
    engine = create_sync_engine(database_url)
    try:
        factory = PostgresUnitOfWork(engine)
        stale_save_blocked = _stale_save_is_blocked(
            factory,
            owner_runtime_instance_id=stale.runtime_instance_id,
            lease_epoch=stale.acquired_epoch,
        )
        stale_worker_blocked, dispatch_count = _stale_worker_is_blocked(
            factory,
            owner_runtime_instance_id=stale.runtime_instance_id,
            lease_epoch=stale.acquired_epoch,
        )
        if release_current_owner:
            with factory.open() as uow:
                uow.research_orchestrations.release_lease(
                    RUN_ID,
                    owner_runtime_instance_id=current.runtime_instance_id,
                    expected_lease_epoch=current.acquired_epoch,
                )
                uow.commit()
        return J11StaleProofResult(
            stale_owner_runtime_instance_id=stale.runtime_instance_id,
            current_owner_runtime_instance_id=current.runtime_instance_id,
            stale_process_id=stale.process_id,
            current_process_id=current.process_id,
            stale_epoch=stale.acquired_epoch,
            current_epoch=current.acquired_epoch,
            stale_save_blocked=stale_save_blocked,
            stale_worker_blocked=stale_worker_blocked,
            stale_inner_dispatch_count=dispatch_count,
            stopped_runtime_count=int(stale.stopped) + int(current.stopped),
        )
    finally:
        engine.dispose()


def cleanup_j11_owner(
    engine,
    *,
    owner_runtime_instance_id: str | None = None,
    expected_lease_epoch: int | None = None,
) -> J11CleanupResult:
    """Release only an expired/stopped J11 qualification owner via lease CAS.

    This is operator ergonomics for Checkpoint 16 evidence collection. It is
    not a production bypass: a live owner is refused, owner/epoch must match,
    and the actual clear is the same repository release CAS used by runtime
    code.
    """

    explicit = owner_runtime_instance_id is not None or expected_lease_epoch is not None
    if explicit and (
        not owner_runtime_instance_id
        or not isinstance(expected_lease_epoch, int)
        or expected_lease_epoch < 0
    ):
        raise J11QualificationError(
            "j11 cleanup requires both owner_runtime_instance_id and "
            "expected_lease_epoch"
        )

    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        current = uow.research_orchestrations.get(RUN_ID)
        if current is None:
            uow.rollback()
            raise J11QualificationError("run-1 orchestration missing; run seed first")
        if current.owner_runtime_instance_id is None:
            if current.lease_expires_at is not None:
                uow.rollback()
                raise J11QualificationError(
                    "run-1 is unowned but still has lease_expires_at; refusing cleanup"
                )
            if expected_lease_epoch is not None and current.lease_epoch != expected_lease_epoch:
                uow.rollback()
                raise J11QualificationError(
                    "run-1 is already unowned but lease epoch does not match"
                )
            uow.rollback()
            return J11CleanupResult(
                cleanup="ALREADY_UNOWNED",
                owner_runtime_instance_id=None,
                expected_lease_epoch=expected_lease_epoch,
                lease_epoch_after=current.lease_epoch,
                owner_after=None,
                lease_expires_at_after=None,
                derived_owner=not explicit,
                owner_status=None,
                owner_expired=True,
                release_cas_applied=False,
            )

        current_owner = current.owner_runtime_instance_id
        current_epoch = current.lease_epoch
        if explicit:
            assert owner_runtime_instance_id is not None
            assert expected_lease_epoch is not None
            if owner_runtime_instance_id != current_owner or expected_lease_epoch != current_epoch:
                uow.rollback()
                raise J11QualificationError(
                    "j11 cleanup owner/epoch mismatch; refusing to clear owner"
                )
        else:
            owner_runtime_instance_id = current_owner
            expected_lease_epoch = current_epoch

        owner = uow.runtime_instances.get(current_owner)
        if owner is None:
            uow.rollback()
            raise J11QualificationError(
                "j11 cleanup owner runtime_instance is missing; refusing cleanup"
            )
        expired = (
            current.lease_expires_at is not None
            and current.lease_expires_at <= datetime.now(timezone.utc)
        )
        if not _is_stopped_j11_qualification_owner(owner):
            uow.rollback()
            raise J11QualificationError(
                "j11 cleanup owner is not a stopped checkpoint16-j11 qualification runtime"
            )
        if not expired:
            uow.rollback()
            raise J11QualificationError("j11 cleanup refuses to clear a live owner")

        released = uow.research_orchestrations.release_lease(
            RUN_ID,
            owner_runtime_instance_id=owner_runtime_instance_id,
            expected_lease_epoch=expected_lease_epoch,
        )
        if not released:
            uow.rollback()
            raise J11QualificationError(
                "j11 cleanup release CAS failed; owner/epoch changed"
            )
        reloaded = uow.research_orchestrations.get(RUN_ID)
        if reloaded is None:
            uow.rollback()
            raise J11QualificationError("run-1 orchestration disappeared during cleanup")
        uow.commit()

    return J11CleanupResult(
        cleanup="RELEASED_EXPIRED_STOPPED_OWNER",
        owner_runtime_instance_id=owner_runtime_instance_id,
        expected_lease_epoch=expected_lease_epoch,
        lease_epoch_after=reloaded.lease_epoch,
        owner_after=reloaded.owner_runtime_instance_id,
        lease_expires_at_after=(
            reloaded.lease_expires_at.isoformat()
            if reloaded.lease_expires_at is not None
            else None
        ),
        derived_owner=not explicit,
        owner_status=owner.status,
        owner_expired=expired,
        release_cas_applied=True,
    )


def _run_one_race_iteration(
    database_url: str,
    *,
    lease_ttl_seconds: float,
) -> tuple[J11RaceChildResult, J11RaceChildResult]:
    ctx = multiprocessing.get_context("fork")
    start_event = ctx.Event()
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_race_child,
            args=(database_url, "contender-a", start_event, result_queue, lease_ttl_seconds),
        ),
        ctx.Process(
            target=_race_child,
            args=(database_url, "contender-b", start_event, result_queue, lease_ttl_seconds),
        ),
    ]
    for process in processes:
        process.start()
    start_event.set()
    results: list[J11RaceChildResult] = []
    deadline = time.monotonic() + 20
    while len(results) < 2 and time.monotonic() < deadline:
        try:
            results.append(result_queue.get(timeout=0.5))
        except queue.Empty:
            pass
    for process in processes:
        process.join(timeout=5)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    if len(results) != 2:
        raise J11QualificationError(f"expected two child results, got {len(results)}")
    errors = [item for item in results if item.error is not None]
    if errors:
        raise J11QualificationError(f"child process error: {errors[0].error}")
    return (results[0], results[1])


def _run_one_acquire_process(
    database_url: str,
    *,
    label: str,
    lease_ttl_seconds: float,
) -> J11RaceChildResult:
    ctx = multiprocessing.get_context("fork")
    result_queue = ctx.Queue()
    process = ctx.Process(
        target=_acquire_child,
        args=(database_url, label, result_queue, lease_ttl_seconds),
    )
    process.start()
    try:
        result = result_queue.get(timeout=20)
    except queue.Empty as exc:
        raise J11QualificationError(f"{label} acquire child produced no result") from exc
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if result.error is not None:
        raise J11QualificationError(f"{label} acquire child error: {result.error}")
    return result


def _acquire_child(
    database_url: str,
    label: str,
    result_queue,
    lease_ttl_seconds: float,
) -> None:
    engine = create_sync_engine(database_url)
    factory = PostgresUnitOfWork(engine)
    runtime_id = ""
    stopped = False
    try:
        runtime = _register_qualification_runtime(factory, label)
        runtime_id = runtime.runtime_instance_id
        with factory.open() as uow:
            acquired = uow.research_orchestrations.acquire_lease(
                RUN_ID,
                owner_runtime_instance_id=runtime.runtime_instance_id,
                ttl_seconds=lease_ttl_seconds,
            )
            uow.commit()
        epoch = acquired.record.lease_epoch if acquired.record is not None else None
        mark_runtime_status(factory, runtime_id, "STOPPED")
        stopped = True
        result_queue.put(
            J11RaceChildResult(
                label=label,
                process_id=os.getpid(),
                runtime_instance_id=runtime.runtime_instance_id,
                acquire_outcome=acquired.outcome.value,
                acquired_epoch=epoch,
                loser_worker_blocked=False,
                inner_dispatch_count=0,
                stopped=stopped,
            )
        )
    except Exception as exc:
        result_queue.put(
            J11RaceChildResult(
                label=label,
                process_id=os.getpid(),
                runtime_instance_id=runtime_id or "unregistered",
                acquire_outcome="ERROR",
                acquired_epoch=None,
                loser_worker_blocked=False,
                inner_dispatch_count=0,
                stopped=stopped,
                error=f"{type(exc).__name__}: {exc}",
            )
        )
    finally:
        if runtime_id and not stopped:
            try:
                mark_runtime_status(factory, runtime_id, "STOPPED")
            except Exception:
                pass
        engine.dispose()


def _race_child(
    database_url: str,
    label: str,
    start_event,
    result_queue,
    lease_ttl_seconds: float,
) -> None:
    engine = create_sync_engine(database_url)
    factory = PostgresUnitOfWork(engine)
    runtime_id = ""
    stopped = False
    try:
        runtime = _register_qualification_runtime(factory, label)
        runtime_id = runtime.runtime_instance_id
        start_event.wait(timeout=10)
        with factory.open() as uow:
            acquired = uow.research_orchestrations.acquire_lease(
                RUN_ID,
                owner_runtime_instance_id=runtime.runtime_instance_id,
                ttl_seconds=lease_ttl_seconds,
            )
            uow.commit()
        loser_worker_blocked = False
        inner_dispatch_count = 0
        epoch = acquired.record.lease_epoch if acquired.record is not None else None
        if acquired.outcome is not LeaseAcquireOutcome.ACQUIRED:
            loser_worker_blocked, inner_dispatch_count = _stale_worker_is_blocked(
                factory,
                owner_runtime_instance_id=runtime.runtime_instance_id,
                lease_epoch=1,
            )
        mark_runtime_status(factory, runtime.runtime_instance_id, "STOPPED")
        stopped = True
        result_queue.put(
            J11RaceChildResult(
                label=label,
                process_id=os.getpid(),
                runtime_instance_id=runtime.runtime_instance_id,
                acquire_outcome=acquired.outcome.value,
                acquired_epoch=epoch,
                loser_worker_blocked=loser_worker_blocked,
                inner_dispatch_count=inner_dispatch_count,
                stopped=stopped,
            )
        )
    except Exception as exc:
        result_queue.put(
            J11RaceChildResult(
                label=label,
                process_id=os.getpid(),
                runtime_instance_id=runtime_id or "unregistered",
                acquire_outcome="ERROR",
                acquired_epoch=None,
                loser_worker_blocked=False,
                inner_dispatch_count=0,
                stopped=stopped,
                error=f"{type(exc).__name__}: {exc}",
            )
        )
    finally:
        if runtime_id and not stopped:
            try:
                mark_runtime_status(factory, runtime_id, "STOPPED")
            except Exception:
                pass
        engine.dispose()


def _register_qualification_runtime(factory: PostgresUnitOfWork, label: str):
    runtime = register_runtime_instance(
        factory,
        host_identity=f"{socket.gethostname()}:checkpoint16-j11:{label}",
        process_id=str(os.getpid()),
        capabilities_summary={
            "qualification": "checkpoint16-j11-fencing",
            "operator_api": False,
            "worker_capabilities": ["diagnostic.echo"],
            "side_effect_ceiling": 0,
        },
    )
    return mark_runtime_status(factory, runtime.runtime_instance_id, "RUNNING")


def _is_stopped_j11_qualification_owner(owner) -> bool:
    summary = dict(owner.capabilities_summary or {})
    return (
        owner.status == "STOPPED"
        and summary.get("qualification") == "checkpoint16-j11-fencing"
        and summary.get("operator_api") is False
        and summary.get("side_effect_ceiling") == 0
        and "diagnostic.echo" in tuple(summary.get("worker_capabilities") or ())
    )


def _stop_runtimes(factory: PostgresUnitOfWork, runtime_ids: list[str]) -> int:
    stopped = 0
    for runtime_id in runtime_ids:
        try:
            mark_runtime_status(factory, runtime_id, "STOPPED")
            stopped += 1
        except Exception:
            pass
    return stopped


def _stale_worker_is_blocked(
    factory: PostgresUnitOfWork,
    *,
    owner_runtime_instance_id: str,
    lease_epoch: int,
) -> tuple[bool, int]:
    inner = _CountingDiagnosticWorker()
    fenced = LeaseFencedWorkerPort(
        inner,
        factory,
        research_run_id=RUN_ID,
        owner_runtime_instance_id=owner_runtime_instance_id,
        lease_epoch=lease_epoch,
    )
    try:
        fenced.invoke(
            {
                "worker_capability": "diagnostic.echo",
                "action": "echo",
                "message": "checkpoint16-j11",
                "side_effect_level": 0,
            },
            timeout_ms=1000,
        )
    except LeaseFencingError:
        return True, inner.invocation_count
    return False, inner.invocation_count


def _stale_save_is_blocked(
    factory: PostgresUnitOfWork,
    *,
    owner_runtime_instance_id: str,
    lease_epoch: int,
) -> bool:
    fenced_factory = SingleRunFencedUowFactory(
        factory,
        research_run_id=RUN_ID,
        owner_runtime_instance_id=owner_runtime_instance_id,
        lease_epoch=lease_epoch,
    )
    with fenced_factory.open() as uow:
        current = uow.research_orchestrations.get(RUN_ID)
        if current is None:
            uow.rollback()
            raise J11QualificationError("orchestration disappeared")
        try:
            uow.research_orchestrations.save(
                replace(current, last_phase="j11_stale_write_should_fail")
            )
        except LeaseFencingError:
            uow.rollback()
            return True
        uow.rollback()
        return False


def _release_owner(
    database_url: str,
    *,
    owner_runtime_instance_id: str,
    lease_epoch: int,
) -> None:
    engine = create_sync_engine(database_url)
    try:
        factory = PostgresUnitOfWork(engine)
        with factory.open() as uow:
            uow.research_orchestrations.release_lease(
                RUN_ID,
                owner_runtime_instance_id=owner_runtime_instance_id,
                expected_lease_epoch=lease_epoch,
            )
            uow.commit()
    finally:
        engine.dispose()


def _load_orchestration(database_url: str):
    engine = create_sync_engine(database_url)
    try:
        factory = PostgresUnitOfWork(engine)
        with factory.open() as uow:
            current = uow.research_orchestrations.get(RUN_ID)
            uow.rollback()
        if current is None:
            raise J11QualificationError("orchestration missing")
        return current
    finally:
        engine.dispose()
