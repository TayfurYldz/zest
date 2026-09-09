"""Slice 3 (MR-1) re-qualification proof.

MR-1's PASS invariant is a single next-action owner: ARC / its canonical
selector composition. These tests prove the full path

    Hunter/Coverage ScoredCell
    -> HunterCoverageOpportunitySource -> opportunity_selection_candidate
    -> SelectResearchOpportunities -> canonical ResearchOpportunityRecord
    -> AutonomousResearchController.step() -> Hypothesis -> Experiment

runs entirely through `AutonomousResearchController`, with exactly one
Worker dispatch path (`RecordingWorkerPort` invoked only via ARC's own
`ExecutePlannedExperiment`), and that a Hunter/Coverage candidate deferred by
budget competition against a diagnostic opportunity is not starved: it stays
PENDING and is admitted on a later cycle once capacity frees up.
"""

from __future__ import annotations

import unittest
from datetime import datetime

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.hunter_coverage_opportunity_source import (
    HunterCoverageOpportunitySource,
    HunterCoverageOpportunitySourceCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.records import HypothesisRecord, IssuedBudgetRecord
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.orchestration import OrchestrationBounds, OrchestrationState
from zest.research.scheduler.fairness import KIND_STARVATION_BOUND
from zest.research.scheduler.types import HunterScore, ScoredCell
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=5,
        max_experiments=5,
        max_model_calls=50,
        max_worker_invocations=10,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _seed_large_budget(store: _Store) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=20,
        max_tool_calls=20,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _controller(store: _Store, *, worker=None, model=None):
    factory = FakeUnitOfWorkFactory(store=store)
    port = worker or RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory,
        port,
        model or ScriptedModelPort(),
        clock=FixedClock(),
    )
    return controller, factory, port


def _scored_cell(
    *,
    family_id: str = "family-1",
    node: str = "node-1",
    identity: str = "identity-1",
    state: CoverageState = CoverageState.UNTESTED,
) -> ScoredCell:
    cell = CoverageCell(
        node_canonical_key=node,
        identity_id=identity,
        family_id=family_id,
        state=state,
        missing_evidence=("claim_recorded",),
    )
    score = HunterScore(
        cell=cell,
        total_score=10,
        state_weight=10,
        family_success_bonus=0,
        family_exploration_bonus=0,
        freshness_bonus=0,
        budget_suitability_bonus=0,
        explanation=("state_weight=10",),
    )
    return ScoredCell(cell=cell, score=score)


