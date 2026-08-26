"""GATE 09 — Exploration / Temporal Integrity on real PostgreSQL.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Selection is not authorization. Change is not a vulnerability. GATE 04B may remain PENDING.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
from research_os.application.capture_diagnostic_snapshot import (
    CaptureDiagnosticSnapshot,
    CaptureDiagnosticSnapshotCommand,
)
from research_os.application.compare_diagnostic_differential import (
    CompareDiagnosticDifferential,
    CompareDiagnosticDifferentialCommand,
)
from research_os.application.compare_diagnostic_snapshots import (
    CompareDiagnosticSnapshots,
    CompareDiagnosticSnapshotsCommand,
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
from research_os.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from research_os.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
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
from research_os.research.differential import (
    DifferentialCase,
    DifferentialDimension,
    DifferentialOutcome,
)
from research_os.research.epistemic import EpistemicClass
from research_os.research.exploration import OpportunityMode, ResearchPolicyBudget
from research_os.research.planning import DIAGNOSTIC_CLAIM, plan_diagnostic_echo
from research_os.research.temporal import ChangeOutcome, SnapshotOutcome
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
T2 = NOW + timedelta(hours=1)


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
class Gate09ExplorationTemporalTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 09 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
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

    def _echo_observations(self, factory) -> dict[str, str]:
        with factory.open() as uow:
            mapping = {
                str(item.payload.get("echoed")): item.observation_id
                for item in uow.observations.list_for_research_run("run-1")
            }
            uow.commit()
        return mapping

    def test_part_a_bounded_selection_does_not_execute(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")
        AdmitDiagnosticInvariant(factory, clock=FixedClock()).execute(
            AdmitDiagnosticInvariantCommand(research_run_id="run-1")
        )
        ComposeDiagnosticChain(factory, clock=FixedClock()).execute(
            ComposeDiagnosticChainCommand(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference="target-1",
                hypothesis_id="hyp-1",
            )
        )
        with factory.open() as uow:
            attempts_before = len(uow.worker_results.list_for_research_run("run-1"))
            uow.commit()
        selected = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=3, max_exploratory=1),
            )
        )
        self.assertTrue(selected.selected)
        self.assertLessEqual(len(selected.selected), 3)
        exploratory = [
            item
            for item in selected.selected
            if item.opportunity.mode is OpportunityMode.EXPLORATION
        ]
        self.assertLessEqual(len(exploratory), 1)
        kinds = {item.opportunity.opportunity_kind for item in selected.selected}
        self.assertGreaterEqual(len(kinds), 2)
        with factory.open() as uow:
            self.assertEqual(
                len(uow.worker_results.list_for_research_run("run-1")),
                attempts_before,
            )
            self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
            self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.commit()

    def test_part_b_snapshot_change_time_differential_and_reload(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")
        self._execute(factory, "exp-2", "beta")
        echoes = self._echo_observations(factory)
        obs_a = echoes["alpha"]
        obs_b = echoes["beta"]
        captured_a, snap_a = CaptureDiagnosticSnapshot(factory, clock=FixedClock()).execute(
            CaptureDiagnosticSnapshotCommand(
                research_run_id="run-1",
                target_identity="target-1",
                observation_ids=(obs_a,),
                snapshot_id="snap-1",
                captured_at=NOW,
            )
        )
        captured_b, snap_b = CaptureDiagnosticSnapshot(factory, clock=FixedClock()).execute(
            CaptureDiagnosticSnapshotCommand(
                research_run_id="run-1",
                target_identity="target-1",
                observation_ids=(obs_b,),
                snapshot_id="snap-2",
                captured_at=T2,
            )
        )
        self.assertEqual(captured_a, SnapshotOutcome.CAPTURED)
        self.assertEqual(captured_b, SnapshotOutcome.CAPTURED)
        assert snap_a is not None and snap_b is not None
        change = CompareDiagnosticSnapshots(factory, clock=FixedClock()).execute(
            CompareDiagnosticSnapshotsCommand(
                research_run_id="run-1",
                baseline_snapshot_id="snap-1",
                variant_snapshot_id="snap-2",
            )
        )
        self.assertEqual(change.outcome, ChangeOutcome.COMPARED)
        assert change.change_event is not None
        compared = CompareDiagnosticDifferential(factory, clock=FixedClock()).execute(
            CompareDiagnosticDifferentialCommand(
                case=DifferentialCase(
                    case_id="case-time-1",
                    research_run_id="run-1",
                    baseline_observation_ids=(obs_a,),
                    variant_observation_ids=(obs_b,),
                    changed_dimensions=(
                        DifferentialDimension.TIME,
                        DifferentialDimension.INPUT,
                    ),
                    common_dimensions=(DifferentialDimension.ACTION,),
                    baseline_snapshot_id="snap-1",
                    variant_snapshot_id="snap-2",
                )
            )
        )
        self.assertEqual(compared.outcome, DifferentialOutcome.COMPARED)
        selected = SelectResearchOpportunities(factory, clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        self.assertTrue(
            any(
                change.change_event.change_event_id in item.opportunity.source_refs
                for item in selected.selected
            )
        )
        opportunity_id = next(
            item.opportunity.opportunity_id for item in selected.selected
        )
        proposed = ProposeResearchHypothesis(
            factory, ScriptedModelPort(), clock=FixedClock()
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="What diagnostic behavior changed between snapshot t1 and t2?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-gate09",
                opportunity_id=opportunity_id,
                change_event_id=change.change_event.change_event_id,
            )
        )
        self.assertEqual(proposed.outcome, AdmissionOutcome.ADMITTED)
        change_item = proposed.context.item_by_id(change.change_event.change_event_id)
        assert change_item is not None
        self.assertEqual(change_item.epistemic_class, EpistemicClass.DERIVED_FACT)
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                snapshot = uow.snapshots.get("snap-1")
                assert snapshot is not None
                self.assertEqual(snapshot.target_identity, "target-1")
                event = uow.change_events.get(change.change_event.change_event_id)
                assert event is not None
                self.assertEqual(event.baseline_snapshot_id, "snap-1")
                self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
                self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
                self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
                uow.commit()
        finally:
            reloaded.dispose()
        assert self.engine is not None
        with self.engine.connect() as connection:
            trans = connection.begin()
            with self.assertRaises(DBAPIError):
                connection.execute(
                    text(
                        "UPDATE snapshot SET target_identity = 'tamper' "
                        "WHERE snapshot_id = 'snap-1'"
                    )
                )
            trans.rollback()

    def test_rollback_leaves_no_partial_opportunity(self) -> None:
        factory = self._factory()
        self._execute(factory, "exp-1", "alpha")

        class FailingOpportunityUoW(PostgresUnitOfWork):
            def __enter__(self):
                uow = super().__enter__()
                original = uow.research_opportunities.insert

                def boom(record):
                    original(record)
                    raise PersistenceError("injected persistence failure")

                uow.research_opportunities.insert = boom  # type: ignore[method-assign]
                return uow

        class FailingFactory:
            def __init__(self, engine):
                self._engine = engine

            def open(self):
                return FailingOpportunityUoW(self._engine)

        assert self.engine is not None
        with self.assertRaises(PersistenceError):
            SelectResearchOpportunities(
                FailingFactory(self.engine), clock=FixedClock()
            ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        with factory.open() as reload:
            self.assertEqual(reload.research_opportunities.list_for_research_run("run-1"), [])
            self.assertEqual(reload.research_selections.list_for_research_run("run-1"), [])
            reload.commit()

    def test_migration_head_includes_exploration_temporal_tables(self) -> None:
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
        self.assertEqual(version, "a44_001_oast_correlation")
        self.assertIn("research_opportunity", tables)
        self.assertIn("research_selection", tables)
        self.assertIn("snapshot", tables)
        self.assertIn("snapshot_member", tables)
        self.assertIn("change_event", tables)


if __name__ == "__main__":
    unittest.main()
