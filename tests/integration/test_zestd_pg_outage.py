"""J7 PostgreSQL-outage Operator API contract against a real engine boundary."""

from __future__ import annotations

import json
import os
import socket
import sys
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

from sqlalchemy.engine import make_url

from zest.application.orchestration_lease import LeaseConfig
from zest.application.operator_errors import OperatorErrorCode
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
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from zest.interface.operator_api import OperatorApiServer
from zest.platform.health import ComponentHealth, HealthCheck
from zest.research.model_runtime import api_runtime_identity
from zest.research.routing import CandidateLocality, RuntimeCandidate
from integration.harness import alembic_upgrade, truncate_spine
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort

NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)
TARGET = "https://example.com/"

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )

SECRET_MARKERS = (
    "password=",
    "api_key=",
    "postgresql+psycopg://",
    "super-secret",
    "authorization:",
    "traceback",
)


class SwappableUowFactory:
    def __init__(self, engine) -> None:
        self.engine = engine

    def open(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(self.engine)


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


def _seed_startable_run(uow: PostgresUnitOfWork) -> None:
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


def _http(base: str, method: str, path: str) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(
        base + path,
        data=b"{}" if method != "GET" else None,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, payload, raw


def _orchestration_count(engine) -> int:
    with PostgresUnitOfWork(engine) as uow:
        current = uow.research_orchestrations.get("run-1")
        uow.rollback()
    return 0 if current is None else 1


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class ZestdPostgresOutageTests(unittest.TestCase):
    engine = None
    dead_engine = None

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
        dead_url = make_url(TEST_URL).set(port=1).render_as_string(hide_password=False)
        cls.dead_engine = create_sync_engine(dead_url)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()
        if cls.dead_engine is not None:
            cls.dead_engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            _seed_startable_run(uow)
            uow.commit()
        self.factory = SwappableUowFactory(self.engine)
        self.runtime = ZestdRuntime(
            self.factory,
            RecordingWorkerPort(),
            ScriptedModelPort(),
            lease_config=LeaseConfig(heartbeat_interval_seconds=30, lease_ttl_seconds=60),
            cadence_seconds=30,
            probe_schema=lambda: SchemaHealthInput(True, "ok"),
            probe_worker=_healthy_worker,
            probe_model=_healthy_model,
        )
        self.runtime.start_process()
        self.server = OperatorApiServer(self.runtime, host="127.0.0.1", port=0)
        self.server.start()
        host, port = self.server.address
        deadline = time.time() + 2
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail("operator API did not listen")
        self.base = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.runtime.drain(join_timeout=1)

    def test_health_and_start_degrade_then_same_daemon_recovers(self) -> None:
        status, health, raw = _http(self.base, "GET", "/health")
        self.assertEqual(status, 200)
        self.assertFalse(health["pg_unavailable"])
        self.assertTrue(health["database"]["available_now"])
        self.assertEqual(health["database"]["health"], "HEALTHY")
        instance_id = health["runtime_instance_id"]
        before = _orchestration_count(self.engine)
        self.assertEqual(before, 0)

        self.factory.engine = self.dead_engine

        status, outage, raw = _http(self.base, "GET", "/health")
        self.assertEqual(status, 200, raw)
        self.assertTrue(outage["pg_unavailable"])
        self.assertFalse(outage["ready_for_start"])
        self.assertFalse(outage["database"]["available_now"])
        self.assertEqual(outage["database"]["health"], "UNAVAILABLE")
        self.assertEqual(outage["database"]["detail"], "unavailable")
        self.assertEqual(outage["runtime_instance_id"], instance_id)
        rendered = raw.decode("utf-8", errors="replace").lower()
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, rendered)

        status, start, raw = _http(self.base, "POST", "/api/runs/run-1/start")
        self.assertEqual(status, 503, raw)
        self.assertFalse(start["ok"])
        self.assertEqual(start["error"], OperatorErrorCode.DATABASE_UNAVAILABLE.value)
        self.assertNotIn("Traceback", raw.decode("utf-8", errors="replace"))
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, raw.decode("utf-8", errors="replace").lower())
        self.assertEqual(_orchestration_count(self.engine), before)
        self.assertFalse(self.runtime.is_supervising("run-1"))

        self.factory.engine = self.engine

        status, recovered, raw = _http(self.base, "GET", "/health")
        self.assertEqual(status, 200, raw)
        self.assertFalse(recovered["pg_unavailable"])
        self.assertTrue(recovered["database"]["available_now"])
        self.assertEqual(recovered["database"]["health"], "HEALTHY")
        self.assertEqual(recovered["runtime_instance_id"], instance_id)
        self.assertEqual(_orchestration_count(self.engine), before)
        self.assertFalse(self.runtime.is_supervising("run-1"))
