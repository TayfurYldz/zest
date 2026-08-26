from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from zest.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
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
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from zest.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLAIM,
    CandidateAdmissionOutcome,
    CandidateProposal,
    CandidateState,
)
from zest.research.planning import plan_diagnostic_echo
from zest.research.verification import VerificationOutcome
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    completed_diagnostic_outcome,
    invocation_outcome,
)
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


def _plan(message: str = "alpha"):
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


def _run_experiment(store: _Store, experiment_id: str, message: str, handler=None) -> None:
    factory = FakeUnitOfWorkFactory(store)
    if experiment_id not in store.experiments:
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=_plan(message),
            )
        )
    worker = RecordingWorkerPort(store=store, handler=handler)
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


def _original_evidence(store: _Store) -> str:
    _run_experiment(store, "exp-1", "alpha")
    admitted = AdmitDiagnosticEvidence(
        FakeUnitOfWorkFactory(store), clock=FixedClock()
    ).execute(AdmitDiagnosticEvidenceCommand(experiment_id="exp-1"))
    assert admitted.evidence_id is not None
    return admitted.evidence_id


class CandidateLifecycleApplicationTests(unittest.TestCase):
    def test_evidence_seeds_open_candidate_not_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        self.assertEqual(len(store.candidates), 0)
        result = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        self.assertEqual(result.outcome, CandidateAdmissionOutcome.ADMITTED)
        self.assertEqual(result.state, CandidateState.OPEN)
        candidate = next(iter(store.candidates.values()))
        self.assertEqual(candidate.claim, DIAGNOSTIC_CANDIDATE_CLAIM)
        self.assertEqual(candidate.state, "OPEN")
        self.assertFalse(hasattr(candidate, "severity"))
        self.assertFalse(hasattr(candidate, "cvss"))
        self.assertEqual(len(store.verifications), 0)

    def test_evidence_does_not_auto_create_candidate(self) -> None:
        store = _Store()
        seed_spine(store)
        _original_evidence(store)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.candidate_admissions), 0)

    def test_rejected_proposal_keeps_history_without_candidate(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        proposal = CandidateProposal(
            proposal_id="prop-ghost",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            evidence_ids=("ev-missing",),
            claim=DIAGNOSTIC_CANDIDATE_CLAIM,
            classification="DIAGNOSTIC_PLUMBING",
            rationale={"reason_code": "test", "not_a_vulnerability": True},
            provenance={"source": "test"},
        )
        result = ProposeCandidateFromEvidence(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id, proposal=proposal)
        )
        self.assertEqual(
            result.outcome, CandidateAdmissionOutcome.REJECTED_BROKEN_PROVENANCE
        )
        self.assertIsNone(result.candidate_id)
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.candidate_admissions), 1)

    def test_independent_reproduction_validates_without_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        started = StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        self.assertEqual(started.state, CandidateState.VERIFYING)
        _run_experiment(store, "exp-repro", "beta")
        AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-repro")
        )
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.VALIDATED)
        self.assertEqual(completed.state, CandidateState.VALIDATED)
        self.assertEqual(store.candidates[proposed.candidate_id].state, "VALIDATED")
        self.assertEqual(len(store.verifications), 1)
        verification = next(iter(store.verifications.values()))
        self.assertNotEqual(
            verification.original_evidence_ids, verification.reproduction_evidence_ids
        )
        self.assertFalse(hasattr(verification, "severity"))

    def test_mismatch_reproduction_rejects_candidate(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        _run_experiment(store, "exp-repro", "beta", handler=_mismatched)
        AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-repro")
        )
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.REJECTED)
        self.assertEqual(completed.state, CandidateState.REJECTED)

    def test_timeout_is_inconclusive_not_rejected(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-timeout",
                research_run_id="run-1",
                plan=_plan("beta"),
            )
        )
        worker = RecordingWorkerPort(
            store=store, outcome=invocation_outcome(InvocationStatus.TIMED_OUT)
        )
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-timeout",
                plan=_plan("beta"),
                scope=_allow_scope(),
            )
        )
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id="exp-timeout",
                execution_outcome="INVOCATION_FAILED",
                invocation_status=InvocationStatus.TIMED_OUT.value,
            )
        )
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-timeout",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertEqual(completed.state, CandidateState.INCONCLUSIVE)
        self.assertNotEqual(completed.outcome, VerificationOutcome.REJECTED)

    def test_cannot_complete_validation_from_open(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        _run_experiment(store, "exp-repro", "beta")
        AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-repro")
        )
        with self.assertRaises(ApplicationError):
            CompleteCandidateVerification(factory, clock=FixedClock()).execute(
                CompleteCandidateVerificationCommand(
                    candidate_id=proposed.candidate_id,
                    reproduction_experiment_id="exp-repro",
                )
            )
        self.assertEqual(store.candidates[proposed.candidate_id].state, "OPEN")
        self.assertEqual(len(store.verifications), 0)

    def test_rollback_leaves_no_partial_candidate(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store, fail_on="candidate_admissions")
        with self.assertRaises(Exception):
            ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
                ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
            )
        self.assertEqual(len(store.candidates), 0)
        self.assertEqual(len(store.candidate_admissions), 0)

    def test_same_original_experiment_cannot_self_validate(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-1",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertEqual(completed.state, CandidateState.INCONCLUSIVE)

    def test_duplicate_propose_and_start_are_idempotent(self) -> None:
        store = _Store()
        seed_spine(store)
        evidence_id = _original_evidence(store)
        factory = FakeUnitOfWorkFactory(store)
        first = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        second = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(len(store.candidates), 1)
        assert first.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=first.candidate_id)
        )
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=first.candidate_id)
        )
        self.assertEqual(store.candidates[first.candidate_id].state, "VERIFYING")


if __name__ == "__main__":
    unittest.main()
