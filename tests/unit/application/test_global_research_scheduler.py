"""Phase 6.1 global research scheduler fabric (G1–G10)."""

from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.global_research_work_audit import (
    NOT_YET_CONNECTED,
    global_research_work_audit,
)
from zest.application.hunter_coverage_opportunity_source import (
    HunterCoverageOpportunitySource,
    HunterCoverageOpportunitySourceCommand,
)
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import (
    FrontierEventRecord,
    FrontierItemRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
)
from zest.research.coverage.types import CoverageCell, CoverageState
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
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
from zest.research.orchestration import NextCycleAction, OrchestrationBounds, OrchestrationState, StopReason, OrchestrationUsage, next_cycle_action
from zest.research.scheduler.fairness import is_plumbing_opportunity, ranked_opportunities
from zest.research.scheduler.types import HunterScore, ScoredCell
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_authorization_run


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


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope() -> CompiledScope:
    return CompiledScope(
        rules=(
            CompiledScopeRule(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme="http",
                host="127.0.0.1",
                host_pattern=None,
                port=9,
                path_prefix=None,
                source_reference="scope-src",
                expires_at=None,
            ),
        )
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=6,
        max_experiments=8,
        max_model_calls=50,
        max_worker_invocations=10,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=2,
        allow_repeated_control_experiments=False,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _seed(store: _Store) -> None:
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
        target_reference="http://127.0.0.1:9/",
        scope=_allow_scope(),
        bounds=_bounds(),
        compiled_scope=_compiled_scope(),
        selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _controller(store: _Store):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock()
    )
    return controller, port


def _frontier(
    frontier_id: str,
    *,
    capability: str = "http.transaction",
    side_effect: int = 2,
    goal_kind: str = "CHARACTERIZE_HTTP_OPERATION",
    action: str = "read",
) -> FrontierItemRecord:
    return FrontierItemRecord(
        frontier_id=frontier_id,
        research_run_id="run-1",
        strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        goal_kind=goal_kind,
        candidate_origin="http://127.0.0.1:9",
        candidate_path="/handoff",
        identity_id="ANONYMOUS",
        proposed_capability=capability,
        proposed_action=action,
        expected_side_effect=side_effect,
        budget_class=side_effect,
        structural_signature="sig-" + frontier_id,
        dedupe_identity="dedupe-" + frontier_id,
        created_at=CREATED_AT,
        current_state="DEFERRED_TO_RESEARCH",
        state_version=3,
        attributes={"method": "GET"},
    )


def _event(event_id: str, frontier_id: str, kind: str, sequence: int) -> FrontierEventRecord:
    return FrontierEventRecord(
        event_id=event_id,
        frontier_id=frontier_id,
        research_run_id="run-1",
        event_kind=kind,
        sequence=sequence,
        created_at=CREATED_AT,
        reason_code="SIDE_EFFECT_NOT_DISCOVERY_EXECUTABLE" if kind == "DEFERRED_TO_RESEARCH" else None,
    )


def _seed_handoff(store: _Store, *, frontier_id="front-se2", **kwargs) -> None:
    store.frontier_items[frontier_id] = _frontier(frontier_id, **kwargs)
    store.frontier_events["ev-1"] = _event("ev-1", frontier_id, "CREATED", 1)
    store.frontier_events["ev-2"] = _event("ev-2", frontier_id, "ELIGIBLE", 2)
    store.frontier_events["ev-3"] = _event("ev-3", frontier_id, "DEFERRED_TO_RESEARCH", 3)


