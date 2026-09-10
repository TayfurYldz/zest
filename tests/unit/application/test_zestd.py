from __future__ import annotations

import ast
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
)
from zest.application.errors import ApplicationError
from zest.application.operator_errors import (
    OperatorError,
    OperatorErrorCode,
)
from zest.application.lease_fencing import (
    LeaseFencedWorkerPort,
    SingleRunFencedUowFactory,
)
from zest.application.local_run_supervisor import LocalRunSupervisor
from zest.application.orchestration_config import fingerprint_for_start
from zest.application.orchestration_lease import LeaseConfig
from zest.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.application.zestd import ZestdRuntime
from zest.application.runtime_instance import (
    ENGINE_VERSION,
    register_runtime_instance,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import DatabaseUnavailableError, LeaseFencingError
from zest.data.records import (
    BudgetConsumptionRecord,
    ExecutionAttemptRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ResearchOrchestrationRecord,
    ScopeRuleV2Record,
)
from zest.platform.health import ComponentHealth, HealthCheck
from zest.research.model_runtime import api_runtime_identity
from zest.research.orchestration import OrchestrationBounds, OrchestrationState
from zest.research.routing import CandidateLocality, RuntimeCandidate
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    completed_diagnostic_outcome,
    invocation_outcome,
)
from support.spine import CREATED_AT, seed_authorization_run
from zest.platform.worker import InvocationStatus

TARGET = "https://example.com/"
REPO_ROOT = Path(__file__).resolve().parents[3]


class FixedClock:
    def now(self):
        return CREATED_AT


def _healthy_browser_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "ok"),
        available_capabilities=frozenset({"diagnostic.echo", "browser.page"}),
        browser_containment=HealthCheck("browser-containment", ComponentHealth.HEALTHY, "ready"),
    )


def _completed_worker_outcome(request):
    outcome = completed_diagnostic_outcome(request)
    if request.get("worker_capability") != "browser.page":
        return outcome
    worker_result = dict(outcome.worker_result or {})
    worker_result["worker_id"] = "local-python-browser"
    worker_result["raw_result"] = {
        "attempted_network_requests": 0,
        "browser_context_reference": "ctx-1",
        "page_reference": "page-1",
        "snapshot_fingerprint": "fp-1",
        "normalized_url": TARGET,
        "path": "/",
        "ready_state": "complete",
        "frame_count": 1,
        "controls": [],
        "network_events": [],
        "snapshot_schema_version": "browser.page.snapshot.v1",
    }
    return replace(outcome, worker_result=worker_result)


def _healthy_model() -> ModelReadinessInput:
    candidate = RuntimeCandidate(
        identity=api_runtime_identity(adapter_id="fake", runtime_id="fake"),
        available=True,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.LOCAL,
    )
    return ModelReadinessInput(
        candidate=candidate,
        health=HealthCheck("model", ComponentHealth.HEALTHY, "ok"),
    )


def _ok_schema() -> SchemaHealthInput:
    return SchemaHealthInput(at_expected_head=True, detail="ok")


def _policy() -> ProgramPolicyRecord:
    return ProgramPolicyRecord(
        program_id="prog-1",
        loopback_fixture=False,
        max_response_bytes=4096,
        timeout_ms=2000,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        action_policy={
            "run": {
                "target_reference": TARGET,
                "research_question": "diagnostic echo",
            },
            "orchestration": {
                "max_cycles": 50,
                "max_experiments": 50,
                "max_model_calls": 4,
                "max_worker_invocations": 4,
                "max_elapsed_ms": 60_000,
                "max_selected_opportunities": 1,
                "max_runtime_fallback": 0,
                "side_effect_ceiling": 0,
                "allow_repeated_control_experiments": True,
            },
        },
    )


def _seed() -> _Store:
    store = _Store()
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
    store.program_policies["prog-1"] = _policy()
    store.scope_rules_v2["rule-allow"] = ScopeRuleV2Record(
        rule_id="rule-allow",
        program_id="prog-1",
        effect=ScopeRuleEffect.ALLOW,
        scheme="https",
        host="example.com",
        source_reference="scope-src",
        created_at=CREATED_AT,
    )
    return store


