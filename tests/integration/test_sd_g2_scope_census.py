"""SD-G2 scope/census integration: UNKNOWN allows census, OUT_OF_SCOPE denies.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent (PENDING, not PASS).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.program_research_context import (
    load_program_research_context,
)
from zest.application.sensor.runner import SensorAcquisitionRunner
from zest.core.enums import ReasonCode, ScopeClassification, ScopeRuleEffect
from zest.core.scope_compiler import evaluate_scope_candidate
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import ProgramPolicyRecord, ScopeRuleV2Record
from zest.platform.url_normalize import normalize_url
from zest.research.sensor import DNSSensor
from zest.research.sensor.fixture_loader import FileFixtureLoader
from zest.research.sensor.types import ScopeCensusView

TEST_URL = configured_test_url()
FIXTURE_DIR = _REPO / "tests" / "fixtures" / "sensor"


def _scope_view_from_target(program_context, target: str) -> ScopeCensusView:
    check = evaluate_scope_candidate(
        normalize_url(target),
        program_context.compiled_scope,
    )
    return ScopeCensusView(
        classification=check.classification,
        reason_code=check.reason_code,
        matched_rule_ids=check.matched_rule_ids,
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG2ScopeCensusIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id="prog-1",
                    loopback_fixture=False,
                    max_response_bytes=4096,
                    timeout_ms=2000,
                    created_at=NOW,
                    updated_at=NOW,
                    action_policy={},
                )
            )
            uow.scope_rules_v2.insert(
                ScopeRuleV2Record(
                    rule_id="rule-allow",
                    program_id="prog-1",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="allowed.example.com",
                    source_reference="scope-src",
                    created_at=NOW,
                )
            )
            uow.scope_rules_v2.insert(
                ScopeRuleV2Record(
                    rule_id="rule-deny",
                    program_id="prog-1",
                    effect=ScopeRuleEffect.OUT_OF_SCOPE,
                    scheme="https",
                    host="denied.example.com",
                    source_reference="scope-src",
                    created_at=NOW,
                )
            )
            uow.commit()

    def test_unknown_target_allows_census(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            context = load_program_research_context(uow, "prog-1")
            uow.rollback()
        self.assertIsNotNone(context)

        scope_view = _scope_view_from_target(context, "https://unknown.example.com")
        self.assertEqual(scope_view.classification, ScopeClassification.UNKNOWN)
        self.assertEqual(scope_view.reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

        runner = SensorAcquisitionRunner(
            uow_factory,
            [DNSSensor(FileFixtureLoader(FIXTURE_DIR))],
        )
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://unknown.example.com",
            scope_view=scope_view,
        )

        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(result.errors), 0)

        with PostgresUnitOfWork(self.engine) as uow:
            records = uow.sensor_observations.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(len(records), 1)

    def test_out_of_scope_target_denies_census(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            context = load_program_research_context(uow, "prog-1")
            uow.rollback()
        self.assertIsNotNone(context)

        scope_view = _scope_view_from_target(context, "https://denied.example.com")
        self.assertEqual(scope_view.classification, ScopeClassification.OUT_OF_SCOPE)
        self.assertEqual(scope_view.reason_code, ReasonCode.SCOPE_DENIED)

        runner = SensorAcquisitionRunner(
            uow_factory,
            [DNSSensor(FileFixtureLoader(FIXTURE_DIR))],
        )
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://denied.example.com",
            scope_view=scope_view,
        )

        self.assertEqual(len(result.observations), 0)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].reason_code, ReasonCode.CENSUS_DENIED)
        self.assertEqual(result.budget_units_consumed, 0)

        with PostgresUnitOfWork(self.engine) as uow:
            records = uow.sensor_observations.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(len(records), 0)

    def test_in_scope_target_allows_census(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            context = load_program_research_context(uow, "prog-1")
            uow.rollback()
        self.assertIsNotNone(context)

        scope_view = _scope_view_from_target(context, "https://allowed.example.com")
        self.assertEqual(scope_view.classification, ScopeClassification.IN_SCOPE)

        runner = SensorAcquisitionRunner(
            uow_factory,
            [DNSSensor(FileFixtureLoader(FIXTURE_DIR))],
        )
        result = runner.run(
            research_run_id="run-1",
            target_reference="https://allowed.example.com",
            scope_view=scope_view,
        )

        self.assertEqual(len(result.observations), 1)
        self.assertEqual(len(result.errors), 0)


if __name__ == "__main__":
    unittest.main()
