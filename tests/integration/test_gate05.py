"""GATE 05 — Verification / Candidate integrity on real PostgreSQL.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
VALIDATED Candidate is not a Finding. GATE 04B may remain PENDING.
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
    ResearchLoopStatus,
)
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from research_os.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from research_os.core.enums import ScopeRuleEffect
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
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.candidate import CandidateAdmissionOutcome, CandidateState
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


def _mismatched(request):
    from support.recording_worker import completed_diagnostic_outcome

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
class Gate05CandidateVerificationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 05 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
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

    def _execute(
        self,
        factory: PostgresUnitOfWorkFactory,
        experiment_id: str,
        message: str,
        worker=None,
        *,
        execution_outcome: str | None = None,
        invocation_status: str | None = None,
    ) -> None:
        plan = _plan(message)
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=plan,
            )
        )
        loop = ExecutePlannedExperiment(
            factory, worker or _local_worker(), clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=_allow_scope(),
            )
        )
        if worker is None:
            self.assertEqual(loop.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id=experiment_id,
                execution_outcome=execution_outcome or loop.status.value,
                invocation_status=invocation_status
                or (loop.invocation_status.value if loop.invocation_status else None),
            )
        )

    def _admit_evidence(self, factory, experiment_id: str) -> str:
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=experiment_id)
        )
        self.assertIsNotNone(result.evidence_id)
        assert result.evidence_id is not None
        return result.evidence_id

    def test_validated_diagnostic_candidate_reloads_and_is_not_finding(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        evidence_id = self._admit_evidence(factory, "exp-1")
        with factory.open() as check:
            self.assertEqual(check.candidates.list_for_research_run("run-1"), [])
            check.commit()
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        self.assertEqual(proposed.outcome, CandidateAdmissionOutcome.ADMITTED)
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        self._execute(factory, "exp-repro", "beta")
        self._admit_evidence(factory, "exp-repro")
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.VALIDATED)
        self.assertEqual(completed.state, CandidateState.VALIDATED)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                candidate = uow.candidates.get(proposed.candidate_id)
                verifications = uow.verifications.list_for_candidate(proposed.candidate_id)
                self.assertIsNotNone(candidate)
                assert candidate is not None
                self.assertEqual(candidate.state, "VALIDATED")
                self.assertEqual(len(verifications), 1)
                self.assertNotEqual(
                    verifications[0].original_evidence_ids,
                    verifications[0].reproduction_evidence_ids,
                )
                self.assertFalse(hasattr(candidate, "severity"))
                self.assertFalse(hasattr(candidate, "cvss"))
        finally:
            reloaded.dispose()

    def test_mismatch_reproduction_rejects_candidate(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        evidence_id = self._admit_evidence(factory, "exp-1")
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        self._execute(
            factory, "exp-repro", "beta", worker=RecordingWorkerPort(handler=_mismatched)
        )
        self._admit_evidence(factory, "exp-repro")
        rejected = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(rejected.outcome, VerificationOutcome.REJECTED)
        self.assertEqual(rejected.state, CandidateState.REJECTED)

    def test_timeout_reproduction_is_inconclusive(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        evidence_id = self._admit_evidence(factory, "exp-1")
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        self._execute(
            factory,
            "exp-timeout",
            "beta",
            worker=RecordingWorkerPort(
                outcome=invocation_outcome(InvocationStatus.TIMED_OUT)
            ),
            execution_outcome="INVOCATION_FAILED",
            invocation_status=InvocationStatus.TIMED_OUT.value,
        )
        inconclusive = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-timeout",
            )
        )
        self.assertEqual(inconclusive.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertEqual(inconclusive.state, CandidateState.INCONCLUSIVE)

    def test_illegal_open_to_validated_and_append_only_verification(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        evidence_id = self._admit_evidence(factory, "exp-1")
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id)
        )
        assert proposed.candidate_id is not None
        self._execute(factory, "exp-repro", "beta")
        self._admit_evidence(factory, "exp-repro")
        with self.assertRaises(ApplicationError):
            CompleteCandidateVerification(factory, clock=FixedClock()).execute(
                CompleteCandidateVerificationCommand(
                    candidate_id=proposed.candidate_id,
                    reproduction_experiment_id="exp-repro",
                )
            )
        with factory.open() as reload:
            candidate = reload.candidates.get(proposed.candidate_id)
            assert candidate is not None
            self.assertEqual(candidate.state, "OPEN")
            self.assertEqual(reload.verifications.list_for_candidate(proposed.candidate_id), [])
            reload.commit()

        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(completed.state, CandidateState.VALIDATED)
        assert self.engine is not None
        with self.engine.begin() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    text("UPDATE verification SET outcome = 'REJECTED' WHERE verification_id = :id"),
                    {"id": completed.verification_id},
                )

    def test_rollback_leaves_no_partial_candidate(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        evidence_id = self._admit_evidence(factory, "exp-1")

        class FailingCandidateUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.candidates.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.candidates.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingCandidateUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            ProposeCandidateFromEvidence(
                FailingFactory(self.engine), clock=FixedClock()
            ).execute(ProposeCandidateFromEvidenceCommand(evidence_id=evidence_id))
        with factory.open() as reload:
            self.assertEqual(reload.candidates.list_for_research_run("run-1"), [])
            self.assertEqual(reload.candidate_admissions.list_for_research_run("run-1"), [])
            reload.commit()

    def test_migration_head_includes_candidate_and_verification(self) -> None:
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
        self.assertEqual(version, "a39_001_mr5_durability_uq")
        self.assertIn("candidate", tables)
        self.assertIn("verification", tables)
        self.assertIn("evidence", tables)


if __name__ == "__main__":
    unittest.main()
