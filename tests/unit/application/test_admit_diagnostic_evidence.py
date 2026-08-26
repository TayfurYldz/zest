from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from zest.application.errors import ApplicationError
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.platform.worker import InvocationStatus
from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    EvidenceAdmissionOutcome,
    EvidencePolarity,
    EvidenceProposal,
)
from zest.research.planning import plan_diagnostic_echo
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, invocation_outcome
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


def _plan(message: str = "ping"):
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )


def _run_success(store: _Store) -> None:
    factory = FakeUnitOfWorkFactory(store)
    worker = RecordingWorkerPort(store=store)
    ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
        ExecutePlannedExperimentCommand(
            experiment_id="exp-1",
            plan=_plan(),
            scope=_allow_scope(),
        )
    )
    EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
        EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
    )


class AdmitDiagnosticEvidenceTests(unittest.TestCase):
    def test_valid_diagnostic_creates_evidence_and_not_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_success(store)
        factory = FakeUnitOfWorkFactory(store)
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-1")
        )
        self.assertEqual(result.outcome, EvidenceAdmissionOutcome.ADMITTED)
        self.assertIsNotNone(result.evidence_id)
        self.assertEqual(len(store.evidence), 1)
        self.assertEqual(len(store.evidence_admissions), 1)
        evidence = next(iter(store.evidence.values()))
        self.assertEqual(evidence.claim_scope, DIAGNOSTIC_ECHO_MATCHED_CLAIM)
        self.assertFalse(hasattr(evidence, "severity"))
        self.assertFalse(hasattr(evidence, "confidence"))
        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(len(store.candidates), 0)

    def test_assessment_alone_does_not_create_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_success(store)
        self.assertEqual(len(store.evidence), 0)

    def test_rejected_proposal_keeps_admission_history(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_success(store)
        factory = FakeUnitOfWorkFactory(store)
        observation_id = next(iter(store.observations))
        assessment_id = next(iter(store.hypothesis_assessments))
        proposal = EvidenceProposal(
            proposal_id="prop-wrong-run",
            research_run_id="run-other",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            observation_ids=(observation_id,),
            assessment_ids=(assessment_id,),
            polarity=EvidencePolarity.SUPPORTING,
            claim_scope=DIAGNOSTIC_ECHO_MATCHED_CLAIM,
            rationale={"reason_code": "ECHO_MATCHED", "not_vulnerability_evidence": True},
            provenance={"source": "test"},
        )
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-1", proposal=proposal)
        )
        self.assertEqual(result.outcome, EvidenceAdmissionOutcome.REJECTED_BROKEN_PROVENANCE)
        self.assertIsNone(result.evidence_id)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 1)

    def test_timeout_creates_no_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        worker = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT),
        )
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id="exp-1",
                execution_outcome="INVOCATION_FAILED",
                invocation_status=InvocationStatus.TIMED_OUT.value,
            )
        )
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-1")
        )
        self.assertEqual(result.outcome, EvidenceAdmissionOutcome.REJECTED_EXECUTION_UNUSABLE)
        self.assertIsNone(result.evidence_id)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 1)

    def test_rollback_leaves_no_partial_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_success(store)
        factory = FakeUnitOfWorkFactory(store, fail_on="evidence_admissions")
        with self.assertRaises(Exception):
            AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
                AdmitDiagnosticEvidenceCommand(experiment_id="exp-1")
            )
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(store.evidence_admissions), 0)

    def test_second_admit_of_the_same_assessment_is_idempotent(self) -> None:
        store = _Store()
        seed_spine(store)
        _run_success(store)
        factory = FakeUnitOfWorkFactory(store)
        use_case = AdmitDiagnosticEvidence(factory, clock=FixedClock())
        first = use_case.execute(AdmitDiagnosticEvidenceCommand(experiment_id="exp-1"))
        second = use_case.execute(AdmitDiagnosticEvidenceCommand(experiment_id="exp-1"))
        self.assertEqual(first.outcome, EvidenceAdmissionOutcome.ADMITTED)
        self.assertEqual(second.admission_record_id, first.admission_record_id)
        self.assertEqual(second.evidence_id, first.evidence_id)
        self.assertEqual(len(store.evidence), 1)
        self.assertEqual(len(store.evidence_admissions), 1)

    def test_missing_experiment_is_application_error(self) -> None:
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        with self.assertRaises(ApplicationError):
            AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
                AdmitDiagnosticEvidenceCommand(experiment_id="missing")
            )


if __name__ == "__main__":
    unittest.main()
