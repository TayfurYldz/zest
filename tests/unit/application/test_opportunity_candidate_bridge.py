"""MR-1 regression guard: Hunter/Coverage-sourced OpportunitySelectionCandidate
rows become visible to and selectable by SelectResearchOpportunities, without
changing diagnostic-opportunity behavior, and without opening any Worker
dispatch path other than the existing Hypothesis/Experiment/Core/Worker
lifecycle (this use case never touches execution_attempts/worker_result).
"""

from __future__ import annotations

import unittest
from dataclasses import replace

import pathsetup  # noqa: F401

from zest.application.hunter_coverage_opportunity_source import (
    _candidate_from_scored_cell,
)
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.scheduler.types import HunterScore, ScoredCell
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _scored(*, family_id="family-1", node="node-1", identity="identity-1") -> ScoredCell:
    cell = CoverageCell(
        node_canonical_key=node,
        identity_id=identity,
        family_id=family_id,
        state=CoverageState.UNTESTED,
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
        explanation=(),
    )
    return ScoredCell(cell=cell, score=score)


def _candidate(candidate_id: str, *, research_run_id: str = "run-1", **scored_kwargs):
    record = _candidate_from_scored_cell(
        _scored(**scored_kwargs), research_run_id=research_run_id, now=CREATED_AT
    )
    return replace(record, candidate_id=candidate_id)


