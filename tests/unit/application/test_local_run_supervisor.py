from __future__ import annotations

import unittest
from dataclasses import replace
from unittest import mock

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.discovery.runner import SurfaceDiscoveryStart
from zest.application.local_run_supervisor import (
    LocalRunSupervisor,
    LocalRunSupervisorRegistry,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import TerminalOrchestrationStateError
from zest.research.orchestration import OrchestrationBounds, OrchestrationState
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from support.fake_model import ScriptedModelPort, default_generator_output
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.spine import CREATED_AT, seed_authorization_run
from zest.data.records import IssuedBudgetRecord
from zest.platform.worker import InvocationStatus


class FixedClock:
    def now(self):
        return CREATED_AT


def _command() -> StartAutonomousResearchCommand:
    return StartAutonomousResearchCommand(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "src"),),
            ambiguous=False,
        ),
        bounds=OrchestrationBounds(
            max_cycles=1,
            max_experiments=1,
            max_model_calls=2,
            max_worker_invocations=2,
            max_elapsed_ms=1000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
        ),
    )


def _seed() -> _Store:
    store = _Store()
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=10,
        max_tool_calls=10,
        max_runtime_ms=1000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    return store


def _discovery_command() -> StartAutonomousResearchCommand:
    target = "http://127.0.0.1:9/"
    base = _command()
    return replace(
        base,
        bounds=replace(base.bounds, max_cycles=2, max_experiments=2),
        target_reference=target,
        surface_discovery=SurfaceDiscoveryStart(
            config=DiscoveryRunConfig(
                research_run_id="run-1",
                seed_target_reference=target,
                normalized_origin="http://127.0.0.1:9",
                normalized_path="/",
                bounds=DiscoveryBounds(
                    max_discovery_cycles=2,
                    max_frontier_items=2,
                    max_new_facts_per_cycle=1,
                    max_browser_actions=1,
                    max_http_transactions=1,
                    max_per_route_revisit=1,
                    max_identity_variants=0,
                    max_transition_depth=1,
                    max_graph_depth_from_seed=1,
                    max_template_inference_fanout=1,
                    max_duplicate_observations=1,
                ),
            ),
            compiled_scope=CompiledScope(
                rules=(
                    CompiledScopeRule(
                        rule_id="rule-allow",
                        effect=ScopeRuleEffect.ALLOW,
                        scheme="http",
                        host="127.0.0.1",
                        host_pattern=None,
                        port=9,
                        path_prefix=None,
                        source_reference="scope-src",
                        expires_at=None,
                    ),
                )
            ),
        ),
    )


