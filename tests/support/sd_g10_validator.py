"""Test helper for SD-G10 validator tier evidence."""

from __future__ import annotations

from datetime import datetime

from zest.data.records import AuditEventRecord
from zest.data.unit_of_work import UnitOfWork


def seed_validator_pass(
    uow: UnitOfWork,
    *,
    research_run_id: str,
    hypothesis_id: str,
    created_at: datetime,
    family_id: str = "hf-object-authz",
    marker: str | None = None,
) -> None:
    suffix = f"-{marker}" if marker else ""
    for tier in ("V1", "V2", "V3"):
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=(
                    f"sdg10-{research_run_id}-{hypothesis_id}{suffix}-{tier.lower()}"
                ),
                occurred_at=created_at,
                actor_id="control-plane:hunt-validation",
                actor_type="CONTROL_PLANE",
                event_type="HUNT_TIER_DECISION",
                subject_type="hypothesis",
                subject_id=hypothesis_id,
                correlation_id=research_run_id,
                payload={
                    "research_run_id": research_run_id,
                    "family_id": family_id,
                    "tier": tier,
                    "outcome": "PASSED",
                    "reason_code": f"{tier}_PASSED",
                    "node_canonical_key": "sdg10-validator-test-node",
                },
            )
        )
