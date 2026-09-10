from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.budget_enforced_model import BudgetEnforcedModelPort
from zest.application.budget_consumption import BudgetConsumptionRejected
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.budget_ledger import ledger_totals
from zest.data.records import BudgetConsumptionRecord, IssuedBudgetRecord
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelRole,
    ProviderAuthError,
    ProviderTimeoutError,
    StructuredOutputTransportError,
)
from zest.research.orchestration import OrchestrationBounds
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run
from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.program_daily_budget import (
    AllocateProgramDailyBudget,
    AllocateProgramDailyBudgetCommand,
    program_daily_budget_id,
)
from zest.data.records import ProgramPolicyRecord
from support.recording_worker import RecordingWorkerPort


class FixedClock:
    def now(self):
        return CREATED_AT


def _request(role: ModelRole = ModelRole.GENERATOR) -> ModelCallRequest:
    return ModelCallRequest(
        role=role,
        correlation_id="c1",
        context_fingerprint="fp",
        instructions="propose",
        payload={"note": "ok"},
    )


def _seed(store: _Store, *, max_model_calls: int = 1) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=20,
        max_tool_calls=20,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    from zest.application.autonomous_research_controller import AutonomousResearchController
    from support.recording_worker import RecordingWorkerPort

    factory = FakeUnitOfWorkFactory(store=store)
    controller = AutonomousResearchController(
        factory,
        RecordingWorkerPort(store=store),
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


class PreInvocationBudgetTests(unittest.TestCase):
    def test_generator_consumes_before_call_and_blocks_falsifier(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        inner = ScriptedModelPort()
        port = BudgetEnforcedModelPort(
            inner,
            FakeUnitOfWorkFactory(store=store),
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            clock=FixedClock(),
        )
        port.complete(_request(ModelRole.GENERATOR))
        self.assertEqual(len(inner.calls), 1)
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(_request(ModelRole.FALSIFIER))
        self.assertEqual(len(inner.calls), 1)
        totals = ledger_totals(list(store.budget_consumptions.values()))
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 0)

    def test_failed_calls_still_consume(self) -> None:
        cases = (
            ContentPolicyBlockedError("blocked"),
            ProviderTimeoutError("timeout"),
            ProviderAuthError("auth"),
            StructuredOutputTransportError("schema"),
        )
        for error in cases:
            with self.subTest(error=type(error).__name__):
                store = _Store()
                _seed(store, max_model_calls=1)
                inner = ScriptedModelPort(error=error)
                port = BudgetEnforcedModelPort(
                    inner,
                    FakeUnitOfWorkFactory(store=store),
                    budget_id="budget-1",
                    research_run_id="run-1",
                    cycle_id="cycle-1",
                    clock=FixedClock(),
                )
                with self.assertRaises(type(error)):
                    port.complete(_request())
                totals = ledger_totals(list(store.budget_consumptions.values()))
                self.assertEqual(totals.model_calls, 1)

    def test_replay_same_invocation_does_not_double_charge(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=2)
        inner = ScriptedModelPort()
        factory = FakeUnitOfWorkFactory(store=store)
        port = BudgetEnforcedModelPort(
            inner,
            factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            clock=FixedClock(),
        )
        port.complete(_request())
        # Force the same identity by resetting attempt counter and reusing request id path
        port._attempts[ModelRole.GENERATOR] = 0
        port.complete(_request())
        totals = ledger_totals(list(store.budget_consumptions.values()))
        self.assertEqual(totals.model_calls, 1)

    def test_runtime_namespaces_charge_physical_attempts_separately(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=2)
        factory = FakeUnitOfWorkFactory(store=store)

        primary = BudgetEnforcedModelPort(
            ScriptedModelPort(),
            factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            invocation_namespace="slot-0",
            clock=FixedClock(),
        )

        secondary = BudgetEnforcedModelPort(
            ScriptedModelPort(),
            factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            invocation_namespace="slot-1",
            clock=FixedClock(),
        )

        primary.complete(_request())
        secondary.complete(_request())

        totals = ledger_totals(
            list(
                store.budget_consumptions.values()
            )
        )

        self.assertEqual(
            totals.model_calls,
            2,
        )

        request_ids = {
            item.request_id
            for item
            in store.budget_consumptions.values()
            if item.resource_type
            == "MODEL_CALL"
        }

        self.assertEqual(
            len(request_ids),
            2,
        )

    def test_worker_request_does_not_increment_model_calls(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        store.budget_consumptions["req-1"] = BudgetConsumptionRecord(
            consumption_id="cons-1",
            budget_id="budget-1",
            research_run_id="run-1",
            resource_type="REQUEST",
            amount=3,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="worker",
            request_id="worker-req-1",
        )
        totals = ledger_totals(list(store.budget_consumptions.values()))
        self.assertEqual(totals.model_calls, 0)
        self.assertEqual(totals.worker_requests, 3)

    def test_model_call_does_not_increment_worker_requests(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        inner = ScriptedModelPort()
        port = BudgetEnforcedModelPort(
            inner,
            FakeUnitOfWorkFactory(store=store),
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            clock=FixedClock(),
        )
        port.complete(_request(ModelRole.GENERATOR))
        totals = ledger_totals(list(store.budget_consumptions.values()))
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 0)


class TokenAccountingTests(unittest.TestCase):
    def _seed_program_budget(self, store: _Store, limit_microdollars: int = 1_000_000) -> None:
        store.program_policies["prog-1"] = ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=True,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            daily_llm_budget_microdollars=limit_microdollars,
        )
        AllocateProgramDailyBudget(FakeUnitOfWorkFactory(store=store), clock=FixedClock()).execute(
            AllocateProgramDailyBudgetCommand(
                program_id="prog-1",
                budget_date=CREATED_AT.date().isoformat(),
                limit_microdollars=limit_microdollars,
            )
        )

    def test_token_consumption_recorded_on_program_daily_budget(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        self._seed_program_budget(store)
        inner = ScriptedModelPort(
            model_id="local-fixture", prompt_tokens=10, completion_tokens=5
        )
        port = BudgetEnforcedModelPort(
            inner,
            FakeUnitOfWorkFactory(store=store),
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
            clock=FixedClock(),
        )
        port.complete(_request(ModelRole.GENERATOR))
        daily_budget_id = program_daily_budget_id("prog-1", CREATED_AT.date().isoformat())
        daily_records = [
            item for item in store.budget_consumptions.values() if item.budget_id == daily_budget_id
        ]
        types = {item.resource_type for item in daily_records}
        self.assertIn("MODEL_TOKENS_IN", types)
        self.assertIn("MODEL_TOKENS_OUT", types)
        self.assertEqual(
            sum(item.amount for item in daily_records if item.resource_type == "MODEL_TOKENS_IN"),
            10,
        )
        self.assertEqual(
            sum(item.amount for item in daily_records if item.resource_type == "MODEL_TOKENS_OUT"),
            5,
        )
        for item in daily_records:
            self.assertEqual((item.resource_metadata or {}).get("model_id"), "local-fixture")

    def test_replay_same_invocation_does_not_double_record_tokens(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=2)
        self._seed_program_budget(store)
        inner = ScriptedModelPort(
            model_id="local-fixture", prompt_tokens=10, completion_tokens=5
        )
        factory = FakeUnitOfWorkFactory(store=store)
        port = BudgetEnforcedModelPort(
            inner,
            factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
            clock=FixedClock(),
        )
        port.complete(_request())
        port._attempts[ModelRole.GENERATOR] = 0
        port.complete(_request())
        daily_budget_id = program_daily_budget_id("prog-1", CREATED_AT.date().isoformat())
        daily_records = [
            item for item in store.budget_consumptions.values() if item.budget_id == daily_budget_id
        ]
        self.assertEqual(
            sum(item.amount for item in daily_records if item.resource_type == "MODEL_TOKENS_IN"),
            10,
        )
        self.assertEqual(
            sum(item.amount for item in daily_records if item.resource_type == "MODEL_TOKENS_OUT"),
            5,
        )

    def test_missing_program_daily_budget_denies_call(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        # Program policy exists but no daily envelope allocated.
        store.program_policies["prog-1"] = ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=True,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            daily_llm_budget_microdollars=1_000_000,
        )
        inner = ScriptedModelPort(model_id="local-fixture")
        port = BudgetEnforcedModelPort(
            inner,
            FakeUnitOfWorkFactory(store=store),
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
            clock=FixedClock(),
        )
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(_request())

    def test_unset_daily_limit_denies_call(self) -> None:
        store = _Store()
        _seed(store, max_model_calls=1)
        store.program_policies["prog-1"] = ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=True,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            daily_llm_budget_microdollars=None,
        )
        inner = ScriptedModelPort(model_id="local-fixture")
        port = BudgetEnforcedModelPort(
            inner,
            FakeUnitOfWorkFactory(store=store),
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-1",
            program_id="prog-1",
            clock=FixedClock(),
        )
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(_request())


if __name__ == "__main__":
    unittest.main()
