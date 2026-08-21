"""Add promotion_run durable PromotionPipeline state (canonical MR-5).

Additive table only. Does not rewrite Evidence, Candidate, Verification, or
FindingProposal. Finding is still created only after Human Review + Core
Approval.

Revision ID: a38_001_promotion_run
Revises: a37_001_impact_edge_proof
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a38_001_promotion_run"
down_revision: Union[str, Sequence[str], None] = "a37_001_impact_edge_proof"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_run",
        sa.Column("promotion_run_id", sa.Text(), primary_key=True),
        sa.Column("research_run_id", sa.Text(), nullable=False),
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("original_experiment_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.Text(), nullable=True),
        sa.Column("verification_id", sa.Text(), nullable=True),
        sa.Column("finding_proposal_id", sa.Text(), nullable=True),
        sa.Column("reproduction_experiment_id", sa.Text(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_run.research_run_id"],
            name="fk_promotion_run_research_run",
        ),
        sa.UniqueConstraint("assessment_id", name="uq_promotion_run_assessment"),
        sa.CheckConstraint(
            "stage IN ("
            "'EVIDENCE_REJECTED', 'EVIDENCE_ADMITTED', 'CANDIDATE_REJECTED', "
            "'CANDIDATE_OPEN', 'VERIFYING', 'REPRODUCTION_EXECUTED', "
            "'VERIFIED', 'PROPOSAL_RECORDED', 'STOPPED')",
            name="ck_promotion_run_stage",
        ),
    )
    op.create_index(
        "ix_promotion_run_research_run",
        "promotion_run",
        ["research_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_promotion_run_research_run", table_name="promotion_run")
    op.drop_table("promotion_run")
