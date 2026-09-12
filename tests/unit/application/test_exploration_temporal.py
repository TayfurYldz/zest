from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

import pathsetup  # noqa: F401

from core.fixtures import base_request
from zest.application.admit_diagnostic_invariant import (
    AdmitDiagnosticInvariant,
    AdmitDiagnosticInvariantCommand,
)
from zest.application.capture_diagnostic_snapshot import (
    CaptureDiagnosticSnapshot,
    CaptureDiagnosticSnapshotCommand,
)
from zest.application.compare_diagnostic_differential import (
    CompareDiagnosticDifferential,
    CompareDiagnosticDifferentialCommand,
)
from zest.application.compare_diagnostic_snapshots import (
    CompareDiagnosticSnapshots,
    CompareDiagnosticSnapshotsCommand,
)
from zest.application.compose_diagnostic_chain import (
    ComposeDiagnosticChain,
    ComposeDiagnosticChainCommand,
)
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.core.enums import ExecutionDecisionKind, ScopeRuleEffect, SideEffectLevel
from zest.core.execution import evaluate_execution
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceError
from zest.data.records import HypothesisAssessmentRecord, HypothesisRecord
from zest.research.admission import AdmissionOutcome
from zest.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialOutcome,
)
from zest.research.epistemic import EpistemicClass
from zest.research.exploration import OpportunityMode, ResearchPolicyBudget, SelectionOutcome
from zest.research.planning import plan_diagnostic_echo
from zest.research.scheduler.fairness import KIND_STARVATION_BOUND
from zest.research.temporal import ChangeOutcome, SnapshotOutcome
from zest.research.types import ResearchInputError
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _plan(message: str):
    return plan_diagnostic_echo(
        "hyp-1", budget_id="budget-1", target_reference="target-1", message=message
    )


def _seed(store: _Store) -> None:
    seed_spine(store)
    store.issued_budgets["budget-1"] = replace(
        store.issued_budgets["budget-1"], max_requests=8, max_tool_calls=8
    )


def _run_experiment(store: _Store, experiment_id: str, message: str) -> None:
    factory = FakeUnitOfWorkFactory(store)
    if experiment_id not in store.experiments:
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=_plan(message),
            )
        )
    worker = RecordingWorkerPort(store=store)
    ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
        ExecutePlannedExperimentCommand(
            experiment_id=experiment_id, plan=_plan(message), scope=_allow_scope()
        )
    )
    EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
        EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
    )


def _observation_ids(store: _Store) -> tuple[str, ...]:
    return tuple(sorted(store.observations))


