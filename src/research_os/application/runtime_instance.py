"""Durable research-osd process identity. Not research authority."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.data.records import RuntimeInstanceRecord

ENGINE_VERSION = "phase-j.1"

_ACTIVE_STATUSES = frozenset({"STARTING", "RUNNING", "DRAINING"})


def register_runtime_instance(
    uow_factory: UnitOfWorkFactory,
    *,
    host_identity: str,
    process_id: str,
    clock: Clock | None = None,
    engine_version: str = ENGINE_VERSION,
    capabilities_summary: Mapping[str, object] | None = None,
) -> RuntimeInstanceRecord:
    """Insert one new runtime_instance row. Every process start gets a new id."""

    now = (clock or SystemClock()).now()
    record = RuntimeInstanceRecord(
        runtime_instance_id=new_opaque_id(),
        host_identity=host_identity,
        process_id=process_id,
        engine_version=engine_version,
        status="STARTING",
        capabilities_summary=dict(capabilities_summary or {"operator_api": True}),
        started_at=now,
        last_seen_at=now,
    )
    with uow_factory.open() as uow:
        uow.runtime_instances.insert(record)
        uow.commit()
    return record


def mark_runtime_status(
    uow_factory: UnitOfWorkFactory,
    runtime_instance_id: str,
    status: str,
    *,
    clock: Clock | None = None,
) -> RuntimeInstanceRecord:
    now = (clock or SystemClock()).now()
    with uow_factory.open() as uow:
        current = uow.runtime_instances.get(runtime_instance_id)
        if current is None:
            uow.rollback()
            raise ApplicationError("runtime instance not found")
        updated = replace(
            current,
            status=status,
            last_seen_at=now,
            stopped_at=now if status == "STOPPED" else current.stopped_at,
        )
        uow.runtime_instances.save(updated)
        uow.commit()
    return updated


def heartbeat_runtime_instance(
    uow_factory: UnitOfWorkFactory,
    runtime_instance_id: str,
    *,
    clock: Clock | None = None,
) -> RuntimeInstanceRecord:
    now = (clock or SystemClock()).now()
    with uow_factory.open() as uow:
        current = uow.runtime_instances.get(runtime_instance_id)
        if current is None:
            uow.rollback()
            raise ApplicationError("runtime instance not found")
        if current.status not in _ACTIVE_STATUSES:
            uow.rollback()
            raise ApplicationError("runtime instance is not active")
        updated = replace(current, last_seen_at=now)
        uow.runtime_instances.save(updated)
        uow.commit()
    return updated
