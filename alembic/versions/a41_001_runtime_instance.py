"""Phase J durable runtime_instance identity for research-osd.

One row per daemon process start. Process/host metadata is operational only
and is never reused as ownership across restart. Does not delete rows.

Revision ID: a41_001_runtime_instance
Revises: a40_001_mr6a_identity_anomaly
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a41_001_runtime_instance"
down_revision: Union[str, Sequence[str], None] = "a40_001_mr6a_identity_anomaly"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runtime_instance",
        sa.Column("runtime_instance_id", sa.Text(), primary_key=True),
        sa.Column("host_identity", sa.Text(), nullable=False),
        sa.Column("process_id", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("capabilities_summary", JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('STARTING', 'RUNNING', 'DRAINING', 'STOPPED')",
            name="ck_runtime_instance_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("runtime_instance")
