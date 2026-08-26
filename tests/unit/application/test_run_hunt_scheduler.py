"""SD-G9 RunHuntScheduler application unit tests."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.run_hunt_scheduler import (
    HUNT_SCHEDULE_RECOMMENDED,
    RunHuntScheduler,
    RunHuntSchedulerCommand,
)
from zest.core.enums import ScopeClassification
from zest.data.records import (
    HypothesisAssessmentRecord,
    HypothesisRecord,
    SensorObservationRecord,
)
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.selection import HunterFamilyView
from zest.research.target_model import TargetEpistemicStatus
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _node(
    *,
    node_id: str,
    canonical_key: str,
    identity_ids: tuple[str, ...] = ("alice",),
    provenance_refs: tuple[str, ...] = ("sensor_observation:so-1",),
) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id=node_id,
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key=canonical_key,
        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
        identity_ids=identity_ids,
        provenance_refs=provenance_refs,
        scope_classification=ScopeClassification.IN_SCOPE,
        attributes={
            "origin": "http://example.com",
            "path": "/api/users",
            "method": "GET",
            "resource_id": "users",
        },
    )


def _family() -> HunterFamilyView:
    return HunterFamilyView(
        family_id="hf-object-authz-id",
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
    )


def _graph(node: AttackSurfaceNode) -> AttackSurfaceGraph:
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(node,),
        edges=(),
    )


class RunHuntSchedulerTests(unittest.TestCase):
    def test_ranks_untested_cell_and_emits_audit(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        graph = _graph(_node(node_id="op-1", canonical_key="ck-1"))

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
            )
        )

        self.assertEqual(result.recommended_count, 1)
        self.assertFalse(result.no_op)
        self.assertEqual(result.recommended[0].cell.identity_id, "alice")
        self.assertEqual(result.recommended[0].cell.family_id, "hf-object-authz-id")
        self.assertEqual(len(store.audit_events), 1)
        audit = list(store.audit_events.values())[0]
        self.assertEqual(audit.event_type, HUNT_SCHEDULE_RECOMMENDED)
        self.assertEqual(audit.payload["matrix_hash"], result.matrix_hash)
        self.assertEqual(audit.payload["recommended_count"], 1)

    def test_no_op_when_no_debt_cells(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        out_of_scope_node = AttackSurfaceNode(
            node_id="op-1",
            kind=AttackSurfaceNodeKind.HTTP_OPERATION,
            canonical_key="ck-1",
            epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
            identity_ids=("alice",),
            provenance_refs=(),
            scope_classification=ScopeClassification.OUT_OF_SCOPE,
            attributes={},
        )
        graph = _graph(out_of_scope_node)

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
            )
        )

        self.assertEqual(result.recommended_count, 0)
        self.assertTrue(result.no_op)

    def test_budget_exhausted_adjusts_ranking(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        node = _node(node_id="op-1", canonical_key="ck-1", identity_ids=("alice", "bob"))
        graph = _graph(node)

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
                daily_llm_budget_microdollars=1000,
                consumed_microdollars=1000,
            )
        )

        self.assertEqual(result.recommended_count, 2)
        # Both cells are UNTESTED (cheap path) so they receive the cheap bonus.
        for scored in result.recommended:
            self.assertEqual(scored.score.budget_suitability_bonus, 5)

    def test_family_stats_from_assessments(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.hypotheses["hyp-1"] = HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim="claim",
            origin_reference="hf-object-authz-id",
            identity_id="alice",
            created_at=CREATED_AT,
        )
        store.hypothesis_assessments["assess-1"] = HypothesisAssessmentRecord(
            assessment_id="assess-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            assessment_outcome="CONSISTENT_WITH_PREDICTION",
            observation_ids=("obs-1",),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="v1",
            rationale={},
            evaluation_strategy="manual",
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        graph = _graph(_node(node_id="op-1", canonical_key="ck-1"))

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
            )
        )

        self.assertEqual(result.recommended_count, 1)
        self.assertEqual(result.recommended[0].score.family_success_bonus, 5)
        self.assertEqual(result.recommended[0].score.family_exploration_bonus, 5)

    def test_freshness_from_sensor_observation(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        collected_at = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)
        store.sensor_observations["so-1"] = SensorObservationRecord(
            observation_id="so-1",
            research_run_id="run-1",
            sensor_id="sensor.archive",
            target_reference="http://example.com",
            collected_at=collected_at,
            payload_digest="digest",
            epistemic_status="UNTRUSTED_EXTERNAL",
            source_metadata={},
            payload={},
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        graph = _graph(_node(node_id="op-1", canonical_key="ck-1"))

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
            )
        )

        self.assertEqual(result.recommended_count, 1)
        self.assertGreater(result.recommended[0].score.freshness_bonus, 0)
        self.assertIn("latest_activity_age_hours", result.recommended[0].score.explanation[2])

    def test_freshness_uses_latest_sensor_observation_for_changed_node(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        old_observation = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
        new_observation = datetime(2026, 8, 19, 10, 0, 0, tzinfo=timezone.utc)
        store.sensor_observations["so-old"] = SensorObservationRecord(
            observation_id="so-old",
            research_run_id="run-1",
            sensor_id="sensor.archive",
            target_reference="http://example.com",
            collected_at=old_observation,
            payload_digest="digest-old",
            epistemic_status="UNTRUSTED_EXTERNAL",
            source_metadata={},
            payload={},
            created_at=CREATED_AT,
        )
        store.sensor_observations["so-new"] = SensorObservationRecord(
            observation_id="so-new",
            research_run_id="run-1",
            sensor_id="sensor.archive",
            target_reference="http://example.com",
            collected_at=new_observation,
            payload_digest="digest-new",
            epistemic_status="UNTRUSTED_EXTERNAL",
            source_metadata={},
            payload={},
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        graph = _graph(
            _node(
                node_id="op-1",
                canonical_key="ck-1",
                provenance_refs=(
                    "sensor_observation:so-old",
                    "sensor_observation:so-new",
                ),
            )
        )

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
            )
        )

        self.assertEqual(result.recommended_count, 1)
        explanation = result.recommended[0].score.explanation[2]
        self.assertIn("first_seen_age_hours", explanation)
        self.assertIn("latest_activity_age_hours", explanation)
        self.assertGreater(result.recommended[0].score.freshness_bonus, 0)

    def test_top_n_caps_recommendations(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        factory = FakeUnitOfWorkFactory(store)
        scheduler = RunHuntScheduler(factory, clock=_FixedClock())
        node = _node(node_id="op-1", canonical_key="ck-1", identity_ids=("alice", "bob", "carol"))
        graph = _graph(node)

        result = scheduler.execute(
            RunHuntSchedulerCommand(
                research_run_id="run-1",
                graph=graph,
                registry=(_family(),),
                top_n=2,
            )
        )

        self.assertEqual(result.recommended_count, 2)
        self.assertEqual(len(result.scored_cells), 3)


if __name__ == "__main__":
    unittest.main()