class ExplorationTemporalApplicationTests(unittest.TestCase):
    def test_selection_does_not_dispatch_or_authorize(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        factory = FakeUnitOfWorkFactory(store)
        AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        ComposeDiagnosticChain(factory, clock=FixedClock()).execute(
            ComposeDiagnosticChainCommand(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference="target-1",
                hypothesis_id="hyp-1",
            )
        )
        worker_before = len(store.execution_attempts)
        core_before = evaluate_execution(base_request(side_effect_level=SideEffectLevel.LEVEL_0))
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=3, max_exploratory=1),
            )
        )
        self.assertTrue(result.selected)
        modes = {item.opportunity.mode for item in result.selected}
        self.assertIn(OpportunityMode.EXPLORATION, modes)
        self.assertIn(OpportunityMode.EXPLOITATION, modes)
        self.assertEqual(len(store.execution_attempts), worker_before)
        core_after = evaluate_execution(base_request(side_effect_level=SideEffectLevel.LEVEL_0))
        self.assertEqual(core_before.decision, core_after.decision)
        self.assertEqual(core_before.decision, ExecutionDecisionKind.ALLOW)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.findings), 0)

    def test_surface_discovery_blocked_sibling_preserves_observed_followup(
        self,
    ) -> None:
        store = _Store()
        _seed(store)
        store.hypotheses["hyp-1"] = replace(
            store.hypotheses["hyp-1"],
            origin_reference="surface-discovery-v1",
        )

        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            uow.experiments.set_execution_state("exp-1", "BLOCKED")
            uow.commit()

        _run_experiment(store, "exp-2", "alpha")

        self.assertEqual(
            store.experiments["exp-1"].execution_state,
            "BLOCKED",
        )
        self.assertEqual(
            store.experiments["exp-2"].execution_state,
            "EXECUTION_SUCCEEDED",
        )
        self.assertTrue(store.observations)

        result = SelectResearchOpportunities(
            factory,
            clock=FixedClock(),
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1"
            )
        )

        followups = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value
            == "HYPOTHESIS_FOLLOWUP"
        ]

        self.assertTrue(followups)
        self.assertTrue(any(item.selected for item in followups))

        # Fix must never rewrite the truthful blocked sibling.
        self.assertEqual(
            store.experiments["exp-1"].execution_state,
            "BLOCKED",
        )

    def test_surface_discovery_only_blocked_without_observation_stays_suppressed(
        self,
    ) -> None:
        store = _Store()
        _seed(store)
        store.hypotheses["hyp-1"] = replace(
            store.hypotheses["hyp-1"],
            origin_reference="surface-discovery-v1",
        )

        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            uow.experiments.set_execution_state("exp-1", "BLOCKED")
            uow.commit()

        result = SelectResearchOpportunities(
            factory,
            clock=FixedClock(),
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1"
            )
        )

        followups = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value
            == "HYPOTHESIS_FOLLOWUP"
        ]

        self.assertFalse(followups)
        self.assertEqual(
            store.experiments["exp-1"].execution_state,
            "BLOCKED",
        )

    def test_normal_blocked_hypothesis_remains_suppressed_with_successful_sibling(
        self,
    ) -> None:
        store = _Store()
        _seed(store)

        factory = FakeUnitOfWorkFactory(store)

        with factory.open() as uow:
            uow.experiments.set_execution_state("exp-1", "BLOCKED")
            uow.commit()

        _run_experiment(store, "exp-2", "alpha")

        self.assertTrue(store.observations)

        result = SelectResearchOpportunities(
            factory,
            clock=FixedClock(),
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1"
            )
        )

        followups = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value
            == "HYPOTHESIS_FOLLOWUP"
        ]

        self.assertFalse(followups)
        self.assertEqual(
            store.experiments["exp-1"].execution_state,
            "BLOCKED",
        )

    def test_research_work_fabric_anchor_is_not_hypothesis_followup_source(
        self,
    ) -> None:
        store = _Store()
        _seed(store)

        store.hypotheses["work-anchor-1"] = HypothesisRecord(
            hypothesis_id="work-anchor-1",
            research_run_id="run-1",
            claim="Execute selected research work under Core authorization.",
            origin_reference="research-work-fabric.v1:opportunity-1",
            created_at=CREATED_AT,
        )

        result = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store),
            clock=FixedClock(),
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1"
            )
        )

        recursive = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value
            == "HYPOTHESIS_FOLLOWUP"
            and item.opportunity.source_refs == ("work-anchor-1",)
        ]

        self.assertFalse(
            recursive,
            "research-work execution anchors must never recursively become "
            "research hypotheses",
        )

    def test_zero_and_negative_exploration_budget(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        factory = FakeUnitOfWorkFactory(store)
        AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        zero = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=4, max_exploratory=0),
            )
        )
        self.assertFalse(
            any(item.opportunity.mode is OpportunityMode.EXPLORATION for item in zero.selected)
        )
        with self.assertRaises(ResearchInputError):
            ResearchPolicyBudget(max_exploratory=-1)

    def test_same_context_contradiction_and_changed_context_revisit(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        obs_id = next(iter(store.observations))
        store.hypothesis_assessments["assess-neg-1"] = HypothesisAssessmentRecord(
            assessment_id="assess-neg-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-2",
            research_run_id="run-1",
            assessment_outcome="CONTRADICTS_PREDICTION",
            observation_ids=(obs_id,),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="diagnostic.echo.v1",
            rationale={"not_evidence": True},
            evaluation_strategy="diagnostic.echo.v1",
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        result = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        followups = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value == "HYPOTHESIS_FOLLOWUP"
        ]
        revisits = [
            item
            for item in result.decisions
            if item.opportunity.opportunity_kind.value == "NEGATIVE_KNOWLEDGE_REVISIT"
        ]
        self.assertTrue(followups)
        self.assertEqual(followups[0].outcome, SelectionOutcome.SKIP_LOW_INFORMATION)
        self.assertTrue(revisits)

        revisit_selected = any(item.selected for item in revisits)

        for _ in range(KIND_STARVATION_BOUND):
            if revisit_selected:
                break

            result = SelectResearchOpportunities(
                factory,
                clock=FixedClock(),
            ).execute(
                SelectResearchOpportunitiesCommand(
                    research_run_id="run-1"
                )
            )

            revisits = [
                item
                for item in result.decisions
                if item.opportunity.opportunity_kind.value
                == "NEGATIVE_KNOWLEDGE_REVISIT"
            ]

            revisit_selected = any(
                item.selected for item in revisits
            )

        self.assertTrue(
            revisit_selected,
            "negative-knowledge revisit must remain eligible and eventually "
            "receive a scheduler slot",
        )

        original = store.hypothesis_assessments["assess-neg-1"]
        self.assertEqual(original.assessment_outcome, "CONTRADICTS_PREDICTION")

    def test_snapshot_change_and_time_differential(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        factory = FakeUnitOfWorkFactory(store)
        obs_a, obs_b = _observation_ids(store)
        t1 = CREATED_AT
        t2 = CREATED_AT + timedelta(hours=1)
        captured_a, snap_a = CaptureDiagnosticSnapshot(factory, clock=FixedClock()).execute(
            CaptureDiagnosticSnapshotCommand(
                research_run_id="run-1",
                target_identity="target-1",
                observation_ids=(obs_a,),
                snapshot_id="snap-1",
                captured_at=t1,
            )
        )
        captured_b, snap_b = CaptureDiagnosticSnapshot(factory, clock=FixedClock()).execute(
            CaptureDiagnosticSnapshotCommand(
                research_run_id="run-1",
                target_identity="target-1",
                observation_ids=(obs_b,),
                snapshot_id="snap-2",
                captured_at=t2,
            )
        )
        self.assertEqual(captured_a, SnapshotOutcome.CAPTURED)
        self.assertEqual(captured_b, SnapshotOutcome.CAPTURED)
        assert snap_a is not None and snap_b is not None
        change = CompareDiagnosticSnapshots(factory, clock=FixedClock()).execute(
            CompareDiagnosticSnapshotsCommand(
                research_run_id="run-1",
                baseline_snapshot_id="snap-1",
                variant_snapshot_id="snap-2",
            )
        )
        self.assertEqual(change.outcome, ChangeOutcome.COMPARED)
        assert change.change_event is not None
        self.assertNotIn("vulnerability", change.change_event.statement.lower())
        missing = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-time-missing",
                    research_run_id="run-1",
                    baseline_observation_ids=(obs_a,),
                    variant_observation_ids=(obs_b,),
                    changed_dimensions=(DifferentialDimension.TIME,),
                    common_dimensions=(DifferentialDimension.ACTION,),
                )
            )
        )
        self.assertEqual(
            missing.outcome, DifferentialOutcome.REJECTED_MISSING_TEMPORAL_PROVENANCE
        )
        compared = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-time-1",
                    research_run_id="run-1",
                    baseline_observation_ids=(obs_a,),
                    variant_observation_ids=(obs_b,),
                    changed_dimensions=(
                        DifferentialDimension.TIME,
                        DifferentialDimension.INPUT,
                    ),
                    common_dimensions=(DifferentialDimension.ACTION,),
                    baseline_snapshot_id="snap-1",
                    variant_snapshot_id="snap-2",
                )
            )
        )
        self.assertEqual(compared.outcome, DifferentialOutcome.COMPARED)
        worker_before = len(store.execution_attempts)

        change_decision = None

        for _ in range(KIND_STARVATION_BOUND + 1):
            selected = SelectResearchOpportunities(
                factory,
                clock=FixedClock(),
            ).execute(
                SelectResearchOpportunitiesCommand(
                    research_run_id="run-1"
                )
            )

            change_decision = next(
                (
                    item
                    for item in selected.selected
                    if item.opportunity.context_signature.startswith("change:")
                ),
                None,
            )

            if change_decision is not None:
                break

        self.assertIsNotNone(
            change_decision,
            "durable ChangeEvent work must eventually receive a scheduler slot",
        )

        self.assertEqual(
            len(store.execution_attempts),
            worker_before,
            "selection alone must not dispatch a Worker",
        )

        assert change_decision is not None
        opportunity_id = change_decision.opportunity.opportunity_id
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="What diagnostic behavior changed between snapshot t1 and t2?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-gate09",
                opportunity_id=opportunity_id,
                change_event_id=change.change_event.change_event_id,
            )
        )
        self.assertEqual(proposed.outcome, AdmissionOutcome.ADMITTED)
        opp_item = proposed.context.item_by_id(opportunity_id)
        change_item = proposed.context.item_by_id(change.change_event.change_event_id)
        assert opp_item is not None and change_item is not None
        self.assertEqual(opp_item.epistemic_class, EpistemicClass.HYPOTHESIS)
        self.assertTrue(opp_item.payload["not_hypothesis_truth"])
        self.assertEqual(change_item.epistemic_class, EpistemicClass.DERIVED_FACT)
        self.assertTrue(change_item.payload["not_a_vulnerability"])
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.findings), 0)
        self.assertIs(store.snapshots["snap-1"], store.snapshots["snap-1"])

    def test_rollback_leaves_no_partial_opportunity_or_change(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        AdmitDiagnosticInvariant(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(AdmitDiagnosticInvariantCommand(research_run_id="run-1"))
        with self.assertRaises(PersistenceError):
            SelectResearchOpportunities(
                FakeUnitOfWorkFactory(store, fail_on="research_opportunities"),
                clock=FixedClock(),
            ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        self.assertEqual(len(store.research_opportunities), 0)
        self.assertEqual(len(store.research_selections), 0)
        obs_id = next(iter(store.observations))
        with self.assertRaises(PersistenceError):
            CaptureDiagnosticSnapshot(
                FakeUnitOfWorkFactory(store, fail_on="snapshots"),
                clock=FixedClock(),
            ).execute(
                CaptureDiagnosticSnapshotCommand(
                    research_run_id="run-1",
                    target_identity="target-1",
                    observation_ids=(obs_id,),
                )
            )
        self.assertEqual(len(store.snapshots), 0)


if __name__ == "__main__":
    unittest.main()
