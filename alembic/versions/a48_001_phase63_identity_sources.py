"""Allow Authentication / Authorization / Workflow candidate source systems.

Revision ID: a48_001_phase63_identity_sources
Revises: a47_001_research_work_fabric
Create Date: 2026-08-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a48_001_phase63_identity_sources"
down_revision: Union[str, Sequence[str], None] = "a47_001_research_work_fabric"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_SYSTEMS = (
    "HUNTER_COVERAGE",
    "REGISTRY_EXTERNAL_ANOMALY",
    "DISCOVERY_HANDOFF",
    "AUTHENTICATION",
    "AUTHORIZATION",
    "WORKFLOW",
)
LEGACY_SYSTEMS = (
    "HUNTER_COVERAGE",
    "REGISTRY_EXTERNAL_ANOMALY",
    "DISCOVERY_HANDOFF",
)


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
