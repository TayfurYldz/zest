"""OAST-1 provider-neutral correlation and callback-delivery persistence."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a44_001_oast_correlation"
down_revision: Union[str, Sequence[str], None] = "a43_001_budget_type_check_repair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _append_only_trigger(table_name: str) -> None:
    op.execute(
        sa.text(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE PROCEDURE research_os_reject_mutation();"
        )
    )


def upgrade() -> None:
    op.create_table(
        "oast_correlation",
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("experiment_id", sa.Text(), nullable=False),
        sa.Column("research_run_id", sa.Text(), nullable=False),
        sa.Column("target_reference", sa.Text(), nullable=False),
        sa.Column("identity_id", sa.Text(), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id", "research_run_id"],
            ["execution_attempt.attempt_id", "execution_attempt.research_run_id"],
            name="fk_oast_correlation_attempt_same_run",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "research_run_id"],
            ["experiment_plan.experiment_id", "experiment_plan.research_run_id"],
            name="fk_oast_correlation_plan_same_run",
        ),
        sa.PrimaryKeyConstraint("correlation_id", name="pk_oast_correlation"),
        sa.UniqueConstraint(
            "correlation_id",
            "research_run_id",
            name="uq_oast_correlation_id_run",
        ),
        sa.UniqueConstraint("attempt_id", name="uq_oast_correlation_attempt"),
        sa.CheckConstraint(
            "expires_at > armed_at",
            name="ck_oast_correlation_window",
        ),
    )
    op.create_table(
        "oast_callback_delivery",
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("provider_adapter_id", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "normalized_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("normalized_digest", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["correlation_id"],
            ["oast_correlation.correlation_id"],
            name="fk_oast_callback_correlation",
        ),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_oast_callback_delivery"),
        sa.UniqueConstraint(
            "provider_adapter_id",
            "provider_event_id",
            name="uq_oast_callback_provider_event",
        ),
        sa.UniqueConstraint(
            "correlation_id",
            "normalized_digest",
            name="uq_oast_callback_correlation_digest",
        ),
        sa.CheckConstraint(
            "char_length(provider_adapter_id) BETWEEN 1 AND 128",
            name="ck_oast_callback_provider_adapter_id",
        ),
        sa.CheckConstraint(
            "char_length(provider_event_id) BETWEEN 1 AND 256",
            name="ck_oast_callback_provider_event_id",
        ),
        sa.CheckConstraint(
            "normalized_payload IS NOT NULL "
            "AND jsonb_typeof(normalized_payload) = 'object' "
            "AND octet_length(normalized_payload::text) <= 16384",
            name="ck_oast_callback_payload_bounded",
        ),
        sa.CheckConstraint(
            "normalized_digest ~ '^[0-9a-fA-F]{64}$'",
            name="ck_oast_callback_normalized_digest",
        ),
    )
    op.create_index(
        "ix_oast_callback_correlation_received",
        "oast_callback_delivery",
        ["correlation_id", "received_at"],
    )
    op.create_table(
        "oast_admission",
        sa.Column("admission_id", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("research_run_id", sa.Text(), nullable=False),
        sa.Column("sensor_observation_id", sa.Text(), nullable=False),
        sa.Column("discovery_fact_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["correlation_id", "research_run_id"],
            ["oast_correlation.correlation_id", "oast_correlation.research_run_id"],
            name="fk_oast_admission_correlation_same_run",
        ),
        sa.ForeignKeyConstraint(
            ["sensor_observation_id", "research_run_id"],
            ["sensor_observation.observation_id", "sensor_observation.research_run_id"],
            name="fk_oast_admission_sensor_observation_same_run",
        ),
        sa.ForeignKeyConstraint(
            ["discovery_fact_id", "research_run_id"],
            ["discovery_fact.fact_id", "discovery_fact.research_run_id"],
            name="fk_oast_admission_discovery_fact_same_run",
        ),
        sa.PrimaryKeyConstraint("admission_id", name="pk_oast_admission"),
        sa.UniqueConstraint("correlation_id", name="uq_oast_admission_correlation"),
    )
    _append_only_trigger("oast_correlation")
    _append_only_trigger("oast_callback_delivery")
    _append_only_trigger("oast_admission")


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ("oast_admission", "oast_callback_delivery", "oast_correlation"):
        if bind.execute(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")
        ).scalar():
            raise RuntimeError(
                "cannot downgrade a44: OAST-1 data exists; no rows were deleted"
            )
    op.execute(sa.text("DROP TRIGGER trg_oast_admission_append_only ON oast_admission"))
    op.execute(
        sa.text(
            "DROP TRIGGER trg_oast_callback_delivery_append_only "
            "ON oast_callback_delivery"
        )
    )
    op.execute(sa.text("DROP TRIGGER trg_oast_correlation_append_only ON oast_correlation"))
    op.drop_table("oast_admission")
    op.drop_index(
        "ix_oast_callback_correlation_received",
        table_name="oast_callback_delivery",
    )
    op.drop_table("oast_callback_delivery")
    op.drop_table("oast_correlation")
