from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.errors import ApplicationError
from zest.application.hunter_coverage_opportunity_source import (
    HunterCoverageOpportunitySource,
    HunterCoverageOpportunitySourceCommand,
)
from zest.data.records import OpportunitySelectionCandidateRecord, ResearchOpportunityRecord
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.exploration import OpportunityKind
from zest.research.scheduler.types import HunterScore, ScoredCell
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _scored(
    *,
    family_id: str = "family-1",
    node: str = "node-1",
    identity: str = "identity-1",
    state: CoverageState = CoverageState.UNTESTED,
    missing_evidence: tuple[str, ...] = ("claim_recorded",),
) -> ScoredCell:
    cell = CoverageCell(
        node_canonical_key=node,
        identity_id=identity,
        family_id=family_id,
        state=state,
        missing_evidence=missing_evidence,
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


class HunterCoverageOpportunitySourceTests(unittest.TestCase):
    def _store(self) -> _Store:
        store = _Store()
        seed_authorization_run(store)
        return store

    def test_untested_cell_produces_one_pending_candidate(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored(),)
            )
        )
        self.assertEqual(result.candidates_created, 1)
        self.assertEqual(result.skipped_ineligible_state, 0)
        self.assertEqual(result.skipped_duplicate, 0)
        candidates = list(store.opportunity_selection_candidates.values())
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertIsInstance(candidate, OpportunitySelectionCandidateRecord)
        self.assertEqual(candidate.outcome, "PENDING")
        self.assertEqual(candidate.source_system, "HUNTER_COVERAGE")
        self.assertEqual(candidate.opportunity_kind, OpportunityKind.HUNTER_COVERAGE_GAP.value)
        self.assertEqual(candidate.mode, "EXPLORATION")
        self.assertIsNone(candidate.resulting_opportunity_id)
        self.assertIsNone(candidate.decided_at)
        # No side effect on the epistemic/coverage state itself: proposing a
        # candidate is not itself coverage reduction.
        self.assertEqual(len(store.research_opportunities), 0)

    def test_covered_and_not_applicable_cells_are_ineligible(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        cells = (
            _scored(identity="i1", state=CoverageState.COVERED),
            _scored(identity="i2", state=CoverageState.NOT_APPLICABLE),
            _scored(identity="i4", state=CoverageState.V1_PASSED),
            _scored(identity="i5", state=CoverageState.V2_PASSED),
        )
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(research_run_id="run-1", scored_cells=cells)
        )
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(result.skipped_ineligible_state, 4)
        self.assertEqual(len(store.opportunity_selection_candidates), 0)

    def test_v3_queued_cell_produces_pending_candidate(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1",
                scored_cells=(_scored(state=CoverageState.V3_QUEUED),),
            )
        )
        self.assertEqual(result.candidates_created, 1)
        self.assertEqual(result.skipped_ineligible_state, 0)

    def test_hypothesized_cell_is_eligible(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1",
                scored_cells=(_scored(state=CoverageState.HYPOTHESIZED),),
            )
        )
        self.assertEqual(result.candidates_created, 1)

    def test_rerunning_with_the_same_cell_does_not_duplicate(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored(),)
            )
        )
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored(),)
            )
        )
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(result.skipped_duplicate, 1)
        self.assertEqual(len(store.opportunity_selection_candidates), 1)

    def test_does_not_duplicate_an_already_canonical_opportunity(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        cell = _scored()
        # Pre-seed a canonical opportunity with the exact identity this cell
        # would produce, simulating a prior cycle that already admitted it.
        from zest.application.hunter_coverage_opportunity_source import (
            _candidate_from_scored_cell,
        )

        pre = _candidate_from_scored_cell(cell, research_run_id="run-1", now=CREATED_AT)
        store.research_opportunities["opp-existing"] = ResearchOpportunityRecord(
            opportunity_id="opp-existing",
            research_run_id="run-1",
            opportunity_kind=pre.opportunity_kind,
            mode=pre.mode,
            source_refs=pre.source_refs,
            proposed_direction=pre.proposed_direction,
            unresolved_question=pre.unresolved_question,
            expected_information_value_description=pre.expected_information_value_description,
            assumptions=pre.assumptions,
            dimensions=pre.dimensions,
            context_signature=pre.context_signature,
            novelty_composition_marker=False,
            prior_attempt_refs=(),
            structural_identity=pre.structural_identity,
            strategy_version=pre.strategy_version,
            created_at=CREATED_AT,
        )
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(research_run_id="run-1", scored_cells=(cell,))
        )
        self.assertEqual(result.candidates_created, 0)
        self.assertEqual(result.skipped_duplicate, 1)

    def test_max_candidates_bounds_created_count(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        cells = tuple(_scored(identity=f"identity-{i}") for i in range(5))
        result = source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=cells, max_candidates=2
            )
        )
        self.assertEqual(result.candidates_created, 2)
        self.assertEqual(len(store.opportunity_selection_candidates), 2)

    def test_zero_max_candidates_is_rejected(self) -> None:
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        with self.assertRaises(ApplicationError):
            source.execute(
                HunterCoverageOpportunitySourceCommand(
                    research_run_id="run-1", scored_cells=(_scored(),), max_candidates=0
                )
            )

    def test_missing_research_run_raises(self) -> None:
        store = _Store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        with self.assertRaises(ApplicationError):
            source.execute(
                HunterCoverageOpportunitySourceCommand(
                    research_run_id="does-not-exist", scored_cells=(_scored(),)
                )
            )

    def test_no_worker_execution_or_authorization_side_effects(self) -> None:
        """This producer must never touch Core/Worker/Hypothesis/Experiment state."""
        store = self._store()
        source = HunterCoverageOpportunitySource(FakeUnitOfWorkFactory(store), clock=FixedClock())
        source.execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored(),)
            )
        )
        self.assertEqual(len(store.hypotheses), 0)
        self.assertEqual(len(store.experiments), 0)
        self.assertEqual(len(store.execution_attempts), 0)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.findings), 0)


if __name__ == "__main__":
    unittest.main()