def _runtime(
    store: _Store,
    worker=None,
    *,
    cadence_seconds: float = 0.05,
    lease_config: LeaseConfig | None = None,
) -> ZestdRuntime:
    factory = FakeUnitOfWorkFactory(store=store)
    return ZestdRuntime(
        factory,
        worker or RecordingWorkerPort(store=store, handler=_completed_worker_outcome),
        ScriptedModelPort(),
        lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
        cadence_seconds=cadence_seconds,
        probe_schema=_ok_schema,
        probe_worker=_healthy_browser_worker,
        probe_model=_healthy_model,
    )


def _blocking_tick(entered: threading.Event, release: threading.Event):
    def _tick(supervisor: LocalRunSupervisor):
        del supervisor
        entered.set()
        if not release.wait(timeout=15):
            raise AssertionError("blocking supervisor was not released")
        return None

    return _tick


def _orchestration_record(**overrides) -> ResearchOrchestrationRecord:
    bounds = OrchestrationBounds(
        max_cycles=1,
        max_experiments=1,
        max_model_calls=4,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    fingerprint = fingerprint_for_start(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference=TARGET,
        research_question="diagnostic echo",
        policy_version="orchestration.bounded.v1",
        bounds=bounds,
        routing_policy_version=None,
        scope_fp=None,
    )
    values = dict(
        research_run_id="run-1",
        state="RUNNING",
        cycle_number=1,
        last_phase="running",
        policy_version="orchestration.bounded.v1",
        max_cycles=1,
        max_experiments=1,
        max_model_calls=4,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        checkpoint_at=CREATED_AT,
        budget_id="budget-1",
        target_reference=TARGET,
        research_question="diagnostic echo",
        configuration_fingerprint=fingerprint,
        current_phase="CYCLE_READY",
    )
    values.update(overrides)
    return ResearchOrchestrationRecord(**values)


def _attempt(state: str, *, side_effect_level: int = 0) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id="ea-1",
        request_id="req-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        correlation_id="corr-1",
        worker_capability="diagnostic.echo",
        action="echo",
        target_reference=TARGET,
        budget_id="budget-1",
        side_effect_level=side_effect_level,
        authorization_decision_reference="ae-1",
        state=state,
        created_at=CREATED_AT,
        authorized_at=CREATED_AT,
    )


class RuntimeInstanceTests(unittest.TestCase):
    def test_each_registration_gets_a_new_id(self) -> None:
        store = _Store()
        factory = FakeUnitOfWorkFactory(store=store)
        first = register_runtime_instance(
            factory, host_identity="host-a", process_id="1", clock=FixedClock()
        )
        second = register_runtime_instance(
            factory, host_identity="host-a", process_id="2", clock=FixedClock()
        )
        self.assertNotEqual(first.runtime_instance_id, second.runtime_instance_id)
        self.assertEqual(first.engine_version, ENGINE_VERSION)
        self.assertEqual(first.status, "STARTING")
        self.assertNotIn("token", first.capabilities_summary)
        self.assertNotIn("password", first.capabilities_summary)


