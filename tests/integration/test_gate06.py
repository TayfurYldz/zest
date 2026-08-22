"""GATE 06 — Human Finding Acceptance Integrity on real PostgreSQL.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Finding is diagnostic plumbing, not a vulnerability. GATE 04B may remain PENDING.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

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
from research_os.core.enums import ActorType, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.errors import PersistenceError
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    AuthorizationSourceRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from research_os.platform.worker import InvocationStatus
from research_os.research.candidate import CandidateState
from research_os.research.finding_proposal import (
    DIAGNOSTIC_FINDING_PROPOSAL_TITLE,
    FindingCreationOutcome,
    FindingProposalState,
    HumanReviewDecision,
)
from research_os.research.planning import DIAGNOSTIC_CLAIM, plan_diagnostic_echo
from research_os.research.verification import VerificationOutcome
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from integration.harness import (
    FixedClock,
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    truncate_spine,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

WORKERS_PYTHON = _REPO / "workers" / "python"
TEST_HUMAN = "operator-test-1"


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


def _local_worker():
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=WORKERS_PYTHON,
                default_timeout_ms=5_000,
            )
        )
    )


def _seed_run(uow: PostgresUnitOfWork) -> None:
    uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=NOW, name="lab"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="written-auth-1",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id="run-1",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=8,
            max_tool_calls=8,
            max_runtime_ms=30_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )
    uow.hypotheses.insert(
        HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim=DIAGNOSTIC_CLAIM,
            origin_reference="human-seed-1",
            created_at=NOW,
        )
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate06FindingAcceptanceTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 06 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def _factory(self) -> PostgresUnitOfWorkFactory:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        return factory

    def _execute(self, factory, experiment_id: str, message: str, *, worker=None) -> None:
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=_plan(message),
            )
        )
        ExecutePlannedExperiment(factory, worker or _local_worker(), clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=_plan(message),
                scope=_allow_scope(),
            )
        )
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
        )

    def _admit_evidence(self, factory, experiment_id: str) -> str:
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=experiment_id)
        )
        self.assertIsNotNone(result.evidence_id)
        assert result.evidence_id is not None
        return result.evidence_id

    def _open_candidate(
        self, factory, *, experiment_id: str = "exp-1", message: str = "alpha"
    ) -> str:
        self._execute(factory, experiment_id, message)
        evidence_id = self._admit_evidence(factory, experiment_id)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        return proposed.candidate_id

    def _validated_candidate(
        self,
        factory,
        *,
        experiment_id: str = "exp-1",
        repro_id: str = "exp-repro",
        message: str = "alpha",
        repro_msg: str = "beta",
    ) -> str:
        candidate_id = self._open_candidate(
            factory, experiment_id=experiment_id, message=message
        )
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        self._execute(factory, repro_id, repro_msg)
        self._admit_evidence(factory, repro_id)
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=candidate_id,
                reproduction_experiment_id=repro_id,
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.VALIDATED)
        return candidate_id

    def _review(
        self,
        factory,
        proposal_id: str,
        *,
        decision: HumanReviewDecision = HumanReviewDecision.APPROVE,
        actor_type: ActorType = ActorType.HUMAN_OPERATOR,
        reviewer_id: str = TEST_HUMAN,
    ) -> None:
        StartHumanReview(factory).execute(StartHumanReviewCommand(proposal_id=proposal_id))
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=proposal_id,
                reviewer_id=reviewer_id,
                actor_type=actor_type,
                decision=decision,
                note="diagnostic plumbing review",
            )
        )

    def test_diagnostic_plumbing_finding_reloads_full_provenance(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        self._review(factory, submitted.proposal_id)
        finalized = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(finalized.outcome, FindingCreationOutcome.CREATED)
        self.assertEqual(finalized.proposal_state, FindingProposalState.APPROVED)
        assert finalized.finding_id is not None
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                finding = uow.findings.get(finalized.finding_id)
                self.assertIsNotNone(finding)
                assert finding is not None
                self.assertEqual(finding.title, DIAGNOSTIC_FINDING_PROPOSAL_TITLE)
                self.assertEqual(finding.classification, "DIAGNOSTIC_PLUMBING")
                self.assertNotIn("vulnerability", finding.title.lower())
                self.assertFalse(hasattr(finding, "severity"))
                self.assertFalse(hasattr(finding, "cvss"))
                proposal = uow.finding_proposals.get(finding.finding_proposal_id)
                assert proposal is not None
                self.assertEqual(proposal.state, "APPROVED")
                candidate = uow.candidates.get(finding.candidate_id)
                assert candidate is not None
                self.assertEqual(candidate.state, "VALIDATED")
                verifications = uow.verifications.list_for_candidate(candidate.candidate_id)
                self.assertEqual(len(verifications), 1)
                verification = verifications[0]
                self.assertNotEqual(
                    verification.original_evidence_ids,
                    verification.reproduction_evidence_ids,
                )
                original = uow.evidence.get(verification.original_evidence_ids[0])
                reproduction = uow.evidence.get(verification.reproduction_evidence_ids[0])
                assert original is not None
                assert reproduction is not None
                self.assertNotEqual(original.evidence_id, reproduction.evidence_id)
                self.assertNotEqual(original.experiment_id, reproduction.experiment_id)
                self.assertNotEqual(original.observation_ids, reproduction.observation_ids)
                approval = uow.approvals.get(finding.approval_id)
                review = uow.human_reviews.get(finding.human_review_id)
                assert approval is not None
                assert review is not None
                self.assertEqual(approval.decision, "APPROVE")
                self.assertEqual(review.actor_type, ActorType.HUMAN_OPERATOR.value)
                self.assertEqual(review.reviewer_id, TEST_HUMAN)
                uow.commit()
        finally:
            reloaded.dispose()
        assert self.engine is not None
        with self.engine.connect() as connection:
            event_types = {
                row[0]
                for row in connection.execute(text("SELECT event_type FROM audit_event"))
            }
        self.assertIn("FINDING_PROPOSAL_CREATED", event_types)
        self.assertIn("HUMAN_REVIEW_RECORDED", event_types)
        self.assertIn("CORE_APPROVAL_RECORDED", event_types)
        self.assertIn("FINDING_CREATED", event_types)

    def test_open_candidate_cannot_create_proposal(self) -> None:
        factory = self._factory()
        candidate_id = self._open_candidate(factory)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(factory, clock=FixedClock()).execute(
                SubmitFindingProposalCommand(candidate_id=candidate_id)
            )
        with factory.open() as uow:
            self.assertEqual(uow.finding_proposals.list_for_candidate(candidate_id), [])
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_inconclusive_candidate_cannot_create_proposal(self) -> None:
        factory = self._factory()
        candidate_id = self._open_candidate(factory)
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
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT)
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
        self.assertEqual(completed.state, CandidateState.INCONCLUSIVE)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(factory, clock=FixedClock()).execute(
                SubmitFindingProposalCommand(candidate_id=candidate_id)
            )
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_human_reject_creates_no_finding_and_leaves_candidate_validated(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        self._review(factory, submitted.proposal_id, decision=HumanReviewDecision.REJECT)
        result = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(result.outcome, FindingCreationOutcome.REJECTED_PROPOSAL)
        self.assertIsNone(result.finding_id)
        with factory.open() as uow:
            proposal = uow.finding_proposals.get(submitted.proposal_id)
            candidate = uow.candidates.get(candidate_id)
            assert proposal is not None
            assert candidate is not None
            self.assertEqual(proposal.state, "REJECTED")
            self.assertEqual(candidate.state, "VALIDATED")
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_non_human_actor_cannot_approve(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        self._review(factory, submitted.proposal_id)
        with self.assertRaises(ApplicationError):
            FinalizeFinding(factory, clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=submitted.proposal_id,
                    decided_by="control-plane",
                    actor_type=ActorType.CONTROL_PLANE,
                )
            )
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            proposal = uow.finding_proposals.get(submitted.proposal_id)
            assert proposal is not None
            self.assertEqual(proposal.state, "HUMAN_REVIEW")
            self.assertIsNone(
                uow.approvals.get_by_subject(
                    f"finding-proposal:{proposal.proposal_id}:{proposal.content_fingerprint}"
                )
            )
            uow.commit()

    def test_wrong_proposal_cannot_use_another_review(self) -> None:
        factory = self._factory()
        first_id = self._validated_candidate(factory)
        second_id = self._validated_candidate(
            factory,
            experiment_id="exp-2",
            repro_id="exp-repro-2",
            message="gamma",
            repro_msg="delta",
        )
        first = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=first_id)
        )
        second = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=second_id)
        )
        assert first.proposal_id is not None
        assert second.proposal_id is not None
        self._review(factory, first.proposal_id)
        with self.assertRaises(ApplicationError):
            FinalizeFinding(factory, clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=second.proposal_id,
                    decided_by=TEST_HUMAN,
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_missing_human_review_creates_no_finding(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
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
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_rollback_leaves_no_partial_finding(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        self._review(factory, submitted.proposal_id)

        class FailingFindingUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.findings.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.findings.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingFindingUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            FinalizeFinding(FailingFactory(self.engine), clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=submitted.proposal_id,
                    decided_by=TEST_HUMAN,
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        with factory.open() as reload:
            self.assertEqual(reload.findings.list_for_research_run("run-1"), [])
            proposal = reload.finding_proposals.get(submitted.proposal_id)
            assert proposal is not None
            self.assertEqual(proposal.state, "HUMAN_REVIEW")
            self.assertIsNone(
                reload.approvals.get_by_subject(
                    f"finding-proposal:{proposal.proposal_id}:{proposal.content_fingerprint}"
                )
            )
            reload.commit()

    def test_duplicate_finalize_returns_same_finding(self) -> None:
        factory = self._factory()
        candidate_id = self._validated_candidate(factory)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        self._review(factory, submitted.proposal_id)
        first = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        second = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=TEST_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(first.finding_id, second.finding_id)
        self.assertIn("IDEMPOTENT_EXISTING_FINDING", second.reason_codes)
        with factory.open() as uow:
            self.assertEqual(len(uow.findings.list_for_research_run("run-1")), 1)
            uow.commit()

    def test_migration_head_includes_finding_acceptance_tables(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
        self.assertEqual(version, "a42_001_preflight_report")
        self.assertIn("finding_proposal", tables)
        self.assertIn("human_review", tables)
        self.assertIn("approval", tables)
        self.assertIn("finding", tables)
        self.assertIn("candidate", tables)
        self.assertIn("verification", tables)


if __name__ == "__main__":
    unittest.main()
