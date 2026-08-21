from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from research_os.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
)
from research_os.application.errors import ApplicationError
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from research_os.application.record_human_review import (
    RecordHumanReview,
    RecordHumanReviewCommand,
)
from research_os.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from research_os.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from research_os.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from research_os.core.approval import ApprovalView, evaluate_recorded_approval
from research_os.core.enums import ActorType, ApprovalDecision, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.errors import PersistenceError
from research_os.data.records import AuditEventRecord, CandidateRecord, VerificationRecord
from research_os.platform.worker import InvocationStatus
from research_os.research.candidate import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    CandidateState,
)
from research_os.research.finding_proposal import (
    DIAGNOSTIC_FINDING_PROPOSAL_TITLE,
    FindingCreationOutcome,
    FindingProposalAdmissionOutcome,
    FindingProposalState,
    HumanReviewDecision,
    approval_subject_for,
)
from research_os.research.planning import plan_diagnostic_echo
from research_os.research.validation.tier_gate import ValidationTier, ValidationTierOutcome
from research_os.research.verification import VerificationOutcome
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.spine import CREATED_AT, seed_spine

TEST_HUMAN = "operator-test-1"


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


def _original_evidence(store: _Store) -> str:
    _run_experiment(store, "exp-1", "alpha")
    admitted = AdmitDiagnosticEvidence(
        FakeUnitOfWorkFactory(store), clock=FixedClock()
    ).execute(AdmitDiagnosticEvidenceCommand(experiment_id="exp-1"))
    assert admitted.evidence_id is not None
    return admitted.evidence_id


def _open_candidate(store: _Store) -> str:
    evidence_id = _original_evidence(store)
    proposed = ProposeCandidateFromEvidence(
        FakeUnitOfWorkFactory(store), clock=FixedClock()
    ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
    assert proposed.candidate_id is not None
    return proposed.candidate_id


def _validated_candidate(store: _Store) -> str:
    candidate_id = _open_candidate(store)
    factory = FakeUnitOfWorkFactory(store)
    StartCandidateVerification(factory).execute(
        StartCandidateVerificationCommand(candidate_id=candidate_id)
    )
    _run_experiment(store, "exp-repro", "beta")
    AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
        AdmitDiagnosticEvidenceCommand(experiment_id="exp-repro")
    )
    completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
        CompleteCandidateVerificationCommand(
            candidate_id=candidate_id,
            reproduction_experiment_id="exp-repro",
        )
    )
    assert completed.outcome is VerificationOutcome.VALIDATED
    return candidate_id


def _second_validated_candidate(store: _Store) -> str:
    candidate_id = "candidate-diag-2"
    store.candidates[candidate_id] = CandidateRecord(
        candidate_id=candidate_id,
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        claim="diagnostic runtime returns the provided echo value",
        classification="DIAGNOSTIC_PLUMBING",
        state=CandidateState.VALIDATED.value,
        evidence_ids=("evidence-diag-2",),
        admission_record_id="candidate-admission-diag-2",
        created_at=CREATED_AT,
    )
    store.verifications["verification-diag-2"] = VerificationRecord(
        verification_id="verification-diag-2",
        candidate_id=candidate_id,
        research_run_id="run-1",
        strategy="diagnostic.echo.v1",
        outcome=VerificationOutcome.VALIDATED.value,
        proposed_candidate_state=CandidateState.VALIDATED.value,
        original_evidence_ids=("evidence-diag-2",),
        reproduction_evidence_ids=("evidence-diag-2-repro",),
        negative_control_evidence_ids=(),
        alternative_explanation_checks={},
        verifier_kind="DETERMINISTIC",
        verifier_identity="validator-diagnostic-v1",
        created_at=CREATED_AT,
    )
    return candidate_id


def _inconclusive_candidate(store: _Store) -> str:
    candidate_id = _open_candidate(store)
    factory = FakeUnitOfWorkFactory(store)
    StartCandidateVerification(factory).execute(
        StartCandidateVerificationCommand(candidate_id=candidate_id)
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
            candidate_id=candidate_id,
            reproduction_experiment_id="exp-timeout",
        )
    )
    assert completed.state is CandidateState.INCONCLUSIVE
    return candidate_id


def _validated_security_candidate(store: _Store) -> str:
    candidate_id = "candidate-authz-1"
    store.candidates[candidate_id] = CandidateRecord(
        candidate_id=candidate_id,
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
        classification=HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
        state=CandidateState.VALIDATED.value,
        evidence_ids=("evidence-authz-1",),
        admission_record_id="candidate-admission-authz-1",
        created_at=CREATED_AT,
    )
    store.verifications["verification-authz-1"] = VerificationRecord(
        verification_id="verification-authz-1",
        candidate_id=candidate_id,
        research_run_id="run-1",
        strategy="http.authorization.differential.v1",
        outcome=VerificationOutcome.VALIDATED.value,
        proposed_candidate_state=CandidateState.VALIDATED.value,
        original_evidence_ids=("evidence-authz-1",),
        reproduction_evidence_ids=("evidence-authz-2",),
        negative_control_evidence_ids=("evidence-negative-1",),
        alternative_explanation_checks={"ownership_control": "passed"},
        verifier_kind="DETERMINISTIC",
        verifier_identity="validator-authz-v1",
        created_at=CREATED_AT,
    )
    return candidate_id