class ClassifyRuntimeRecoveryTests(unittest.TestCase):
    def test_authorized_never_dispatched_requires_reauthorization(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record()
        store.execution_attempts["ea-1"] = _attempt("AUTHORIZED")
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.SAFE_RETRY_AFTER_REAUTHORIZATION)

    def test_dispatching_is_reconciliation_required(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record()
        store.execution_attempts["ea-1"] = _attempt("DISPATCHING")
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.RECONCILIATION_REQUIRED)

    def test_side_effectful_unknown_is_human_required(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record()
        store.execution_attempts["ea-1"] = _attempt("UNKNOWN_OUTCOME", side_effect_level=2)
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.HUMAN_REQUIRED)

    def test_completed_attempt_is_safe_resume(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record()
        store.execution_attempts["ea-1"] = _attempt("COMPLETED")
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.SAFE_RESUME)

    def test_recovery_classifier_is_reentrant(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record(
            state="READY"
        )

        classifier = ClassifyRuntimeRecovery(
            FakeUnitOfWorkFactory(store)
        )

        first = classifier.execute("run-1")
        second = classifier.execute("run-1")

        self.assertEqual(
            first.action,
            RuntimeRecoveryAction.SAFE_RESUME,
        )
        self.assertEqual(
            second.action,
            RuntimeRecoveryAction.SAFE_RESUME,
        )

    def test_paused_is_not_resumed(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record(state="PAUSED")
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.DO_NOT_RESUME)

    def test_stale_running_is_not_operational_failure(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record(state="RUNNING")
        decision = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(decision.action, RuntimeRecoveryAction.SAFE_RESUME)
        self.assertNotEqual(decision.action.value, "MARK_OPERATIONAL_FAILURE")


class LeaseFencingCompositionTests(unittest.TestCase):
    def test_stale_epoch_cannot_save_or_dispatch(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        controller = AutonomousResearchController(
            factory, RecordingWorkerPort(store=store), ScriptedModelPort()
        )
        controller.start(
            StartAutonomousResearchCommand(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference=TARGET,
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "src"),),
                    ambiguous=False,
                ),
                bounds=OrchestrationBounds(
                    max_cycles=1,
                    max_experiments=1,
                    max_model_calls=4,
                    max_worker_invocations=4,
                    max_elapsed_ms=60_000,
                    max_selected_opportunities=1,
                    max_runtime_fallback=0,
                    side_effect_ceiling=0,
                    allow_repeated_control_experiments=True,
                ),
                research_question="diagnostic echo",
            )
        )
        with factory.open() as uow:
            acquired = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(acquired.record.lease_epoch, 1)
        with factory.open() as uow:
            current = uow.research_orchestrations.get("run-1")
            uow.research_orchestrations.save(
                replace(
                    current,
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
            stolen = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-b", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(stolen.record.lease_epoch, 2)
        stale_factory = SingleRunFencedUowFactory(
            factory, owner_runtime_instance_id="owner-a", lease_epoch=1
        )
        with factory.open() as uow:
            current = uow.research_orchestrations.get("run-1")
            uow.rollback()
        with self.assertRaises(LeaseFencingError):
            with stale_factory.open() as uow:
                uow.research_orchestrations.save(replace(current, last_phase="stale"))
                uow.commit()
        worker = LeaseFencedWorkerPort(
            RecordingWorkerPort(store=store),
            factory,
            research_run_id="run-1",
            owner_runtime_instance_id="owner-a",
            lease_epoch=1,
        )
        with self.assertRaises(LeaseFencingError):
            worker.invoke({"contract_version": "v1"})


class ZestdRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.drain(join_timeout=1)

    def test_start_and_duplicate_start_have_one_owner(self) -> None:
        store = _seed()
        self._runtime = _runtime(store, cadence_seconds=30)
        self._runtime.start_process()
        first = self._runtime.start_run("run-1")
        second = self._runtime.start_run("run-1")
        self.assertEqual(first.state, OrchestrationState.READY.value)
        self.assertEqual(second.state, store.research_orchestrations["run-1"].state)
        self.assertTrue(self._runtime.is_supervising("run-1"))
        owners = {
            store.research_orchestrations["run-1"].owner_runtime_instance_id
        }
        self.assertEqual(owners, {self._runtime.runtime_instance_id})

    def test_pause_resume_cancel_and_terminal_reject(self) -> None:
        store = _seed()
        self._runtime = _runtime(
            store,
            cadence_seconds=30,
        )
        self._runtime.start_process()

        # Operator lifecycle is the subject here. Disable autonomous
        # research work while preserving the real supervisor barrier.
        with patch.object(
            LocalRunSupervisor,
            "_tick_once",
            return_value=None,
        ):
            self._runtime.start_run("run-1")

            supervisor = (
                self._runtime._registry.supervisor(
                    "run-1"
                )
            )
            self.assertIsNotNone(supervisor)

            paused = self._runtime.pause_run(
                "run-1"
            )
            self.assertEqual(
                paused.state,
                OrchestrationState.PAUSED.value,
            )
            self.assertTrue(
                self._runtime.is_supervising(
                    "run-1"
                )
            )

            resumed = self._runtime.resume_run(
                "run-1"
            )
            self.assertEqual(
                resumed.state,
                OrchestrationState.READY.value,
            )
            self.assertIs(
                self._runtime._registry.supervisor(
                    "run-1"
                ),
                supervisor,
            )

            cancelled = self._runtime.cancel_run(
                "run-1"
            )
            self.assertEqual(
                cancelled.state,
                OrchestrationState.COMPLETED.value,
            )

            again = self._runtime.cancel_run(
                "run-1"
            )
            self.assertEqual(
                again.state,
                OrchestrationState.COMPLETED.value,
            )
            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].stop_reason,
                "OPERATOR_CANCELLED",
            )

    def test_pause_waits_for_inflight_tick_before_claiming_paused(
        self,
    ) -> None:
        store = _seed()
        entered = threading.Event()
        release = threading.Event()

        self._runtime = _runtime(
            store,
            cadence_seconds=30,
        )
        self._runtime.start_process()

        results = []
        errors = []
        pause_called = threading.Event()

        with patch.object(
            LocalRunSupervisor,
            "_tick_once",
            new=_blocking_tick(
                entered,
                release,
            ),
        ):
            self._runtime.start_run("run-1")
            self.assertTrue(
                entered.wait(timeout=10)
            )

            supervisor = (
                self._runtime._registry.supervisor(
                    "run-1"
                )
            )
            self.assertIsNotNone(supervisor)

            def _pause():
                pause_called.set()
                try:
                    results.append(
                        self._runtime.pause_run(
                            "run-1"
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(
                target=_pause,
            )
            thread.start()

            self.assertTrue(
                pause_called.wait(timeout=5)
            )

            # The in-flight tick still owns the barrier. The operator
            # command has been issued but PAUSED cannot yet be claimed.
            self.assertTrue(thread.is_alive())
            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].state,
                OrchestrationState.READY.value,
            )

            release.set()
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(
                results[0].state,
                OrchestrationState.PAUSED.value,
            )

            persisted = (
                store.research_orchestrations[
                    "run-1"
                ]
            )

            self.assertEqual(
                persisted.state,
                OrchestrationState.PAUSED.value,
            )
            self.assertEqual(
                persisted.owner_runtime_instance_id,
                self._runtime.runtime_instance_id,
            )
            self.assertTrue(supervisor.is_running)
            self.assertTrue(
                self._runtime.is_supervising(
                    "run-1"
                )
            )

    def test_pause_timeout_does_not_claim_paused(
        self,
    ) -> None:
        store = _seed()
        entered = threading.Event()
        release = threading.Event()

        self._runtime = _runtime(
            store,
            cadence_seconds=30,
            lease_config=LeaseConfig(
                heartbeat_interval_seconds=0.05,
                lease_ttl_seconds=0.20,
            ),
        )
        self._runtime.start_process()

        try:
            with patch.object(
                LocalRunSupervisor,
                "_tick_once",
                new=_blocking_tick(
                    entered,
                    release,
                ),
            ):
                self._runtime.start_run("run-1")

                self.assertTrue(
                    entered.wait(timeout=10)
                )

                with self.assertRaises(
                    OperatorError
                ) as caught:
                    self._runtime.pause_run(
                        "run-1"
                    )

                self.assertEqual(
                    caught.exception.code,
                    OperatorErrorCode.RECONCILIATION_REQUIRED,
                )
                self.assertEqual(
                    store.research_orchestrations[
                        "run-1"
                    ].state,
                    OrchestrationState.READY.value,
                )
        finally:
            release.set()

    def test_cancel_waits_for_inflight_tick_before_terminalizing(
        self,
    ) -> None:
        store = _seed()
        entered = threading.Event()
        release = threading.Event()

        self._runtime = _runtime(
            store,
            cadence_seconds=30,
        )
        self._runtime.start_process()

        results = []
        errors = []
        cancel_called = threading.Event()

        with patch.object(
            LocalRunSupervisor,
            "_tick_once",
            new=_blocking_tick(
                entered,
                release,
            ),
        ):
            self._runtime.start_run("run-1")

            self.assertTrue(
                entered.wait(timeout=10)
            )

            def _cancel():
                cancel_called.set()
                try:
                    results.append(
                        self._runtime.cancel_run(
                            "run-1"
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(
                target=_cancel,
            )
            thread.start()

            self.assertTrue(
                cancel_called.wait(timeout=5)
            )

            self.assertTrue(thread.is_alive())
            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].state,
                OrchestrationState.READY.value,
            )

            release.set()
            thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(
                results[0].state,
                OrchestrationState.COMPLETED.value,
            )
            self.assertEqual(
                results[0].stop_reason,
                "OPERATOR_CANCELLED",
            )

    def test_resume_returns_authoritative_budget_exhausted_state(
        self,
    ) -> None:
        store = _seed()

        store.research_orchestrations[
            "run-1"
        ] = _orchestration_record(
            state=OrchestrationState.PAUSED.value,
            stop_reason="OPERATOR_PAUSED",
            last_phase="operator",
            current_phase="CYCLE_COMPLETE",
        )

        store.budget_consumptions[
            "cons-resume-exhausted"
        ] = BudgetConsumptionRecord(
            consumption_id="cons-resume-exhausted",
            budget_id="budget-1",
            research_run_id="run-1",
            resource_type="REQUEST",
            amount=20,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="resume-authority-regression",
            request_id="req-resume-exhausted",
        )

        self._runtime = _runtime(
            store,
            cadence_seconds=30,
        )
        self._runtime.start_process()

        resumed = self._runtime.resume_run(
            "run-1"
        )

        persisted = (
            store.research_orchestrations[
                "run-1"
            ]
        )

        self.assertEqual(
            resumed.state,
            OrchestrationState.BUDGET_EXHAUSTED.value,
        )
        self.assertEqual(
            resumed.stop_reason,
            "BUDGET_EXHAUSTED",
        )
        self.assertEqual(
            persisted.state,
            OrchestrationState.BUDGET_EXHAUSTED.value,
        )
        self.assertEqual(
            persisted.stop_reason,
            "BUDGET_EXHAUSTED",
        )
        self.assertFalse(
            self._runtime.is_supervising(
                "run-1"
            )
        )

    def test_retained_supervisor_resume_checks_exhausted_budget(
        self,
    ) -> None:
        store = _seed()

        self._runtime = _runtime(
            store,
            cadence_seconds=30,
        )
        self._runtime.start_process()

        with patch.object(
            LocalRunSupervisor,
            "_tick_once",
            return_value=None,
        ):
            self._runtime.start_run("run-1")

            paused = self._runtime.pause_run(
                "run-1"
            )
            self.assertEqual(
                paused.state,
                OrchestrationState.PAUSED.value,
            )
            self.assertTrue(
                self._runtime.is_supervising(
                    "run-1"
                )
            )

            store.budget_consumptions[
                "cons-retained-exhausted"
            ] = BudgetConsumptionRecord(
                consumption_id="cons-retained-exhausted",
                budget_id="budget-1",
                research_run_id="run-1",
                resource_type="REQUEST",
                amount=20,
                unit="count",
                occurred_at=CREATED_AT,
                provenance="retained-resume-regression",
                request_id="req-retained-exhausted",
            )

            resumed = self._runtime.resume_run(
                "run-1"
            )

            self.assertEqual(
                resumed.state,
                OrchestrationState.BUDGET_EXHAUSTED.value,
            )
            self.assertEqual(
                resumed.stop_reason,
                "BUDGET_EXHAUSTED",
            )
            self.assertEqual(
                store.research_orchestrations[
                    "run-1"
                ].state,
                OrchestrationState.BUDGET_EXHAUSTED.value,
            )

    def test_two_daemons_only_one_owner(self) -> None:
        store = _seed()
        entered = threading.Event()
        release = threading.Event()
        a = _runtime(store, cadence_seconds=30)
        b = _runtime(store, cadence_seconds=30)
        self._runtime = a
        a.start_process()
        b.start_process()
        try:
            with patch.object(LocalRunSupervisor, "tick", new=_blocking_tick(entered, release)):
                a.start_run("run-1")
                b.start_run("run-1")
                self.assertTrue(entered.wait(timeout=10))
            self.assertTrue(a.is_supervising("run-1"))
            self.assertFalse(b.is_supervising("run-1"))
            self.assertEqual(
                store.research_orchestrations["run-1"].owner_runtime_instance_id,
                a.runtime_instance_id,
            )
        finally:
            release.set()
            b.drain(join_timeout=1)

    def test_ten_two_daemon_races_have_exactly_one_owner(self) -> None:
        for _ in range(10):
            store = _seed()
            entered = threading.Event()
            release_a = threading.Event()
            release_b = threading.Event()
            a = _runtime(store, cadence_seconds=30)
            b = _runtime(store, cadence_seconds=30)
            a.start_process()
            b.start_process()
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def _start(runtime: ZestdRuntime) -> None:
                try:
                    barrier.wait(timeout=5)
                    runtime.start_run("run-1")
                except BaseException as exc:  # noqa: BLE001 - collect race failures
                    errors.append(exc)

            threads = [
                threading.Thread(target=_start, args=(a,)),
                threading.Thread(target=_start, args=(b,)),
            ]
            with patch.object(
                LocalRunSupervisor,
                "tick",
                new=_blocking_tick(entered, release_a),
            ):
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                try:
                    self.assertEqual(errors, [])
                    self.assertTrue(entered.wait(timeout=10))
                    owners = [
                        runtime.runtime_instance_id
                        for runtime in (a, b)
                        if runtime.is_supervising("run-1")
                    ]
                    self.assertEqual(len(owners), 1)
                    self.assertEqual(
                        store.research_orchestrations["run-1"].owner_runtime_instance_id,
                        owners[0],
                    )
                finally:
                    release_a.set()
                    release_b.set()
                    a.drain(join_timeout=1)
                    b.drain(join_timeout=1)

    def test_new_process_recovers_from_store_after_stop_without_drain(self) -> None:
        store = _seed()
        first = _runtime(store, cadence_seconds=30)
        first.start_process()
        first.start_run("run-1")
        first_id = first.runtime_instance_id
        first._stop.set()
        for run_id in first._registry.owned_run_ids():
            supervisor = first._registry.supervisor(run_id)
            if supervisor is not None:
                supervisor._lease_lost = True
                supervisor.request_stop()
                supervisor.join(1.0)
        with FakeUnitOfWorkFactory(store).open() as uow:
            current = uow.research_orchestrations.get("run-1")
            uow.research_orchestrations.save(
                replace(
                    current,
                    owner_runtime_instance_id=None,
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
            uow.commit()
        second = _runtime(store, cadence_seconds=30)
        self._runtime = second
        second.start_process()
        self.assertNotEqual(second.runtime_instance_id, first_id)
        recovered = store.research_orchestrations["run-1"]
        self.assertNotEqual(recovered.state, "FAILED_OPERATIONAL")
        if recovered.state in {"READY", "RUNNING"}:
            self.assertEqual(recovered.owner_runtime_instance_id, second.runtime_instance_id)

    def test_exhausted_budget_recovery_terminalizes_without_attach(self) -> None:
        store = _seed()

        store.research_orchestrations["run-1"] = _orchestration_record(
            state="READY"
        )

        store.budget_consumptions["cons-exhausted"] = BudgetConsumptionRecord(
            consumption_id="cons-exhausted",
            budget_id="budget-1",
            research_run_id="run-1",
            resource_type="REQUEST",
            amount=20,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="recovery-regression",
            request_id="req-exhausted",
        )

        worker = RecordingWorkerPort(store=store)

        self._runtime = _runtime(
            store,
            worker=worker,
            cadence_seconds=30,
        )

        self._runtime.start_process()

        recovered = store.research_orchestrations["run-1"]

        self.assertEqual(
            recovered.state,
            OrchestrationState.BUDGET_EXHAUSTED.value,
        )
        self.assertEqual(
            recovered.stop_reason,
            "BUDGET_EXHAUSTED",
        )
        self.assertIsNone(
            recovered.owner_runtime_instance_id
        )
        self.assertFalse(
            self._runtime.is_supervising("run-1")
        )
        self.assertEqual(worker.calls, [])

    def test_dispatching_recovery_does_not_retry(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record(state="RUNNING")
        store.execution_attempts["ea-1"] = _attempt("DISPATCHING")
        worker = RecordingWorkerPort(store=store)
        self._runtime = _runtime(store, worker=worker)
        self._runtime.start_process()
        self.assertFalse(self._runtime.is_supervising("run-1"))
        self.assertEqual(store.research_orchestrations["run-1"].state, "WAITING_HUMAN")
        self.assertEqual(worker.calls, [])

    def test_unknown_outcome_not_replayed(self) -> None:
        store = _seed()
        store.research_orchestrations["run-1"] = _orchestration_record(state="RUNNING")
        store.execution_attempts["ea-1"] = _attempt("UNKNOWN_OUTCOME", side_effect_level=2)
        worker = RecordingWorkerPort(store=store)
        self._runtime = _runtime(store, worker=worker)
        self._runtime.start_process()
        self.assertFalse(self._runtime.is_supervising("run-1"))
        self.assertEqual(worker.calls, [])

    def test_worker_failure_is_operational(self) -> None:
        store = _seed()
        worker = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.PROCESS_FAILED, reason="crash"),
        )
        self._runtime = _runtime(store, worker=worker)
        self._runtime.start_process()
        self._runtime.start_run("run-1")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            record = store.research_orchestrations.get("run-1")
            if record is not None and record.state in {
                "FAILED_OPERATIONAL",
                "COMPLETED",
                "WAITING_HUMAN",
                "BLOCKED",
            }:
                break
            time.sleep(0.05)
        record = store.research_orchestrations["run-1"]
        self.assertNotIn("FALSE", (record.stop_reason or "").upper())
        hypotheses = list(store.hypotheses.values())
        for hypothesis in hypotheses:
            self.assertNotEqual(getattr(hypothesis, "status", None), "FALSE")

    def test_pg_outage_refuses_new_dispatch(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)

        class DisconnectingFactory:
            def __init__(self) -> None:
                self.fail = False

            def open(self):
                if self.fail:
                    raise DatabaseUnavailableError("postgresql unavailable")
                return factory.open()

        wrapped = DisconnectingFactory()
        runtime = ZestdRuntime(
            wrapped,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
            cadence_seconds=0.05,
            probe_schema=_ok_schema,
            probe_worker=_healthy_browser_worker,
            probe_model=_healthy_model,
        )
        self._runtime = runtime
        runtime.start_process()
        wrapped.fail = True
        with self.assertRaisesRegex(ApplicationError, "postgresql unavailable"):
            runtime.start_run("run-1")
        health = runtime.health()
        self.assertTrue(health["pg_unavailable"])
        self.assertFalse(health["ready_for_start"])
        self.assertFalse(health["database"]["available_now"])
        self.assertEqual(health["database"]["health"], "UNAVAILABLE")
        self.assertNotIn("password", str(health).lower())
        wrapped.fail = False
        recovered = runtime.health()
        self.assertFalse(recovered["pg_unavailable"])
        self.assertEqual(recovered["runtime_instance_id"], health["runtime_instance_id"])

    def test_health_and_status_have_no_secrets(self) -> None:
        store = _seed()
        self._runtime = _runtime(store)
        self._runtime.start_process()
        self._runtime.start_run("run-1")
        rendered = str(self._runtime.health()) + str(self._runtime.run_status("run-1"))
        self.assertNotIn("sk-", rendered.lower())
        self.assertNotIn("password", rendered.lower())
        self.assertNotIn("api_key", rendered.lower())

    def test_execute_preflight_persists_and_does_not_authorize_start(self) -> None:
        store = _seed()
        self._runtime = _runtime(store)
        self._runtime.start_process()
        payload = self._runtime.execute_preflight("run-1")
        self.assertEqual(payload["status"], "READY_TO_START")
        self.assertFalse(payload["authorizes_start"])
        latest = self._runtime.latest_preflight("run-1")
        self.assertIsNotNone(latest)
        self.assertFalse(latest["authorizes_start"])
        self.assertEqual(len(store.preflight_reports), 1)

    def test_stale_preflight_does_not_authorize_start(self) -> None:
        from zest.application.operator_errors import OperatorError, OperatorErrorCode

        store = _seed()
        self._runtime = _runtime(store)
        self._runtime.start_process()
        payload = self._runtime.execute_preflight("run-1")
        self.assertEqual(payload["status"], "READY_TO_START")
        current = store.authorization_sources["as-1"]
        store.authorization_sources["as-1"] = replace(current, state="EXPIRED")
        with self.assertRaises(OperatorError) as caught:
            self._runtime.start_run("run-1")
        self.assertEqual(caught.exception.code, OperatorErrorCode.AUTHORIZATION_UNAVAILABLE)
        latest = self._runtime.latest_preflight("run-1")
        self.assertEqual(latest["status"], "NOT_READY")
        self.assertFalse(self._runtime.is_supervising("run-1"))

    def test_health_does_not_claim_ready_when_model_auth_missing(self) -> None:
        store = _seed()
        factory = FakeUnitOfWorkFactory(store=store)
        runtime = ZestdRuntime(
            factory,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
            cadence_seconds=0.05,
            probe_schema=_ok_schema,
            probe_worker=_healthy_browser_worker,
            probe_model=lambda: ModelReadinessInput(
                candidate=None,
                health=HealthCheck("model", ComponentHealth.AUTH_REQUIRED, "login required"),
            ),
        )
        self._runtime = runtime
        runtime.start_process()
        health = runtime.health()
        self.assertTrue(health["ok"])
        self.assertFalse(health["ready_for_start"])
        self.assertFalse(health["model"]["available_now"])
        self.assertEqual(health["model"]["health"], "AUTH_REQUIRED")
        self.assertEqual(health["model"]["gate_04b_is_not_availability"], True)

    def test_drain_marks_stopped_and_is_idempotent(self) -> None:
        store = _seed()
        worker = RecordingWorkerPort(store=store, handler=_completed_worker_outcome)
        self._runtime = _runtime(store, worker, cadence_seconds=30)
        self._runtime.start_process()
        instance_id = self._runtime.runtime_instance_id
        self._runtime.start_run("run-1")
        self.assertTrue(self._runtime.is_supervising("run-1"))
        deadline = time.time() + 2
        while time.time() < deadline and not worker.calls:
            time.sleep(0.05)
        before = len(worker.calls)
        self._runtime.drain(join_timeout=1)
        record = store.runtime_instances[instance_id]
        self.assertEqual(record.status, "STOPPED")
        self.assertIsNotNone(record.stopped_at)
        self._runtime.drain(join_timeout=1)
        again = store.runtime_instances[instance_id]
        self.assertEqual(again.status, "STOPPED")
        self.assertEqual(len(worker.calls), before)

    def test_idle_drain_persists_terminal_lifecycle(self) -> None:
        store = _seed()
        self._runtime = _runtime(store, cadence_seconds=30)
        self._runtime.start_process()
        instance_id = self._runtime.runtime_instance_id
        self.assertEqual(store.runtime_instances[instance_id].status, "RUNNING")
        self._runtime.drain(join_timeout=1)
        record = store.runtime_instances[instance_id]
        self.assertEqual(record.status, "STOPPED")
        self.assertIsNotNone(record.stopped_at)


class ZestdAuthorityAuditTests(unittest.TestCase):
    def test_daemon_sources_do_not_contain_research_authority(self) -> None:
        forbidden = (
            "HunterScore",
            "ProposeResearchHypothesis",
            "AdmitEvidence",
            "FinalizeFinding",
            "family matching",
        )
        files = [
            REPO_ROOT / "src/zest/application/zestd.py",
            REPO_ROOT / "src/zest/application/runtime_instance.py",
            REPO_ROOT / "src/zest/application/classify_runtime_recovery.py",
            REPO_ROOT / "src/zest/application/lease_fencing.py",
            REPO_ROOT / "src/zest/interface/operator_api.py",
            REPO_ROOT / "src/zest/interface/zestd.py",
            REPO_ROOT / "src/zest/interface/osd_client.py",
        ]
        for path in files:
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, msg=f"{path.name} contains {token}")
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            self.assertNotIn("zest.application.score_finding_severity", imported)
            self.assertNotIn("zest.application.finalize_finding", imported)

    def test_dashboard_does_not_own_supervisors(self) -> None:
        source = (REPO_ROOT / "src/zest/interface/dashboard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("LocalRunSupervisorRegistry", source)
        self.assertNotIn("ZestdRuntime", source)
        self.assertIn("ZEST_URL", source)
        self.assertIn("must not own run supervisors", source)
        self.assertIn("client_only", source)
