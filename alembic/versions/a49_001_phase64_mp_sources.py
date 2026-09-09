"""Allow Mutation / Protocol candidate source systems.

Revision ID: a49_001_phase64_mp_sources
Revises: a48_001_phase63_identity_sources
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a49_001_phase64_mp_sources"
down_revision: Union[str, Sequence[str], None] = "a48_001_phase63_identity_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_SYSTEMS = (
    "HUNTER_COVERAGE",
    "REGISTRY_EXTERNAL_ANOMALY",
    "DISCOVERY_HANDOFF",
    "AUTHENTICATION",
    "AUTHORIZATION",
    "WORKFLOW",
    "MUTATION",
    "PROTOCOL",
)
LEGACY_SYSTEMS = NEW_SYSTEMS[:-2]


def upgrade() -> None:
    op.drop_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        type_="check",
    )
    op.create_check_constraint(
        "ck_opportunity_selection_candidate_source_system",
        "opportunity_selection_candidate",
        "source_system IN (" + ", ".join(f"'{item}'" for item in NEW_SYSTEMS) + ")",
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
        "source_system IN (" + ", ".join(f"'{item}'" for item in LEGACY_SYSTEMS) + ")",
    )