def _record_tier_event(
    store: _Store,
    tier: ValidationTier,
    outcome: ValidationTierOutcome,
    *,
    suffix: str,
) -> None:
    store.audit_events[f"audit-tier-{tier.value.lower()}-{suffix}"] = AuditEventRecord(
        audit_event_id=f"audit-tier-{tier.value.lower()}-{suffix}",
        occurred_at=CREATED_AT,
        actor_id="control-plane:hunt-validation",
        actor_type=ActorType.CONTROL_PLANE.value,
        event_type="HUNT_TIER_DECISION",
        subject_type="hypothesis",
        subject_id="hyp-1",
        correlation_id="run-1",
        payload={
            "research_run_id": "run-1",
            "family_id": "hf-object-authz",
            "tier": tier.value,
            "outcome": outcome.value,
            "reason_code": f"{tier.value}_{outcome.value}",
            "node_canonical_key": "origin:http://example.test|path:/api/users|method:GET",
        },
    )


def _record_security_validation_pass(store: _Store) -> None:
    _record_tier_event(store, ValidationTier.V1, ValidationTierOutcome.PASSED, suffix="pass")
    _record_tier_event(store, ValidationTier.V2, ValidationTierOutcome.PASSED, suffix="pass")
    _record_tier_event(store, ValidationTier.V3, ValidationTierOutcome.PASSED, suffix="pass")


def _submit_and_review(
    store: _Store,
    candidate_id: str,
    *,
    decision: HumanReviewDecision = HumanReviewDecision.APPROVE,
) -> str:
    factory = FakeUnitOfWorkFactory(store)
    submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
        SubmitFindingProposalCommand(candidate_id=candidate_id)
    )
    assert submitted.proposal_id is not None
    StartHumanReview(factory).execute(
        StartHumanReviewCommand(proposal_id=submitted.proposal_id)
    )
    RecordHumanReview(factory, clock=FixedClock()).execute(
        RecordHumanReviewCommand(
            proposal_id=submitted.proposal_id,
            reviewer_id=TEST_HUMAN,
            actor_type=ActorType.HUMAN_OPERATOR,
            decision=decision,
            note="diagnostic plumbing review",
        )
    )
    return submitted.proposal_id


