from __future__ import annotations

import ast
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pathsetup  # noqa: F401

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from research_os.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
)
from research_os.application.errors import ApplicationError
from research_os.application.lease_fencing import (
    LeaseFencedWorkerPort,
    SingleRunFencedUowFactory,
)
from research_os.application.orchestration_config import fingerprint_for_start
from research_os.application.orchestration_lease import LeaseConfig
from research_os.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.application.runtime_instance import (
    ENGINE_VERSION,
    register_runtime_instance,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.errors import LeaseFencingError, PersistenceError
from research_os.data.records import (
    ExecutionAttemptRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ResearchOrchestrationRecord,
    ScopeRuleV2Record,
)
from research_os.platform.health import ComponentHealth, HealthCheck
from research_os.research.model_runtime import api_runtime_identity
from research_os.research.orchestration import OrchestrationBounds, OrchestrationState
from research_os.research.routing import CandidateLocality, RuntimeCandidate
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.spine import CREATED_AT, seed_authorization_run
from research_os.platform.worker import InvocationStatus

TARGET = "https://example.com/"
REPO_ROOT = Path(__file__).resolve().parents[3]


class FixedClock:
    def now(self):
        return CREATED_AT


def _healthy_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "ok"),
        available_capabilities=frozenset({"diagnostic.echo"}),
    )


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


def _runtime(store: _Store, worker=None, *, cadence_seconds: float = 0.05) -> ResearchOsdRuntime:
    factory = FakeUnitOfWorkFactory(store=store)
    return ResearchOsdRuntime(
        factory,
        worker or RecordingWorkerPort(store=store),
        ScriptedModelPort(),
        lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
        cadence_seconds=cadence_seconds,
        probe_schema=_ok_schema,
        probe_worker=_healthy_worker,
        probe_model=_healthy_model,
    )


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


class ResearchOsdRuntimeTests(unittest.TestCase):
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
        self._runtime = _runtime(store, cadence_seconds=30)
        self._runtime.start_process()
        self._runtime.start_run("run-1")
        paused = self._runtime.pause_run("run-1")
        self.assertEqual(paused.state, OrchestrationState.PAUSED.value)
        resumed = self._runtime.resume_run("run-1")
        self.assertEqual(resumed.state, OrchestrationState.READY.value)
        cancelled = self._runtime.cancel_run("run-1")
        self.assertEqual(cancelled.state, OrchestrationState.COMPLETED.value)
        again = self._runtime.cancel_run("run-1")
        self.assertEqual(again.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(store.research_orchestrations["run-1"].stop_reason, "OPERATOR_CANCELLED")

    def test_two_daemons_only_one_owner(self) -> None:
        store = _seed()
        a = _runtime(store, cadence_seconds=30)
        b = _runtime(store, cadence_seconds=30)
        self._runtime = a
        a.start_process()
        b.start_process()
        try:
            a.start_run("run-1")
            b.start_run("run-1")
            self.assertTrue(a.is_supervising("run-1"))
            self.assertFalse(b.is_supervising("run-1"))
            self.assertEqual(
                store.research_orchestrations["run-1"].owner_runtime_instance_id,
                a.runtime_instance_id,
            )
        finally:
            b.drain(join_timeout=1)

    def test_ten_two_daemon_races_have_exactly_one_owner(self) -> None:
        for _ in range(10):
            store = _seed()
            a = _runtime(store, cadence_seconds=30)
            b = _runtime(store, cadence_seconds=30)
            a.start_process()
            b.start_process()
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def _start(runtime: ResearchOsdRuntime) -> None:
                try:
                    barrier.wait(timeout=5)
                    runtime.start_run("run-1")
                except BaseException as exc:  # noqa: BLE001 - collect race failures
                    errors.append(exc)

            threads = [
                threading.Thread(target=_start, args=(a,)),
                threading.Thread(target=_start, args=(b,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(errors, [])
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
                    raise PersistenceError("disconnected")
                return factory.open()

        wrapped = DisconnectingFactory()
        runtime = ResearchOsdRuntime(
            wrapped,
            RecordingWorkerPort(store=store),
            ScriptedModelPort(),
            lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
            cadence_seconds=0.05,
            probe_schema=_ok_schema,
            probe_worker=_healthy_worker,
            probe_model=_healthy_model,
        )
        self._runtime = runtime
        runtime.start_process()
        wrapped.fail = True
        runtime._pg_unavailable = True
        with self.assertRaisesRegex(ApplicationError, "postgresql unavailable"):
            runtime.start_run("run-1")

    def test_health_and_status_have_no_secrets(self) -> None:
        store = _seed()
        self._runtime = _runtime(store)
        self._runtime.start_process()
        self._runtime.start_run("run-1")
        rendered = str(self._runtime.health()) + str(self._runtime.run_status("run-1"))
        self.assertNotIn("sk-", rendered.lower())
        self.assertNotIn("password", rendered.lower())
        self.assertNotIn("api_key", rendered.lower())


class ResearchOsdAuthorityAuditTests(unittest.TestCase):
    def test_daemon_sources_do_not_contain_research_authority(self) -> None:
        forbidden = (
            "HunterScore",
            "ProposeResearchHypothesis",
            "AdmitEvidence",
            "FinalizeFinding",
            "family matching",
        )
        files = [
            REPO_ROOT / "src/research_os/application/research_osd.py",
            REPO_ROOT / "src/research_os/application/runtime_instance.py",
            REPO_ROOT / "src/research_os/application/classify_runtime_recovery.py",
            REPO_ROOT / "src/research_os/application/lease_fencing.py",
            REPO_ROOT / "src/research_os/interface/operator_api.py",
            REPO_ROOT / "src/research_os/interface/research_osd.py",
            REPO_ROOT / "src/research_os/interface/osd_client.py",
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
            self.assertNotIn("research_os.application.score_finding_severity", imported)
            self.assertNotIn("research_os.application.finalize_finding", imported)

    def test_dashboard_does_not_own_supervisors(self) -> None:
        source = (REPO_ROOT / "src/research_os/interface/dashboard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("LocalRunSupervisorRegistry", source)
        self.assertIn("RESEARCH_OSD_URL", source)
        self.assertIn("must not own run supervisors", source)
