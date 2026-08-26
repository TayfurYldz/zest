from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.records import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExperimentExecutionState,
    WorkerResultRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.planning import plan_diagnostic_echo
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    completed_diagnostic_outcome,
    invocation_outcome,
)
from support.spine import CREATED_AT, DIAGNOSTIC_CLAIM, seed_spine


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


def _plan(message: str = "ping"):
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )


def _mismatched(request):
    outcome = completed_diagnostic_outcome(request)
    result = dict(outcome.worker_result)
    result["raw_result"] = {"echoed": "nope", "capability": "diagnostic.echo"}
    return WorkerInvocationOutcome(
        invocation_status=outcome.invocation_status,
        started_at=outcome.started_at,
        completed_at=outcome.completed_at,
        worker_result=result,
        exit_code=outcome.exit_code,
        stderr_diagnostics=outcome.stderr_diagnostics,
        stderr_truncated=outcome.stderr_truncated,
        reason=outcome.reason,
    )


class EvaluateExperimentFeedbackTests(unittest.TestCase):
    def test_matching_echo_is_consistent_and_does_not_mutate_hypothesis(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store)
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        self.assertEqual(
            feedback.assessment_outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION
        )
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertFalse(hasattr(feedback, "severity"))
        self.assertFalse(hasattr(feedback, "confidence"))
        self.assertEqual(len(store.hypothesis_assessments), 1)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 0)

    def test_mismatch_contradicts_prediction_without_global_reject(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store, handler=_mismatched)
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        self.assertEqual(
            feedback.assessment_outcome, AssessmentOutcome.CONTRADICTS_PREDICTION
        )
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertEqual(len(store.hypotheses), 1)
        self.assertNotEqual(feedback.assessment_outcome.value, "REJECTED")

    def test_timeout_is_execution_unusable_and_not_negative_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT),
        )
        loop = ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        self.assertEqual(loop.status, ResearchLoopStatus.INVOCATION_FAILED)
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id="exp-1",
                execution_outcome=loop.status.value,
                invocation_status=loop.invocation_status.value,
            )
        )
        self.assertEqual(feedback.assessment_outcome, AssessmentOutcome.EXECUTION_UNUSABLE)
        self.assertFalse(feedback.execution_usable)
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertEqual(len(store.hypothesis_assessments), 1)

    def test_insufficient_observation_is_inconclusive(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-2",
                research_run_id="run-1",
                plan=plan_diagnostic_echo(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    message="ping",
                ),
            )
        )
        with factory.open() as uow:
            uow.experiments.set_execution_state(
                "exp-2", ExperimentExecutionState.EXECUTION_SUCCEEDED.value
            )
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="ea:exp-2",
                    request_id="req-exp-2",
                    experiment_id="exp-2",
                    research_run_id="run-1",
                    correlation_id="corr-exp-2",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    target_reference="target-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference="audit-exp-2",
                    state=ExecutionAttemptState.COMPLETED.value,
                    created_at=CREATED_AT,
                    authorized_at=CREATED_AT,
                    completed_at=CREATED_AT,
                )
            )
            uow.worker_results.insert(
                WorkerResultRecord(
                    worker_result_id="wr-exp-2",
                    experiment_id="exp-2",
                    research_run_id="run-1",
                    request_id="req-exp-2",
                    correlation_id="corr-exp-2",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    authorization_decision_reference="audit-exp-2",
                    budget_id="budget-1",
                    side_effect_level=0,
                    contract_version="v1",
                    worker_id="local-python-diagnostic",
                    status="SUCCEEDED",
                    received_at=CREATED_AT,
                )
            )
            uow.commit()
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-2")
        )
        self.assertEqual(feedback.assessment_outcome, AssessmentOutcome.INCONCLUSIVE)
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertEqual(len(store.hypothesis_assessments), 1)

    def test_does_not_create_evidence_or_loop(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        ExecutePlannedExperiment(
            factory, RecordingWorkerPort(store=store), clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        first = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        second = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        self.assertEqual(first.assessment_outcome, second.assessment_outcome)
        self.assertEqual(first.assessment_id, second.assessment_id)
        self.assertEqual(len(store.hypothesis_assessments), 1)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)


if __name__ == "__main__":
    unittest.main()
