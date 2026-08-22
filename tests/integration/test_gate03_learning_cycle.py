"""GATE 03 — closed research learning cycle on real PostgreSQL.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
SQLite is not a substitute. This does not prove vulnerability discovery.
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
from research_os.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
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
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from research_os.platform.worker import InvocationStatus
from research_os.research.admission import AdmissionOutcome
from research_os.research.assessment import AssessmentOutcome
from research_os.research.planning import DIAGNOSTIC_CLAIM
from support.fake_model import ScriptedModelPort, default_generator_output
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


def _propose_command(**overrides) -> ProposeResearchHypothesisCommand:
    values = dict(
        research_run_id="run-1",
        research_question="Does the diagnostic capability return the submitted value?",
        budget_id="budget-1",
        target_reference="target-1",
        correlation_id="corr-gate03",
        echo_message="ping",
    )
    values.update(overrides)
    return ProposeResearchHypothesisCommand(**values)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate03LearningCycleTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(
            "DESTRUCTIVE PostgreSQL integration tests: TRUNCATE CASCADE against "
            f"{redacted_database_url(TEST_URL)}",
            flush=True,
        )
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_full_diagnostic_cycle_persists_assessment_and_reloads(self) -> None:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(_propose_command())
        self.assertEqual(proposed.outcome, AdmissionOutcome.ADMITTED)
        assert proposed.hypothesis_id is not None
        assert proposed.experiment_plan is not None
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-gate03",
                research_run_id="run-1",
                plan=proposed.experiment_plan,
            )
        )
        worker = RecordingWorkerPort(
            inner=LocalProcessWorkerAdapter(
                LocalProcessWorkerConfig(
                    workers_python_path=WORKERS_PYTHON,
                    default_timeout_ms=5_000,
                )
            )
        )
        loop = ExecutePlannedExperiment(
            factory, worker, clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-gate03",
                plan=proposed.experiment_plan,
                scope=_allow_scope(),
            )
        )
        self.assertEqual(loop.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id="exp-gate03",
                execution_outcome=loop.status.value,
                invocation_status=(
                    loop.invocation_status.value if loop.invocation_status else None
                ),
            )
        )
        self.assertEqual(
            feedback.assessment_outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION
        )
        self.assertFalse(hasattr(feedback, "severity"))
        self.assertFalse(hasattr(feedback, "finding"))

        with factory.open() as reload:
            hypothesis = reload.hypotheses.get(proposed.hypothesis_id)
            assert hypothesis is not None
            self.assertEqual(hypothesis.claim, DIAGNOSTIC_CLAIM)
            plan = reload.experiment_plans.get("exp-gate03")
            assert plan is not None
            self.assertEqual(plan.expected_observation, "echoed value matches input")
            self.assertEqual(plan.evaluation_strategy, "diagnostic.echo.v1")
            assessments = reload.hypothesis_assessments.list_for_experiment("exp-gate03")
            self.assertEqual(len(assessments), 1)
            self.assertEqual(
                assessments[0].assessment_outcome, "CONSISTENT_WITH_PREDICTION"
            )
            self.assertEqual(assessments[0].evaluator_kind, "DETERMINISTIC")
            self.assertNotIn("confidence", assessments[0].rationale)
            self.assertNotIn("severity", assessments[0].rationale)
            self.assertEqual(
                reload.research_admissions.list_for_research_run("run-1")[0].outcome,
                "ADMITTED",
            )
            reload.commit()

    def test_rejected_reasoning_reloads_without_hypothesis(self) -> None:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()

        def hallucinate(request):
            payload = dict(default_generator_output(request))
            payload["source_references"] = ["obs:does-not-exist"]
            return payload

        result = ProposeResearchHypothesis(
            factory, ScriptedModelPort(generator=hallucinate), clock=FixedClock()
        ).execute(_propose_command())
        self.assertEqual(result.outcome, AdmissionOutcome.NEEDS_MORE_CONTEXT)
        with factory.open() as reload:
            self.assertEqual(reload.hypotheses.list_for_research_run("run-1"), [])
            reasoning = reload.research_reasoning.list_for_research_run("run-1")
            self.assertEqual(len(reasoning), 2)
            self.assertTrue(all(record.hypothesis_id is None for record in reasoning))
            admissions = reload.research_admissions.list_for_research_run("run-1")
            self.assertEqual(len(admissions), 1)
            self.assertEqual(admissions[0].outcome, "NEEDS_MORE_CONTEXT")
            self.assertIsNone(admissions[0].admitted_hypothesis_id)
            self.assertNotIn("prompt", reasoning[0].structured_output)
            reload.commit()

    def test_timeout_assessment_is_unusable_and_does_not_loop(self) -> None:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(_propose_command())
        assert proposed.experiment_plan is not None
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-timeout",
                research_run_id="run-1",
                plan=proposed.experiment_plan,
            )
        )
        worker = RecordingWorkerPort(
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT)
        )
        loop = ExecutePlannedExperiment(
            factory, worker, clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-timeout",
                plan=proposed.experiment_plan,
                scope=_allow_scope(),
            )
        )
        self.assertEqual(loop.status, ResearchLoopStatus.INVOCATION_FAILED)
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(
                experiment_id="exp-timeout",
                execution_outcome=loop.status.value,
                invocation_status=InvocationStatus.TIMED_OUT.value,
            )
        )
        self.assertEqual(feedback.assessment_outcome, AssessmentOutcome.EXECUTION_UNUSABLE)
        with factory.open() as reload:
            hypothesis = reload.hypotheses.get(proposed.hypothesis_id)
            assert hypothesis is not None
            self.assertEqual(hypothesis.claim, DIAGNOSTIC_CLAIM)
            assessments = reload.hypothesis_assessments.list_for_experiment("exp-timeout")
            self.assertEqual(len(assessments), 1)
            self.assertEqual(assessments[0].assessment_outcome, "EXECUTION_UNUSABLE")
            experiments = reload.experiments.list_for_research_run("run-1")
            self.assertEqual(len(experiments), 1)
            reload.commit()
        self.assertEqual(len(worker.calls), 1)

    def test_durable_plan_survives_reload(self) -> None:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(_propose_command())
        assert proposed.experiment_plan is not None
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-plan",
                research_run_id="run-1",
                plan=proposed.experiment_plan,
            )
        )
        with factory.open() as reload:
            plan = reload.experiment_plans.get("exp-plan")
            assert plan is not None
            self.assertEqual(plan.expected_observation, "echoed value matches input")
            self.assertEqual(
                plan.disconfirming_observation, "no result or mismatched value"
            )
            self.assertEqual(plan.evaluation_strategy, "diagnostic.echo.v1")
            self.assertEqual(plan.arguments["message"], "ping")
            reload.commit()

    def test_assessment_insert_failure_rolls_back(self) -> None:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(_propose_command())
        assert proposed.experiment_plan is not None
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-rollback",
                research_run_id="run-1",
                plan=proposed.experiment_plan,
            )
        )
        worker = RecordingWorkerPort(
            inner=LocalProcessWorkerAdapter(
                LocalProcessWorkerConfig(
                    workers_python_path=WORKERS_PYTHON,
                    default_timeout_ms=5_000,
                )
            )
        )
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-rollback",
                plan=proposed.experiment_plan,
                scope=_allow_scope(),
            )
        )

        class FailingAssessmentUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.hypothesis_assessments.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.hypothesis_assessments.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingAssessmentUoW(self._engine)

        with self.assertRaises(PersistenceError):
            EvaluateExperimentFeedback(FailingFactory(self.engine), clock=FixedClock()).execute(
                EvaluateExperimentFeedbackCommand(experiment_id="exp-rollback")
            )
        with factory.open() as reload:
            self.assertEqual(
                reload.hypothesis_assessments.list_for_experiment("exp-rollback"),
                [],
            )
            hypothesis = reload.hypotheses.get(proposed.hypothesis_id)
            assert hypothesis is not None
            self.assertEqual(hypothesis.claim, DIAGNOSTIC_CLAIM)
            reload.commit()

    def test_migration_head_includes_learning_cycle(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                    )
                )
            }
        self.assertEqual(version, "a42_001_preflight_report")
        self.assertIn("research_admission", tables)
        self.assertIn("experiment_plan", tables)
        self.assertIn("hypothesis_assessment", tables)
        self.assertIn("evidence", tables)
        self.assertIn("candidate", tables)
        self.assertIn("verification", tables)


if __name__ == "__main__":
    unittest.main()
