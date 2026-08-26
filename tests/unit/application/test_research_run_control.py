from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.local_run_supervisor import LocalRunSupervisorRegistry
from zest.application.reconcile_research_run import ReconcileResearchRun
from zest.application.research_run_control import ResearchRunControl
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.research.orchestration import OrchestrationBounds, OrchestrationState
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_authorization_run
from zest.data.records import IssuedBudgetRecord, ResearchOrchestrationRecord


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


def _store() -> _Store:
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


class ResearchRunControlTests(unittest.TestCase):
    def test_start_start_is_idempotent_and_supervisor_is_shared(self) -> None:
        store = _store()
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
        control = ResearchRunControl(controller, registry, factory, cadence_seconds=10)

        first = control.start(_command())
        second = control.start(_command())
        control.cancel("run-1")

        self.assertEqual(first.state, OrchestrationState.PAUSED.value)
        self.assertEqual(second.state, OrchestrationState.PAUSED.value)
        self.assertEqual(len(store.research_orchestrations), 1)

    def test_pause_and_cancel_delegate_to_controller(self) -> None:
        store = _store()
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        # A real LocalRunSupervisorRegistry would spin up a live background
        # thread the instant start() reaches READY, racing this test's own
        # immediate pause()/cancel() calls to actually finish the bounded
        # run first (a pre-existing race, previously masked because pause/
        # cancel used to unconditionally overwrite state -- see RT-A).
        # This test is about delegation, not the real supervisor thread, so
        # use the no-op double already defined below for that purpose.
        control = ResearchRunControl(controller, _FakeSupervisors(active=False), factory)
        control.start(_command())
        paused = control.pause("run-1")
        cancelled = control.cancel("run-1")

        self.assertEqual(paused.state, OrchestrationState.PAUSED.value)
        self.assertEqual(cancelled.state, OrchestrationState.COMPLETED.value)


class _FakeSupervisors:
    """Duck-typed LocalRunSupervisorRegistry stand-in with no real threads."""

    def __init__(self, *, active: bool) -> None:
        self._active = active
        self.started: list[str] = []
        self.stopped: list[str] = []

    def is_active(self, research_run_id: str) -> bool:
        return self._active

    def start(self, *, research_run_id: str, **_kwargs) -> None:
        self.started.append(research_run_id)

    def stop(self, research_run_id: str) -> None:
        self.stopped.append(research_run_id)


def _plant_running_orchestration(store: _Store) -> None:
    store.research_orchestrations["run-1"] = ResearchOrchestrationRecord(
        research_run_id="run-1",
        state=OrchestrationState.RUNNING.value,
        cycle_number=1,
        last_phase="running",
        policy_version="orchestration.bounded.v1",
        max_cycles=1,
        max_experiments=1,
        max_model_calls=2,
        max_worker_invocations=2,
        max_elapsed_ms=1000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=False,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        checkpoint_at=CREATED_AT,
        budget_id="budget-1",
        target_reference="target-1",
        research_question="q",
        configuration_fingerprint="0" * 64,
        current_phase="CYCLE_READY",
    )


class ReconcileStaleRunningWiringTests(unittest.TestCase):
    """RT-B: `ReconcileResearchRun` must be invoked, and acted upon, by the
    production start() path -- not merely constructible in isolation."""

    def _control(self, store: _Store, *, supervisors) -> ResearchRunControl:
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        return ResearchRunControl(
            controller,
            supervisors,
            factory,
            reconciler=ReconcileResearchRun(factory, clock=FixedClock()),
        )

    def test_start_reconciles_a_crash_left_running_checkpoint(self) -> None:
        store = _store()
        _plant_running_orchestration(store)
        supervisors = _FakeSupervisors(active=False)
        control = self._control(store, supervisors=supervisors)

        result = control.start(_command())

        self.assertEqual(result.state, OrchestrationState.FAILED_OPERATIONAL.value)
        self.assertEqual(
            store.research_orchestrations["run-1"].state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        self.assertEqual(supervisors.started, [])

    def test_start_does_not_reconcile_a_run_this_process_already_owns(self) -> None:
        store = _store()
        _plant_running_orchestration(store)
        supervisors = _FakeSupervisors(active=True)
        control = self._control(store, supervisors=supervisors)

        result = control.start(_command())

        self.assertEqual(result.state, OrchestrationState.RUNNING.value)
        self.assertEqual(
            store.research_orchestrations["run-1"].state, OrchestrationState.RUNNING.value
        )

    def test_start_without_reconciler_leaves_stale_running_untouched(self) -> None:
        store = _store()
        _plant_running_orchestration(store)
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        control = ResearchRunControl(controller, _FakeSupervisors(active=False), factory)

        result = control.start(_command())

        self.assertEqual(result.state, OrchestrationState.RUNNING.value)


if __name__ == "__main__":
    unittest.main()
