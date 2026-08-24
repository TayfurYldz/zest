"""research-osd SIGTERM / SIGINT process-exit contract against real PostgreSQL."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from datetime import datetime, timezone

from research_os.application.orchestration_lease import LeaseConfig
from research_os.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.core.enums import ScopeRuleEffect
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
)
from research_os.interface.operator_api import OperatorApiServer
from research_os.platform.health import ComponentHealth, HealthCheck
from research_os.research.model_runtime import api_runtime_identity
from research_os.research.routing import CandidateLocality, RuntimeCandidate
from integration.harness import alembic_upgrade, truncate_spine
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort
from sqlalchemy import text

NOW = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)
TARGET = "https://example.com/"

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

LISTEN_PREFIX = "research-osd listening on "
EXIT_BOUND_SECONDS = 8.0
SECRET_MARKERS = (
    "password=",
    "api_key=",
    "authorization:",
    "cookie=",
    "bearer ",
)


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


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_for_listen(proc: subprocess.Popen[str], timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    buf = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            raise AssertionError(f"research-osd exited early rc={proc.returncode} stderr={stderr}")
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        buf += line
        if LISTEN_PREFIX in line:
            url = line.strip().split(LISTEN_PREFIX, 1)[1]
            if url.endswith(":0"):
                raise AssertionError(f"research-osd published unbound port: {url}")
            return url
    raise AssertionError(f"research-osd did not print listen address: {buf}")


def _stop_and_collect(
    proc: subprocess.Popen[str],
    *,
    sig: int,
    repeats: int = 1,
) -> tuple[int | None, str, str]:
    for _ in range(repeats):
        if proc.poll() is None:
            proc.send_signal(sig)
    try:
        stdout, stderr = proc.communicate(timeout=EXIT_BOUND_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise AssertionError("research-osd did not exit after signal within bound")
    return proc.returncode, stdout, stderr


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class ResearchOsdSigtermTests(unittest.TestCase):
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
        self._proc: subprocess.Popen[str] | None = None

    def tearDown(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.communicate(timeout=5)

    def _spawn(self) -> subprocess.Popen[str]:
        assert TEST_URL is not None
        env = dict(os.environ)
        env["RESEARCH_OS_DATABASE_URL"] = TEST_URL
        env["RESEARCH_OSD_BIND_HOST"] = "127.0.0.1"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("RESEARCH_OSD_LOG_PATH", None)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "research_os.interface.research_osd",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
            ],
            cwd=str(_REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._proc = proc
        return proc

    def _assert_terminal_instance(self, process_id: str) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, stopped_at FROM runtime_instance "
                    "WHERE process_id = :pid ORDER BY started_at DESC"
                ),
                {"pid": process_id},
            ).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.status, "STOPPED")
        self.assertIsNotNone(row.stopped_at)
        self.assertNotEqual(row.status, "RUNNING")

    def _assert_no_secrets(self, *blobs: str) -> None:
        rendered = "\n".join(blobs).lower()
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, rendered)

    def test_sigterm_exits_and_persists_stopped(self) -> None:
        proc = self._spawn()
        url = _wait_for_listen(proc)
        health = _http_json(url + "/health")
        self.assertTrue(health.get("ok"))
        instance_id = health["runtime_instance_id"]
        host, port_s = url.rsplit(":", 1)
        host = host.replace("http://", "")
        port = int(port_s)
        self.assertTrue(_port_open(host, port))
        rc, stdout, stderr = _stop_and_collect(proc, sig=signal.SIGTERM)
        self.assertEqual(rc, 0)
        self.assertFalse(_port_open(host, port))
        self._assert_terminal_instance(str(proc.pid))
        self._assert_no_secrets(stdout, stderr, json.dumps(health))
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT runtime_instance_id FROM runtime_instance "
                    "WHERE process_id = :pid"
                ),
                {"pid": str(proc.pid)},
            ).first()
        self.assertEqual(row.runtime_instance_id, instance_id)

    def test_sigint_exits_and_persists_stopped(self) -> None:
        proc = self._spawn()
        url = _wait_for_listen(proc)
        _http_json(url + "/health")
        host, port_s = url.rsplit(":", 1)
        port = int(port_s)
        host = host.replace("http://", "")
        rc, stdout, stderr = _stop_and_collect(proc, sig=signal.SIGINT)
        self.assertEqual(rc, 0)
        self.assertFalse(_port_open(host, port))
        self._assert_terminal_instance(str(proc.pid))
        self._assert_no_secrets(stdout, stderr)

    def test_repeated_sigterm_is_idempotent(self) -> None:
        proc = self._spawn()
        url = _wait_for_listen(proc)
        _http_json(url + "/health")
        rc, stdout, stderr = _stop_and_collect(proc, sig=signal.SIGTERM, repeats=3)
        self.assertEqual(rc, 0)
        self._assert_terminal_instance(str(proc.pid))
        self._assert_no_secrets(stdout, stderr)
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    text("SELECT status FROM runtime_instance WHERE process_id = :pid"),
                    {"pid": str(proc.pid)},
                )
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "STOPPED")

    def test_drain_with_supervisor_does_not_duplicate_worker(self) -> None:
        assert self.engine is not None
        worker = RecordingWorkerPort()
        with PostgresUnitOfWork(self.engine) as uow:
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
            uow.commit()
        factory = PostgresUnitOfWork(self.engine)
        runtime = ResearchOsdRuntime(
            factory,
            worker,
            ScriptedModelPort(),
            lease_config=LeaseConfig(heartbeat_interval_seconds=0.2, lease_ttl_seconds=0.8),
            cadence_seconds=30,
            probe_schema=lambda: SchemaHealthInput(True, "ok"),
            probe_worker=_healthy_worker,
            probe_model=_healthy_model,
        )
        server = OperatorApiServer(runtime, host="127.0.0.1", port=0)
        try:
            runtime.start_process()
            server.start()
            runtime.start_run("run-1")
            self.assertTrue(runtime.is_supervising("run-1"))
            deadline = time.time() + 2
            while time.time() < deadline and not worker.calls:
                time.sleep(0.05)
            before = len(worker.calls)
            instance_id = runtime.runtime_instance_id
            server.shutdown()
            runtime.drain(join_timeout=1)
            self.assertEqual(len(worker.calls), before)
            with self.engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT status, stopped_at "
                        "FROM runtime_instance WHERE runtime_instance_id = :id"
                    ),
                    {"id": instance_id},
                ).first()
                orch = connection.execute(
                    text(
                        "SELECT owner_runtime_instance_id, lease_epoch "
                        "FROM research_orchestration WHERE research_run_id = 'run-1'"
                    )
                ).first()
            self.assertEqual(row.status, "STOPPED")
            self.assertIsNotNone(row.stopped_at)
            self.assertIsNotNone(orch)
        finally:
            server.shutdown()
            runtime.drain(join_timeout=1)


if __name__ == "__main__":
    unittest.main()
