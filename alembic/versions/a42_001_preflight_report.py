"""Checkpoint 15 durable operator Preflight report.

Append-only evidence of what WAS true at Preflight time. Never authorizes
a later START. Does not delete rows.

Revision ID: a42_001_preflight_report
Revises: a41_001_runtime_instance
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a42_001_preflight_report"
down_revision: Union[str, Sequence[str], None] = "a41_001_runtime_instance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "preflight_report",
        sa.Column("preflight_report_id", sa.Text(), primary_key=True),
        sa.Column(
            "research_run_id",
            sa.Text(),
            sa.ForeignKey("research_run.research_run_id"),
            nullable=False,
        ),
        sa.Column(
            "runtime_instance_id",
            sa.Text(),
            sa.ForeignKey("runtime_instance.runtime_instance_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_version", sa.Text(), nullable=False),
        sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("checks", JSONB(), nullable=False),
        sa.CheckConstraint(
            "status IN ('READY_TO_START', 'NOT_READY')",
            name="ck_preflight_report_status",
        ),
    )
    op.create_index(
        "ix_preflight_report_run_created",
        "preflight_report",
        ["research_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_preflight_report_run_created", table_name="preflight_report")
    op.drop_table("preflight_report")
