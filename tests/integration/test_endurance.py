"""Bounded endurance test. Diagnostic fixtures only. Not a security target."""

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
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.research.orchestration import OrchestrationBounds, OrchestrationState
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    truncate_spine,
)
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _command() -> StartAutonomousResearchCommand:
    return StartAutonomousResearchCommand(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=OrchestrationBounds(
            max_cycles=3,
            max_experiments=3,
            max_model_calls=30,
            max_worker_invocations=6,
            max_elapsed_ms=60_000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
            allow_repeated_control_experiments=True,
        ),
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class EnduranceOrchestrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"ENDURANCE PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_restart_midway_finishes_without_duplicate_authoritative_records(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        now = FixedClock().now()
        with factory.open() as uow:
            uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=now, name="lab"))
            uow.authorization_sources.insert(
                AuthorizationSourceRecord(
                    authorization_source_id="as-1",
                    program_id="prog-1",
                    state="ACTIVE",
                    provenance_reference="written-auth-1",
                    created_at=now,
                )
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id="run-1",
                    program_id="prog-1",
                    authorization_source_id="as-1",
                    initiated_by_actor_id="operator-1",
                    initiated_by_actor_type="HUMAN_OPERATOR",
                    started_at=now,
                )
            )
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id="budget-1",
                    research_run_id="run-1",
                    max_requests=30,
                    max_tool_calls=30,
                    max_runtime_ms=10_000,
                    max_concurrency=1,
                    issued_at=now,
                )
            )
            uow.commit()
        worker = RecordingWorkerPort()
        first = AutonomousResearchController(
            factory, worker, ScriptedModelPort(), clock=FixedClock()
        )
        first.start(_command())
        first.step(_command())
        second = AutonomousResearchController(
            factory, worker, ScriptedModelPort(), clock=FixedClock()
        )
        finished = second.run_bounded(_command())
        self.assertEqual(finished.state, OrchestrationState.COMPLETED.value)
        with factory.open() as uow:
            experiments = uow.experiments.list_for_research_run("run-1")
            hypotheses = uow.hypotheses.list_for_research_run("run-1")
            promotions = uow.promotion_runs.list_for_research_run("run-1")
            findings = uow.findings.list_for_research_run("run-1")
            uow.rollback()
        reproduction_ids = {
            item.reproduction_experiment_id
            for item in promotions
            if item.reproduction_experiment_id is not None
        }
        research_experiments = [
            item
            for item in experiments
            if item.experiment_id not in reproduction_ids
        ]
        self.assertEqual(len(research_experiments), 3)
        self.assertEqual(len(hypotheses), 3)
        self.assertEqual(len(findings), 0)
        self.assertEqual(len(worker.calls), 6)
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a42_001_preflight_report")
