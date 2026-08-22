"""MR-6A registry-external identity-anomaly admission durability.

Adds REGISTRY_EXTERNAL_ANOMALY as an opportunity-candidate source system and a
partial unique index so one ResearchRun cannot persist two exploratory
hypotheses for the same identity-anomaly origin. Does not delete rows.

Revision ID: a40_001_mr6a_identity_anomaly
Revises: a39_001_mr5_durability_uq
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a40_001_mr6a_identity_anomaly"
down_revision: Union[str, Sequence[str], None] = "a39_001_mr5_durability_uq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        type_="check",
    )
    op.create_check_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        "source_system IN ('HUNTER_COVERAGE', 'REGISTRY_EXTERNAL_ANOMALY')",
    )
    op.create_index(
        "uq_hypothesis_exploratory_origin",
        "hypothesis",
        ["research_run_id", "origin_reference"],
        unique=True,
        postgresql_where=sa.text("origin_reference LIKE 'exh:%'"),
    )


def downgrade() -> None:
    op.drop_index("uq_hypothesis_exploratory_origin", table_name="hypothesis")
    op.drop_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        type_="check",
    )
    op.create_check_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        "source_system IN ('HUNTER_COVERAGE')",
    )