class FindingAcceptanceApplicationTests(unittest.TestCase):
    def test_validated_candidate_becomes_diagnostic_plumbing_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        factory = FakeUnitOfWorkFactory(store)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        self.assertEqual(submitted.outcome, FindingProposalAdmissionOutcome.ADMITTED)
        self.assertEqual(submitted.state, FindingProposalState.PROPOSED)
        assert submitted.proposal_id is not None
        self.assertEqual(len(store.findings), 0)
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
            )
        )
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.approvals), 0)
        finalized = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(finalized.outcome, FindingCreationOutcome.CREATED)
        self.assertIsNotNone(finalized.finding_id)
        finding = next(iter(store.findings.values()))
        self.assertEqual(finding.title, DIAGNOSTIC_FINDING_PROPOSAL_TITLE)
        self.assertEqual(finding.classification, "DIAGNOSTIC_PLUMBING")
        self.assertNotIn("vulnerability", finding.title.lower())
        self.assertFalse(hasattr(finding, "severity"))
        self.assertFalse(hasattr(finding, "cvss"))
        self.assertEqual(store.candidates[candidate_id].state, "VALIDATED")
        event_types = {event.event_type for event in store.audit_events.values()}
        self.assertIn("FINDING_PROPOSAL_CREATED", event_types)
        self.assertIn("HUMAN_REVIEW_RECORDED", event_types)
        self.assertIn("CORE_APPROVAL_RECORDED", event_types)
        self.assertIn("FINDING_CREATED", event_types)

    def test_open_candidate_cannot_create_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _open_candidate(store)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(
                FakeUnitOfWorkFactory(store), clock=FixedClock()
            ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(len(store.finding_proposals), 0)
        self.assertEqual(len(store.findings), 0)

    def test_inconclusive_candidate_cannot_create_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _inconclusive_candidate(store)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(
                FakeUnitOfWorkFactory(store), clock=FixedClock()
            ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(len(store.finding_proposals), 0)
        self.assertEqual(len(store.findings), 0)

    def test_security_candidate_without_validator_pass_cannot_create_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_security_candidate(store)

        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))

        self.assertEqual(
            submitted.outcome,
            FindingProposalAdmissionOutcome.REJECTED_VALIDATION_NOT_PASSED,
        )
        self.assertIsNone(submitted.proposal_id)
        self.assertEqual(submitted.reason_codes, ("V1_MISSING", "V2_MISSING", "V3_MISSING"))
        self.assertEqual(len(store.finding_proposals), 0)
        events = list(store.audit_events.values())
        self.assertTrue(
            any(event.event_type == "FINDING_PROPOSAL_VALIDATION_REJECTED" for event in events)
        )

    def test_security_candidate_v3_queued_is_not_validator_pass(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_security_candidate(store)
        _record_tier_event(store, ValidationTier.V1, ValidationTierOutcome.PASSED, suffix="pass")
        _record_tier_event(store, ValidationTier.V2, ValidationTierOutcome.PASSED, suffix="pass")
        _record_tier_event(store, ValidationTier.V3, ValidationTierOutcome.QUEUED, suffix="queued")

        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))

        self.assertEqual(
            submitted.outcome,
            FindingProposalAdmissionOutcome.REJECTED_VALIDATION_NOT_PASSED,
        )
        self.assertEqual(submitted.reason_codes, ("V3_QUEUED",))
        self.assertEqual(len(store.finding_proposals), 0)

    def test_security_candidate_with_validator_pass_creates_proposal(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_security_candidate(store)
        _record_security_validation_pass(store)

        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))

        self.assertEqual(submitted.outcome, FindingProposalAdmissionOutcome.ADMITTED)
        self.assertEqual(submitted.state, FindingProposalState.PROPOSED)
        self.assertIsNotNone(submitted.proposal_id)
        self.assertEqual(len(store.finding_proposals), 1)

    def test_human_reject_leaves_validated_candidate_without_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        proposal_id = _submit_and_review(
            store, candidate_id, decision=HumanReviewDecision.REJECT
        )
        result = FinalizeFinding(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            FinalizeFindingCommand(
                proposal_id=proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(result.outcome, FindingCreationOutcome.REJECTED_PROPOSAL)
        self.assertIsNone(result.finding_id)
        self.assertEqual(store.finding_proposals[proposal_id].state, "REJECTED")
        self.assertEqual(store.candidates[candidate_id].state, "VALIDATED")
        self.assertEqual(len(store.findings), 0)

    def test_non_human_actor_cannot_approve(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        proposal_id = _submit_and_review(store, candidate_id)
        with self.assertRaises(ApplicationError):
            FinalizeFinding(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=proposal_id,
                    decided_by="control-plane",
                    actor_type=ActorType.CONTROL_PLANE,
                )
            )
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.approvals), 0)
        self.assertEqual(store.finding_proposals[proposal_id].state, "HUMAN_REVIEW")

    def test_approval_for_wrong_proposal_is_rejected(self) -> None:
        store = _Store()
        seed_spine(store)
        first_id = _validated_candidate(store)
        second_id = _second_validated_candidate(store)
        factory = FakeUnitOfWorkFactory(store)
        first = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=first_id)
        )
        second = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=second_id)
        )
        assert first.proposal_id is not None
        assert second.proposal_id is not None
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=first.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=first.proposal_id,
                reviewer_id=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
            )
        )
        with self.assertRaises(ApplicationError):
            FinalizeFinding(factory, clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=second.proposal_id,
                    decided_by=TEST_HUMAN,
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        self.assertEqual(len(store.findings), 0)

    def test_approval_subject_does_not_transfer_across_proposals(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        first_id = _submit_and_review(store, candidate_id)
        factory = FakeUnitOfWorkFactory(store)
        second = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=_second_validated_candidate(store))
        )
        assert second.proposal_id is not None
        FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=first_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        approval = next(iter(store.approvals.values()))
        second_proposal = store.finding_proposals[second.proposal_id]
        reused = evaluate_recorded_approval(
            ApprovalView(
                approval_id=approval.approval_id,
                subject_reference=approval.subject_reference,
                decision=ApprovalDecision(approval.decision),
                decided_by=approval.decided_by,
                actor_type=ActorType(approval.actor_type),
                recorded=approval.recorded,
            ),
            approval_subject_for(
                second_proposal.proposal_id, second_proposal.content_fingerprint
            ),
        )
        self.assertFalse(reused.valid_record)
        self.assertFalse(reused.authorizes)

    def test_missing_human_review_creates_no_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        factory = FakeUnitOfWorkFactory(store)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        with self.assertRaises(ApplicationError):
            FinalizeFinding(factory, clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=submitted.proposal_id,
                    decided_by=TEST_HUMAN,
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.approvals), 0)

    def test_transaction_failure_leaves_no_partial_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        proposal_id = _submit_and_review(store, candidate_id)
        with self.assertRaises(PersistenceError):
            FinalizeFinding(
                FakeUnitOfWorkFactory(store, fail_on="findings"), clock=FixedClock()
            ).execute(
                FinalizeFindingCommand(
                    proposal_id=proposal_id,
                    decided_by=TEST_HUMAN,
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        self.assertEqual(len(store.findings), 0)
        self.assertEqual(len(store.approvals), 0)
        self.assertEqual(store.finding_proposals[proposal_id].state, "HUMAN_REVIEW")

    def test_duplicate_finalize_returns_same_finding(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        proposal_id = _submit_and_review(store, candidate_id)
        factory = FakeUnitOfWorkFactory(store)
        first = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        second = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(first.finding_id, second.finding_id)
        self.assertEqual(len(store.findings), 1)
        self.assertIn("IDEMPOTENT_EXISTING_FINDING", second.reason_codes)


if __name__ == "__main__":
    unittest.main()
