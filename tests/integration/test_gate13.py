"""GATE 13 — Operational Readiness on real PostgreSQL.

Diagnostic workloads only. PASS does not mean real autonomous security research.
GATE 04B remains PENDING in this environment unless live comparison actually runs.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
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
from research_os.application.budget_consumption import (
    BudgetConsumptionRejected,
    RecordBudgetConsumption,
    RecordBudgetConsumptionCommand,
)
from research_os.application.budget_enforced_model import BudgetEnforcedModelPort
from research_os.application.operator_status import OperatorStatusSnapshot, render_operator_status
from research_os.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationResolution,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.budget_ledger import ledger_totals
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    ping_database,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.errors import BudgetOverspendError
from research_os.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    BudgetConsumptionRecord,
    ExecutionAttemptRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.interface.cli import build_status_snapshot
from research_os.interface.git_provenance import collect_source_provenance
from research_os.integrations.models.discovery import gate_04b_status
from research_os.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    RuntimeProcessError,
    StructuredOutputTransportError,
)
from research_os.research.orchestration import OrchestrationBounds
from research_os.safe_data import redact_secret_keys, sanitize_exception
from research_os.platform.observability import TelemetryEvent
from research_os.platform.worker_health import probe_local_python_worker
from research_os.integrations.models.cli_session import probe_codex_cli
from research_os.integrations.strix.adapter import probe_strix_runtime
from research_os.maturity import GATE_04B_STATUS, LIVE_MODEL_VALIDATED, PRODUCTION_READY, SUBSCRIPTION_OAUTH_STATUS
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort
from research_os.platform.artifacts import LocalArtifactStore
from research_os.platform.secrets import (
    EnvSecretResolver,
    SecretReference,
    SecretResolutionStatus,
    SecretScheme,
)
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    seed_authorized_spine,
    truncate_spine,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate13OperationalReadinessTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 13 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_budget_ledger_postgres_health_reconciliation_and_status(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        self.assertTrue(ping_database(self.engine))
        consume = RecordBudgetConsumption(factory, clock=FixedClock())
        first = consume.execute(
            RecordBudgetConsumptionCommand(
                budget_id="budget-1",
                research_run_id="run-1",
                resource_type="REQUEST",
                amount=1,
                unit="count",
                provenance="gate13",
                request_id="req-gate13",
            )
        )
        replay = consume.execute(
            RecordBudgetConsumptionCommand(
                budget_id="budget-1",
                research_run_id="run-1",
                resource_type="REQUEST",
                amount=1,
                unit="count",
                provenance="gate13-replay",
                request_id="req-gate13",
            )
        )
        self.assertTrue(replay.already_recorded)
        self.assertEqual(first.usage.requests, 1)
        with self.assertRaises(BudgetConsumptionRejected):
            consume.execute(
                RecordBudgetConsumptionCommand(
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="REQUEST",
                    amount=10,
                    unit="count",
                    provenance="gate13-overspend",
                    request_id="req-other",
                )
            )
        with factory.open() as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="ae-stale",
                    occurred_at=FixedClock().now(),
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
                    attempt_id="ea-stale",
                    request_id="req-stale",
                    experiment_id="exp-1",
                    research_run_id="run-1",
                    correlation_id="corr-stale",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    target_reference="target-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference="ae-stale",
                    state="AUTHORIZED",
                    created_at=FixedClock().now(),
                    authorized_at=FixedClock().now(),
                )
            )
            uow.commit()
        recon = ReconcileResearchRun(factory, clock=FixedClock()).execute(
            ReconcileResearchRunCommand("run-1")
        )
        self.assertTrue(
            any(item.resolution is ReconciliationResolution.SAFE_TO_RETRY for item in recon.items)
        )
        resolver = EnvSecretResolver({})
        secret = resolver.resolve(SecretReference(SecretScheme.ENV_REFERENCE, "MISSING_SECRET"))
        self.assertEqual(secret.status, SecretResolutionStatus.UNAVAILABLE)
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalArtifactStore(Path(tmp), max_bytes=64)
            ref = store.persist("diag.txt", b"diagnostic")
            self.assertEqual(store.verify("diag.txt", ref.sha256), b"diagnostic")
        text_status = render_operator_status(
            OperatorStatusSnapshot(
                postgresql="HEALTHY",
                worker={"local-python": "HEALTHY"},
                model_runtimes={
                    "API": "UNAVAILABLE",
                    "CLI_SESSION": "UNAVAILABLE",
                    "LOCAL_MODEL": "UNAVAILABLE",
                    "EXTERNAL_AGENT": "UNAVAILABLE",
                },
                strix="UNAVAILABLE",
                auth="no live credentials resolved",
                orchestrator="no active run",
                budget_ledger="append-only present",
                reconciliation="classified",
                observability="structured events",
                gate_04b="PENDING",
            )
        )
        self.assertIn("GATE 04B:", text_status)
        self.assertIn("PENDING", text_status)
        self.assertFalse(PRODUCTION_READY)
        self.assertFalse(LIVE_MODEL_VALIDATED)
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
        self.assertEqual(version, "a39_001_mr5_durability_uq")
        self.assertIn("research_orchestration", tables)
        self.assertIn("budget_consumption", tables)
        self.assertIn("research_cycle", tables)

    def _start_orchestration(self, factory, *, max_model_calls: int = 1) -> None:
        controller = AutonomousResearchController(
            factory,
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

    def test_model_budget_consumes_before_call_and_blocks_falsifier(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        self._start_orchestration(factory, max_model_calls=1)
        inner = ScriptedModelPort()
        port = BudgetEnforcedModelPort(
            inner,
            factory,
            budget_id="budget-1",
            research_run_id="run-1",
            cycle_id="cycle-gate13",
            clock=FixedClock(),
        )
        request = ModelCallRequest(
            role=ModelRole.GENERATOR,
            correlation_id="c-gate13",
            context_fingerprint="fp",
            instructions="propose",
            payload={"note": "ok"},
        )
        port.complete(request)
        self.assertEqual(len(inner.calls), 1)
        with self.assertRaises(BudgetConsumptionRejected):
            port.complete(
                ModelCallRequest(
                    role=ModelRole.FALSIFIER,
                    correlation_id="c-gate13",
                    context_fingerprint="fp",
                    instructions="challenge",
                    payload={"note": "ok"},
                )
            )
        self.assertEqual(len(inner.calls), 1)
        with factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 0)

    def test_failed_model_attempts_each_consume_once(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        cases = (
            ContentPolicyBlockedError("blocked"),
            ProviderAuthError("auth"),
            ProviderRateLimitError("rate"),
            ProviderTimeoutError("timeout"),
            RuntimeProcessError("proc"),
            StructuredOutputTransportError("schema"),
        )
        for index, error in enumerate(cases, start=1):
            with self.subTest(error=type(error).__name__):
                truncate_spine(self.engine)
                with factory.open() as uow:
                    seed_authorized_spine(uow)
                    uow.commit()
                self._start_orchestration(factory, max_model_calls=1)
                inner = ScriptedModelPort(error=error)
                port = BudgetEnforcedModelPort(
                    inner,
                    factory,
                    budget_id="budget-1",
                    research_run_id="run-1",
                    cycle_id=f"cycle-fail-{index}",
                    clock=FixedClock(),
                )
                with self.assertRaises(type(error)):
                    port.complete(
                        ModelCallRequest(
                            role=ModelRole.GENERATOR,
                            correlation_id=f"c-fail-{index}",
                            context_fingerprint="fp",
                            instructions="propose",
                            payload={"note": "ok"},
                        )
                    )
                self.assertEqual(len(inner.calls), 1)
                with factory.open() as uow:
                    totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
                    uow.rollback()
                self.assertEqual(totals.model_calls, 1)

    def test_a17_rollback_then_upgrade(self) -> None:
        assert TEST_URL is not None
        cfg = Config(str(_REPO / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", TEST_URL)
        try:
            command.downgrade(cfg, "a16_001_orchestration_operations")
            with self.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                has_fingerprint = connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'research_orchestration' "
                        "AND column_name = 'configuration_fingerprint'"
                    )
                ).scalar()
            self.assertEqual(version, "a16_001_orchestration_operations")
            self.assertIsNone(has_fingerprint)
        finally:
            command.upgrade(cfg, "head")
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a39_001_mr5_durability_uq")

    def test_nested_secrets_rejected_in_telemetry_and_exceptions(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            TelemetryEvent(
                event="test",
                outcome="ok",
                fields={"nested": {"token": "super-secret-value"}},
            )
        self.assertNotIn("super-secret-value", str(ctx.exception))
        redacted = redact_secret_keys(
            {"nested": [{"authorization": "Bearer abc"}]}, "status"
        )
        self.assertNotIn("Bearer abc", str(redacted))
        class Boom(Exception):
            headers = {"Authorization": "Bearer leaked-token"}

        safe = sanitize_exception(Boom("Bearer leaked-token"))
        self.assertNotIn("leaked-token", str(safe))

    def test_worker_health_and_missing_runtimes(self) -> None:
        check = probe_local_python_worker()
        self.assertEqual(check.health.value, "HEALTHY")
        self.assertFalse(check.contains_secrets)
        codex = probe_codex_cli()
        self.assertIsNotNone(codex.readiness)
        assert codex.readiness is not None
        if not codex.readiness.installed:
            self.assertFalse(codex.available)
            self.assertEqual(codex.readiness.stage.value, "NOT_INSTALLED")
        else:
            self.assertFalse(codex.readiness.benchmark_compatible)
        original_resolve = probe_strix_runtime.__globals__["resolve_executable"]
        probe_strix_runtime.__globals__["resolve_executable"] = lambda _name: None
        try:
            strix = probe_strix_runtime()
        finally:
            probe_strix_runtime.__globals__["resolve_executable"] = original_resolve
        self.assertFalse(strix["available"])
        self.assertFalse(strix["installed"])
        self.assertEqual(SUBSCRIPTION_OAUTH_STATUS, "NOT_IMPLEMENTED")
        self.assertEqual(GATE_04B_STATUS, "PENDING")
        self.assertFalse(PRODUCTION_READY)
        self.assertFalse(LIVE_MODEL_VALIDATED)

    def test_model_call_and_worker_request_are_separate(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        self._start_orchestration(factory, max_model_calls=2)
        issued = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=20,
            max_tool_calls=20,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=FixedClock().now(),
        )
        with factory.open() as uow:
            uow.budget_consumptions.insert_within_allowance(
                BudgetConsumptionRecord(
                    consumption_id="cons-model-sep",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="MODEL_CALL",
                    amount=1,
                    unit="count",
                    occurred_at=FixedClock().now(),
                    provenance="gate13-sep",
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
                    provenance="gate13-sep",
                    request_id="worker-req-sep",
                ),
                issued,
            )
            uow.commit()
        with factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)
        self.assertEqual(totals.worker_requests, 2)

    def test_concurrent_model_call_cannot_overspend(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        self._start_orchestration(factory, max_model_calls=1)
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
                provenance="gate13-concurrent",
                request_id=f"cycle:cycle-1:generator:{index}",
            )
            try:
                with factory.open() as uow:
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
        with factory.open() as uow:
            totals = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            uow.rollback()
        self.assertEqual(totals.model_calls, 1)

    def test_status_separates_application_and_test_databases(self) -> None:
        assert TEST_URL is not None
        snapshot = build_status_snapshot(
            env={
                "RESEARCH_OS_DATABASE_URL": (
                    "postgresql+psycopg://appuser:secret-pass@127.0.0.1:5432/research_os"
                ),
                TEST_DATABASE_URL_ENV: TEST_URL,
            }
        )
        self.assertNotEqual(snapshot.postgresql, snapshot.test_postgresql)
        self.assertIn("research_os_test", snapshot.test_dsn)
        self.assertNotIn("secret-pass", snapshot.application_dsn)
        self.assertNotIn("secret-pass", snapshot.test_dsn)
        text = render_operator_status(snapshot)
        self.assertIn("POSTGRESQL:", text)
        self.assertIn("TEST_POSTGRESQL:", text)
        self.assertNotIn("secret-pass", text)
        self.assertNotIn("appuser:secret-pass", snapshot.application_dsn)

    def test_live_source_provenance_blocks_authoritative_gate_04b_when_dirty(self) -> None:
        provenance = collect_source_provenance(_REPO)
        if provenance.git_dirty:
            self.assertFalse(provenance.authoritative)
            result = gate_04b_status(
                available_model_configurations=("openai", "anthropic"),
                executed_live_configurations=("openai", "anthropic"),
                comparable=True,
                harness_invariant_failed=False,
                runs_per_scenario=3,
                development_suite=True,
                source_authoritative=False,
            )
            self.assertNotEqual(result["status"], "PASS")
        else:
            self.assertTrue(provenance.authoritative)
        self.assertTrue(provenance.commit_hash)
        self.assertNotIn("password", provenance.source_fingerprint)


if __name__ == "__main__":
    unittest.main()

