"""Canonical MR-5 durability uniqueness and promotion_run FKs.

One supporting Evidence per experiment, one Candidate per Evidence, one
Verification per Candidate, one FindingProposal per Candidate, and one
reserved reproduction experiment id per PromotionRun. Does not delete
duplicate rows; upgrade fails visibly if duplicates already exist.

Revision ID: a39_001_mr5_durability_uq
Revises: a38_001_promotion_run
Create Date: 2026-08-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a39_001_mr5_durability_uq"
down_revision: Union[str, Sequence[str], None] = "a38_001_promotion_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _require_no_duplicates(bind, sql: str, label: str) -> None:
    rows = bind.execute(sa.text(sql)).fetchall()
    if rows:
        raise RuntimeError(
            f"a39 cannot add {label}: duplicate rows exist: {rows!r}. "
            "Migration does not delete records."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _require_no_duplicates(
        bind,
        "SELECT experiment_id, COUNT(*) AS n FROM evidence "
        "WHERE polarity = 'SUPPORTING' GROUP BY experiment_id HAVING COUNT(*) > 1",
        "uq_evidence_experiment_supporting",
    )
    _require_no_duplicates(
        bind,
        "SELECT evidence_id, COUNT(*) AS n FROM candidate_evidence "
        "GROUP BY evidence_id HAVING COUNT(*) > 1",
        "uq_candidate_evidence_evidence_id",
    )
    _require_no_duplicates(
        bind,
        "SELECT candidate_id, COUNT(*) AS n FROM verification "
        "GROUP BY candidate_id HAVING COUNT(*) > 1",
        "uq_verification_candidate",
    )
    _require_no_duplicates(
        bind,
        "SELECT candidate_id, COUNT(*) AS n FROM finding_proposal "
        "GROUP BY candidate_id HAVING COUNT(*) > 1",
        "uq_finding_proposal_candidate",
    )
    _require_no_duplicates(
        bind,
        "SELECT reproduction_experiment_id, COUNT(*) AS n FROM promotion_run "
        "WHERE reproduction_experiment_id IS NOT NULL "
        "GROUP BY reproduction_experiment_id HAVING COUNT(*) > 1",
        "uq_promotion_run_reproduction_experiment",
    )

    op.create_index(
        "uq_evidence_experiment_supporting",
        "evidence",
        ["experiment_id"],
        unique=True,
        postgresql_where=sa.text("polarity = 'SUPPORTING'"),
    )
    op.create_unique_constraint(
        "uq_candidate_evidence_evidence_id",
        "candidate_evidence",
        ["evidence_id"],
    )
    op.create_unique_constraint(
        "uq_verification_candidate",
        "verification",
        ["candidate_id"],
    )
    op.create_unique_constraint(
        "uq_finding_proposal_candidate",
        "finding_proposal",
        ["candidate_id"],
    )
    op.create_index(
        "uq_promotion_run_reproduction_experiment",
        "promotion_run",
        ["reproduction_experiment_id"],
        unique=True,
        postgresql_where=sa.text("reproduction_experiment_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_promotion_run_evidence",
        "promotion_run",
        "evidence",
        ["evidence_id"],
        ["evidence_id"],
    )
    op.create_foreign_key(
        "fk_promotion_run_candidate",
        "promotion_run",
        "candidate",
        ["candidate_id"],
        ["candidate_id"],
    )
    op.create_foreign_key(
        "fk_promotion_run_verification",
        "promotion_run",
        "verification",
        ["verification_id"],
        ["verification_id"],
    )
    op.create_foreign_key(
        "fk_promotion_run_finding_proposal",
        "promotion_run",
        "finding_proposal",
        ["finding_proposal_id"],
        ["proposal_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_promotion_run_finding_proposal", "promotion_run", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_promotion_run_verification", "promotion_run", type_="foreignkey"
    )
    op.drop_constraint("fk_promotion_run_candidate", "promotion_run", type_="foreignkey")
    op.drop_constraint("fk_promotion_run_evidence", "promotion_run", type_="foreignkey")
    op.drop_index(
        "uq_promotion_run_reproduction_experiment", table_name="promotion_run"
    )
    op.drop_constraint(
        "uq_finding_proposal_candidate", "finding_proposal", type_="unique"
    )
    op.drop_constraint("uq_verification_candidate", "verification", type_="unique")
    op.drop_constraint(
        "uq_candidate_evidence_evidence_id", "candidate_evidence", type_="unique"
    )
    op.drop_index("uq_evidence_experiment_supporting", table_name="evidence")
