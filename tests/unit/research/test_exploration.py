from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.exploration import (
    DiagnosticOpportunitySources,
    NegativeKnowledge,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    ResearchOpportunity,
    ResearchPolicyBudget,
    SelectionOutcome,
    opportunity_structural_identity,
    propose_diagnostic_opportunities,
    select_research_opportunities,
)
from zest.research.types import ResearchInputError


def _opp(**overrides) -> ResearchOpportunity:
    kind = overrides.get("opportunity_kind", OpportunityKind.DIFFERENTIAL_FOLLOWUP)
    sources = overrides.get("source_refs", ("diff-1",))
    context = overrides.get("context_signature", "differential:diff-1")
    direction = overrides.get(
        "proposed_direction", "Reproduce the controlled diagnostic input difference."
    )
    identity = opportunity_structural_identity(
        kind=kind, source_refs=sources, context_signature=context, proposed_direction=direction
    )
    from zest.research.exploration import OpportunityDimensions

    values = dict(
        opportunity_id="opp-1",
        research_run_id="run-1",
        opportunity_kind=kind,
        mode=overrides.get("mode", OpportunityMode.EXPLOITATION),
        source_refs=sources,
        proposed_direction=direction,
        unresolved_question="Does diagnostic echo still differ by submitted input?",
        expected_information_value_description="High information unresolved diagnostic difference.",
        assumptions=("diagnostic.echo is plumbing, not authorization",),
        dimensions=overrides.get(
            "dimensions",
            OpportunityDimensions(
                expected_information_value=OrdinalLevel.HIGH,
                security_relevance_potential=OrdinalLevel.LOW,
                novelty_composition=OrdinalLevel.LOW,
                unresolved_uncertainty=OrdinalLevel.MEDIUM,
                chain_potential=OrdinalLevel.LOW,
                evidence_coverage=OrdinalLevel.LOW,
                execution_cost=OrdinalLevel.LOW,
                side_effect_requirement=0,
                duplicate_risk=OrdinalLevel.LOW,
                previous_failed_attempts=0,
            ),
        ),
        context_signature=context,
        novelty_composition_marker=False,
        prior_attempt_refs=(),
        strategy_version="exploration.diagnostic.echo.v1",
        structural_identity=identity,
    )
    values.update(overrides)
    if "structural_identity" not in overrides:
        values["structural_identity"] = opportunity_structural_identity(
            kind=values["opportunity_kind"],
            source_refs=values["source_refs"],
            context_signature=values["context_signature"],
            proposed_direction=values["proposed_direction"],
        )
    return ResearchOpportunity(**values)