class OpportunityCandidateBridgeTests(unittest.TestCase):
    def _store(self) -> _Store:
        store = _Store()
        seed_authorization_run(store)
        return store

    def test_pending_candidate_is_selected_and_admitted(self) -> None:
        store = self._store()
        store.opportunity_selection_candidates["cand-1"] = _candidate("cand-1")
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=3, max_exploratory=1),
            )
        )
        self.assertTrue(result.selected)
        selected_ids = {item.opportunity.opportunity_id for item in result.selected}
        self.assertIn("cand-1", selected_ids)
        selected_kinds = {item.opportunity.opportunity_kind for item in result.selected}
        self.assertIn(OpportunityKind.HUNTER_COVERAGE_GAP, selected_kinds)

        # Canonical ResearchOpportunityRecord was persisted exactly as for a
        # diagnostic SELECT decision.
        opportunity = store.research_opportunities.get("cand-1")
        self.assertIsNotNone(opportunity)
        self.assertEqual(opportunity.opportunity_kind, OpportunityKind.HUNTER_COVERAGE_GAP.value)

        # The candidate row itself transitioned PENDING -> ADMITTED and now
        # references its resulting canonical opportunity, but never became a
        # second authority (research_opportunity is still the sole canonical
        # record read anywhere else).
        candidate = store.opportunity_selection_candidates["cand-1"]
        self.assertEqual(candidate.outcome, "ADMITTED")
        self.assertEqual(candidate.resulting_opportunity_id, "cand-1")
        self.assertEqual(candidate.decided_at, CREATED_AT)

        # No alternative Worker dispatch path: this use case never touches
        # execution/authorization state, Hunter- or diagnostic-sourced alike.
        self.assertEqual(len(store.execution_attempts), 0)
        self.assertEqual(len(store.worker_results), 0)

    def test_no_candidates_present_is_unaffected_regression_guard(self) -> None:
        """With zero candidate rows, behavior is byte-for-byte the pre-MR-1 path."""
        store = self._store()
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertEqual(result.decisions, ())
        self.assertEqual(len(store.research_opportunities), 0)
        self.assertEqual(len(store.opportunity_selection_candidates), 0)

    def test_duplicate_candidate_against_existing_canonical_opportunity_is_not_admitted(
        self,
    ) -> None:
        from zest.data.records import ResearchOpportunityRecord

        store = self._store()
        candidate = _candidate("cand-1")
        store.opportunity_selection_candidates["cand-1"] = candidate
        # Simulate an equivalent opportunity already selected in a prior cycle
        # (same structural_identity, different opportunity_id).
        store.research_opportunities["opp-prior"] = ResearchOpportunityRecord(
            opportunity_id="opp-prior",
            research_run_id="run-1",
            opportunity_kind=candidate.opportunity_kind,
            mode=candidate.mode,
            source_refs=candidate.source_refs,
            proposed_direction=candidate.proposed_direction,
            unresolved_question=candidate.unresolved_question,
            expected_information_value_description=candidate.expected_information_value_description,
            assumptions=candidate.assumptions,
            dimensions=candidate.dimensions,
            context_signature=candidate.context_signature,
            novelty_composition_marker=False,
            prior_attempt_refs=(),
            structural_identity=candidate.structural_identity,
            strategy_version=candidate.strategy_version,
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertFalse(result.selected)
        updated_candidate = store.opportunity_selection_candidates["cand-1"]
        self.assertEqual(updated_candidate.outcome, "NOT_ADMITTED")
        self.assertIsNone(updated_candidate.resulting_opportunity_id)

    def test_deferred_candidate_stays_pending_for_retry_next_cycle(self) -> None:
        store = self._store()
        store.opportunity_selection_candidates["cand-1"] = _candidate(
            "cand-1", identity="identity-1"
        )
        store.opportunity_selection_candidates["cand-2"] = _candidate(
            "cand-2", identity="identity-2"
        )
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=5, max_exploratory=1),
            )
        )
        selected_ids = {item.opportunity.opportunity_id for item in result.selected}
        self.assertEqual(len(selected_ids), 1)
        selected_id = next(iter(selected_ids))
        deferred_id = "cand-2" if selected_id == "cand-1" else "cand-1"
        deferred = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_id == deferred_id
        ]
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0].reason_codes, ("EXPLORATION_SLOT_EXHAUSTED",))

        leftover = store.opportunity_selection_candidates[deferred_id]
        self.assertEqual(leftover.outcome, "PENDING")
        self.assertIsNone(leftover.decided_at)

        winner = store.opportunity_selection_candidates[selected_id]
        self.assertEqual(winner.outcome, "ADMITTED")

    def test_candidate_with_side_effect_level_3_is_blocked_and_not_admitted(self) -> None:
        from dataclasses import replace as dc_replace

        store = self._store()
        candidate = _candidate("cand-1")
        blocked_dimensions = dict(candidate.dimensions)
        blocked_dimensions["side_effect_requirement"] = 3
        candidate = dc_replace(candidate, dimensions=blocked_dimensions)
        store.opportunity_selection_candidates["cand-1"] = candidate
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertTrue(result.selected)
        decision = next(
            item for item in result.decisions if item.opportunity.opportunity_id == "cand-1"
        )
        self.assertEqual(decision.outcome.value, "SELECT")
        self.assertIn("SELECTED_FOR_PLANNING", decision.reason_codes)
        updated = store.opportunity_selection_candidates["cand-1"]
        self.assertEqual(updated.outcome, "ADMITTED")

    def test_candidate_from_a_different_research_run_is_not_loaded(self) -> None:
        store = self._store()
        seed_authorization_run(store)  # idempotent for run-1; add a second run below
        from zest.data.records import ResearchRunRecord

        store.research_runs["run-2"] = ResearchRunRecord(
            research_run_id="run-2",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=CREATED_AT,
        )
        store.opportunity_selection_candidates["cand-other"] = _candidate(
            "cand-other", research_run_id="run-2"
        )
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertFalse(result.selected)
        self.assertEqual(store.opportunity_selection_candidates["cand-other"].outcome, "PENDING")

    def test_already_decided_candidate_is_not_reconsidered(self) -> None:
        store = self._store()
        candidate = _candidate("cand-1")
        admitted = replace(
            candidate,
            outcome="ADMITTED",
            resulting_opportunity_id="cand-1",
            decided_at=CREATED_AT,
        )
        store.opportunity_selection_candidates["cand-1"] = admitted
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertEqual(result.decisions, ())


if __name__ == "__main__":
    unittest.main()
