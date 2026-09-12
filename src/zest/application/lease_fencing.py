"""Compose fenced UnitOfWork and WorkerPort around existing lease identity.

Does not change ARC internals. Used only by zestd when it already
holds (owner_runtime_instance_id, lease_epoch) for one run. A stale owner
cannot persist orchestration or invoke Worker.
"""

from __future__ import annotations

from typing import Mapping

from zest.application.ports import UnitOfWorkFactory
from zest.data.errors import LeaseFencingError, PersistenceError
from zest.platform.worker import WorkerInvocationOutcome, WorkerPort


class FencedOrchestrationRepository:
    def __init__(
        self,
        inner,
        *,
        owner_runtime_instance_id: str,
        lease_epoch: int,
    ) -> None:
        self._inner = inner
        self._owner_runtime_instance_id = owner_runtime_instance_id
        self._lease_epoch = lease_epoch

    def save(self, record, **kwargs):
        # The wrapper owns the fencing identity. A caller may not
        # override it with a newer/different lease.
        kwargs["expect_owner_runtime_instance_id"] = (
            self._owner_runtime_instance_id
        )
        kwargs["expect_lease_epoch"] = (
            self._lease_epoch
        )
        return self._inner.save(record, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class SingleRunFencedUnitOfWork:
    def __init__(
        self,
        inner,
        *,
        research_run_id: str,
        owner_runtime_instance_id: str,
        lease_epoch: int,
    ) -> None:
        self._inner = inner
        self._research_run_id = research_run_id
        self._owner_runtime_instance_id = (
            owner_runtime_instance_id
        )
        self._lease_epoch = lease_epoch

    def __enter__(self) -> "SingleRunFencedUnitOfWork":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._inner.__exit__(exc_type, exc, tb)

    def commit(self) -> None:
        # Linearize ownership immediately before commit.
        #
        # PostgreSQL keeps the orchestration row locked until the
        # underlying transaction commits, so another runtime cannot
        # advance the lease epoch between this check and persistence
        # of the rest of this UnitOfWork.
        try:
            (
                self._inner.research_orchestrations
                .assert_lease_current_for_update(
                    self._research_run_id,
                    owner_runtime_instance_id=(
                        self._owner_runtime_instance_id
                    ),
                    expected_lease_epoch=(
                        self._lease_epoch
                    ),
                )
            )
        except PersistenceError:
            self._inner.rollback()
            raise

        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    @property
    def research_orchestrations(self):
        return FencedOrchestrationRepository(
            self._inner.research_orchestrations,
            owner_runtime_instance_id=self._owner_runtime_instance_id,
            lease_epoch=self._lease_epoch,
        )

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class SingleRunFencedUowFactory:
    def __init__(
        self,
        inner: UnitOfWorkFactory,
        *,
        research_run_id: str,
        owner_runtime_instance_id: str,
        lease_epoch: int,
    ) -> None:
        self._inner = inner
        self._research_run_id = research_run_id
        self._owner_runtime_instance_id = (
            owner_runtime_instance_id
        )
        self._lease_epoch = lease_epoch

    def open(self) -> SingleRunFencedUnitOfWork:
        return SingleRunFencedUnitOfWork(
            self._inner.open(),
            research_run_id=self._research_run_id,
            owner_runtime_instance_id=(
                self._owner_runtime_instance_id
            ),
            lease_epoch=self._lease_epoch,
        )


class LeaseFencedWorkerPort:
    """Refuse Worker.invoke unless the remembered lease still matches SoR."""

    def __init__(
        self,
        inner: WorkerPort,
        uow_factory: UnitOfWorkFactory,
        *,
        research_run_id: str,
        owner_runtime_instance_id: str,
        lease_epoch: int,
    ) -> None:
        self._inner = inner
        self._uow_factory = uow_factory
        self._research_run_id = research_run_id
        self._owner_runtime_instance_id = owner_runtime_instance_id
        self._lease_epoch = lease_epoch

    def invoke(
        self,
        request: Mapping[str, object],
        *,
        timeout_ms: int | None = None,
    ) -> WorkerInvocationOutcome:
        try:
            with self._uow_factory.open() as uow:
                record = uow.research_orchestrations.get(self._research_run_id)
                uow.rollback()
        except PersistenceError as exc:
            raise LeaseFencingError(
                "lease cannot be confirmed; refusing Worker dispatch"
            ) from exc
        if record is None:
            raise LeaseFencingError("orchestration not found; refusing Worker dispatch")
        if (
            record.owner_runtime_instance_id != self._owner_runtime_instance_id
            or record.lease_epoch != self._lease_epoch
        ):
            raise LeaseFencingError(
                "stale lease epoch cannot dispatch Worker "
                f"(expected owner={self._owner_runtime_instance_id!r} "
                f"epoch={self._lease_epoch}; "
                f"actual owner={record.owner_runtime_instance_id!r} "
                f"epoch={record.lease_epoch})"
            )
        return self._inner.invoke(request, timeout_ms=timeout_ms)
