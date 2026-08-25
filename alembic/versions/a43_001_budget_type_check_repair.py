"""Remove the obsolete pre-token budget resource-type CHECK constraint.

``a28_001_token_economy`` added the wider ``*_v2`` constraint but did not
remove the narrower constraint created by ``a16_001_orchestration_operations``.
PostgreSQL evaluates both constraints, so token-economy rows remained rejected.

The downgrade is deliberately data-safe: it refuses to recreate the historical
narrow constraint while token-economy rows exist, because that would make the
authoritative budget ledger incompatible with the downgraded schema.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a43_001_budget_type_check_repair"
down_revision: Union[str, Sequence[str], None] = "a42_001_preflight_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_CONSTRAINT = "ck_budget_consumption_resource_type"
V2_CONSTRAINT = "ck_budget_consumption_resource_type_v2"
TABLE = "budget_consumption"
TOKEN_RESOURCE_TYPES = (
    "MODEL_TOKENS_IN",
    "MODEL_TOKENS_OUT",
    "MODEL_ESCALATION_DECISION",
)


def upgrade() -> None:
    """Leave the canonical v2 vocabulary as the sole resource-type guard."""

    op.drop_constraint(LEGACY_CONSTRAINT, TABLE, type_="check")


def downgrade() -> None:
    """Restore history only when no rows would violate the old constraint."""

    bind = op.get_bind()
    incompatible = bind.execute(
        sa.text(
            "SELECT resource_type, COUNT(*) AS row_count "
            "FROM budget_consumption "
            "WHERE resource_type IN "
            "('MODEL_TOKENS_IN', 'MODEL_TOKENS_OUT', 'MODEL_ESCALATION_DECISION') "
            "GROUP BY resource_type "
            "ORDER BY resource_type"
        )
    ).fetchall()
    if incompatible:
        summary = ", ".join(f"{row[0]}={row[1]}" for row in incompatible)
        raise RuntimeError(
            "cannot downgrade a43: budget_consumption contains resource types "
            f"rejected by {LEGACY_CONSTRAINT}: {summary}; no rows were deleted or rewritten"
        )

    op.create_check_constraint(
        LEGACY_CONSTRAINT,
        TABLE,
        "resource_type IN ("
        "'MODEL_CALL', 'WORKER_INVOCATION', 'REQUEST', "
        "'EXECUTION_TIME', 'ARTIFACT_BYTES', 'COST')",
    )
