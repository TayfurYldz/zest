"""RT-A — real PostgreSQL proof that a terminal research_orchestration row is
immutable at the repository/data boundary, not merely by application-layer
convention.

Skipped when ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
SQLite is not a substitute.
"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.data.errors import TerminalOrchestrationStateError
from zest.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import ResearchOrchestrationRecord
from integration.harness import (
    NOW,
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
class OrchestrationTerminalImmutabilityTests(unittest.TestCase):
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

    def test_save_into_a_non_terminal_row_succeeds(self) -> None:
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.research_orchestrations.insert(_orchestration())
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            uow.research_orchestrations.save(
                replace(current, state="BUDGET_EXHAUSTED", stop_reason="BUDGET_EXHAUSTED")
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            reloaded = uow.research_orchestrations.get("run-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, "BUDGET_EXHAUSTED")

    def test_save_into_a_terminal_row_is_rejected_and_leaves_it_unchanged(self) -> None:
        assert self.engine is not None
        for terminal_state, stop_reason in (
            ("COMPLETED", "MAX_CYCLES_REACHED"),
            ("BUDGET_EXHAUSTED", "BUDGET_EXHAUSTED"),
            ("FAILED_OPERATIONAL", "OPERATIONAL_FAILURE"),
        ):
            with self.subTest(terminal_state=terminal_state):
                truncate_spine(self.engine)
                with PostgresUnitOfWork(self.engine) as uow:
                    seed_authorized_spine(uow)
                    uow.research_orchestrations.insert(
                        _orchestration(state=terminal_state, stop_reason=stop_reason)
                    )
                    uow.commit()
                with PostgresUnitOfWork(self.engine) as uow:
                    current = uow.research_orchestrations.get("run-1")
                    assert current is not None
                    with self.assertRaises(TerminalOrchestrationStateError):
                        uow.research_orchestrations.save(
                            replace(
                                current,
                                state="PAUSED",
                                stop_reason="OPERATOR_PAUSED",
                                last_phase="tampered",
                            )
                        )
                    uow.rollback()
                with PostgresUnitOfWork(self.engine) as uow:
                    reloaded = uow.research_orchestrations.get("run-1")
                assert reloaded is not None
                self.assertEqual(reloaded.state, terminal_state)
                self.assertEqual(reloaded.stop_reason, stop_reason)
                self.assertEqual(reloaded.last_phase, "running")

    def test_rejected_write_does_not_persist_partial_changes(self) -> None:
        """A rejected save() must not leave any column mutated -- the whole
        UPDATE is filtered out by the WHERE predicate, not applied then
        reverted, so there is nothing for a crash mid-write to partially
        commit."""
        assert self.engine is not None
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.research_orchestrations.insert(
                _orchestration(
                    state="COMPLETED",
                    stop_reason="MAX_CYCLES_REACHED",
                    last_hypothesis_id=None,
                )
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            current = uow.research_orchestrations.get("run-1")
            assert current is not None
            with self.assertRaises(TerminalOrchestrationStateError):
                uow.research_orchestrations.save(
                    replace(
                        current,
                        state="RUNNING",
                        stop_reason=None,
                        cycle_number=99,
                        current_phase="CYCLE_COMPLETE",
                    )
                )
            uow.rollback()
        with PostgresUnitOfWork(self.engine) as uow:
            reloaded = uow.research_orchestrations.get("run-1")
        assert reloaded is not None
        self.assertEqual(reloaded.state, "COMPLETED")
        self.assertEqual(reloaded.stop_reason, "MAX_CYCLES_REACHED")
        self.assertEqual(reloaded.cycle_number, 1)
        self.assertEqual(reloaded.current_phase, "CYCLE_READY")


if __name__ == "__main__":
    unittest.main()
