"""Slice 5 / lock MR-4: one Evidence-admission attempt per CONSISTENT assessment."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.application.promotion_pipeline import (
    AdvancePromotionCommand,
    AdvancePromotionPipeline,
    PromotionOutcome,
    PromotionPipeline,
    PromoteOnAssessment,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.records import IssuedBudgetRecord
from research_os.research.assessment import AssessmentOutcome, ResearchFeedback
from research_os.research.orchestration import OrchestrationBounds, OrchestrationState, StopReason
from research_os.research.planning import plan_diagnostic_echo
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    completed_diagnostic_outcome,
    invocation_outcome,
)
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from support.spine import CREATED_AT, seed_authorization_run, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _plan(message: str = "ping"):
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )


def _feedback(**overrides) -> ResearchFeedback:
    values = dict(
        hypothesis_id="hyp-1",
        experiment_id="exp-1",
        assessment_id="assess-1",
        assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
        observation_ids=("obs-1",),
        execution_usable=True,
        evaluation_strategy="diagnostic.echo.v1",
        research_run_id="run-1",
    )
    values.update(overrides)
    return ResearchFeedback(**values)


def _run_echo(store: _Store, *, handler=None) -> ResearchFeedback:
    factory = FakeUnitOfWorkFactory(store)
    worker = RecordingWorkerPort(store=store, handler=handler)
    ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
        ExecutePlannedExperimentCommand(
            experiment_id="exp-1",
            plan=_plan(),
            scope=_allow_scope(),
        )
    )
    return EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
        EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
    )


class PromotionPipelineTests(unittest.TestCase):
    def test_consistent_assessment_admits_evidence_exactly_once(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        self.assertEqual(feedback.assessment_outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        self.assertEqual(len(store.evidence), 0)
        factory = FakeUnitOfWorkFactory(store)
        pipeline = PromotionPipeline(factory, clock=FixedClock())
        first = pipeline.on_assessment(feedback)
        second = pipeline.on_assessment(feedback)
        self.assertEqual(first.outcome, PromotionOutcome.VERIFICATION_STARTED)
        self.assertIsNotNone(first.evidence_id)
        self.assertIsNotNone(first.candidate_id)
        self.assertEqual(second.outcome, PromotionOutcome.SKIPPED_ALREADY_ATTEMPTED)
        self.assertEqual(second.evidence_id, first.evidence_id)
        self.assertEqual(second.candidate_id, first.candidate_id)
        self.assertEqual(len(store.evidence), 1)
        self.assertEqual(len(store.evidence_admissions), 1)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.verifications), 0)
        self.assertEqual(len(store.finding_proposals), 0)
        self.assertEqual(len(store.findings), 0)

    def test_inconclusive_does_not_admit(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        feedback = _feedback(
            assessment_outcome=AssessmentOutcome.INCONCLUSIVE,
            execution_usable=True,
        )
        result = PromotionPipeline(factory, clock=FixedClock()).on_assessment(feedback)
        self.assertEqual(result.outcome, PromotionOutcome.SKIPPED_NOT_EVIDENCE_ELIGIBLE)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 0)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.promotion_runs), 0)

    def test_contradicts_does_not_auto_admit(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        feedback = _feedback(assessment_outcome=AssessmentOutcome.CONTRADICTS_PREDICTION)
        result = PromotionPipeline(factory, clock=FixedClock()).on_assessment(feedback)
        self.assertEqual(result.outcome, PromotionOutcome.SKIPPED_NOT_EVIDENCE_ELIGIBLE)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 0)
        self.assertEqual(len(store.candidates), 0)

    def test_execution_unusable_does_not_admit(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        feedback = _feedback(
            assessment_outcome=AssessmentOutcome.EXECUTION_UNUSABLE,
            execution_usable=False,
        )
        result = PromotionPipeline(factory, clock=FixedClock()).on_assessment(feedback)
        self.assertEqual(result.outcome, PromotionOutcome.SKIPPED_NOT_EVIDENCE_ELIGIBLE)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)

    def test_unsupported_strategy_does_not_admit(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        feedback = _feedback(evaluation_strategy="http.transaction.v1")
        result = PromotionPipeline(factory, clock=FixedClock()).on_assessment(feedback)
        self.assertEqual(result.outcome, PromotionOutcome.SKIPPED_UNSUPPORTED_STRATEGY)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.candidates), 0)

    def test_evaluate_use_case_still_does_not_admit_on_its_own(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_echo(store)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 0)


class ArcPromotionHookTests(unittest.TestCase):
    def test_arc_reaches_finding_proposal_and_never_finding(self) -> None:
        store = _Store()
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
        factory = FakeUnitOfWorkFactory(store)
        port = RecordingWorkerPort(store=store)
        controller = AutonomousResearchController(
            factory, port, ScriptedModelPort(), clock=FixedClock()
        )
        command = StartAutonomousResearchCommand(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            scope=_allow_scope(),
            bounds=OrchestrationBounds(
                max_cycles=1,
                max_experiments=2,
                max_model_calls=20,
                max_worker_invocations=4,
                max_elapsed_ms=60_000,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
                allow_repeated_control_experiments=True,
            ),
        )
        result = controller.run_bounded(command)
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, StopReason.MAX_CYCLES_REACHED.value)
        self.assertEqual(len(store.hypothesis_assessments), 2)
        promotion = next(iter(store.promotion_runs.values()))
        original = next(
            item
            for item in store.hypothesis_assessments.values()
            if item.experiment_id == promotion.original_experiment_id
        )
        self.assertEqual(original.assessment_outcome, "CONSISTENT_WITH_PREDICTION")
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.promotion_runs), 1)
        evidence = store.evidence[next(iter(store.candidates.values())).evidence_ids[0]]
        self.assertEqual(evidence.assessment_ids, (original.assessment_id,))
        AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=original.experiment_id)
        )
        supporting_original = [
            item
            for item in store.evidence.values()
            if item.experiment_id == original.experiment_id and item.polarity == "SUPPORTING"
        ]
        self.assertEqual(len(supporting_original), 1)

    def test_pending_promotion_resumes_after_terminal_research_run(self) -> None:
        store = _Store()
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
        factory = FakeUnitOfWorkFactory(store)
        port = RecordingWorkerPort(store=store)
        controller = AutonomousResearchController(
            factory, port, ScriptedModelPort(), clock=FixedClock()
        )
        command = StartAutonomousResearchCommand(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            scope=_allow_scope(),
            bounds=OrchestrationBounds(
                max_cycles=1,
                max_experiments=2,
                max_model_calls=20,
                max_worker_invocations=4,
                max_elapsed_ms=60_000,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
                allow_repeated_control_experiments=True,
            ),
        )
        result = controller.run_bounded(command)
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.findings), 0)
        worker_calls_after_run = len(port.calls)
        advanced = AdvancePromotionPipeline(
            factory, port, clock=FixedClock()
        ).execute(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        self.assertEqual(advanced, ())
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(port.calls), worker_calls_after_run)
        self.assertEqual(store.research_orchestrations["run-1"].state, OrchestrationState.COMPLETED.value)


class PromoteOnAssessmentWrapperTests(unittest.TestCase):
    def test_wrapper_creates_candidate_not_finding(self) -> None:
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
        evaluate = EvaluateExperimentFeedback(factory, clock=FixedClock())
        wrapped = PromoteOnAssessment(evaluate, PromotionPipeline(factory, clock=FixedClock()))
        wrapped.execute(EvaluateExperimentFeedbackCommand(experiment_id="exp-1"))
        wrapped.execute(EvaluateExperimentFeedbackCommand(experiment_id="exp-1"))
        self.assertEqual(len(store.evidence), 1)
        self.assertEqual(len(store.evidence_admissions), 1)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.verifications), 0)
        self.assertEqual(len(store.findings), 0)


class CanonicalPromotionPipelineTests(unittest.TestCase):
    def test_supported_assessment_reaches_finding_proposal_not_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store)
        pipeline = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        started = pipeline.on_assessment(feedback)
        self.assertEqual(started.outcome, PromotionOutcome.VERIFICATION_STARTED)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(next(iter(store.candidates.values())).state, "VERIFYING")
        advanced = pipeline.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(len(advanced), 1)
        self.assertEqual(advanced[0].outcome, PromotionOutcome.FINDING_PROPOSAL_RECORDED)
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.human_reviews), 0)
        self.assertEqual(len(store.approvals), 0)
        candidate = next(iter(store.candidates.values()))
        self.assertEqual(candidate.state, "VALIDATED")
        reproduction_ids = {
            item.reproduction_experiment_id for item in store.promotion_runs.values()
        }
        self.assertTrue(reproduction_ids)
        self.assertNotIn(feedback.experiment_id, reproduction_ids)
        original_evidence = next(iter(store.evidence.values()))
        self.assertNotEqual(
            next(iter(reproduction_ids)), original_evidence.experiment_id
        )

    def test_duplicate_advance_does_not_duplicate_authoritative_records(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store)
        pipeline = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        pipeline.on_assessment(feedback)
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        self.assertEqual(len(store.evidence), 2)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.promotion_runs), 1)
        self.assertEqual(len(store.findings), 0)

    def test_reload_between_stages_does_not_duplicate(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)
        first = PromotionPipeline(factory, clock=FixedClock())
        first.on_assessment(feedback)
        second = PromotionPipeline(factory, clock=FixedClock())
        reloaded = second.on_assessment(feedback)
        self.assertEqual(reloaded.outcome, PromotionOutcome.SKIPPED_ALREADY_ATTEMPTED)
        self.assertEqual(len(store.candidates), 1)
        worker = RecordingWorkerPort(store=store)
        third = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        third.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        fourth = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        fourth.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.findings), 0)

    def test_inconclusive_verification_does_not_create_finding_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)

        def _mismatch(request):
            outcome = completed_diagnostic_outcome(request)
            payload = dict(outcome.worker_result)
            raw = dict(payload["raw_result"])
            raw["echoed"] = "not-the-submitted-value"
            payload["raw_result"] = raw
            return WorkerInvocationOutcome(
                invocation_status=outcome.invocation_status,
                started_at=outcome.started_at,
                completed_at=outcome.completed_at,
                worker_result=payload,
                exit_code=outcome.exit_code,
            )

        pipeline = PromotionPipeline(
            factory, clock=FixedClock(), worker=RecordingWorkerPort(store=store, handler=_mismatch)
        )
        pipeline.on_assessment(feedback)
        advanced = pipeline.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(len(advanced), 1)
        candidate = next(iter(store.candidates.values()))
        self.assertNotEqual(candidate.state, "VALIDATED")
        self.assertEqual(len(store.finding_proposals), 0)
        self.assertEqual(len(store.findings), 0)

    def test_operational_failure_does_not_validate_or_retry(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)
        failing = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.PROCESS_FAILED),
        )
        pipeline = PromotionPipeline(factory, clock=FixedClock(), worker=failing)
        pipeline.on_assessment(feedback)
        first = pipeline.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].stage, "STOPPED")
        self.assertEqual(first[0].reason_codes, ("INVOCATION_FAILED",))
        candidate = next(iter(store.candidates.values()))
        self.assertNotEqual(candidate.state, "VALIDATED")
        self.assertEqual(len(store.verifications), 0)
        self.assertEqual(len(store.finding_proposals), 0)
        calls_after_stop = len(failing.calls)
        second = pipeline.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(second, ())
        self.assertEqual(len(failing.calls), calls_after_stop)
        self.assertEqual(len(store.verifications), 0)
        self.assertEqual(len(store.findings), 0)

    def test_crash_reload_after_reproduction_executed_does_not_duplicate(self) -> None:
        store = _Store()
        seed_spine(store)
        feedback = _run_echo(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(store=store)
        first = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        first.on_assessment(feedback)
        first.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        run = next(iter(store.promotion_runs.values()))
        self.assertEqual(run.stage, "PROPOSAL_RECORDED")
        from dataclasses import replace

        store.promotion_runs[run.promotion_run_id] = replace(
            run,
            stage="REPRODUCTION_EXECUTED",
            verification_id=None,
            finding_proposal_id=None,
        )
        store.verifications.clear()
        store.finding_proposals.clear()
        candidate_id = next(iter(store.candidates))
        store.candidates[candidate_id] = replace(
            store.candidates[candidate_id],
            state="VERIFYING",
        )
        reloaded = PromotionPipeline(factory, clock=FixedClock(), worker=worker)
        reloaded.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        self.assertEqual(len(store.verifications), 1)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(len(store.promotion_runs), 1)
        self.assertEqual(len(store.findings), 0)


if __name__ == "__main__":
    unittest.main()