class LocalRunSupervisorTests(unittest.TestCase):
    def _supervisor(self, store: _Store) -> LocalRunSupervisor:
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        controller.start(_command())
        return LocalRunSupervisor("run-1", controller, _command(), factory)

    def test_tick_delegates_one_step_and_stops_on_terminal_state(self) -> None:
        store = _seed()
        supervisor = self._supervisor(store)

        result = supervisor.tick()

        self.assertIsNotNone(result)
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)

    def test_controller_fault_is_durable_and_stops_supervisor(self) -> None:
        store = _seed()
        supervisor = self._supervisor(store)
        supervisor.controller.step = mock.Mock(
            side_effect=RuntimeError("planning failed password=not-persisted")
        )

        result = supervisor.tick()

        self.assertEqual(
            result.state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        self.assertEqual(
            store.research_orchestrations["run-1"].state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        self.assertTrue(supervisor._stop_event.is_set())
        self.assertEqual(len(store.run_faults), 1)
        fault = next(iter(store.run_faults.values()))
        self.assertTrue(fault.fatal)
        self.assertNotIn("not-persisted", fault.diagnostic_summary)
        self.assertEqual(fault.research_run_id, "run-1")

    def test_unsupported_capability_is_bounded_without_supervisor_fault_or_dispatch(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        worker = RecordingWorkerPort(store=store)

        def unsupported(request):
            output = default_generator_output(request)
            output["suggested_capability"] = "unsupported.capability"
            return output

        controller = AutonomousResearchController(
            factory,
            worker,
            ScriptedModelPort(generator=unsupported),
            clock=FixedClock(),
        )
        controller.start(_command())
        supervisor = LocalRunSupervisor("run-1", controller, _command(), factory)

        result = supervisor.tick()

        self.assertIn(result.state, {OrchestrationState.READY.value, OrchestrationState.RUNNING.value})
        self.assertEqual(worker.calls, [])
        self.assertEqual(store.run_faults, {})
        self.assertEqual(len(store.research_admissions), 1)
        admission = next(iter(store.research_admissions.values()))
        self.assertEqual(admission.outcome, "REJECTED_UNSUPPORTED")
        self.assertEqual(admission.reason_code, "UNSUPPORTED_CAPABILITY")
        self.assertIsNone(admission.admitted_hypothesis_id)

    def test_discovery_invocation_failure_stops_before_generic_planning(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        worker = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(
                InvocationStatus.START_FAILED,
                reason="browser process failed to start",
            ),
        )
        model = ScriptedModelPort()
        command = _discovery_command()
        controller = AutonomousResearchController(factory, worker, model, clock=FixedClock())
        controller.start(command)
        supervisor = LocalRunSupervisor("run-1", controller, command, factory)

        first = supervisor.tick()
        second = supervisor.tick()

        self.assertEqual(first.state, OrchestrationState.FAILED_OPERATIONAL.value)
        self.assertEqual(second.state, OrchestrationState.FAILED_OPERATIONAL.value)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(model.calls, [])
        self.assertEqual(len(store.worker_results), 0)
        self.assertEqual(len(store.observations), 0)
        self.assertEqual(len(store.evidence), 0)

    def test_non_runnable_state_does_not_step(self) -> None:
        store = _seed()
        supervisor = self._supervisor(store)
        supervisor.controller.pause("run-1")

        result = supervisor.tick()

        self.assertEqual(result.state, OrchestrationState.PAUSED.value)
        self.assertEqual(len(store.experiments), 0)

    def test_registry_deduplicates_running_supervisor(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        controller.start(_command())
        controller.pause("run-1")
        registry = LocalRunSupervisorRegistry()

        first = registry.start(
            research_run_id="run-1",
            controller=controller,
            command=_command(),
            uow_factory=factory,
            cadence_seconds=10,
        )
        second = registry.start(
            research_run_id="run-1",
            controller=controller,
            command=_command(),
            uow_factory=factory,
            cadence_seconds=10,
        )
        first.request_stop()
        first.join(1)

        self.assertIs(first, second)

    def test_registry_is_active_reflects_live_supervisor_only(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        controller.start(_command())
        controller.pause("run-1")
        registry = LocalRunSupervisorRegistry()

        self.assertFalse(registry.is_active("run-1"))

        supervisor = registry.start(
            research_run_id="run-1",
            controller=controller,
            command=_command(),
            uow_factory=factory,
            cadence_seconds=10,
        )
        self.assertTrue(registry.is_active("run-1"))

        supervisor.request_stop()
        supervisor.join(2)
        self.assertFalse(registry.is_active("run-1"))

    def test_tick_treats_a_terminal_race_as_stop_not_a_crash(self) -> None:
        """RT-A follow-through: if another writer (operator cancel, or
        reconciliation) finalizes the run terminally while this tick's
        step() is in flight, the resulting TerminalOrchestrationStateError
        must not escape tick() as an unhandled exception -- the persisted
        terminal state wins and this supervisor stops cleanly."""
        from dataclasses import replace

        store = _seed()
        supervisor = self._supervisor(store)
        current = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            current, state=OrchestrationState.RUNNING.value
        )

        def _racing_step(*_args, **_kwargs):
            finalized = store.research_orchestrations["run-1"]
            store.research_orchestrations["run-1"] = replace(
                finalized,
                state=OrchestrationState.COMPLETED.value,
                stop_reason="OPERATOR_CANCELLED",
            )
            raise TerminalOrchestrationStateError("race: run finalized concurrently")

        supervisor.controller.step = _racing_step  # type: ignore[method-assign]

        result = supervisor.tick()

        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, "OPERATOR_CANCELLED")


if __name__ == "__main__":
    unittest.main()
