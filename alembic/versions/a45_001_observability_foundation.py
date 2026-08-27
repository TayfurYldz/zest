"""Durable operational faults and attempt contact truth."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a45_001_observability_foundation"
down_revision: Union[str, Sequence[str], None] = "a44_001_oast_correlation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "execution_attempt",
        sa.Column(
            "target_contact_status",
            sa.Text(),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.create_check_constraint(
        "ck_execution_attempt_target_contact_status",
        "execution_attempt",
        "target_contact_status IN ('CONFIRMED', 'NOT_CONTACTED', 'UNKNOWN')",
    )
    op.create_table(
        "run_fault",
        sa.Column("fault_id", sa.Text(), nullable=False),
        sa.Column(
            "research_run_id",
            sa.Text(),
            sa.ForeignKey("research_run.research_run_id"),
            nullable=False,
        ),
        sa.Column("hypothesis_id", sa.Text(), nullable=True),
        sa.Column("experiment_id", sa.Text(), nullable=True),
        sa.Column("attempt_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("capability", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column(
            "runtime_instance_id",
            sa.Text(),
            sa.ForeignKey("runtime_instance.runtime_instance_id"),
            nullable=True,
        ),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("fault_class", sa.Text(), nullable=False),
        sa.Column("fault_code", sa.Text(), nullable=False),
        sa.Column("fatal", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("diagnostic_summary", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "research_run_id"],
            ["hypothesis.hypothesis_id", "hypothesis.research_run_id"],
            name="fk_run_fault_hypothesis_same_run",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "research_run_id"],
            ["experiment.experiment_id", "experiment.research_run_id"],
            name="fk_run_fault_experiment_same_run",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "research_run_id"],
            ["execution_attempt.attempt_id", "execution_attempt.research_run_id"],
            name="fk_run_fault_attempt_same_run",
        ),
        sa.PrimaryKeyConstraint("fault_id", name="pk_run_fault"),
        sa.CheckConstraint(
            "component IN ('CONTROL', 'EXECUTION', 'WORKER', 'RUNTIME', 'SUPERVISOR', 'PERSISTENCE', 'PLANNING')",
            name="ck_run_fault_component",
        ),
        sa.CheckConstraint(
            "phase IN ('AUTHORIZATION', 'DISPATCH', 'INVOCATION', 'INGESTION', 'TICK', 'HEARTBEAT', 'RECONCILIATION', 'PERSISTENCE', 'PLANNING')",
            name="ck_run_fault_phase",
        ),
        sa.CheckConstraint(
            "fault_class IN ('EXECUTION', 'RUNTIME', 'SUPERVISOR', 'PERSISTENCE', 'POLICY')",
            name="ck_run_fault_class",
        ),
        sa.CheckConstraint(
            "char_length(fault_code) BETWEEN 1 AND 128",
            name="ck_run_fault_code",
        ),
        sa.CheckConstraint(
            "char_length(diagnostic_summary) BETWEEN 1 AND 1000",
            name="ck_run_fault_diagnostic_summary",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= occurred_at",
            name="ck_run_fault_resolution_order",
        ),
    )
    op.create_index(
        "ix_run_fault_run_occurred",
        "run_fault",
        ["research_run_id", "occurred_at"],
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_run_fault_append_only "
            "BEFORE UPDATE OR DELETE ON run_fault "
            "FOR EACH ROW EXECUTE PROCEDURE research_os_reject_mutation();"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM run_fault)")).scalar():
        raise RuntimeError("cannot downgrade a45: RunFault data exists; no rows were deleted")
    op.execute(sa.text("DROP TRIGGER trg_run_fault_append_only ON run_fault"))
    op.drop_index("ix_run_fault_run_occurred", table_name="run_fault")
    op.drop_table("run_fault")
    op.drop_constraint(
        "ck_execution_attempt_target_contact_status",
        "execution_attempt",
        type_="check",
    )
    op.drop_column("execution_attempt", "target_contact_status")