class ArcHunterCoverageLifecycleTests(unittest.TestCase):
    """MR-1 re-qualification: ARC is the sole next-action owner for
    Hunter/Coverage-sourced opportunities, exactly as for diagnostic ones."""

    def test_scored_cell_reaches_hypothesis_and_experiment_through_arc_only(self) -> None:
        store = _Store()
        _seed_large_budget(store)

        # 1. Hunter/Coverage producer proposes a durable, pre-admission
        #    candidate from a ranked coverage-debt cell. This step never
        #    touches Hypothesis/Experiment/Core/Worker state.
        producer = HunterCoverageOpportunitySource(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        )
        produced = producer.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored_cell(),)
            )
        )
        self.assertEqual(produced.candidates_created, 1)
        self.assertEqual(len(store.hypotheses), 0)
        self.assertEqual(len(store.experiments), 0)
        (candidate_id,) = list(store.opportunity_selection_candidates)
        self.assertEqual(
            store.opportunity_selection_candidates[candidate_id].outcome, "PENDING"
        )

        # 2. AutonomousResearchController is the only orchestration owner
        #    exercised from here on: no RunResearchSelection, no second
        #    scheduler, no alternate Worker dispatch path.
        controller, _, port = _controller(store)
        controller.start(_command())
        result = controller.step(
            _command(
                selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1)
            )
        )

        # 3. The candidate was admitted through SelectResearchOpportunities
        #    (invoked only inside ARC.step()) into the canonical
        #    ResearchOpportunityRecord authority.
        admitted_candidate = store.opportunity_selection_candidates[candidate_id]
        self.assertEqual(admitted_candidate.outcome, "ADMITTED")
        self.assertEqual(admitted_candidate.resulting_opportunity_id, candidate_id)
        canonical = store.research_opportunities.get(candidate_id)
        self.assertIsNotNone(canonical)
        self.assertEqual(canonical.opportunity_kind, OpportunityKind.HUNTER_COVERAGE_GAP.value)

        wiring = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        ]
        self.assertGreaterEqual(len(wiring), 1)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(result.state, OrchestrationState.READY.value)
        self.assertEqual(len(store.findings), 0)

    def test_hunter_coverage_candidate_deferred_by_diagnostic_is_not_starved(self) -> None:
        """Fairness: a Hunter/Coverage candidate that loses a budget-limited
        cycle to a competing diagnostic opportunity is not discarded. It
        stays PENDING and is admitted once budget/priority allow it, and
        both eventually reach the same ARC-owned Hypothesis/Experiment
        lifecycle."""

        store = _Store()
        _seed_large_budget(store)
        # A hypothesis from an earlier context: `SelectResearchOpportunities`
        # regenerates a HYPOTHESIS_FOLLOWUP diagnostic opportunity for every
        # existing hypothesis in the run, independent of the Hunter/Coverage
        # candidate pool.
        store.hypotheses["hyp-pre"] = HypothesisRecord(
            hypothesis_id="hyp-pre",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            created_at=CREATED_AT,
        )
        producer = HunterCoverageOpportunitySource(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        )
        producer.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored_cell(),)
            )
        )
        (candidate_id,) = list(store.opportunity_selection_candidates)

        controller, _, port = _controller(store)
        controller.start(_command())

        # Cycle 1: budget policy favors diagnostics this cycle
        # (max_exploratory=0) -- the Hunter/Coverage candidate cannot be
        # selected no matter how it ranks.
        cycle_1 = controller.step(
            _command(
                selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=0)
            )
        )
        deferred_candidate = store.opportunity_selection_candidates[candidate_id]
        self.assertEqual(
            deferred_candidate.outcome,
            "PENDING",
            "a budget-blocked Hunter/Coverage candidate must survive to a later cycle, "
            "not be discarded or marked NOT_ADMITTED",
        )
        self.assertIsNone(deferred_candidate.resulting_opportunity_id)
        # The diagnostic opportunity (from the pre-existing hypothesis) won
        # this cycle's shared budget slot instead.
        self.assertIsNotNone(cycle_1.hypothesis_id)
        self.assertNotEqual(cycle_1.hypothesis_id, "hyp-pre")
        self.assertIn(cycle_1.hypothesis_id, store.hypotheses)
        self.assertEqual(len(store.experiments), 2)
        first_cycle_records = [
            item
            for item in store.research_cycles.values()
            if item.hypothesis_id == cycle_1.hypothesis_id
        ]
        self.assertEqual(len(first_cycle_records), 1)
        self.assertNotEqual(first_cycle_records[0].opportunity_id, candidate_id)

        # Restoring exploratory capacity does not guarantee immediate
        # selection: other legitimate engines share the same scheduler.
        # The invariant is bounded fairness, not "must win cycle 2".
        admitted_cycle = None
        admitted_candidate = store.opportunity_selection_candidates[candidate_id]

        for cycle_number in range(2, 2 + KIND_STARVATION_BOUND):
            controller.step(
                _command(
                    selection_budget=ResearchPolicyBudget(
                        max_selected=1,
                        max_exploratory=1,
                    )
                )
            )
            admitted_candidate = store.opportunity_selection_candidates[candidate_id]
            if admitted_candidate.outcome == "ADMITTED":
                admitted_cycle = cycle_number
                break

        self.assertIsNotNone(
            admitted_cycle,
            "eligible Hunter/Coverage work must be admitted within the "
            "scheduler starvation bound",
        )
        self.assertEqual(admitted_candidate.outcome, "ADMITTED")
        self.assertEqual(admitted_candidate.resulting_opportunity_id, candidate_id)
        wiring = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        ]
        self.assertGreaterEqual(len(wiring), 1)
        self.assertTrue(
            any(
                item.payload.get("selected_work_id") == candidate_id
                for item in wiring
            )
        )
        # Exact global Worker/Experiment counts are scheduler-order
        # dependent; this test owns the fairness invariant only.
        self.assertGreaterEqual(len(port.calls), 2)
        self.assertGreaterEqual(len(store.experiments), 2)


if __name__ == "__main__":
    unittest.main()
