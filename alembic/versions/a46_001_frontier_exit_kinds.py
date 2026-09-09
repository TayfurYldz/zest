"""Frontier exit event kinds and explicit duplicate rows."""

from typing import Sequence, Union

from alembic import op

revision: str = "a46_001_frontier_exit_kinds"
down_revision: Union[str, Sequence[str], None] = "a45_001_observability_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FRONTIER_EVENTS = (
    "CREATED",
    "ELIGIBLE",
    "SELECTED",
    "BLOCKED_SCOPE",
    "BLOCKED_AUTH",
    "BLOCKED_BUDGET",
    "AWAITING_REAUTHORIZATION",
    "NO_NEW_INFORMATION",
    "OBSERVED",
    "FAILED_TRANSIENT",
    "FAILED_TERMINAL",
    "SUPERSEDED",
    "DEFERRED_TO_RESEARCH",
    "UNSUPPORTED",
)
LEGACY_EVENTS = FRONTIER_EVENTS[:-2]


def upgrade() -> None:
    op.drop_constraint("ck_frontier_event_kind", "frontier_event", type_="check")
    op.create_check_constraint(
        "ck_frontier_event_kind",
        "frontier_event",
        "event_kind IN (" + ", ".join(f"'{item}'" for item in FRONTIER_EVENTS) + ")",
    )
    op.drop_constraint("uq_frontier_item_dedupe", "frontier_item", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_frontier_item_dedupe",
        "frontier_item",
        ["research_run_id", "dedupe_identity"],
    )
    op.drop_constraint("ck_frontier_event_kind", "frontier_event", type_="check")
    op.create_check_constraint(
        "ck_frontier_event_kind",
        "frontier_event",
        "event_kind IN (" + ", ".join(f"'{item}'" for item in LEGACY_EVENTS) + ")",
    )
