"""SD-G4 token economy policy integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.budget_consumption import BudgetConsumptionRejected
from zest.application.budget_enforced_model import BudgetEnforcedModelPort
from zest.application.program_daily_budget import (
    AllocateProgramDailyBudget,
    AllocateProgramDailyBudgetCommand,
    ProgramDailyBudgetUsage,
    program_daily_budget_id,
)
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import ProgramPolicyRecord, ResearchOrchestrationRecord
from zest.research.model_port import ModelCallRequest, ModelRole
from zest.research.model_runtime import (
    AuthMode,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeKind,
)

TEST_URL = configured_test_url()


class _FixtureModelPort:
    def __init__(self, model_id: str = "local-fixture") -> None:
        self.model_id = model_id
        self.calls: list[ModelCallRequest] = []

    def complete(self, request: ModelCallRequest):
        from zest.research.model_port import ModelCallResult

        self.calls.append(request)
        return ModelCallResult(
            role=request.role,
            adapter_identity="fixture",
            provider_adapter_identity="fixture",
            structured_output={"ok": True},
            model_id=self.model_id,
            prompt_tokens=100,
            completion_tokens=50,
        )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG4TokenEconomyIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.research_orchestrations.insert(
                ResearchOrchestrationRecord(
                    research_run_id="run-1",
                    state="READY",
                    cycle_number=0,
                    last_phase="CYCLE_READY",
                    policy_version="v1",
                    max_cycles=10,
                    max_experiments=10,
                    max_model_calls=10,
                    max_worker_invocations=10,
                    max_elapsed_ms=60_000,
                    max_selected_opportunities=10,
                    max_runtime_fallback=0,
                    side_effect_ceiling=0,
                    allow_repeated_control_experiments=True,
                    created_at=NOW,
                    updated_at=NOW,
                    checkpoint_at=NOW,
                    budget_id="budget-1",
                    target_reference="target-1",
                    research_question="test",
                    configuration_fingerprint="0" * 64,
                    current_phase="CYCLE_READY",
                )
            )
            uow.commit()

    def _set_daily_limit(self, limit: int) -> None:
        from zest.data.postgres.unit_of_work import PostgresUnitOfWork

        with PostgresUnitOfWork(self.engine) as uow:
            existing = uow.program_policies.get("prog-1")
            if existing is None:
                record = ProgramPolicyRecord(
                    program_id="prog-1",
                    loopback_fixture=False,
                    max_response_bytes=4096,
                    timeout_ms=2000,
                    created_at=NOW,
                    updated_at=NOW,
                    daily_llm_budget_microdollars=limit,
                )
                uow.program_policies.insert(record)
            else:
                # Update via raw SQL since repository only supports insert/get.
                from sqlalchemy import text

                uow._connection.execute(
                    text(
                        "UPDATE program_policy SET daily_llm_budget_microdollars = :limit "
                        "WHERE program_id = :program_id"
                    ),
                    {"limit": limit, "program_id": "prog-1"},
                )
            uow.commit()

    def test_cheap_call_records_tokens_and_deny_when_limit_reached(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        self._set_daily_limit(10_000)
        today = datetime.now(timezone.utc).date().isoformat()
        AllocateProgramDailyBudget(uow_factory).execute(
            AllocateProgramDailyBudgetCommand(
                program_id="prog-1", budget_date=today, limit_microdollars=10_000
            )
        )

        inner = _FixtureModelPort()
        port = BudgetEnforcedModelPort(
            inner,
            uow_factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
        )
        port.complete(
            ModelCallRequest(
                role=ModelRole.GENERATOR,
                correlation_id="c1",
                context_fingerprint="fp",
                instructions="do work",
                payload={},
            )
        )

        usage = ProgramDailyBudgetUsage(uow_factory).execute("prog-1", today)
        self.assertEqual(usage.tokens_in, 100)
        self.assertEqual(usage.tokens_out, 50)

        # local-fixture costs 0, so remaining is full limit; set an artificially low
        # limit to force exhaustion on the next call by updating policy and adding spend.
        self._set_daily_limit(0)
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(
                ModelCallRequest(
                    role=ModelRole.GENERATOR,
                    correlation_id="c2",
                    context_fingerprint="fp",
                    instructions="do work",
                    payload={},
                )
            )

    def test_unset_daily_limit_denies_call(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        self._set_daily_limit(None)
        inner = _FixtureModelPort()
        port = BudgetEnforcedModelPort(
            inner,
            uow_factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
        )
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(
                ModelCallRequest(
                    role=ModelRole.GENERATOR,
                    correlation_id="c1",
                    context_fingerprint="fp",
                    instructions="do work",
                    payload={},
                )
            )


if __name__ == "__main__":
    unittest.main()
