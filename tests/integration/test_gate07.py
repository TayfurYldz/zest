"""GATE 07 — Target Model / Differential Integrity on real PostgreSQL.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
Difference is not a vulnerability. GATE 04B may remain PENDING.
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

from zest.application.admit_target_inference import (
    AdmitTargetInference,
    AdmitTargetInferenceCommand,
)
from zest.application.compare_diagnostic_differential import (
    CompareDiagnosticDifferential,
    CompareDiagnosticDifferentialCommand,
)
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
from zest.application.project_diagnostic_target_model import (
    ProjectDiagnosticTargetModel,
    ProjectDiagnosticTargetModelCommand,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
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
from zest.research.admission import AdmissionOutcome
from zest.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialInterpretation,
    DifferentialOutcome,
)
from zest.research.epistemic import EpistemicClass
from zest.research.planning import DIAGNOSTIC_CLAIM, plan_diagnostic_echo
from zest.research.target_model import (
    TargetElementKind,
    TargetEpistemicStatus,
    TargetInferenceDraft,
    TargetInferenceOutcome,
)
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
class Gate07TargetDifferentialTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 07 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
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
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=_plan(message),
            )
        )
        ExecutePlannedExperiment(factory, _local_worker(), clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=_plan(message),
                scope=_allow_scope(),
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

    def test_diagnostic_projection_differential_and_hypothesis_reload(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")
        projection = ProjectDiagnosticTargetModel(factory).execute(
            ProjectDiagnosticTargetModelCommand(research_run_id="run-1")
        )
        self.assertTrue(projection.elements_with(TargetEpistemicStatus.OBSERVED))
        self.assertTrue(projection.elements_with(TargetEpistemicStatus.DERIVED))
        self.assertFalse(projection.elements_with(TargetEpistemicStatus.INFERRED))
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
        self.assertEqual(
            compared.observation.interpretation,
            DifferentialInterpretation.CONTROLLED_DIFFERENCE,
        )
        result = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="Does diagnostic echo differ by input?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-gate07",
                differential_id=compared.observation.differential_id,
            )
        )
        self.assertEqual(result.outcome, AdmissionOutcome.ADMITTED)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                diffs = uow.differential_observations.list_for_research_run("run-1")
                self.assertEqual(len(diffs), 1)
                self.assertEqual(diffs[0].interpretation, "CONTROLLED_DIFFERENCE")
                self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
                self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
                self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
                uow.commit()
        finally:
            reloaded.dispose()
        item = result.context.item_by_id(compared.observation.differential_id)
        assert item is not None
        self.assertEqual(item.epistemic_class, EpistemicClass.DERIVED_FACT)

    def test_inference_survives_reload_as_inferred(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        obs_id = self._observation_ids(factory)[0]
        admitted = AdmitTargetInference(factory, clock=FixedClock()).execute(
            AdmitTargetInferenceCommand(
                draft=TargetInferenceDraft(
                    inference_id="inf-1",
                    research_run_id="run-1",
                    kind=TargetElementKind.RELATIONSHIP,
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    opaque_ref="maybe-related",
                    statement="Actor handle may be related to the diagnostic resource.",
                    source_refs=(obs_id,),
                    attributes={"not_ownership": True},
                )
            )
        )
        self.assertEqual(admitted.outcome, TargetInferenceOutcome.ADMITTED)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                record = uow.target_inferences.get("inf-1")
                assert record is not None
                self.assertEqual(record.epistemic_status, "INFERRED")
                uow.commit()
        finally:
            reloaded.dispose()
        projection = ProjectDiagnosticTargetModel(factory).execute(
            ProjectDiagnosticTargetModelCommand(research_run_id="run-1")
        )
        inferred = projection.elements_with(TargetEpistemicStatus.INFERRED)
        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0].epistemic_status, TargetEpistemicStatus.INFERRED)

    def test_hallucinated_and_cross_run_sources_are_rejected(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        missing = AdmitTargetInference(factory, clock=FixedClock()).execute(
            AdmitTargetInferenceCommand(
                draft=TargetInferenceDraft(
                    inference_id="inf-ghost",
                    research_run_id="run-1",
                    kind=TargetElementKind.RELATIONSHIP,
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    opaque_ref="ghost",
                    statement="A related diagnostic handle may exist.",
                    source_refs=("obs-missing",),
                    attributes={},
                )
            )
        )
        self.assertEqual(missing.outcome, TargetInferenceOutcome.REJECTED_HALLUCINATED_SOURCE)
        compared = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-cross",
                    research_run_id="run-1",
                    baseline_observation_ids=("obs-missing",),
                    variant_observation_ids=("obs-also-missing",),
                    changed_dimensions=(DifferentialDimension.INPUT,),
                    common_dimensions=(DifferentialDimension.ACTION,),
                )
            )
        )
        self.assertEqual(compared.outcome, DifferentialOutcome.REJECTED_MISSING_SOURCE)
        with factory.open() as uow:
            self.assertEqual(uow.target_inferences.list_for_research_run("run-1"), [])
            self.assertEqual(
                uow.differential_observations.list_for_research_run("run-1"), []
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id="run-2",
                    program_id="prog-1",
                    authorization_source_id="as-1",
                    initiated_by_actor_id="operator-1",
                    initiated_by_actor_type="HUMAN_OPERATOR",
                    started_at=NOW,
                )
            )
            uow.commit()
        obs_id = self._observation_ids(factory)[0]
        cross = AdmitTargetInference(factory, clock=FixedClock()).execute(
            AdmitTargetInferenceCommand(
                draft=TargetInferenceDraft(
                    inference_id="inf-cross",
                    research_run_id="run-2",
                    kind=TargetElementKind.RELATIONSHIP,
                    epistemic_status=TargetEpistemicStatus.INFERRED,
                    opaque_ref="cross-run",
                    statement="A related diagnostic handle may exist.",
                    source_refs=(obs_id,),
                    attributes={},
                )
            )
        )
        self.assertEqual(cross.outcome, TargetInferenceOutcome.REJECTED_CROSS_RUN)
        compared_cross = CompareDiagnosticDifferential(
            factory, clock=FixedClock()
        ).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-cross-run",
                    research_run_id="run-2",
                    baseline_observation_ids=(obs_id,),
                    variant_observation_ids=(obs_id,),
                    changed_dimensions=(DifferentialDimension.INPUT,),
                    common_dimensions=(DifferentialDimension.ACTION,),
                )
            )
        )
        self.assertEqual(compared_cross.outcome, DifferentialOutcome.REJECTED_CROSS_RUN)

    def test_rollback_leaves_no_partial_differential(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")
        obs_a, obs_b = self._observation_ids(factory)

        class FailingDiffUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.differential_observations.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.differential_observations.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingDiffUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            CompareDiagnosticDifferential(
                FailingFactory(self.engine), clock=FixedClock()
            ).execute(
                CompareDiagnosticDifferentialCommand(
                    case=DifferentialCase(
                        case_id="case-1",
                        research_run_id="run-1",
                        baseline_observation_ids=(obs_a,),
                        variant_observation_ids=(obs_b,),
                        changed_dimensions=(DifferentialDimension.INPUT,),
                        common_dimensions=(DifferentialDimension.ACTION,),
                    )
                )
            )
        with factory.open() as reload:
            self.assertEqual(
                reload.differential_observations.list_for_research_run("run-1"), []
            )
            reload.commit()

    def test_migration_head_includes_target_differential_tables(self) -> None:
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
        self.assertEqual(version, "a45_001_observability_foundation")
        self.assertIn("target_inference", tables)
        self.assertIn("differential_observation", tables)
        self.assertIn("finding", tables)


if __name__ == "__main__":
    unittest.main()
