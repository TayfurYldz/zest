"""Phase J research-osd against real PostgreSQL. SQLite is not a substitute."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from research_os.application.orchestration_lease import LeaseConfig
from research_os.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.application.runtime_instance import register_runtime_instance
from research_os.core.enums import ScopeRuleEffect
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    AuditEventRecord,
    ExecutionAttemptRecord,
    ProgramPolicyRecord,
    ResearchOrchestrationRecord,
    ScopeRuleV2Record,
)
from research_os.platform.health import ComponentHealth, HealthCheck
from research_os.research.model_runtime import api_runtime_identity
from research_os.research.orchestration import OrchestrationBounds
from research_os.research.routing import CandidateLocality, RuntimeCandidate
from research_os.application.orchestration_config import fingerprint_for_start
from integration.harness import alembic_upgrade, seed_authorized_spine, truncate_spine
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort
from sqlalchemy import text

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

TARGET = "https://example.com/"
NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)


def _healthy_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "ok"),
        available_capabilities=frozenset({"diagnostic.echo"}),
    )


def _healthy_model() -> ModelReadinessInput:
    return ModelReadinessInput(
        candidate=RuntimeCandidate(
            identity=api_runtime_identity(adapter_id="fake", runtime_id="fake"),
            available=True,
            authenticated=True,
            structured_output_compatible=True,
            locality=CandidateLocality.LOCAL,
        ),
        health=HealthCheck("model", ComponentHealth.HEALTHY, "ok"),
    )


def _runtime(engine) -> ResearchOsdRuntime:
    factory = PostgresUnitOfWork(engine)
    return ResearchOsdRuntime(
        factory,
        RecordingWorkerPort(),
        ScriptedModelPort(),
        lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
        cadence_seconds=30,
        probe_schema=lambda: SchemaHealthInput(True, "ok"),
        probe_worker=_healthy_worker,
        probe_model=_healthy_model,
    )


def _seed_policy_and_scope(uow: PostgresUnitOfWork) -> None:
    uow.program_policies.insert(
        ProgramPolicyRecord(
            program_id="prog-1",
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            created_at=NOW,
            updated_at=NOW,
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
    )
    uow.scope_rules_v2.insert(
        ScopeRuleV2Record(
            rule_id="rule-allow",
            program_id="prog-1",
            effect=ScopeRuleEffect.ALLOW.value,
            scheme="https",
            host="example.com",
            source_reference="scope-src",
            created_at=NOW,
        )
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class ResearchOsdPostgresTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(
            "DESTRUCTIVE PostgreSQL integration tests: TRUNCATE CASCADE against "
            f"{redacted_database_url(TEST_URL)}",
            flush=True,
        )
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            _seed_policy_and_scope(uow)
            uow.commit()
        self._runtimes: list[ResearchOsdRuntime] = []

    def tearDown(self) -> None:
        for runtime in self._runtimes:
            runtime.drain(join_timeout=1)

    def test_schema_head_is_runtime_instance_revision(self) -> None:
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
        self.assertIn("runtime_instance", tables)
        self.assertIn("preflight_report", tables)

    def test_runtime_instance_persists_and_new_start_gets_new_id(self) -> None:
        factory = PostgresUnitOfWork(self.engine)
        first = register_runtime_instance(
            factory, host_identity="host-a", process_id="1"
        )
        second = register_runtime_instance(
            factory, host_identity="host-a", process_id="2"
        )
        self.assertNotEqual(first.runtime_instance_id, second.runtime_instance_id)
        with factory.open() as uow:
            loaded = uow.runtime_instances.get(first.runtime_instance_id)
            active = uow.runtime_instances.list_active()
            uow.rollback()
        self.assertIsNotNone(loaded)
        self.assertGreaterEqual(len(active), 2)

    def test_two_daemons_one_owner_ten_times(self) -> None:
        for _ in range(10):
            truncate_spine(self.engine)
            with PostgresUnitOfWork(self.engine) as uow:
                seed_authorized_spine(uow)
                _seed_policy_and_scope(uow)
                uow.commit()
            a = _runtime(self.engine)
            b = _runtime(self.engine)
            self._runtimes = [a, b]
            a.start_process()
            b.start_process()
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def _start(runtime: ResearchOsdRuntime) -> None:
                try:
                    barrier.wait(timeout=5)
                    runtime.start_run("run-1")
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(target=_start, args=(a,)),
                threading.Thread(target=_start, args=(b,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertEqual(errors, [])
            owners = [runtime for runtime in (a, b) if runtime.is_supervising("run-1")]
            self.assertEqual(len(owners), 1)
            with PostgresUnitOfWork(self.engine) as uow:
                current = uow.research_orchestrations.get("run-1")
                uow.rollback()
            self.assertEqual(current.owner_runtime_instance_id, owners[0].runtime_instance_id)
            a.drain(join_timeout=1)
            b.drain(join_timeout=1)
            self._runtimes = []

    def test_new_process_recovers_after_lease_expiry(self) -> None:
        first = _runtime(self.engine)
        self._runtimes.append(first)
        first.start_process()
        first.start_run("run-1")
        first_id = first.runtime_instance_id
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE research_orchestration SET owner_runtime_instance_id = NULL, "
                    "lease_expires_at = NOW() - INTERVAL '1 second' "
                    "WHERE research_run_id = 'run-1'"
                )
            )
        first._stop.set()
        second = _runtime(self.engine)
        self._runtimes.append(second)
        second.start_process()
        self.assertNotEqual(second.runtime_instance_id, first_id)
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            uow.rollback()
        self.assertNotEqual(current.state, "FAILED_OPERATIONAL")
        if current.state in {"READY", "RUNNING"}:
            self.assertEqual(current.owner_runtime_instance_id, second.runtime_instance_id)

    def test_dispatching_ambiguity_is_not_replayed(self) -> None:
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
        with PostgresUnitOfWork(self.engine) as uow:
            uow.research_orchestrations.insert(
                ResearchOrchestrationRecord(
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
                    created_at=NOW,
                    updated_at=NOW,
                    checkpoint_at=NOW,
                    budget_id="budget-1",
                    target_reference=TARGET,
                    research_question="diagnostic echo",
                    configuration_fingerprint=fingerprint,
                    current_phase="CYCLE_READY",
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="ae-1",
                    occurred_at=NOW,
                    actor_id="control-plane",
                    actor_type="CONTROL_PLANE",
                    event_type="EXECUTION_DECISION",
                    subject_type="experiment",
                    subject_id="exp-1",
                    payload={"decision": "ALLOW", "not_dispatched": True},
                )
            )
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="ea-1",
                    request_id="req-1",
                    experiment_id="exp-1",
                    research_run_id="run-1",
                    correlation_id="corr-1",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    target_reference=TARGET,
                    budget_id="budget-1",
                    side_effect_level=1,
                    authorization_decision_reference="ae-1",
                    state="DISPATCHING",
                    created_at=NOW,
                    authorized_at=NOW,
                    dispatch_started_at=NOW,
                )
            )
            uow.commit()
        runtime = _runtime(self.engine)
        self._runtimes.append(runtime)
        runtime.start_process()
        self.assertFalse(runtime.is_supervising("run-1"))
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            attempts = uow.execution_attempts.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(current.state, "WAITING_HUMAN")
        self.assertEqual(attempts[0].state, "DISPATCHING")
