from __future__ import annotations

import unittest
from dataclasses import replace

import pathsetup  # noqa: F401

from zest.application.admit_diagnostic_invariant import (
    AdmitDiagnosticInvariant,
    AdmitDiagnosticInvariantCommand,
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
from zest.application.record_invariant_counterexample import (
    RecordInvariantCounterexample,
    RecordInvariantCounterexampleCommand,
)
from zest.core.enums import ExecutionDecisionKind, ReasonCode, ScopeRuleEffect, SideEffectLevel
from zest.core.execution import evaluate_execution
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceError
from zest.research.admission import AdmissionOutcome
from zest.research.chain import ChainOutcome, experiment_plan_for_chain_step
from zest.research.epistemic import EpistemicClass
from zest.research.invariant import InvariantAdmissionOutcome, InvariantStatus
from zest.research.planning import plan_diagnostic_echo
from zest.research.target_model import TargetEpistemicStatus
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_spine
from fixtures import base_request
from zest.research.chain import ChainNodeKind, ChainStep


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


class InvariantChainApplicationTests(unittest.TestCase):
    def test_invariant_and_chain_enter_hypothesis_cycle_without_evidence(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        factory = FakeUnitOfWorkFactory(store)
        worker_before = len(store.execution_attempts)
        admitted = AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        self.assertEqual(admitted.outcome, InvariantAdmissionOutcome.ADMITTED)
        assert admitted.hypothesis is not None
        composed = ComposeDiagnosticChain(factory, clock=FixedClock()).execute(
            ComposeDiagnosticChainCommand(
                research_run_id="run-1",
                invariant_id=admitted.hypothesis.invariant_id,
                budget_id="budget-1",
                target_reference="target-1",
                hypothesis_id="hyp-1",
            )
        )
        self.assertEqual(composed.decisions[0].outcome, ChainOutcome.ADMITTED)
        self.assertEqual(len(store.execution_attempts), worker_before)
        self.assertTrue(composed.suggested_plans)
        assert composed.decisions[0].hypothesis is not None
        result = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="Does diagnostic echo keep input/output correspondence?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-inv-1",
                invariant_id=admitted.hypothesis.invariant_id,
                chain_id=composed.decisions[0].hypothesis.chain_id,
            )
        )
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        invariant_item = result.context.item_by_id(admitted.hypothesis.invariant_id)
        assert invariant_item is not None
        self.assertEqual(invariant_item.epistemic_class, EpistemicClass.HYPOTHESIS)
        self.assertTrue(invariant_item.payload["not_a_fact"])
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)

    def test_counterexample_is_context_bound(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        factory = FakeUnitOfWorkFactory(store)
        admitted = AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        assert admitted.hypothesis is not None
        obs_id = next(iter(store.observations))
        updated = RecordInvariantCounterexample(factory, clock=FixedClock()).execute(
            RecordInvariantCounterexampleCommand(
                invariant_id=admitted.hypothesis.invariant_id,
                source_ref=obs_id,
                applicability_context={"input": "alpha", "not_global": True},
            )
        )
        self.assertEqual(updated.status, InvariantStatus.CHALLENGED)
        self.assertIn(obs_id, updated.counterexample_refs)

    def test_chain_level_3_cannot_bypass_core(self) -> None:
        plan = experiment_plan_for_chain_step(
            ChainStep(
                step_index=0,
                node_kind=ChainNodeKind.CAPABILITY,
                source_ref="cap-1",
                epistemic_status=TargetEpistemicStatus.DERIVED,
                state_signature="input=alpha",
                side_effect_level=3,
                statement="Derived CAN_OBSERVE_ECHO under diagnostic input.",
            ),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertEqual(plan.side_effect_level, 0)
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_3)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_chain_level_2_still_requires_core_review(self) -> None:
        plan = experiment_plan_for_chain_step(
            ChainStep(
                step_index=0,
                node_kind=ChainNodeKind.CAPABILITY,
                source_ref="cap-1",
                epistemic_status=TargetEpistemicStatus.DERIVED,
                state_signature="input=alpha",
                side_effect_level=2,
                statement="Derived CAN_OBSERVE_ECHO under diagnostic input.",
            ),
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
        )
        self.assertEqual(plan.side_effect_level, 0)
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_2)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_empty_observations_are_untestable(self) -> None:
        store = _Store()
        _seed(store)
        admitted = AdmitDiagnosticInvariant(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(AdmitDiagnosticInvariantCommand(research_run_id="run-1"))
        self.assertEqual(admitted.outcome, InvariantAdmissionOutcome.REJECTED_UNTESTABLE)
        self.assertEqual(len(store.invariant_hypotheses), 0)

    def test_transaction_failure_leaves_no_partial_chain(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        with self.assertRaises(PersistenceError):
            ComposeDiagnosticChain(
                FakeUnitOfWorkFactory(store, fail_on="chain_hypotheses"),
                clock=FixedClock(),
            ).execute(ComposeDiagnosticChainCommand(research_run_id="run-1"))
        self.assertEqual(len(store.chain_hypotheses), 0)


if __name__ == "__main__":
    unittest.main()
