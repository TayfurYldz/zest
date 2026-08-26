"""Checkpoint 15 operator API / Preflight / console against real PostgreSQL."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.application.operator_errors import OperatorErrorCode
from zest.application.orchestration_lease import LeaseConfig
from zest.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.application.zestd import ZestdRuntime
from zest.core.enums import ScopeRuleEffect
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuthorizationSourceRecord,
    BudgetConsumptionRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from zest.interface.dashboard import collect_dashboard_payload
from zest.interface.operator_api import OperatorApiServer
from zest.platform.health import ComponentHealth, HealthCheck
from zest.research.model_runtime import api_runtime_identity
from zest.research.routing import CandidateLocality, RuntimeCandidate
from integration.harness import alembic_upgrade, truncate_spine
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort
from sqlalchemy import text

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
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


def _runtime(engine, *, probe_worker=None, probe_model=None) -> ZestdRuntime:
    factory = PostgresUnitOfWork(engine)
    return ZestdRuntime(
        factory,
        RecordingWorkerPort(),
        ScriptedModelPort(),
        lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
        cadence_seconds=30,
        probe_schema=lambda: SchemaHealthInput(True, "ok"),
        probe_worker=probe_worker or _healthy_worker,
        probe_model=probe_model or _healthy_model,
    )


def _seed_startable_run(uow: PostgresUnitOfWork) -> None:
    """Authorized run with no leftover hypothesis/experiment rows.

    Seeded hyp/exp without orchestration checkpoints make first-start attach
    fail Preflight reconciliation. That is existing reconcile behavior; this
    seed keeps Checkpoint 15 focused on the operator plane.
    """

    uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=NOW, name="lab"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="written-auth-1",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id="run-1",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=10,
            max_tool_calls=10,
            max_runtime_ms=10_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )
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


def _serve(runtime: ZestdRuntime) -> tuple[OperatorApiServer, threading.Thread, str]:
    server = OperatorApiServer(runtime, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.address
    return server, thread, f"http://{host}:{port}"


def _stop(server: OperatorApiServer, thread: threading.Thread) -> None:
    server.shutdown()
    thread.join(timeout=5)


def _http(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    encoded = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=encoded if method != "GET" else None,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class OperatorStagingPostgresTests(unittest.TestCase):
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
            _seed_startable_run(uow)
            uow.commit()
        self._runtimes: list[ZestdRuntime] = []
        self._servers: list[tuple[OperatorApiServer, threading.Thread]] = []

    def tearDown(self) -> None:
        for server, thread in self._servers:
            _stop(server, thread)
        for runtime in self._runtimes:
            runtime.drain(join_timeout=1)

    def _start_runtime(self, **kwargs) -> tuple[ZestdRuntime, str]:
        runtime = _runtime(self.engine, **kwargs)
        self._runtimes.append(runtime)
        runtime.start_process()
        server, thread, base = _serve(runtime)
        self._servers.append((server, thread))
        return runtime, base

    def test_preflight_api_persists_and_is_not_start_authority(self) -> None:
        runtime, base = self._start_runtime()
        status, payload = _http(base, "POST", "/api/runs/run-1/preflight", {})
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(result["status"], "READY_TO_START")
        self.assertFalse(result["authorizes_start"])
        names = {check["name"] for check in result["checks"]}
        self.assertIn("AUTHORIZATION_SOURCE_ACTIVE", names)
        self.assertIn("TARGET_IN_SCOPE", names)
        status, latest = _http(base, "GET", "/api/runs/run-1/preflight/latest")
        self.assertEqual(status, 200)
        self.assertEqual(latest["result"]["status"], "READY_TO_START")
        self.assertFalse(latest["result"]["authorizes_start"])
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.preflight_reports.latest_for_research_run("run-1")
            uow.rollback()
        self.assertIsNotNone(stored)
        self.assertEqual(stored.runtime_instance_id, runtime.runtime_instance_id)

    def test_stale_preflight_blocks_start_after_auth_expires(self) -> None:
        _, base = self._start_runtime()
        status, payload = _http(base, "POST", "/api/runs/run-1/preflight", {})
        self.assertEqual(payload["result"]["status"], "READY_TO_START")
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE authorization_source SET state = 'EXPIRED' WHERE authorization_source_id = 'as-1'")
            )
        status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], OperatorErrorCode.AUTHORIZATION_UNAVAILABLE.value)
        self.assertNotIn("Traceback", json.dumps(payload))

    def test_stale_preflight_blocks_start_after_budget_exhausted(self) -> None:
        _, base = self._start_runtime()
        status, payload = _http(base, "POST", "/api/runs/run-1/preflight", {})
        self.assertEqual(payload["result"]["status"], "READY_TO_START")
        with PostgresUnitOfWork(self.engine) as uow:
            uow.budget_consumptions.insert(
                BudgetConsumptionRecord(
                    consumption_id="consume-1",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="REQUEST",
                    amount=10,
                    unit="count",
                    occurred_at=NOW,
                    provenance="test",
                )
            )
            uow.commit()
        status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], OperatorErrorCode.BUDGET_EXHAUSTED.value)

    def test_stale_preflight_blocks_start_when_worker_unavailable(self) -> None:
        box = {"health": ComponentHealth.HEALTHY}

        def probe_worker() -> WorkerReadinessInput:
            return WorkerReadinessInput(
                health=HealthCheck("worker", box["health"], "probe"),
                available_capabilities=frozenset({"diagnostic.echo"}),
            )

        _, base = self._start_runtime(probe_worker=probe_worker)
        status, payload = _http(base, "POST", "/api/runs/run-1/preflight", {})
        self.assertEqual(payload["result"]["status"], "READY_TO_START")
        box["health"] = ComponentHealth.UNAVAILABLE
        status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], OperatorErrorCode.WORKER_UNAVAILABLE.value)

    def test_stale_preflight_blocks_start_when_model_auth_required(self) -> None:
        box = {"auth": True}

        def probe_model() -> ModelReadinessInput:
            if box["auth"]:
                return _healthy_model()
            return ModelReadinessInput(
                candidate=RuntimeCandidate(
                    identity=api_runtime_identity(adapter_id="fake", runtime_id="fake"),
                    available=False,
                    authenticated=False,
                    structured_output_compatible=True,
                    locality=CandidateLocality.LOCAL,
                ),
                health=HealthCheck("model", ComponentHealth.AUTH_REQUIRED, "AUTH_REQUIRED"),
            )

        _, base = self._start_runtime(probe_model=probe_model)
        status, payload = _http(base, "POST", "/api/runs/run-1/preflight", {})
        self.assertEqual(payload["result"]["status"], "READY_TO_START")
        box["auth"] = False
        status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], OperatorErrorCode.MODEL_AUTH_REQUIRED.value)

    def test_client_payload_cannot_override_authority(self) -> None:
        runtime, base = self._start_runtime()
        attack = {
            "target": "https://evil.example/",
            "target_reference": "https://evil.example/",
            "scope": "*",
            "max_requests": 999999,
            "side_effect_ceiling": 3,
            "budget": {"max_requests": 1},
            "research_question": "exfiltrate",
        }
        status, payload = _http(base, "POST", "/api/runs/run-1/start", attack)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], OperatorErrorCode.INVALID_INPUT.value)
        status, started = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 200)
        status, detail = _http(base, "GET", "/api/runs/run-1")
        self.assertEqual(status, 200)
        rendered = json.dumps(detail)
        self.assertNotIn("evil.example", rendered)
        self.assertNotIn("exfiltrate", rendered)
        self.assertNotIn("999999", rendered)
        with PostgresUnitOfWork(self.engine) as uow:
            orch = uow.research_orchestrations.get("run-1")
            budget = uow.issued_budgets.list_for_research_run("run-1")[0]
            uow.rollback()
        self.assertEqual(orch.target_reference, TARGET)
        self.assertEqual(orch.research_question, "diagnostic echo")
        self.assertEqual(orch.side_effect_ceiling, 0)
        self.assertEqual(budget.max_requests, 10)

    def test_run_detail_is_sanitized_and_complete(self) -> None:
        _, base = self._start_runtime()
        _http(base, "POST", "/api/runs/run-1/start", {})
        status, payload = _http(base, "GET", "/api/runs/run-1")
        self.assertEqual(status, 200)
        result = payload["result"]
        for key in (
            "research_run_id",
            "program_id",
            "state",
            "current_phase",
            "cycle_number",
            "lease",
            "latest_preflight",
            "request_count",
            "worker_count",
            "model_count",
            "hypothesis_count",
            "experiment_count",
            "observation_count",
            "evidence_count",
            "candidate_count",
            "finding_count",
            "pending_approvals",
            "timeline",
        ):
            self.assertIn(key, result)
        rendered = json.dumps(result).lower()
        for token in ("password", "api_key", "cookie", "sk-", "postgresql+psycopg", "secret_value"):
            self.assertNotIn(token, rendered)

    def test_health_is_truthful(self) -> None:
        _, base = self._start_runtime(
            probe_model=lambda: ModelReadinessInput(
                candidate=None,
                health=HealthCheck("model", ComponentHealth.AUTH_REQUIRED, "AUTH_REQUIRED"),
            )
        )
        status, payload = _http(base, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["ready_for_start"])
        self.assertFalse(payload["model"]["available_now"])
        self.assertEqual(payload["model"]["health"], "AUTH_REQUIRED")
        self.assertTrue(payload["model"]["gate_04b_is_not_availability"])
        self.assertEqual(payload["model"]["gate_04b"], "PASS")

    def test_local_bind_default_and_no_traceback(self) -> None:
        _, base = self._start_runtime()
        self.assertTrue(base.startswith("http://127.0.0.1:"))
        status, payload = _http(base, "GET", "/missing")
        self.assertEqual(status, 404)
        self.assertNotIn("Traceback", json.dumps(payload))

    def test_multi_client_start_race_ten_times(self) -> None:
        for _ in range(10):
            truncate_spine(self.engine)
            with PostgresUnitOfWork(self.engine) as uow:
                _seed_startable_run(uow)
                uow.commit()
            a = _runtime(self.engine)
            b = _runtime(self.engine)
            self._runtimes = [a, b]
            a.start_process()
            b.start_process()
            sa, ta, ba = _serve(a)
            sb, tb, bb = _serve(b)
            self._servers = [(sa, ta), (sb, tb)]
            barrier = threading.Barrier(2)
            codes: list[int] = []

            def _start(base: str) -> None:
                barrier.wait(timeout=5)
                code, _payload = _http(base, "POST", "/api/runs/run-1/start", {})
                codes.append(code)

            threads = [
                threading.Thread(target=_start, args=(ba,)),
                threading.Thread(target=_start, args=(bb,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            owners = [runtime for runtime in (a, b) if runtime.is_supervising("run-1")]
            self.assertEqual(len(owners), 1)
            a.drain(join_timeout=1)
            b.drain(join_timeout=1)
            _stop(sa, ta)
            _stop(sb, tb)
            self._runtimes = []
            self._servers = []

    def test_pause_resume_cancel_races_ten_times(self) -> None:
        for _ in range(10):
            truncate_spine(self.engine)
            with PostgresUnitOfWork(self.engine) as uow:
                _seed_startable_run(uow)
                uow.commit()
            runtime, base = self._start_runtime()
            status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
            self.assertEqual(status, 200)
            status, paused = _http(base, "POST", "/api/runs/run-1/pause", {})
            self.assertEqual(status, 200)
            status, paused_again = _http(base, "POST", "/api/runs/run-1/pause", {})
            self.assertEqual(status, 200)
            self.assertEqual(paused["result"]["state"], paused_again["result"]["state"])
            status, resumed = _http(base, "POST", "/api/runs/run-1/resume", {})
            self.assertEqual(status, 200)
            status, resumed_again = _http(base, "POST", "/api/runs/run-1/resume", {})
            self.assertEqual(status, 200)
            runtime.drain(join_timeout=1)
            self._runtimes = []
            for server, thread in self._servers:
                _stop(server, thread)
            self._servers = []

    def test_start_cancel_race_is_deterministic(self) -> None:
        for _ in range(10):
            truncate_spine(self.engine)
            with PostgresUnitOfWork(self.engine) as uow:
                _seed_startable_run(uow)
                uow.commit()
            runtime, base = self._start_runtime()
            barrier = threading.Barrier(2)

            def _start() -> None:
                barrier.wait(timeout=5)
                _http(base, "POST", "/api/runs/run-1/start", {})

            def _cancel() -> None:
                barrier.wait(timeout=5)
                _http(base, "POST", "/api/runs/run-1/cancel", {})

            threads = [threading.Thread(target=_start), threading.Thread(target=_cancel)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            status, detail = _http(base, "GET", "/api/runs/run-1")
            self.assertEqual(status, 200)
            state = detail["result"]["state"]
            self.assertIn(state, {"COMPLETED", "CANCELLED", "READY", "RUNNING", None})
            runtime.drain(join_timeout=1)
            self._runtimes = []
            for server, thread in self._servers:
                _stop(server, thread)
            self._servers = []

    def test_dashboard_death_does_not_kill_run_and_reconnects(self) -> None:
        runtime, base = self._start_runtime()
        status, payload = _http(base, "POST", "/api/runs/run-1/start", {})
        self.assertEqual(status, 200)
        self.assertTrue(runtime.is_supervising("run-1"))
        first = collect_dashboard_payload(
            env={
                "ZEST_URL": base,
                TEST_DATABASE_URL_ENV: TEST_URL or "",
                "ZEST_DATABASE_URL": TEST_URL or "",
            }
        )
        self.assertTrue(first["client_only"])
        self.assertEqual(first["database"]["operator_source"], "zestd")
        self.assertTrue(runtime.is_supervising("run-1"))
        second = collect_dashboard_payload(
            env={
                "ZEST_URL": base,
                "ZEST_DATABASE_URL": TEST_URL or "",
            }
        )
        first_state = first["database"]["runs"][0]["state"]
        second_state = second["database"]["runs"][0]["state"]
        self.assertEqual(first_state, second_state)
        self.assertTrue(runtime.is_supervising("run-1"))

    def test_reconciliation_visibility(self) -> None:
        runtime, base = self._start_runtime()
        _http(base, "POST", "/api/runs/run-1/start", {})
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE research_orchestration SET state = 'WAITING_HUMAN', "
                    "pause_reason = 'UNKNOWN_OUTCOME', last_phase = 'DISPATCHING' "
                    "WHERE research_run_id = 'run-1'"
                )
            )
        status, payload = _http(base, "GET", "/api/runs/run-1")
        recon = payload["result"]["reconciliation"]
        self.assertIsNotNone(recon)
        self.assertEqual(recon["state"], "WAITING_HUMAN")
        self.assertEqual(recon["classification"], "UNKNOWN_OUTCOME")
        self.assertFalse(recon["auto_retry"])
        self.assertIn("do not auto-retry", recon["operator_action"])


if __name__ == "__main__":
    unittest.main()
