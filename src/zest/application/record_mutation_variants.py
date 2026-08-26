"""Record deterministic mutation variants to the audit ledger.

This is a planning action, not execution. Each variant is persisted as an
append-only audit event with a size-bounded, secret-free public summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from zest.application.identity import new_opaque_id
from zest.application.ports import UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.mutation.engine import MutationEngine


@dataclass(frozen=True)
class RecordMutationVariantsResult:
    research_run_id: str
    variant_count: int


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class RecordMutationVariants:
    """Application use case: generate and persist mutation variant plans."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._engine = MutationEngine()
        self._clock = clock or _default_clock

    def execute(
        self,
        node: AttackSurfaceNode,
        graph: AttackSurfaceGraph,
        *,
        research_run_id: str,
    ) -> RecordMutationVariantsResult:
        variants = self._engine.mutate(
            node,
            graph,
            variant_id_prefix=new_opaque_id(),
        )
        with self._uow_factory.open() as uow:
            for variant in variants:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock(),
                        actor_id="control-plane:mutation-recorder",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="MUTATION_VARIANT_PLANNED",
                        subject_type="ATTACK_SURFACE_NODE",
                        subject_id=variant.node_id,
                        correlation_id=variant.variant_id,
                        payload=variant.to_public_summary(),
                    )
                )
            uow.commit()
        return RecordMutationVariantsResult(
            research_run_id=research_run_id,
            variant_count=len(variants),
        )