class ExplorationPolicyTests(unittest.TestCase):
    def test_high_information_unresolved_opportunity_is_selectable(self) -> None:
        decisions = select_research_opportunities(
            (_opp(),), research_run_id="run-1"
        )
        self.assertEqual(decisions[0].outcome, SelectionOutcome.SELECT)
        self.assertIn("NOT_AUTHORIZATION", decisions[0].reason_codes)

    def test_duplicate_exact_opportunity_is_suppressed(self) -> None:
        first = _opp()
        second = _opp(opportunity_id="opp-2")
        decisions = select_research_opportunities(
            (first, second), research_run_id="run-1"
        )
        outcomes = {item.opportunity.opportunity_id: item.outcome for item in decisions}
        self.assertEqual(outcomes["opp-1"], SelectionOutcome.SELECT)
        self.assertEqual(outcomes["opp-2"], SelectionOutcome.SKIP_DUPLICATE)

    def test_zero_exploration_budget_selects_none(self) -> None:
        decisions = select_research_opportunities(
            (_opp(),),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=0),
        )
        self.assertEqual(decisions[0].outcome, SelectionOutcome.BLOCKED_BUDGET)

    def test_negative_budget_is_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            ResearchPolicyBudget(max_selected=-1)

    def test_exploration_slot_is_respected(self) -> None:
        explore = _opp(
            opportunity_id="opp-explore",
            opportunity_kind=OpportunityKind.INVARIANT_CHALLENGE,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("inv-1",),
            context_signature="invariant:inv-1",
            proposed_direction="Challenge the diagnostic input/output correspondence invariant.",
        )
        extra = _opp(
            opportunity_id="opp-explore-2",
            opportunity_kind=OpportunityKind.UNRESOLVED_TARGET_RELATION,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("rel-1",),
            context_signature="relation:rel-1",
            proposed_direction="Inspect an unexplained diagnostic relationship.",
        )
        decisions = select_research_opportunities(
            (explore, extra),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=2, max_exploratory=1),
        )
        selected = [item for item in decisions if item.selected]
        self.assertEqual(len(selected), 1)
        deferred = [item for item in decisions if item.outcome is SelectionOutcome.DEFER]
        self.assertEqual(len(deferred), 1)

    def test_zero_exploratory_slots_select_no_exploration(self) -> None:
        explore = _opp(
            opportunity_id="opp-explore",
            opportunity_kind=OpportunityKind.INVARIANT_CHALLENGE,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("inv-1",),
            context_signature="invariant:inv-1",
            proposed_direction="Challenge the diagnostic input/output correspondence invariant.",
        )
        exploit = _opp(opportunity_id="opp-exploit")
        decisions = select_research_opportunities(
            (explore, exploit),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=2, max_exploratory=0),
        )
        selected = [item for item in decisions if item.selected]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].opportunity.mode, OpportunityMode.EXPLOITATION)
        blocked = [
            item
            for item in decisions
            if item.outcome is SelectionOutcome.BLOCKED_BUDGET
        ]
        self.assertEqual(len(blocked), 1)

    def test_exploitation_can_coexist_with_exploration(self) -> None:
        explore = _opp(
            opportunity_id="opp-explore",
            opportunity_kind=OpportunityKind.INVARIANT_CHALLENGE,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("inv-1",),
            context_signature="invariant:inv-1",
            proposed_direction="Challenge the diagnostic input/output correspondence invariant.",
        )
        exploit = _opp(opportunity_id="opp-exploit")
        decisions = select_research_opportunities(
            (explore, exploit),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=2, max_exploratory=1),
        )
        selected_modes = {
            item.opportunity.mode for item in decisions if item.selected
        }
        self.assertEqual(
            selected_modes, {OpportunityMode.EXPLORATION, OpportunityMode.EXPLOITATION}
        )

    def test_same_context_contradiction_reduces_repetition(self) -> None:
        opportunity = _opp()
        decisions = select_research_opportunities(
            (opportunity,),
            research_run_id="run-1",
            negative_knowledge=(
                NegativeKnowledge(
                    structural_identity=opportunity.structural_identity,
                    context_signature=opportunity.context_signature,
                    strategy_version=opportunity.strategy_version,
                    assessment_ref="assess-1",
                ),
            ),
        )
        self.assertEqual(decisions[0].outcome, SelectionOutcome.SKIP_LOW_INFORMATION)

    def test_changed_context_can_permit_revisit(self) -> None:
        opportunity = _opp(
            opportunity_id="opp-revisit",
            opportunity_kind=OpportunityKind.NEGATIVE_KNOWLEDGE_REVISIT,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("assess-1",),
            context_signature="revisit:differential:diff-1:new",
            proposed_direction="Revisit a previously contradicted diagnostic direction under new context.",
        )
        prior = opportunity_structural_identity(
            kind=OpportunityKind.DIFFERENTIAL_FOLLOWUP,
            source_refs=("diff-1",),
            context_signature="differential:diff-1",
            proposed_direction="Reproduce the controlled diagnostic input difference.",
        )
        decisions = select_research_opportunities(
            (opportunity,),
            research_run_id="run-1",
            negative_knowledge=(
                NegativeKnowledge(
                    structural_identity=prior,
                    context_signature="differential:diff-1",
                    strategy_version="exploration.diagnostic.echo.v1",
                    assessment_ref="assess-1",
                ),
            ),
        )
        self.assertEqual(decisions[0].outcome, SelectionOutcome.SELECT)

    def test_selection_does_not_dispatch(self) -> None:
        decisions = select_research_opportunities((_opp(),), research_run_id="run-1")
        self.assertFalse(hasattr(decisions[0], "invoke"))
        self.assertTrue(decisions[0].selected)

    def test_propose_diagnostic_opportunities_are_not_vulnerabilities(self) -> None:
        items = propose_diagnostic_opportunities(
            "run-1",
            DiagnosticOpportunitySources(
                differential_ids=("diff-1",),
                invariant_ids=("inv-1",),
                chain_ids=("chain-1",),
            ),
            id_prefix="opp",
        )
        self.assertGreaterEqual(len(items), 3)
        kinds = {item.opportunity_kind for item in items}
        self.assertIn(OpportunityKind.DIFFERENTIAL_FOLLOWUP, kinds)
        self.assertIn(OpportunityKind.INVARIANT_CHALLENGE, kinds)


if __name__ == "__main__":
    unittest.main()