def _scored_cell() -> ScoredCell:
    cell = CoverageCell(
        node_canonical_key="node-1",
        identity_id="identity-1",
        family_id="family-1",
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


class GlobalResearchSchedulerTests(unittest.TestCase):
    def test_g1_discovery_handoff_consumed(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(store)
        controller, port = _controller(store)
        controller.start(_command())
        result = controller.step(_command())
        traces = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_SELECTION_TRACE"
        ]
        self.assertTrue(traces)
        self.assertIn("DISCOVERY_HANDOFF", traces[-1].payload["candidate_engines"])
        selected = [
            item
            for item in store.research_selections.values()
            if item.outcome == "SELECT"
        ]
        self.assertTrue(selected)
        self.assertGreaterEqual(len(port.calls), 1)
        self.assertNotEqual(result.stop_reason, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value)

    def test_g2_multi_source_selection_considers_all(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(store)
        store.hypotheses["hyp-pre"] = HypothesisRecord(
            hypothesis_id="hyp-pre",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            created_at=CREATED_AT,
        )
        HunterCoverageOpportunitySource(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            HunterCoverageOpportunitySourceCommand(
                research_run_id="run-1", scored_cells=(_scored_cell(),)
            )
        )
        result = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        engines = {item.opportunity.opportunity_kind for item in result.decisions}
        self.assertIn(OpportunityKind.DISCOVERY_HANDOFF, engines)
        self.assertIn(OpportunityKind.HUNTER_COVERAGE_GAP, engines)
        self.assertIn(OpportunityKind.HYPOTHESIS_FOLLOWUP, engines)
        selected = result.selected[0]
        self.assertNotEqual(
            selected.opportunity.opportunity_kind, OpportunityKind.HYPOTHESIS_FOLLOWUP
        )
        self.assertFalse(is_plumbing_opportunity(selected.opportunity))

    def test_g3_fairness_selects_target_work_while_diagnostics_exist(self) -> None:
        diagnostic = _opp(
            opportunity_id="diag-1",
            opportunity_kind=OpportunityKind.INVARIANT_CHALLENGE,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("inv-1",),
            context_signature="invariant:inv-1",
        )
        hunter = _opp(
            opportunity_id="hunt-1",
            opportunity_kind=OpportunityKind.HUNTER_COVERAGE_GAP,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("family-1", "node-1", "identity-1"),
            context_signature="hunter_coverage:family-1:node-1:identity-1",
            proposed_direction="Investigate HunterFamily family-1 against node node-1.",
            strategy_version="hunter_coverage_opportunity_source.v1",
        )
        ranked = ranked_opportunities((diagnostic, hunter))
        self.assertEqual(ranked[0].opportunity_id, "hunt-1")
        decisions = select_research_opportunities(
            (diagnostic, hunter),
            research_run_id="run-1",
            budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
        )
        selected = [item for item in decisions if item.selected]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].opportunity.opportunity_id, "hunt-1")

    def test_g4_single_dispatch_owner(self) -> None:
        controller = Path(__file__).resolve().parents[3] / "src" / "zest" / "application" / "autonomous_research_controller.py"
        text = controller.read_text(encoding="utf-8")
        self.assertNotIn("DispatchApprovedV3Queue", text)
        self.assertNotIn("dispatch_approved_v3_queue", text)

    def test_g5_missing_precondition_is_explicit(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(
            store,
            frontier_id="front-control",
            capability="browser.page",
            side_effect=1,
            goal_kind="INSPECT_CONTROL",
            action="observe",
        )
        controller, port = _controller(store)
        controller.start(_command(bounds=_bounds(side_effect_ceiling=1)))
        controller.step(_command(bounds=_bounds(side_effect_ceiling=1)))
        missing = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        ]
        self.assertTrue(missing)
        self.assertEqual(len(port.calls), 0)
        candidate = next(iter(store.opportunity_selection_candidates.values()))
        self.assertIn(candidate.outcome, {"PENDING", "ADMITTED"})

    def test_g6_se3_ceiling_is_not_approvable(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(store, frontier_id="front-se3", side_effect=3)
        controller, port = _controller(store)
        command = _command(bounds=_bounds(side_effect_ceiling=0))
        controller.start(command)
        result = controller.step(command)
        self.assertNotEqual(result.state, OrchestrationState.WAITING_HUMAN.value)
        self.assertNotEqual(result.stop_reason, StopReason.REQUIRE_HUMAN_REVIEW.value)
        self.assertEqual(len(port.calls), 0)
        blocked = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING"
        ]
        self.assertTrue(blocked)

    def test_g7_handoff_survives_restart(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(store)
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        first = list(store.opportunity_selection_candidates.values())
        self.assertTrue(first)
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        second = list(store.opportunity_selection_candidates.values())
        self.assertEqual(len(first), len(second))
        self.assertEqual(first[0].candidate_id, second[0].candidate_id)

    def test_g8_equivalent_work_is_deduped(self) -> None:
        first = _opp(opportunity_id="opp-a")
        second = _opp(opportunity_id="opp-b")
        decisions = select_research_opportunities(
            (first, second), research_run_id="run-1"
        )
        outcomes = {item.opportunity.opportunity_id: item.outcome for item in decisions}
        self.assertEqual(outcomes["opp-a"], SelectionOutcome.SELECT)
        self.assertEqual(outcomes["opp-b"], SelectionOutcome.SKIP_DUPLICATE)

    def test_g9_pending_work_forbids_global_complete(self) -> None:
        store = _Store()
        _seed(store)
        _seed_handoff(store)
        store.hypotheses["hyp-pre"] = HypothesisRecord(
            hypothesis_id="hyp-pre",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            created_at=CREATED_AT,
        )
        controller, _ = _controller(store)
        command = _command(
            bounds=_bounds(side_effect_ceiling=0, max_selected_opportunities=1),
            selection_budget=ResearchPolicyBudget(max_selected=0, max_exploratory=0),
        )
        controller.start(command)
        result = controller.step(command)
        self.assertNotEqual(
            result.stop_reason, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertIn("RESEARCH_PENDING", audit.completion_block_reasons)
        self.assertIsInstance(audit.mutation_pending, int)
        self.assertIsInstance(audit.protocol_pending, int)
        self.assertIsInstance(audit.oast_pending, int)
        self.assertIsInstance(audit.differential_pending, int)
        self.assertIsInstance(audit.invariant_pending, int)
        self.assertIsInstance(audit.chain_pending, int)
        self.assertIsInstance(audit.auth_pending, int)
        self.assertIsInstance(audit.authz_pending, int)
        self.assertIsInstance(audit.workflow_pending, int)

    def test_g10_empty_tick_is_not_implicit_exhaustion_policy(self) -> None:
        action, reason = next_cycle_action(
            bounds=OrchestrationBounds(
                max_cycles=3,
                max_experiments=3,
                max_model_calls=12,
                max_worker_invocations=3,
                max_elapsed_ms=60_000,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
            ),
            usage=OrchestrationUsage(
                cycles_completed=1,
                experiments_executed=0,
                model_calls=0,
                worker_invocations=0,
                elapsed_ms=0,
                opportunities_selected=0,
                runtime_fallbacks=0,
            ),
            selected_count=0,
            hypothesis_count=1,
            unknown_outcome_open=False,
        )
        self.assertEqual(action, NextCycleAction.NO_SELECTION_THIS_TICK)
        self.assertIsNone(reason)

    def test_inventory_unknown_is_not_zero(self) -> None:
        store = _Store()
        _seed(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.auth_pending, int)
        self.assertNotEqual(audit.auth_pending, NOT_YET_CONNECTED)


if __name__ == "__main__":
    unittest.main()
