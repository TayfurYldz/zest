"""Checkpoint 16 VDS fixture spine bootstrap against real PostgreSQL."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
import time
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from research_os.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
)
from research_os.application.runtime_instance import (
    heartbeat_runtime_instance,
    register_runtime_instance,
)
from research_os.data.errors import PersistenceError
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.qualification.staging_spine import (
    TRUNCATE_GUARD,
    StagingTruncateDenied,
)
from research_os.qualification.j11_fencing import (
    J11QualificationError,
    cleanup_j11_owner,
    prepare_j11_run,
    run_stale_epoch_proof,
    run_two_process_owner_race,
)
from research_os.research.orchestration import OrchestrationState
from integration.harness import alembic_upgrade, seed_authorized_spine, truncate_spine

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

SECRET_MARKERS = (
    "password=",
    "api_key=",
    "postgresql+psycopg://",
    "super-secret",
    "authorization:",
    "traceback",
)


def _load_fixture():
    path = _REPO / "scripts/vds_checkpoint16_fixture.py"
    spec = importlib.util.spec_from_file_location("vds_checkpoint16_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load vds_checkpoint16_fixture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture(fn) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        fn()
    return buffer.getvalue()


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Checkpoint16FixtureSpineTests(unittest.TestCase):
    engine = None
    fixture = None

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
        cls.fixture = _load_fixture()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        self._previous_guard = os.environ.get(TRUNCATE_GUARD)
        os.environ[TRUNCATE_GUARD] = "YES"

    def tearDown(self) -> None:
        if self._previous_guard is None:
            os.environ.pop(TRUNCATE_GUARD, None)
        else:
            os.environ[TRUNCATE_GUARD] = self._previous_guard

    def _seed(self) -> str:
        assert self.engine is not None and self.fixture is not None
        return _capture(lambda: self.fixture._seed(self.engine, truncate=True))

    def _orchestration(self):
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            record = uow.research_orchestrations.get("run-1")
            uow.rollback()
        return record

    def _attempts(self) -> list:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            rows = uow.execution_attempts.list_for_research_run("run-1")
            uow.rollback()
        return rows

    def _worker_results(self) -> list:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            rows = list(uow.worker_results.list_for_research_run("run-1"))
            uow.rollback()
        return rows

    def _classify(self):
        assert self.engine is not None
        return ClassifyRuntimeRecovery(PostgresUnitOfWork(self.engine)).execute("run-1")

    def test_repository_save_still_requires_existing_row(self) -> None:
        assert self.engine is not None and self.fixture is not None
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            record = self.fixture._orchestration_record(state="READY")
            with self.assertRaises(PersistenceError) as raised:
                uow.research_orchestrations.save(record)
            self.assertIn("not found for checkpoint", str(raised.exception))
            uow.rollback()

    def test_seed_truncate_creates_unowned_orchestration_without_dispatch(self) -> None:
        output = self._seed()
        self.assertIn("seeded=run-1", output)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, output.lower())
        record = self._orchestration()
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.research_run_id, "run-1")
        self.assertEqual(record.state, "RUNNING")
        self.assertEqual(record.current_phase, "CYCLE_READY")
        self.assertEqual(len(record.configuration_fingerprint), 64)
        self.assertIsNone(record.owner_runtime_instance_id)
        self.assertEqual(record.lease_epoch, 0)
        self.assertIsNone(record.lease_expires_at)
        self.assertEqual(self._attempts(), [])
        self.assertEqual(self._worker_results(), [])

    def test_repeated_seed_is_deterministic(self) -> None:
        self._seed()
        first = self._orchestration()
        assert first is not None
        self._seed()
        second = self._orchestration()
        assert second is not None
        self.assertEqual(first.configuration_fingerprint, second.configuration_fingerprint)
        self.assertEqual(second.owner_runtime_instance_id, None)
        self.assertEqual(second.lease_epoch, 0)
        self.assertEqual(second.state, "RUNNING")
        self.assertEqual(self._attempts(), [])
        self.assertEqual(self._worker_results(), [])

    def test_j8_authorized_not_dispatched_classifies_safe_retry(self) -> None:
        assert self.fixture is not None and self.engine is not None
        self._seed()
        _capture(
            lambda: self.fixture._set_attempt(
                self.engine, state="AUTHORIZED", side_effect_level=0
            )
        )
        attempts = self._attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].state, "AUTHORIZED")
        self.assertEqual(self._worker_results(), [])
        decision = self._classify()
        self.assertEqual(
            decision.action, RuntimeRecoveryAction.SAFE_RETRY_AFTER_REAUTHORIZATION
        )
        shown = _capture(lambda: self.fixture._show_runtime(self.engine))
        self.assertIn("orchestration", shown)
        self.assertNotIn("Traceback", shown)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, shown.lower())

    def test_j9_dispatching_classifies_reconciliation_required(self) -> None:
        assert self.fixture is not None and self.engine is not None
        self._seed()
        _capture(
            lambda: self.fixture._set_attempt(
                self.engine, state="DISPATCHING", side_effect_level=1
            )
        )
        decision = self._classify()
        self.assertEqual(decision.action, RuntimeRecoveryAction.RECONCILIATION_REQUIRED)
        self.assertEqual(self._worker_results(), [])

    def test_j10_unknown_outcome_classifies_human_required(self) -> None:
        assert self.fixture is not None and self.engine is not None
        self._seed()
        _capture(
            lambda: self.fixture._set_attempt(
                self.engine, state="UNKNOWN_OUTCOME", side_effect_level=2
            )
        )
        decision = self._classify()
        self.assertEqual(decision.action, RuntimeRecoveryAction.HUMAN_REQUIRED)
        self.assertEqual(self._worker_results(), [])

    def test_seed_preserves_live_runtime_instance_and_heartbeat_survives(self) -> None:
        assert self.engine is not None and self.fixture is not None
        factory = PostgresUnitOfWork(self.engine)
        instance = register_runtime_instance(
            factory,
            host_identity="test-vds-host",
            process_id="99999",
        )
        heartbeat_runtime_instance(factory, instance.runtime_instance_id)
        with factory.open() as uow:
            loaded = uow.runtime_instances.get(instance.runtime_instance_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.status, "STARTING")
            uow.rollback()

        # Seed with truncate: MUST NOT delete or invalidate the live runtime instance
        self._seed()

        # Verify runtime_instance is preserved in DB and can still heartbeat
        with factory.open() as uow:
            persisted = uow.runtime_instances.get(instance.runtime_instance_id)
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.runtime_instance_id, instance.runtime_instance_id)
            uow.rollback()

        hb = heartbeat_runtime_instance(factory, instance.runtime_instance_id)
        self.assertEqual(hb.runtime_instance_id, instance.runtime_instance_id)

        # Verify _show_runtime displays runtime instance without SQL errors
        shown = _capture(lambda: self.fixture._show_runtime(self.engine))
        self.assertIn(f"id={instance.runtime_instance_id}", shown)
        self.assertIn("orchestration", shown)
        self.assertNotIn("Traceback", shown)

    def test_seed_without_truncate_flag_fails_closed(self) -> None:
        assert self.engine is not None and self.fixture is not None
        with self.assertRaises(StagingTruncateDenied) as raised:
            self.fixture._seed(self.engine, truncate=False)
        self.assertIn("--truncate", str(raised.exception))

    def test_seed_without_env_guard_fails_closed(self) -> None:
        assert self.engine is not None and self.fixture is not None
        os.environ[TRUNCATE_GUARD] = "NO"
        with self.assertRaises(StagingTruncateDenied) as raised:
            self.fixture._seed(self.engine, truncate=True)
        self.assertIn(TRUNCATE_GUARD, str(raised.exception))

    def test_j11_prepare_turns_waiting_human_into_unowned_running_surface(self) -> None:
        assert self.engine is not None
        self._seed()
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            uow.research_orchestrations.save(
                replace(
                    current,
                    state=OrchestrationState.WAITING_HUMAN.value,
                    pause_reason="HUMAN_REQUIRED",
                )
            )
            uow.commit()

        result = prepare_j11_run(self.engine)
        self.assertEqual(result.state, "RUNNING")
        self.assertIsNone(result.owner_runtime_instance_id)
        self.assertEqual(result.side_effect_ceiling, 0)
        record = self._orchestration()
        assert record is not None
        self.assertEqual(record.state, "RUNNING")
        self.assertIsNone(record.pause_reason)
        self.assertIsNone(record.owner_runtime_instance_id)

    def test_j11_two_process_owner_race_exactly_one_owner_no_loser_dispatch(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        result = run_two_process_owner_race(TEST_URL, iterations=3)
        self.assertEqual(result.final_owner_count, 1)
        self.assertEqual(len(result.iterations), 3)
        self.assertEqual([item.lease_epoch for item in result.iterations], [1, 2, 3])
        for item in result.iterations:
            with self.subTest(iteration=item.iteration):
                self.assertNotEqual(item.winner_process_id, item.loser_process_id)
                self.assertNotEqual(
                    item.winner_runtime_instance_id,
                    item.loser_runtime_instance_id,
                )
                self.assertTrue(item.loser_worker_blocked)
                self.assertEqual(item.loser_inner_dispatch_count, 0)
        record = self._orchestration()
        assert record is not None
        self.assertEqual(
            record.owner_runtime_instance_id,
            result.final_owner_runtime_instance_id,
        )
        self.assertEqual(record.lease_epoch, result.final_lease_epoch)
        self.assertEqual(record.lease_epoch, 3)
        self.assertEqual(self._worker_results(), [])

    def test_j11_stale_epoch_blocks_db_mutation_and_worker_invocation(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        result = run_stale_epoch_proof(
            TEST_URL,
            expired_ttl_seconds=0.1,
            lease_ttl_seconds=5.0,
        )
        self.assertNotEqual(
            result.stale_owner_runtime_instance_id,
            result.current_owner_runtime_instance_id,
        )
        self.assertNotEqual(result.stale_process_id, result.current_process_id)
        self.assertLess(result.stale_epoch, result.current_epoch)
        self.assertTrue(result.stale_save_blocked)
        self.assertTrue(result.stale_worker_blocked)
        self.assertEqual(result.stale_inner_dispatch_count, 0)
        self.assertEqual(result.stopped_runtime_count, 2)
        self.assertEqual(self._worker_results(), [])

    def test_j11_cleanup_releases_expired_stopped_owner_via_repository_cas(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        race = run_two_process_owner_race(
            TEST_URL,
            iterations=1,
            lease_ttl_seconds=0.1,
        )
        time.sleep(0.45)

        result = cleanup_j11_owner(
            self.engine,
            owner_runtime_instance_id=race.final_owner_runtime_instance_id,
            expected_lease_epoch=race.final_lease_epoch,
        )

        self.assertEqual(result.cleanup, "RELEASED_EXPIRED_STOPPED_OWNER")
        self.assertEqual(result.owner_runtime_instance_id, race.final_owner_runtime_instance_id)
        self.assertEqual(result.expected_lease_epoch, race.final_lease_epoch)
        self.assertEqual(result.lease_epoch_after, race.final_lease_epoch)
        self.assertIsNone(result.owner_after)
        self.assertIsNone(result.lease_expires_at_after)
        self.assertFalse(result.derived_owner)
        self.assertEqual(result.owner_status, "STOPPED")
        self.assertTrue(result.owner_expired)
        self.assertTrue(result.release_cas_applied)
        record = self._orchestration()
        assert record is not None
        self.assertIsNone(record.owner_runtime_instance_id)
        self.assertEqual(record.lease_epoch, race.final_lease_epoch)
        self.assertIsNone(record.lease_expires_at)
        self.assertEqual(self._worker_results(), [])

        repeated = cleanup_j11_owner(self.engine)
        self.assertEqual(repeated.cleanup, "ALREADY_UNOWNED")
        self.assertFalse(repeated.release_cas_applied)

    def test_j11_cleanup_can_derive_only_expired_stopped_qualification_owner(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        race = run_two_process_owner_race(
            TEST_URL,
            iterations=1,
            lease_ttl_seconds=0.1,
        )
        time.sleep(0.45)

        result = cleanup_j11_owner(self.engine)

        self.assertEqual(result.cleanup, "RELEASED_EXPIRED_STOPPED_OWNER")
        self.assertEqual(result.owner_runtime_instance_id, race.final_owner_runtime_instance_id)
        self.assertEqual(result.expected_lease_epoch, race.final_lease_epoch)
        self.assertTrue(result.derived_owner)
        self.assertIsNone(result.owner_after)

    def test_j11_cleanup_refuses_owner_epoch_mismatch(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        race = run_two_process_owner_race(
            TEST_URL,
            iterations=1,
            lease_ttl_seconds=0.1,
        )
        time.sleep(0.45)

        with self.assertRaises(J11QualificationError) as raised:
            cleanup_j11_owner(
                self.engine,
                owner_runtime_instance_id=race.final_owner_runtime_instance_id,
                expected_lease_epoch=race.final_lease_epoch + 1,
            )
        self.assertIn("owner/epoch mismatch", str(raised.exception))
        record = self._orchestration()
        assert record is not None
        self.assertEqual(record.owner_runtime_instance_id, race.final_owner_runtime_instance_id)
        self.assertEqual(record.lease_epoch, race.final_lease_epoch)

    def test_j11_cleanup_refuses_unexpired_owner(self) -> None:
        assert TEST_URL is not None and self.engine is not None
        self._seed()
        prepare_j11_run(self.engine)
        race = run_two_process_owner_race(
            TEST_URL,
            iterations=1,
            lease_ttl_seconds=5.0,
        )

        with self.assertRaises(J11QualificationError) as raised:
            cleanup_j11_owner(
                self.engine,
                owner_runtime_instance_id=race.final_owner_runtime_instance_id,
                expected_lease_epoch=race.final_lease_epoch,
            )
        self.assertIn("refuses to clear a live owner", str(raised.exception))
        record = self._orchestration()
        assert record is not None
        self.assertEqual(record.owner_runtime_instance_id, race.final_owner_runtime_instance_id)

    def test_j11_cleanup_cli_prints_machine_readable_evidence(self) -> None:
        assert TEST_URL is not None and self.engine is not None and self.fixture is not None
        self._seed()
        prepare_j11_run(self.engine)
        race = run_two_process_owner_race(
            TEST_URL,
            iterations=1,
            lease_ttl_seconds=0.1,
        )
        time.sleep(0.45)

        output = _capture(
            lambda: self.fixture._j11_cleanup(
                self.engine,
                owner_runtime_instance_id=race.final_owner_runtime_instance_id,
                expected_lease_epoch=race.final_lease_epoch,
            )
        )

        self.assertIn("j11_cleanup=PASS", output)
        self.assertIn("cleanup=RELEASED_EXPIRED_STOPPED_OWNER", output)
        self.assertIn("release_cas_applied=True", output)
        self.assertIn("owner_after=None", output)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, output.lower())
