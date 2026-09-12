"""RT-C -- real PostgreSQL proof that fenced ownership of research_orchestration
is correct under concurrency: a live lease cannot be stolen, an expired lease
has exactly one winner, a superseded owner's writes are rejected, and a
terminal run can never be leased.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
SQLite is not a substitute.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from sqlalchemy import update

from zest.application.lease_fencing import (
    SingleRunFencedUowFactory,
)
from zest.data.errors import LeaseFencingError
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.tables import research_orchestration as research_orchestration_table
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuditEventRecord,
    LeaseAcquireOutcome,
    ResearchOrchestrationRecord,
)
from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    seed_authorized_spine,
    truncate_spine,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("ZEST_DATABASE_URL")
    )


def _orchestration(**overrides) -> ResearchOrchestrationRecord:
    values = dict(
        research_run_id="run-1",
        state="RUNNING",
        cycle_number=1,
        last_phase="running",
        policy_version="orchestration.bounded.v1",
        max_cycles=3,
        max_experiments=3,
        max_model_calls=12,
        max_worker_invocations=3,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=False,
        created_at=NOW,
        updated_at=NOW,
        checkpoint_at=NOW,
        budget_id="budget-1",
        target_reference="target-1",
        research_question="q",
        configuration_fingerprint="0" * 64,
        current_phase="CYCLE_READY",
    )
    values.update(overrides)
    return ResearchOrchestrationRecord(**values)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class OrchestrationLeaseFencingTests(unittest.TestCase):
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
            uow.research_orchestrations.insert(_orchestration())
            uow.commit()

    def _force_expire(self, *, seconds_ago: float = 1.0) -> None:
        """Directly backdate lease_expires_at, bypassing the fenced API, to
        deterministically simulate an expired lease without depending on
        wall-clock sleeps."""
        assert self.engine is not None
        with self.engine.begin() as connection:
            connection.execute(
                update(research_orchestration_table)
                .where(research_orchestration_table.c.research_run_id == "run-1")
                .values(
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
                )
            )

    def test_owner_a_acquires_lease_on_fresh_non_terminal_run(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            result = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(result.outcome, LeaseAcquireOutcome.ACQUIRED)
        assert result.record is not None
        self.assertEqual(result.record.owner_runtime_instance_id, "owner-a")
        self.assertEqual(result.record.lease_epoch, 1)

    def test_owner_b_cannot_steal_a_live_lease(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(first.outcome, LeaseAcquireOutcome.ACQUIRED)

        with PostgresUnitOfWork(self.engine) as uow:
            second = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-b", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(second.outcome, LeaseAcquireOutcome.DENIED_HELD_BY_OTHER)

        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
        assert current is not None
        self.assertEqual(current.owner_runtime_instance_id, "owner-a")
        self.assertEqual(current.lease_epoch, 1)

    def test_owner_a_can_reacquire_its_own_live_lease_idempotently(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            second = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(first.outcome, LeaseAcquireOutcome.ACQUIRED)
        self.assertEqual(second.outcome, LeaseAcquireOutcome.ACQUIRED)
        assert second.record is not None
        self.assertEqual(second.record.lease_epoch, 2)

    def test_expired_lease_can_be_taken_over_at_next_epoch(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(first.outcome, LeaseAcquireOutcome.ACQUIRED)
        assert first.record is not None
        self.assertEqual(first.record.lease_epoch, 1)

        self._force_expire()

        with PostgresUnitOfWork(self.engine) as uow:
            second = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-b", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(second.outcome, LeaseAcquireOutcome.ACQUIRED)
        assert second.record is not None
        self.assertEqual(second.record.owner_runtime_instance_id, "owner-b")
        self.assertEqual(second.record.lease_epoch, 2)

    def test_stale_owners_renew_and_save_are_rejected_once_superseded(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        assert first.record is not None
        stale_epoch = first.record.lease_epoch

        self._force_expire()
        with PostgresUnitOfWork(self.engine) as uow:
            second = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-b", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(second.outcome, LeaseAcquireOutcome.ACQUIRED)

        # owner-a's heartbeat, believing it still holds epoch 1, must fail.
        with PostgresUnitOfWork(self.engine) as uow:
            renewed = uow.research_orchestrations.renew_lease(
                "run-1",
                owner_runtime_instance_id="owner-a",
                expected_lease_epoch=stale_epoch,
                ttl_seconds=90,
            )
            uow.commit()
        self.assertFalse(renewed)

        # owner-a's checkpoint save, fenced on its remembered epoch, must
        # also fail rather than silently mutate state out from under owner-b.
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            with self.assertRaises(LeaseFencingError):
                uow.research_orchestrations.save(
                    replace(current, cycle_number=current.cycle_number + 1),
                    expect_owner_runtime_instance_id="owner-a",
                    expect_lease_epoch=stale_epoch,
                )
            uow.rollback()

        # owner-b, the real current owner, can still checkpoint normally.
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            uow.research_orchestrations.save(
                replace(current, cycle_number=current.cycle_number + 1),
                expect_owner_runtime_instance_id="owner-b",
                expect_lease_epoch=current.lease_epoch,
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            reloaded = uow.research_orchestrations.get("run-1")
        assert reloaded is not None
        self.assertEqual(reloaded.cycle_number, 2)
        self.assertEqual(reloaded.owner_runtime_instance_id, "owner-b")

    def test_two_contenders_racing_an_expired_lease_exactly_one_wins(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(first.outcome, LeaseAcquireOutcome.ACQUIRED)
        self._force_expire()

        results: dict[str, LeaseAcquireOutcome] = {}
        barrier = threading.Barrier(2)

        def _contend(owner_id: str) -> None:
            barrier.wait(timeout=5)
            with PostgresUnitOfWork(self.engine) as uow:
                outcome = uow.research_orchestrations.acquire_lease(
                    "run-1", owner_runtime_instance_id=owner_id, ttl_seconds=90
                )
                uow.commit()
            results[owner_id] = outcome.outcome

        threads = [
            threading.Thread(target=_contend, args=("owner-b",)),
            threading.Thread(target=_contend, args=("owner-c",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        outcomes = list(results.values())
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes.count(LeaseAcquireOutcome.ACQUIRED), 1)
        self.assertEqual(outcomes.count(LeaseAcquireOutcome.DENIED_HELD_BY_OTHER), 1)

        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
        assert current is not None
        winner = next(owner for owner, outcome in results.items() if outcome is LeaseAcquireOutcome.ACQUIRED)
        self.assertEqual(current.owner_runtime_instance_id, winner)
        self.assertEqual(current.lease_epoch, 2)

    def test_terminal_run_cannot_acquire_a_lease(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            uow.research_orchestrations.save(
                replace(current, state="COMPLETED", stop_reason="MAX_CYCLES_REACHED")
            )
            uow.commit()

        with PostgresUnitOfWork(self.engine) as uow:
            result = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(result.outcome, LeaseAcquireOutcome.DENIED_TERMINAL)
        self.assertIsNone(result.record)

    def test_release_lease_requires_matching_owner_and_epoch(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            first = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-a", ttl_seconds=90
            )
            uow.commit()
        assert first.record is not None

        with PostgresUnitOfWork(self.engine) as uow:
            wrong_owner = uow.research_orchestrations.release_lease(
                "run-1",
                owner_runtime_instance_id="owner-b",
                expected_lease_epoch=first.record.lease_epoch,
            )
            uow.commit()
        self.assertFalse(wrong_owner)

        with PostgresUnitOfWork(self.engine) as uow:
            wrong_epoch = uow.research_orchestrations.release_lease(
                "run-1",
                owner_runtime_instance_id="owner-a",
                expected_lease_epoch=first.record.lease_epoch + 1,
            )
            uow.commit()
        self.assertFalse(wrong_epoch)

        with PostgresUnitOfWork(self.engine) as uow:
            released = uow.research_orchestrations.release_lease(
                "run-1",
                owner_runtime_instance_id="owner-a",
                expected_lease_epoch=first.record.lease_epoch,
            )
            uow.commit()
        self.assertTrue(released)

        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
        assert current is not None
        self.assertIsNone(current.owner_runtime_instance_id)

        # A released run is unowned again and can be freshly acquired.
        with PostgresUnitOfWork(self.engine) as uow:
            reacquired = uow.research_orchestrations.acquire_lease(
                "run-1", owner_runtime_instance_id="owner-c", ttl_seconds=90
            )
            uow.commit()
        self.assertEqual(reacquired.outcome, LeaseAcquireOutcome.ACQUIRED)


    def test_superseded_fenced_uow_cannot_commit_unrelated_row(self) -> None:
        assert self.engine is not None

        with PostgresUnitOfWork(
            self.engine
        ) as uow:
            first = (
                uow.research_orchestrations
                .acquire_lease(
                    "run-1",
                    owner_runtime_instance_id=(
                        "owner-a"
                    ),
                    ttl_seconds=90,
                )
            )
            uow.commit()

        self.assertEqual(
            first.outcome,
            LeaseAcquireOutcome.ACQUIRED,
        )
        assert first.record is not None
        self.assertEqual(
            first.record.lease_epoch,
            1,
        )

        self._force_expire()

        with PostgresUnitOfWork(
            self.engine
        ) as uow:
            second = (
                uow.research_orchestrations
                .acquire_lease(
                    "run-1",
                    owner_runtime_instance_id=(
                        "owner-b"
                    ),
                    ttl_seconds=90,
                )
            )
            uow.commit()

        self.assertEqual(
            second.outcome,
            LeaseAcquireOutcome.ACQUIRED,
        )
        assert second.record is not None
        self.assertEqual(
            second.record.lease_epoch,
            2,
        )

        factory = PostgresUnitOfWorkFactory(
            self.engine
        )

        stale_factory = (
            SingleRunFencedUowFactory(
                factory,
                research_run_id="run-1",
                owner_runtime_instance_id=(
                    "owner-a"
                ),
                lease_epoch=1,
            )
        )

        with self.assertRaises(
            LeaseFencingError
        ):
            with stale_factory.open() as uow:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=(
                            "audit-stale-lease"
                        ),
                        occurred_at=NOW,
                        actor_id="owner-a",
                        actor_type="CONTROL_PLANE",
                        event_type=(
                            "STALE_WRITE_PROBE"
                        ),
                        subject_type=(
                            "research_run"
                        ),
                        subject_id="run-1",
                        payload={
                            "should_commit": False,
                        },
                    )
                )
                uow.commit()

        # The stale transaction must have rolled back,
        # not merely raised after committing its write.
        with PostgresUnitOfWork(
            self.engine
        ) as uow:
            persisted = uow.audit_events.get(
                "audit-stale-lease"
            )
            uow.rollback()

        self.assertIsNone(persisted)


if __name__ == "__main__":
    unittest.main()
