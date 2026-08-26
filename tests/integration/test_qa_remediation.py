"""QA remediation PostgreSQL checks. Skipped when TEST_DATABASE_URL is absent.

Does not fabricate GATE 12/13 PASS.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.errors import OrchestrationIntegrityError
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.budget_ledger import ledger_totals
from zest.data.errors import BudgetOverspendError
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine, validate_test_database_url
from zest.data.records import (
    AuthorizationSourceRecord,
    BudgetConsumptionRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.research.orchestration import OrchestrationBounds
from integration.harness import FixedClock, PostgresUnitOfWorkFactory, alembic_upgrade, truncate_spine
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )


@unittest.skipUnless(TEST_URL, f"{TEST_DATABASE_URL_ENV} is required; skip is not PASS")
class QaRemediationPostgresTests(unittest.TestCase):
    engine = None
    factory = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        cls.factory = PostgresUnitOfWorkFactory(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            pool = None if cls.engine is None else cls.engine.pool
            if pool is not None and hasattr(pool, "checkedout"):
                leftover = pool.checkedout()
                if leftover:
                    raise AssertionError(
                        f"checked-out connections survived class lifecycle: {leftover}"
                    )
        finally:
            if cls.engine is not None:
                cls.engine.dispose()

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with self.factory.open() as uow:
            uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=FixedClock().now(), name="lab"))
            uow.authorization_sources.insert(
                AuthorizationSourceRecord(
                    authorization_source_id="as-1",
                    program_id="prog-1",
                    state="ACTIVE",
                    provenance_reference="written-auth-1",
                    created_at=FixedClock().now(),
                )
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id="run-1",
                    program_id="prog-1",
                    authorization_source_id="as-1",
                    initiated_by_actor_id="operator-1",
                    initiated_by_actor_type="HUMAN_OPERATOR",
                    started_at=FixedClock().now(),
                )
            )
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id="budget-1",
                    research_run_id="run-1",
                    max_requests=20,
                    max_tool_calls=20,
                    max_runtime_ms=10_000,
                    max_concurrency=1,
                    issued_at=FixedClock().now(),
                )
            )
            uow.commit()

    def test_persisted_bounds_reload_rejects_widening(self) -> None:
        controller = AutonomousResearchController(
            self.factory,
            RecordingWorkerPort(),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        command = StartAutonomousResearchCommand(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            scope=ScopeEvaluationInput(
                matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "src"),),
                ambiguous=False,
            ),
            bounds=OrchestrationBounds(
                max_cycles=1,
                max_experiments=2,
                max_model_calls=2,
                max_worker_invocations=4,
                max_elapsed_ms=60_000,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
                allow_repeated_control_experiments=True,
            ),
        )
        controller.start(command)
        with self.assertRaises(OrchestrationIntegrityError):
            controller.step(
                StartAutonomousResearchCommand(
                    research_run_id="run-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    scope=command.scope,
                    bounds=OrchestrationBounds(
                        max_cycles=3,
                        max_experiments=2,
                        max_model_calls=2,
                        max_worker_invocations=4,
                        max_elapsed_ms=60_000,
                        max_selected_opportunities=1,
                        max_runtime_fallback=0,
                        side_effect_ceiling=0,
                        allow_repeated_control_experiments=True,
                    ),
                )
            )

    def test_locked_issued_budget_row_is_allowance_authority(self) -> None:
        with self.factory.open() as uow:
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id="budget-zero",
                    research_run_id="run-1",
                    max_requests=0,
                    max_tool_calls=20,
                    max_runtime_ms=10_000,
                    max_concurrency=1,
                    issued_at=FixedClock().now(),
                )
            )
            uow.commit()
        lying_caller = IssuedBudgetRecord(
            budget_id="budget-zero",
            research_run_id="run-1",
            max_requests=20,
            max_tool_calls=20,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=FixedClock().now(),
        )
        record = BudgetConsumptionRecord(
            consumption_id="cons-lock-1",
            budget_id="budget-zero",
            research_run_id="run-1",
            resource_type="REQUEST",
            amount=1,
            unit="count",
            occurred_at=FixedClock().now(),
            provenance="qa-locked-row",
            request_id="worker-req-lock",
        )
        with self.factory.open() as uow:
            with self.assertRaises(BudgetOverspendError):
                uow.budget_consumptions.insert_within_allowance(record, lying_caller)
            uow.rollback()

    def _start_orchestration(self, *, max_model_calls: int = 2) -> None:
        controller = AutonomousResearchController(
            self.factory,
            RecordingWorkerPort(),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        controller.start(
            StartAutonomousResearchCommand(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference="target-1",
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "src"),),
                    ambiguous=False,
                ),
                bounds=OrchestrationBounds(
                    max_cycles=1,
                    max_experiments=2,
                    max_model_calls=max_model_calls,
                    max_worker_invocations=4,
                    max_elapsed_ms=60_000,
                    max_selected_opportunities=1,
                    max_runtime_fallback=0,
                    side_effect_ceiling=0,
                    allow_repeated_control_experiments=True,
                ),
            )
        )

    def test_replay_same_model_invocation_does_not_double_charge(self) -> None:
        self._start_orchestration(max_model_calls=1)
        issued = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=20,
            max_tool_calls=20,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=FixedClock().now(),
        )
        record = BudgetConsumptionRecord(
            consumption_id="cons-replay-1",
            budget_id="budget-1",
            research_run_id="run-1",
            resource_type="MODEL_CALL",
            amount=1,
            unit="count",
            occurred_at=FixedClock().now(),
            provenance="qa-replay",
            request_id="cycle:cycle-1:generator:1",
        )
        with self.factory.open() as uow:
            uow.budget_consumptions.insert_within_allowance(record, issued)
            uow.budget_consumptions.insert_within_allowance(
                BudgetConsumptionRecord(
                    consumption_id="cons-replay-2",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="MODEL_CALL",
                    amount=1,
                    unit="count",
                    occurred_at=FixedClock().now(),
                    provenance="qa-replay",
                    request_id="cycle:cycle-1:generator:1",
                ),
                issued,
            )
            uow.commit()
        with self.factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 0)

    def test_model_call_and_worker_request_are_separate(self) -> None:
        self._start_orchestration(max_model_calls=2)
        issued = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=20,
            max_tool_calls=20,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=FixedClock().now(),
        )
        with self.factory.open() as uow:
            uow.budget_consumptions.insert_within_allowance(
                BudgetConsumptionRecord(
                    consumption_id="cons-model-sep",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="MODEL_CALL",
                    amount=1,
                    unit="count",
                    occurred_at=FixedClock().now(),
                    provenance="qa-sep",
                    request_id="cycle:cycle-1:generator:1",
                ),
                issued,
            )
            uow.budget_consumptions.insert_within_allowance(
                BudgetConsumptionRecord(
                    consumption_id="cons-req-sep",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="REQUEST",
                    amount=2,
                    unit="count",
                    occurred_at=FixedClock().now(),
                    provenance="qa-sep",
                    request_id="worker-req-sep",
                ),
                issued,
            )
            uow.commit()
        with self.factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 2)

    def test_concurrent_model_call_cannot_overspend(self) -> None:
        self._start_orchestration(max_model_calls=1)
        issued = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=20,
            max_tool_calls=20,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=FixedClock().now(),
        )
        successes: list[int] = []
        overspends: list[int] = []
        lock = threading.Lock()

        def consume(index: int) -> None:
            record = BudgetConsumptionRecord(
                consumption_id=f"cons-concurrent-{index}",
                budget_id="budget-1",
                research_run_id="run-1",
                resource_type="MODEL_CALL",
                amount=1,
                unit="count",
                occurred_at=FixedClock().now(),
                provenance="qa-concurrent",
                request_id=f"cycle:cycle-1:generator:{index}",
            )
            try:
                with self.factory.open() as uow:
                    uow.budget_consumptions.insert_within_allowance(record, issued)
                    uow.commit()
                with lock:
                    successes.append(index)
            except BudgetOverspendError:
                with lock:
                    overspends.append(index)

        workers = [threading.Thread(target=consume, args=(i,)) for i in (1, 2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(overspends), 1)
        with self.factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)


if __name__ == "__main__":
    unittest.main()
