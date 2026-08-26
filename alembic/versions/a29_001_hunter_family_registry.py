"""SD-G5 HunterFamily registry + V3 hunt queue.

Revision ID: a29_001_hunter_family_registry
Revises: a28_001_token_economy
Create Date: 2026-08-19

Adds hunter_family (data-driven hypothesis family registry) and hunt_v3_queue
(pending active-experiment queue). Registry seed rows for 5 families.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.data.postgres.tables import hunter_family

revision: str = "a29_001_hunter_family_registry"
down_revision: Union[str, Sequence[str], None] = "a28_001_token_economy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hunter_family",
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "target_node_kinds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "preconditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("claim_template", sa.Text(), nullable=False),
        sa.Column(
            "evidence_requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("validation_tier", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("family_id", "version"),
        sa.CheckConstraint(
            "validation_tier IN ('V1', 'V2', 'V3')",
            name="ck_hunter_family_validation_tier",
        ),
        sa.CheckConstraint("version >= 1", name="ck_hunter_family_version_positive"),
    )

    op.create_table(
        "hunt_v3_queue",
        sa.Column("queue_id", sa.Text(), nullable=False),
        sa.Column("research_run_id", sa.Text(), nullable=False),
        sa.Column("hypothesis_id", sa.Text(), nullable=False),
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("node_canonical_key", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("side_effect_level", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_run.research_run_id"],
            name="fk_hunt_v3_queue_research_run",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id", "research_run_id"],
            ["hypothesis.hypothesis_id", "hypothesis.research_run_id"],
            name="fk_hunt_v3_queue_hypothesis_same_run",
        ),
        sa.PrimaryKeyConstraint("queue_id"),
        sa.CheckConstraint(
            "side_effect_level IN (0, 1, 2, 3)",
            name="ck_hunt_v3_queue_side_effect_level",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING', 'APPROVED', 'RUN', 'BLOCKED')",
            name="ck_hunt_v3_queue_state",
        ),
    )

    op.bulk_insert(
        hunter_family,
        [
            {
                **family,
                "created_at": datetime.now(timezone.utc),
            }
            for family in SEED_FAMILIES
        ],
    )


def downgrade() -> None:
    op.drop_table("hunt_v3_queue")
    op.drop_table("hunter_family")
