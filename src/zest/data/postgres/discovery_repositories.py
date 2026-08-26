"""PostgreSQL repositories for GATE 22 discovery tables. Persistence only."""

from __future__ import annotations

from dataclasses import asdict
from sqlalchemy import select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from zest.data.errors import PersistenceConflictError, PersistenceError
from zest.data.postgres import mapping as map_row
from zest.data.postgres import tables
from zest.data.postgres.repositories import (
    _apply_read_limit,
    _execute_write,
    _fetch_one,
)
from zest.data.records import (
    AttackSurfaceSnapshotRecord,
    ControlEventRecord,
    CoverageDebtSnapshotRecord,
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    DiscoveryInferenceRecord,
    DiscoveryInferenceSourceRecord,
    DiscoveryProjectionReceiptRecord,
    DiscoveryRunConfigRecord,
    FrontierEventRecord,
    FrontierItemRecord,
    FrontierSourceRecord,
    require_opaque_id,
)


def _list_by_run(
    connection: Connection,
    table,
    run_id: str,
    builder,
    order_column,
    *,
    limit: int | None = None,
):
    require_opaque_id(run_id, "research_run_id")
    try:
        rows = (
            connection.execute(
                _apply_read_limit(
                    select(table).where(
                        table.c.research_run_id == run_id
                    ).order_by(order_column),
                    limit,
                )
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError as exc:
        raise PersistenceError("persistence read failed") from exc
    return [builder(row) for row in rows]


class PostgresDiscoveryRunConfigRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryRunConfigRecord) -> None:
        _execute_write(
            self._connection,
            tables.discovery_run_config.insert().values(**asdict(record)),
        )

    def get(self, research_run_id: str) -> DiscoveryRunConfigRecord | None:
        require_opaque_id(research_run_id, "research_run_id")
        return _fetch_one(
            self._connection,
            tables.discovery_run_config,
            tables.discovery_run_config.c.research_run_id,
            research_run_id,
            map_row.discovery_run_config_from_row,
        )


class PostgresControlEventRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ControlEventRecord) -> None:
        _execute_write(self._connection, tables.control_event.insert().values(**asdict(record)))

    def get(self, control_event_id: str) -> ControlEventRecord | None:
        require_opaque_id(control_event_id, "control_event_id")
        return _fetch_one(
            self._connection,
            tables.control_event,
            tables.control_event.c.control_event_id,
            control_event_id,
            map_row.control_event_from_row,
        )

    def get_by_worker_result(self, worker_result_id: str) -> ControlEventRecord | None:
        require_opaque_id(worker_result_id, "worker_result_id")
        try:
            row = (
                self._connection.execute(
                    select(tables.control_event).where(
                        tables.control_event.c.worker_result_id == worker_result_id
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return map_row.control_event_from_row(row) if row else None

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ControlEventRecord]:
        return _list_by_run(
            self._connection,
            tables.control_event,
            research_run_id,
            map_row.control_event_from_row,
            tables.control_event.c.control_event_id,
            limit=limit,
        )


class PostgresDiscoveryFactRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryFactRecord) -> None:
        _execute_write(self._connection, tables.discovery_fact.insert().values(**asdict(record)))

    def get(self, fact_id: str) -> DiscoveryFactRecord | None:
        require_opaque_id(fact_id, "fact_id")
        return _fetch_one(
            self._connection,
            tables.discovery_fact,
            tables.discovery_fact.c.fact_id,
            fact_id,
            map_row.discovery_fact_from_row,
        )

    def get_by_canonical(self, research_run_id: str, canonical_key: str) -> DiscoveryFactRecord | None:
        require_opaque_id(research_run_id, "research_run_id")
        require_opaque_id(canonical_key, "canonical_key")
        try:
            row = (
                self._connection.execute(
                    select(tables.discovery_fact).where(
                        tables.discovery_fact.c.research_run_id == research_run_id,
                        tables.discovery_fact.c.canonical_key == canonical_key,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return map_row.discovery_fact_from_row(row) if row else None

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[DiscoveryFactRecord]:
        return _list_by_run(
            self._connection,
            tables.discovery_fact,
            research_run_id,
            map_row.discovery_fact_from_row,
            tables.discovery_fact.c.canonical_key,
            limit=limit,
        )


class PostgresDiscoveryFactSourceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryFactSourceRecord) -> None:
        _execute_write(
            self._connection, tables.discovery_fact_source.insert().values(**asdict(record))
        )

    def list_for_fact(self, fact_id: str) -> list[DiscoveryFactSourceRecord]:
        require_opaque_id(fact_id, "fact_id")
        try:
            rows = (
                self._connection.execute(
                    select(tables.discovery_fact_source).where(
                        tables.discovery_fact_source.c.fact_id == fact_id
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.discovery_fact_source_from_row(row) for row in rows]


class PostgresDiscoveryInferenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryInferenceRecord) -> None:
        _execute_write(
            self._connection, tables.discovery_inference.insert().values(**asdict(record))
        )

    def get(self, inference_id: str) -> DiscoveryInferenceRecord | None:
        require_opaque_id(inference_id, "inference_id")
        return _fetch_one(
            self._connection,
            tables.discovery_inference,
            tables.discovery_inference.c.inference_id,
            inference_id,
            map_row.discovery_inference_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[DiscoveryInferenceRecord]:
        return _list_by_run(
            self._connection,
            tables.discovery_inference,
            research_run_id,
            map_row.discovery_inference_from_row,
            tables.discovery_inference.c.canonical_key,
            limit=limit,
        )


class PostgresDiscoveryInferenceSourceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryInferenceSourceRecord) -> None:
        _execute_write(
            self._connection,
            tables.discovery_inference_source.insert().values(**asdict(record)),
        )


class PostgresFrontierItemRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: FrontierItemRecord) -> None:
        _execute_write(self._connection, tables.frontier_item.insert().values(**asdict(record)))

    def get(self, frontier_id: str) -> FrontierItemRecord | None:
        require_opaque_id(frontier_id, "frontier_id")
        return _fetch_one(
            self._connection,
            tables.frontier_item,
            tables.frontier_item.c.frontier_id,
            frontier_id,
            map_row.frontier_item_from_row,
        )

    def lock(self, frontier_id: str) -> FrontierItemRecord | None:
        require_opaque_id(frontier_id, "frontier_id")
        try:
            row = (
                self._connection.execute(
                    select(tables.frontier_item)
                    .where(tables.frontier_item.c.frontier_id == frontier_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return map_row.frontier_item_from_row(row) if row else None

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[FrontierItemRecord]:
        return _list_by_run(
            self._connection,
            tables.frontier_item,
            research_run_id,
            map_row.frontier_item_from_row,
            tables.frontier_item.c.dedupe_identity,
            limit=limit,
        )

    def set_cache_state(
        self, frontier_id: str, current_state: str, state_version: int
    ) -> None:
        require_opaque_id(frontier_id, "frontier_id")
        result = self._connection.execute(
            update(tables.frontier_item)
            .where(tables.frontier_item.c.frontier_id == frontier_id)
            .values(current_state=current_state, state_version=state_version)
        )
        if result.rowcount != 1:
            raise PersistenceError("frontier_item not found for cache update")


class PostgresFrontierSourceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: FrontierSourceRecord) -> None:
        _execute_write(self._connection, tables.frontier_source.insert().values(**asdict(record)))


class PostgresFrontierEventRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: FrontierEventRecord) -> None:
        _execute_write(self._connection, tables.frontier_event.insert().values(**asdict(record)))

    def list_for_frontier(self, frontier_id: str) -> list[FrontierEventRecord]:
        require_opaque_id(frontier_id, "frontier_id")
        try:
            rows = (
                self._connection.execute(
                    select(tables.frontier_event)
                    .where(tables.frontier_event.c.frontier_id == frontier_id)
                    .order_by(tables.frontier_event.c.sequence)
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.frontier_event_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[FrontierEventRecord]:
        return _list_by_run(
            self._connection,
            tables.frontier_event,
            research_run_id,
            map_row.frontier_event_from_row,
            tables.frontier_event.c.sequence,
            limit=limit,
        )


class PostgresDiscoveryProjectionReceiptRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DiscoveryProjectionReceiptRecord) -> None:
        _execute_write(
            self._connection,
            tables.discovery_projection_receipt.insert().values(**asdict(record)),
        )

    def has_observation(self, research_run_id: str, observation_id: str) -> bool:
        require_opaque_id(research_run_id, "research_run_id")
        require_opaque_id(observation_id, "observation_id")
        try:
            row = self._connection.execute(
                select(tables.discovery_projection_receipt.c.receipt_id).where(
                    tables.discovery_projection_receipt.c.research_run_id == research_run_id,
                    tables.discovery_projection_receipt.c.observation_id == observation_id,
                )
            ).first()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return row is not None

    def has_control_event(self, research_run_id: str, control_event_id: str) -> bool:
        require_opaque_id(research_run_id, "research_run_id")
        require_opaque_id(control_event_id, "control_event_id")
        try:
            row = self._connection.execute(
                select(tables.discovery_projection_receipt.c.receipt_id).where(
                    tables.discovery_projection_receipt.c.research_run_id == research_run_id,
                    tables.discovery_projection_receipt.c.control_event_id == control_event_id,
                )
            ).first()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return row is not None


class PostgresAttackSurfaceSnapshotRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: AttackSurfaceSnapshotRecord) -> None:
        _execute_write(
            self._connection,
            tables.attack_surface_snapshot.insert().values(**asdict(record)),
        )

    def get(self, snapshot_id: str) -> AttackSurfaceSnapshotRecord | None:
        require_opaque_id(snapshot_id, "snapshot_id")
        return _fetch_one(
            self._connection,
            tables.attack_surface_snapshot,
            tables.attack_surface_snapshot.c.snapshot_id,
            snapshot_id,
            map_row.attack_surface_snapshot_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[AttackSurfaceSnapshotRecord]:
        return _list_by_run(
            self._connection,
            tables.attack_surface_snapshot,
            research_run_id,
            map_row.attack_surface_snapshot_from_row,
            tables.attack_surface_snapshot.c.created_at,
            limit=limit,
        )


class PostgresCoverageDebtSnapshotRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: CoverageDebtSnapshotRecord) -> None:
        _execute_write(
            self._connection,
            tables.coverage_debt_snapshot.insert().values(**asdict(record)),
        )

    def get(self, snapshot_id: str) -> CoverageDebtSnapshotRecord | None:
        require_opaque_id(snapshot_id, "snapshot_id")
        return _fetch_one(
            self._connection,
            tables.coverage_debt_snapshot,
            tables.coverage_debt_snapshot.c.snapshot_id,
            snapshot_id,
            map_row.coverage_debt_snapshot_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[CoverageDebtSnapshotRecord]:
        return _list_by_run(
            self._connection,
            tables.coverage_debt_snapshot,
            research_run_id,
            map_row.coverage_debt_snapshot_from_row,
            tables.coverage_debt_snapshot.c.created_at,
            limit=limit,
        )
