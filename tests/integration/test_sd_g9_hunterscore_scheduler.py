"""SD-G9 HunterScore scheduler + identity binding integration.

PostgreSQL required. Skipped when ZEST_TEST_DATABASE_URL is absent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.run_hunt_cycle import RunHuntCycle, RunHuntCycleCommand
from zest.application.run_hunt_scheduler import (
    HUNT_SCHEDULE_RECOMMENDED,
    RunHuntScheduler,
    RunHuntSchedulerCommand,
)
from zest.core.enums import ScopeClassification
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    HunterFamilyRecord,
    ProgramPolicyRecord,
)
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.scheduler.types import HunterScore, ScoredCell
from zest.research.target_model import TargetEpistemicStatus

TEST_URL = configured_test_url()


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG9SchedulerIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id="prog-1",
                    loopback_fixture=False,
                    max_response_bytes=4096,
                    timeout_ms=2000,
                    created_at=NOW,
                    updated_at=NOW,
                    action_policy={},
                )
            )
            uow.hunter_families.insert(self._family())
            uow.commit()

    def _family(self) -> HunterFamilyRecord:
        return HunterFamilyRecord(
            family_id=f"hf-object-authz-id-{self._testMethodName}",
            name="OBJECT_AUTHORIZATION",
            target_node_kinds=("HTTP_OPERATION",),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template=(
                "Object authorization boundary on {origin}{path} "
                "may allow cross-owner access to {resource_id} "
                "for identity {identity_id}."
            ),
            evidence_requirements={},
            validation_tier="V3",
            enabled=True,
            version=1,
            created_at=NOW,
        )

    def _family_view(self) -> "HunterFamilyView":
        from zest.research.selection import HunterFamilyView

        record = self._family()
        return HunterFamilyView(
            family_id=record.family_id,
            name=record.name,
            target_node_kinds=record.target_node_kinds,
            preconditions=record.preconditions,
            claim_template=record.claim_template,
            evidence_requirements=record.evidence_requirements,
            validation_tier=record.validation_tier,
            enabled=record.enabled,
            version=record.version,
        )

    def _graph(self) -> AttackSurfaceGraph:
        node = AttackSurfaceNode(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
            identity_ids=("alice",),
            provenance_refs=("sensor_observation:so-1",),
            scope_classification=ScopeClassification.IN_SCOPE,
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        return AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )

    def test_scheduler_emits_recommendation_event(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        scheduler = RunHuntScheduler(uow_factory, clock=FixedClock())

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=self._graph(),
                registry=(self._family_view(),),
                top_n=10,
            )
        )

        self.assertEqual(result.recommended_count, 1)
        self.assertFalse(result.no_op)
        self.assertEqual(result.recommended[0].cell.identity_id, "alice")

        with PostgresUnitOfWork(self.engine) as uow:
            audit_events = uow.audit_events.list_for_subject_type("research_run")
            uow.rollback()

        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0].event_type, HUNT_SCHEDULE_RECOMMENDED)
        self.assertEqual(audit_events[0].payload["matrix_hash"], result.matrix_hash)
        self.assertEqual(audit_events[0].payload["recommended_count"], 1)

    def test_cycle_consumes_schedule_and_queues_v3_with_identity(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        scheduler = RunHuntScheduler(uow_factory, clock=FixedClock())
        cycle = RunHuntCycle(uow_factory, clock=FixedClock())

        schedule_result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=self._graph(),
                registry=(self._family_view(),),
                top_n=10,
            )
        )

        cycle_result = cycle.execute(
            RunHuntCycleCommand(
                research_run_id="run-1",
                graph=self._graph(),
                registry=(self._family_view(),),
                schedule=schedule_result.recommended,
            )
        )

        self.assertEqual(cycle_result.generated, 1)
        self.assertEqual(cycle_result.v1_passed, 1)
        self.assertEqual(cycle_result.v2_passed, 1)
        self.assertEqual(cycle_result.v3_queued, 1)
        self.assertFalse(cycle_result.no_op)

        with PostgresUnitOfWork(self.engine) as uow:
            hypothesis = next(iter(uow.hypotheses.list_for_research_run("run-1")))
            queue_items = uow.hunt_v3_queue.list_for_research_run("run-1")
            uow.rollback()

        self.assertEqual(hypothesis.identity_id, "alice")
        self.assertIn("alice", hypothesis.claim)
        self.assertEqual(len(queue_items), 1)
        self.assertEqual(queue_items[0].identity_id, "alice")

    def test_identity_bound_hypothesis_only_affects_own_cell(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        scheduler = RunHuntScheduler(uow_factory, clock=FixedClock())

        node = AttackSurfaceNode(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="origin:http://example.com|path:/api/users|method:GET",
            epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
            identity_ids=("alice", "bob"),
            provenance_refs=("sensor_observation:so-1",),
            scope_classification=ScopeClassification.IN_SCOPE,
            attributes={
                "origin": "http://example.com",
                "path": "/api/users",
                "method": "GET",
                "resource_id": "users",
            },
        )
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(self._family_view(),),
                top_n=10,
            )
        )

        identities = {item.cell.identity_id for item in result.recommended}
        self.assertEqual(identities, {"alice", "bob"})
        self.assertEqual(len(result.recommended), 2)


if __name__ == "__main__":
    unittest.main()
