"""Transition B — Evidence admission on real PostgreSQL.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
Does not create Candidate, Finding, or Verification.
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

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
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
from zest.data.errors import PersistenceError
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuthorizationSourceRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from zest.research.evidence import (
    DIAGNOSTIC_ECHO_MATCHED_CLAIM,
    EvidenceAdmissionOutcome,
    EvidencePolarity,
    EvidenceProposal,
)
from zest.research.planning import DIAGNOSTIC_CLAIM, plan_diagnostic_echo
from support.recording_worker import RecordingWorkerPort
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
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )

WORKERS_PYTHON = _REPO / "workers" / "python"


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _plan():
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message="ping",
    )


def _worker():
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
            max_requests=1,
            max_tool_calls=1,
            max_runtime_ms=10_000,
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
class TransitionBEvidenceAdmissionTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"Transition B PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def _prepare_success(self, experiment_id: str = "exp-1") -> PostgresUnitOfWorkFactory:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        plan = _plan()
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=plan,
            )
        )
        loop = ExecutePlannedExperiment(
            factory, _worker(), clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=_allow_scope(),
            )
        )
        self.assertEqual(loop.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id=experiment_id,
                execution_outcome=loop.status.value,
                invocation_status=(
                    loop.invocation_status.value if loop.invocation_status else None
                ),
            )
        )
        return factory

    def test_valid_diagnostic_admits_evidence_and_reloads(self) -> None:
        factory = self._prepare_success()
        with factory.open() as check:
            self.assertEqual(check.evidence.list_for_research_run("run-1"), [])
            check.commit()
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-1")
        )
        self.assertEqual(result.outcome, EvidenceAdmissionOutcome.ADMITTED)
        assert result.evidence_id is not None
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                evidence = uow.evidence.get(result.evidence_id)
                admissions = uow.evidence_admissions.list_for_research_run("run-1")
                self.assertIsNotNone(evidence)
                assert evidence is not None
                self.assertEqual(evidence.claim_scope, DIAGNOSTIC_ECHO_MATCHED_CLAIM)
                self.assertEqual(len(admissions), 1)
                self.assertEqual(admissions[0].outcome, "ADMITTED")
                self.assertEqual(admissions[0].admitted_evidence_id, result.evidence_id)
                self.assertFalse(hasattr(evidence, "confidence"))
        finally:
            reloaded.dispose()

    def test_hallucinated_source_rejected_without_evidence(self) -> None:
        factory = self._prepare_success()
        with factory.open() as uow:
            assessment = uow.hypothesis_assessments.list_for_experiment("exp-1")[0]
            uow.commit()
        proposal = EvidenceProposal(
            proposal_id="prop-ghost",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            observation_ids=("obs-does-not-exist",),
            assessment_ids=(assessment.assessment_id,),
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
        with factory.open() as reload:
            self.assertEqual(reload.evidence.list_for_research_run("run-1"), [])
            self.assertEqual(len(reload.evidence_admissions.list_for_research_run("run-1")), 1)
            reload.commit()

    def test_evidence_is_append_only(self) -> None:
        factory = self._prepare_success()
        result = AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id="exp-1")
        )
        assert self.engine is not None and result.evidence_id is not None
        with self.engine.begin() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    text("UPDATE evidence SET claim_scope = 'mutated' WHERE evidence_id = :id"),
                    {"id": result.evidence_id},
                )

    def test_rollback_leaves_no_partial_evidence(self) -> None:
        factory = self._prepare_success("exp-tb-rollback")

        class FailingEvidenceUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.evidence.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.evidence.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingEvidenceUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            AdmitDiagnosticEvidence(FailingFactory(self.engine), clock=FixedClock()).execute(
                AdmitDiagnosticEvidenceCommand(experiment_id="exp-tb-rollback")
            )
        with factory.open() as reload:
            self.assertEqual(
                reload.evidence.list_for_experiment("exp-tb-rollback"),
                [],
            )
            self.assertEqual(
                reload.evidence_admissions.list_for_research_run("run-1"),
                [],
            )
            reload.commit()


if __name__ == "__main__":
    unittest.main()
