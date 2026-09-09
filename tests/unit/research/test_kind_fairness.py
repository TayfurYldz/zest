"""Kind-fairness starvation prevention (F-A–F-E). Not authorization."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    ResearchOpportunity,
    ResearchPolicyBudget,
    SelectionOutcome,
    opportunity_structural_identity,
    select_research_opportunities,
)
from zest.research.scheduler.eligibility import select_time_blockers
from zest.research.scheduler.fairness import (
    KIND_STARVATION_BOUND,
    DurableSelectEvent,
    kind_starvation_ages,
)


def _dims() -> OpportunityDimensions:
    return OpportunityDimensions(
        expected_information_value=OrdinalLevel.HIGH,
        security_relevance_potential=OrdinalLevel.HIGH,
        novelty_composition=OrdinalLevel.MEDIUM,
        unresolved_uncertainty=OrdinalLevel.HIGH,
        chain_potential=OrdinalLevel.MEDIUM,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=0,
        duplicate_risk=OrdinalLevel.LOW,
        previous_failed_attempts=0,
    )


def _opp(
    *,
    opportunity_id: str,
    kind: OpportunityKind,
    source: str,
    strategy_version: str,
    assumptions: tuple[str, ...] = ("not_authorization",),
    source_refs: tuple[str, ...] | None = None,
    dimensions: OpportunityDimensions | None = None,
) -> ResearchOpportunity:
    refs = source_refs if source_refs is not None else (source,)
    context = f"{kind.value}:{source}"
    direction = f"Investigate {kind.value} direction {source}."
    return ResearchOpportunity(
        opportunity_id=opportunity_id,
        research_run_id="run-1",
        opportunity_kind=kind,
        mode=OpportunityMode.EXPLORATION,
        source_refs=refs,
        proposed_direction=direction,
        unresolved_question="Does this direction yield new observations?",
        expected_information_value_description="high information unresolved work",
        assumptions=assumptions,
        dimensions=dimensions or _dims(),
        context_signature=context,
        novelty_composition_marker=False,
        prior_attempt_refs=(),
        strategy_version=strategy_version,
        structural_identity=opportunity_structural_identity(
            kind=kind,
            source_refs=refs,
            context_signature=context,
            proposed_direction=direction,
        ),
    )


def _mixed_pool(*, suffix: str) -> tuple:
    return (
        _opp(
            opportunity_id=f"hunt-{suffix}",
            kind=OpportunityKind.HUNTER_COVERAGE_GAP,
            source=f"h-{suffix}",
            strategy_version="hunter.coverage.opportunity.v1",
        ),
        _opp(
            opportunity_id=f"mut-{suffix}",
            kind=OpportunityKind.MUTATION_VARIANT,
            source=f"m-{suffix}",
            strategy_version="mutation.variant.opportunity.v1",
        ),
        _opp(
            opportunity_id=f"chain-{suffix}",
            kind=OpportunityKind.CHAIN,
            source=f"c-{suffix}",
            strategy_version="chain.dic.opportunity.v1",
        ),
        _opp(
            opportunity_id="oast-1",
            kind=OpportunityKind.OAST_INTERACTION,
            source="oast-fixed",
            strategy_version="oast.interaction.opportunity.v1",
            assumptions=("callback_id:cb-1", "not_authorization"),
            source_refs=("SSRF_SERVER_SIDE_FETCH", "node-1", "id-alice", "url"),
        ),
    )


class KindFairnessTests(unittest.TestCase):
    def test_f_a_every_eligible_kind_selected_within_bound(self) -> None:
        previously: set[str] = set()
        selected_kinds: set[str] = set()
        recent: tuple[str, ...] = ()
        first_seen = {
            OpportunityKind.HUNTER_COVERAGE_GAP.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.MUTATION_VARIANT.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.CHAIN.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.OAST_INTERACTION.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        events: list[DurableSelectEvent] = []
        budget = ResearchPolicyBudget(max_selected=1, max_exploratory=1)
        cycles = KIND_STARVATION_BOUND * 4
        for cycle in range(cycles):
            pool = _mixed_pool(suffix=str(cycle))
            ages = kind_starvation_ages(
                pending_kind_first_seen=first_seen,
                select_events=tuple(events),
            )
            decisions = select_research_opportunities(
                pool,
                research_run_id="run-1",
                budget=budget,
                previously_selected_identities=frozenset(previously),
                recently_selected_kinds=recent,
                kind_starvation_ages=ages,
            )
            chosen = [item for item in decisions if item.selected]
            self.assertEqual(len(chosen), 1)
            kind = chosen[0].opportunity.opportunity_kind.value
            selected_kinds.add(kind)
            previously.add(chosen[0].opportunity.structural_identity)
            created = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=cycle)
            events.append(
                DurableSelectEvent(
                    kind=kind,
                    created_at=created,
                    selection_id=f"sel-{cycle:04d}",
                )
            )
            recent = tuple(item.kind for item in events)
        expected = {
            OpportunityKind.HUNTER_COVERAGE_GAP.value,
            OpportunityKind.MUTATION_VARIANT.value,
            OpportunityKind.CHAIN.value,
            OpportunityKind.OAST_INTERACTION.value,
        }
        self.assertEqual(selected_kinds, expected)

    def test_f_b_oast_selected_while_others_replenish(self) -> None:
        previously: set[str] = set()
        recent: tuple[str, ...] = ()
        first_seen = {
            OpportunityKind.HUNTER_COVERAGE_GAP.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.MUTATION_VARIANT.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.CHAIN.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
            OpportunityKind.OAST_INTERACTION.value: datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        events: list[DurableSelectEvent] = []
        oast_selected_at = None
        budget = ResearchPolicyBudget(max_selected=1, max_exploratory=1)
        for cycle in range(KIND_STARVATION_BOUND + 2):
            pool = _mixed_pool(suffix=str(cycle))
            ages = kind_starvation_ages(
                pending_kind_first_seen=first_seen,
                select_events=tuple(events),
            )
            decisions = select_research_opportunities(
                pool,
                research_run_id="run-1",
                budget=budget,
                previously_selected_identities=frozenset(previously),
                recently_selected_kinds=recent,
                kind_starvation_ages=ages,
            )
            chosen = [item for item in decisions if item.selected][0]
            kind = chosen.opportunity.opportunity_kind.value
            previously.add(chosen.opportunity.structural_identity)
            events.append(
                DurableSelectEvent(
                    kind=kind,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
                    + timedelta(seconds=cycle),
                    selection_id=f"sel-{cycle:04d}",
                )
            )
            recent = tuple(item.kind for item in events)
            if kind == OpportunityKind.OAST_INTERACTION.value:
                oast_selected_at = cycle
                break
        self.assertIsNotNone(oast_selected_at)
        self.assertLessEqual(oast_selected_at, KIND_STARVATION_BOUND)

    def test_f_c_missing_precondition_not_forced(self) -> None:
        incomplete = _opp(
            opportunity_id="oast-bad",
            kind=OpportunityKind.OAST_INTERACTION,
            source="oast-bad",
            strategy_version="oast.interaction.opportunity.v1",
            assumptions=("not_authorization",),
            source_refs=("SSRF_SERVER_SIDE_FETCH",),
        )
        hunter = _opp(
            opportunity_id="hunt-1",
            kind=OpportunityKind.HUNTER_COVERAGE_GAP,
            source="h-1",
            strategy_version="hunter.coverage.opportunity.v1",
        )
        codes = select_time_blockers(incomplete)
        self.assertIsNotNone(codes)
        ages = {OpportunityKind.OAST_INTERACTION.value: KIND_STARVATION_BOUND + 5}
        decisions = select_research_opportunities(
            (incomplete, hunter),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            kind_starvation_ages=ages,
            eligibility_blockers={incomplete.opportunity_id: codes},
        )
        by_id = {item.opportunity.opportunity_id: item for item in decisions}
        self.assertEqual(by_id["oast-bad"].outcome, SelectionOutcome.NEEDS_MORE_CONTEXT)
        self.assertIn("MISSING_PRECONDITION", by_id["oast-bad"].reason_codes)
        self.assertTrue(by_id["hunt-1"].selected)

    def test_f_d_waiting_callback_does_not_reselect(self) -> None:
        oast = _opp(
            opportunity_id="oast-1",
            kind=OpportunityKind.OAST_INTERACTION,
            source="oast-fixed",
            strategy_version="oast.interaction.opportunity.v1",
            assumptions=("callback_id:cb-1",),
            source_refs=("SSRF_SERVER_SIDE_FETCH", "node-1", "id-alice", "url"),
        )
        hunter = _opp(
            opportunity_id="hunt-new",
            kind=OpportunityKind.HUNTER_COVERAGE_GAP,
            source="h-new",
            strategy_version="hunter.coverage.opportunity.v1",
        )
        decisions = select_research_opportunities(
            (oast, hunter),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            previously_selected_identities=frozenset({oast.structural_identity}),
            kind_starvation_ages={
                OpportunityKind.OAST_INTERACTION.value: KIND_STARVATION_BOUND + 8
            },
        )
        by_id = {item.opportunity.opportunity_id: item for item in decisions}
        self.assertEqual(by_id["oast-1"].outcome, SelectionOutcome.SKIP_DUPLICATE)
        self.assertTrue(by_id["hunt-new"].selected)

    def test_f_e_durable_history_survives_restart(self) -> None:
        first_seen = {
            OpportunityKind.HUNTER_COVERAGE_GAP.value: datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
            OpportunityKind.OAST_INTERACTION.value: datetime(
                2026, 1, 1, tzinfo=timezone.utc
            ),
        }
        events = tuple(
            DurableSelectEvent(
                kind=OpportunityKind.HUNTER_COVERAGE_GAP.value,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
                + timedelta(seconds=index),
                selection_id=f"sel-{index:04d}",
            )
            for index in range(KIND_STARVATION_BOUND)
        )
        ages_before = kind_starvation_ages(
            pending_kind_first_seen=first_seen, select_events=events
        )
        # Restart: only durable SELECT events remain; no process-local counter.
        ages_after = kind_starvation_ages(
            pending_kind_first_seen=first_seen, select_events=events
        )
        self.assertEqual(ages_before, ages_after)
        self.assertGreaterEqual(
            ages_after[OpportunityKind.OAST_INTERACTION.value], KIND_STARVATION_BOUND
        )
        hunter = _opp(
            opportunity_id="hunt-r",
            kind=OpportunityKind.HUNTER_COVERAGE_GAP,
            source="h-r",
            strategy_version="hunter.coverage.opportunity.v1",
            dimensions=OpportunityDimensions(
                expected_information_value=OrdinalLevel.HIGH,
                security_relevance_potential=OrdinalLevel.HIGH,
                novelty_composition=OrdinalLevel.MEDIUM,
                unresolved_uncertainty=OrdinalLevel.HIGH,
                chain_potential=OrdinalLevel.HIGH,
                evidence_coverage=OrdinalLevel.LOW,
                execution_cost=OrdinalLevel.LOW,
                side_effect_requirement=0,
                duplicate_risk=OrdinalLevel.LOW,
                previous_failed_attempts=0,
            ),
        )
        pool = (
            hunter,
            _opp(
                opportunity_id="oast-1",
                kind=OpportunityKind.OAST_INTERACTION,
                source="oast-fixed",
                strategy_version="oast.interaction.opportunity.v1",
                assumptions=("callback_id:cb-1",),
                source_refs=("SSRF_SERVER_SIDE_FETCH", "node-1", "id-alice", "url"),
            ),
        )
        decisions = select_research_opportunities(
            pool,
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            recently_selected_kinds=tuple(item.kind for item in events),
            kind_starvation_ages=ages_after,
        )
        selected = [item for item in decisions if item.selected]
        self.assertEqual(
            selected[0].opportunity.opportunity_kind, OpportunityKind.OAST_INTERACTION
        )


if __name__ == "__main__":
    unittest.main()
