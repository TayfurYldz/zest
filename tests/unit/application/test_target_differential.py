from __future__ import annotations

import unittest
from dataclasses import replace

import pathsetup  # noqa: F401

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from zest.application.admit_target_inference import (
    AdmitTargetInference,
    AdmitTargetInferenceCommand,
)
from zest.application.compare_diagnostic_differential import (
    CompareDiagnosticDifferential,
    CompareDiagnosticDifferentialCommand,
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
from zest.application.project_diagnostic_target_model import (
    ProjectDiagnosticTargetModel,
    ProjectDiagnosticTargetModelCommand,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceError
from zest.research.admission import AdmissionOutcome
from zest.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialInterpretation,
    DifferentialOutcome,
)
from zest.research.epistemic import EpistemicClass
from zest.research.planning import plan_diagnostic_echo
from zest.research.target_model import (
    TargetElementKind,
    TargetEpistemicStatus,
    TargetInferenceDraft,
    TargetInferenceOutcome,
)
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_spine

class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _plan(message: str):
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )


def _seed(store: _Store) -> None:
    seed_spine(store)
    store.issued_budgets["budget-1"] = replace(
        store.issued_budgets["budget-1"],
        max_requests=8,
        max_tool_calls=8,
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
            experiment_id=experiment_id,
            plan=_plan(message),
            scope=_allow_scope(),
        )
    )
    EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
        EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
    )


def _observation_ids(store: _Store) -> tuple[str, ...]:
    return tuple(sorted(store.observations))


class TargetDifferentialApplicationTests(unittest.TestCase):
    def test_projection_and_controlled_diff_do_not_create_evidence(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        factory = FakeUnitOfWorkFactory(store)
        projection = ProjectDiagnosticTargetModel(factory).execute(
            ProjectDiagnosticTargetModelCommand(research_run_id="run-1")
        )
        observed = projection.elements_with(TargetEpistemicStatus.OBSERVED)
        derived = projection.elements_with(TargetEpistemicStatus.DERIVED)
        self.assertTrue(observed)
        self.assertTrue(derived)
        self.assertFalse(projection.elements_with(TargetEpistemicStatus.INFERRED))
        obs_a, obs_b = _observation_ids(store)
        compared = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-1",
                    research_run_id="run-1",
                    baseline_observation_ids=(obs_a,),
                    variant_observation_ids=(obs_b,),
                    changed_dimensions=(DifferentialDimension.INPUT,),
                    common_dimensions=(
                        DifferentialDimension.ACTOR,
                        DifferentialDimension.ACTION,
                        DifferentialDimension.RESOURCE,
                    ),
                )
            )
        )
        self.assertEqual(compared.outcome, DifferentialOutcome.COMPARED)
        assert compared.observation is not None
        self.assertEqual(
            compared.observation.interpretation,
            DifferentialInterpretation.CONTROLLED_DIFFERENCE,
        )
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.findings), 0)
        AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-2")
        )
        self.assertEqual(len(store.candidates), 0)

    def test_inference_reload_stays_inferred(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        obs_id = next(iter(store.observations))
        factory = FakeUnitOfWorkFactory(store)
        admitted = AdmitTargetInference(factory, clock=FixedClock()).execute(
            AdmitTargetInferenceCommand(
                draft=TargetInferenceDraft(
                    inference_id="inf-1",
                    research_run_id="run-1",
                    kind=TargetElementKind.RELATIONSHIP,
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    opaque_ref="maybe-related",
                    statement="Actor handle may be related to the diagnostic resource.",
                    source_refs=(obs_id,),
                    attributes={"not_ownership": True},
                )
            )
        )
        self.assertEqual(admitted.outcome, TargetInferenceOutcome.ADMITTED)
        projection = ProjectDiagnosticTargetModel(factory).execute(
            ProjectDiagnosticTargetModelCommand(research_run_id="run-1")
        )
        inferred = projection.elements_with(TargetEpistemicStatus.INFERRED)
        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0].epistemic_status, TargetEpistemicStatus.INFERRED)

    def test_hallucinated_inference_source_is_rejected(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        decision = AdmitTargetInference(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            AdmitTargetInferenceCommand(
                draft=TargetInferenceDraft(
                    inference_id="inf-ghost",
                    research_run_id="run-1",
                    kind=TargetElementKind.RELATIONSHIP,
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    opaque_ref="ghost",
                    statement="A related diagnostic handle may exist.",
                    source_refs=("obs-missing",),
                    attributes={},
                )
            )
        )
        self.assertEqual(decision.outcome, TargetInferenceOutcome.REJECTED_HALLUCINATED_SOURCE)
        self.assertEqual(len(store.target_inferences), 0)

    def test_differential_can_enter_hypothesis_cycle(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        factory = FakeUnitOfWorkFactory(store)
        obs_a, obs_b = _observation_ids(store)
        compared = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-1",
                    research_run_id="run-1",
                    baseline_observation_ids=(obs_a,),
                    variant_observation_ids=(obs_b,),
                    changed_dimensions=(DifferentialDimension.INPUT,),
                    common_dimensions=(
                        DifferentialDimension.ACTOR,
                        DifferentialDimension.ACTION,
                    ),
                )
            )
        )
        assert compared.observation is not None
        result = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="Does diagnostic echo differ by input?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-diff-1",
                differential_id=compared.observation.differential_id,
            )
        )
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        item = result.context.item_by_id(compared.observation.differential_id)
        assert item is not None
        self.assertEqual(item.epistemic_class, EpistemicClass.DERIVED_FACT)
        self.assertTrue(item.payload["not_evidence"])
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)

    def test_transaction_failure_leaves_no_partial_differential(self) -> None:
        store = _Store()
        _seed(store)
        _run_experiment(store, "exp-2", "alpha")
        _run_experiment(store, "exp-3", "beta")
        obs_a, obs_b = _observation_ids(store)
        with self.assertRaises(PersistenceError):
            CompareDiagnosticDifferential(
                FakeUnitOfWorkFactory(store, fail_on="differential_observations"),
                clock=FixedClock(),
            ).execute(
                CompareDiagnosticDifferentialCommand(
                    case=DifferentialCase(
                        case_id="case-1",
                        research_run_id="run-1",
                        baseline_observation_ids=(obs_a,),
                        variant_observation_ids=(obs_b,),
                        changed_dimensions=(DifferentialDimension.INPUT,),
                        common_dimensions=(DifferentialDimension.ACTION,),
                    )
                )
            )
        self.assertEqual(len(store.differential_observations), 0)


if __name__ == "__main__":
    unittest.main()
