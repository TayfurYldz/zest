"""GATE 08 — Invariant / Chain Integrity on real PostgreSQL.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Invariant is not a fact. Chain is not an exploit. GATE 04B may remain PENDING.
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

from research_os.application.admit_diagnostic_invariant import (
    AdmitDiagnosticInvariant,
    AdmitDiagnosticInvariantCommand,
)
from research_os.application.compare_diagnostic_differential import (
    CompareDiagnosticDifferential,
    CompareDiagnosticDifferentialCommand,
)
from research_os.application.compose_diagnostic_chain import (
    ComposeDiagnosticChain,
    ComposeDiagnosticChainCommand,
)
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.project_diagnostic_target_model import (
    ProjectDiagnosticTargetModel,
    ProjectDiagnosticTargetModelCommand,
)
from research_os.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from research_os.application.record_invariant_counterexample import (
    RecordInvariantCounterexample,
    RecordInvariantCounterexampleCommand,
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
from research_os.research.admission import AdmissionOutcome
from research_os.research.chain import ChainOutcome
from research_os.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialOutcome,
)
from research_os.research.epistemic import EpistemicClass
from research_os.research.invariant import InvariantAdmissionOutcome, InvariantStatus
from research_os.research.planning import DIAGNOSTIC_CLAIM, plan_diagnostic_echo
from research_os.research.target_model import TargetEpistemicStatus
from support.fake_model import ScriptedModelPort
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
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

WORKERS_PYTHON = _REPO / "workers" / "python"


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _plan(message: str):
    return plan_diagnostic_echo(
        "hyp-1", budget_id="budget-1", target_reference="target-1", message=message
    )


def _local_worker():
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=WORKERS_PYTHON, default_timeout_ms=5_000
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
class Gate08InvariantChainTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 08 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
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

    def _execute(self, factory, experiment_id: str, message: str) -> None:
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id, research_run_id="run-1", plan=_plan(message)
            )
        )
        ExecutePlannedExperiment(factory, _local_worker(), clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id, plan=_plan(message), scope=_allow_scope()
            )
        )
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
        )

    def _observation_ids(self, factory) -> tuple[str, ...]:
        with factory.open() as uow:
            ids = tuple(
                item.observation_id
                for item in uow.observations.list_for_research_run("run-1")
            )
            uow.commit()
        return ids

    def test_invariant_chain_reload_and_hypothesis_cycle(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")
        projection = ProjectDiagnosticTargetModel(factory).execute(
            ProjectDiagnosticTargetModelCommand(research_run_id="run-1")
        )
        self.assertTrue(projection.elements_with(TargetEpistemicStatus.OBSERVED))
        obs_a, obs_b = self._observation_ids(factory)
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
        admitted = AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(
                research_run_id="run-1",
                differential_id=compared.observation.differential_id,
            )
        )
        self.assertEqual(admitted.outcome, InvariantAdmissionOutcome.ADMITTED)
        assert admitted.hypothesis is not None
        self.assertEqual(admitted.hypothesis.status, InvariantStatus.TESTABLE)
        composed = ComposeDiagnosticChain(factory, clock=FixedClock()).execute(
            ComposeDiagnosticChainCommand(
                research_run_id="run-1",
                invariant_id=admitted.hypothesis.invariant_id,
                budget_id="budget-1",
                target_reference="target-1",
                hypothesis_id="hyp-1",
            )
        )
        self.assertEqual(composed.decisions[0].outcome, ChainOutcome.ADMITTED)
        assert composed.decisions[0].hypothesis is not None
        result = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="Does diagnostic echo keep input/output correspondence?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-gate08",
                invariant_id=admitted.hypothesis.invariant_id,
                chain_id=composed.decisions[0].hypothesis.chain_id,
            )
        )
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        item = result.context.item_by_id(admitted.hypothesis.invariant_id)
        assert item is not None
        self.assertEqual(item.epistemic_class, EpistemicClass.HYPOTHESIS)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                invariant = uow.invariant_hypotheses.get(admitted.hypothesis.invariant_id)
                assert invariant is not None
                self.assertEqual(invariant.status, "TESTABLE")
                chains = uow.chain_hypotheses.list_for_research_run("run-1")
                self.assertEqual(len(chains), 1)
                self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
                self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
                self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
                uow.commit()
        finally:
            reloaded.dispose()

    def test_counterexample_and_hallucinated_sources(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        admitted = AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        assert admitted.hypothesis is not None
        obs_id = self._observation_ids(factory)[0]
        updated = RecordInvariantCounterexample(factory, clock=FixedClock()).execute(
            RecordInvariantCounterexampleCommand(
                invariant_id=admitted.hypothesis.invariant_id,
                source_ref=obs_id,
                applicability_context={"input": "alpha", "not_global": True},
            )
        )
        self.assertEqual(updated.status, InvariantStatus.CHALLENGED)
        missing = AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(
                research_run_id="run-1", differential_id="diff-missing"
            )
        )
        self.assertEqual(missing.outcome, InvariantAdmissionOutcome.NEEDS_MORE_CONTEXT)

    def test_rollback_leaves_no_partial_chain(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")

        class FailingChainUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.chain_hypotheses.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.chain_hypotheses.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingChainUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            ComposeDiagnosticChain(
                FailingFactory(self.engine), clock=FixedClock()
            ).execute(ComposeDiagnosticChainCommand(research_run_id="run-1"))
        with factory.open() as reload:
            self.assertEqual(reload.chain_hypotheses.list_for_research_run("run-1"), [])
            reload.commit()

    def test_migration_head_includes_invariant_chain_tables(self) -> None:
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
        self.assertEqual(version, "a40_001_mr6a_identity_anomaly")
        self.assertIn("invariant_hypothesis", tables)
        self.assertIn("invariant_source_ref", tables)
        self.assertIn("invariant_counterexample_ref", tables)
        self.assertIn("chain_hypothesis", tables)


if __name__ == "__main__":
    unittest.main()
