"""Pin full program policy and compiled scope to an orchestration.

Revision ID: a52_001_authority_context_pins
Revises: a51_001_phase66_dic
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = (
    "a52_001_authority_context_pins"
)
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "a51_001_phase66_dic"
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


def upgrade() -> None:
    op.add_column(
        "research_orchestration",
        sa.Column(
            "compiled_scope_fingerprint",
            sa.Text(),
            nullable=True,
        ),
    )

    op.add_column(
        "research_orchestration",
        sa.Column(
            "program_policy_fingerprint",
            sa.Text(),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_research_orchestration_compiled_scope_fp",
        "research_orchestration",
        "compiled_scope_fingerprint IS NULL "
        "OR char_length(compiled_scope_fingerprint) = 64",
    )

    op.create_check_constraint(
        "ck_research_orchestration_program_policy_fp",
        "research_orchestration",
        "program_policy_fingerprint IS NULL "
        "OR char_length(program_policy_fingerprint) = 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_research_orchestration_program_policy_fp",
        "research_orchestration",
        type_="check",
    )

    op.drop_constraint(
        "ck_research_orchestration_compiled_scope_fp",
        "research_orchestration",
        type_="check",
    )

    op.drop_column(
        "research_orchestration",
        "program_policy_fingerprint",
    )

    op.drop_column(
        "research_orchestration",
        "compiled_scope_fingerprint",
    )
