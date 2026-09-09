"""Add OAST as an opportunity-selection candidate source system.

Revision ID: a50_001_phase65_oast_src
Revises: a49_001_phase64_mp_sources
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a50_001_phase65_oast_src"
down_revision: Union[str, Sequence[str], None] = "a49_001_phase64_mp_sources"
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
        "source_system IN ('HUNTER_COVERAGE', 'REGISTRY_EXTERNAL_ANOMALY', "
        "'DISCOVERY_HANDOFF', 'AUTHENTICATION', 'AUTHORIZATION', 'WORKFLOW', "
        "'MUTATION', 'PROTOCOL', 'OAST')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        type_="check",
    )
    op.create_check_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        "source_system IN ('HUNTER_COVERAGE', 'REGISTRY_EXTERNAL_ANOMALY', "
        "'DISCOVERY_HANDOFF', 'AUTHENTICATION', 'AUTHORIZATION', 'WORKFLOW', "
        "'MUTATION', 'PROTOCOL')",
    )
