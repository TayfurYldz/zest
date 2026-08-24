"""Destructive staging-spine primitives for Checkpoint 16 qualification.

Not a normal runtime mutation path. Callers must gate truncate explicitly.
Does not print secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Engine

from research_os.data.postgres.hunter_family_seed import SEED_FAMILIES
from research_os.data.postgres.tables import hunter_family, metadata
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    AuthorizationSourceRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)

DEFAULT_STAGING_NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)
TRUNCATE_GUARD = "RESEARCH_OSD_ALLOW_SPINE_TRUNCATE"


class StagingTruncateDenied(Exception):
    """Caller asked to seed/truncate without explicit staging authorization."""


def require_explicit_spine_truncate(*, truncate: bool, env: Mapping[str, str]) -> None:
    if not truncate:
        raise StagingTruncateDenied(
            "seed requires --truncate on a dedicated staging database"
        )
    if env.get(TRUNCATE_GUARD) != "YES":
        raise StagingTruncateDenied(f"set {TRUNCATE_GUARD}=YES to allow spine truncate")


def truncate_spine(
    engine: Engine,
    *,
    created_at: datetime | None = None,
    preserve_runtime_instances: bool = True,
) -> None:
    """TRUNCATE application spine tables and re-seed HunterFamily rows.

    Does not check RESEARCH_OSD_ALLOW_SPINE_TRUNCATE. The VDS fixture must
    call require_explicit_spine_truncate first. Integration tests call this
    only against the dedicated test database.

    By default, preserve_runtime_instances=True preserves runtime_instance
    records so running daemon processes (e.g. systemd research-osd) do not
    lose their registered process identity and fail heartbeat validation.
    """

    now = created_at or DEFAULT_STAGING_NOW
    tables = [
        table
        for table in metadata.sorted_tables
        if not (preserve_runtime_instances and table.name == "runtime_instance")
    ]
    table_names = ", ".join(f'"{table.name}"' for table in tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
        connection.execute(
            hunter_family.insert(),
            [{**family, "created_at": now} for family in SEED_FAMILIES],
        )


def seed_authorized_spine(
    uow: PostgresUnitOfWork,
    *,
    created_at: datetime | None = None,
) -> None:
    """Insert the lab program/run/budget/hypothesis/experiment fixture rows."""

    now = created_at or DEFAULT_STAGING_NOW
    uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=now, name="lab"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="written-auth-1",
            created_at=now,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id="run-1",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=now,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=10,
            max_tool_calls=10,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=now,
        )
    )
    uow.hypotheses.insert(
        HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            origin_reference="human-seed-1",
            created_at=now,
        )
    )
    uow.experiments.insert(
        ExperimentRecord(
            experiment_id="exp-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            execution_state="PLANNED",
            created_at=now,
        )
    )
