"""PostgreSQL GATE 18 plan binding tests. SQLite is not a substitute."""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from alembic import command
from alembic.config import Config

from zest.application.capability_binding import CapabilityBindingError, capability_view_for_plan
from zest.application.plan_records import experiment_plan_from_record, experiment_plan_record_for
from zest.core.authorization import AuthorizationSourceView
from zest.core.budget import BudgetUsage, IssuedBudget
from zest.core.enums import AuthorizationSourceState, ExecutionDecisionKind, ReasonCode, ScopeRuleEffect
from zest.core.execution import ExecutionRequest, evaluate_execution
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.research.planning import plan_diagnostic_echo
from zest.tools.registry import load_capability_registry
from integration.harness import alembic_upgrade, configured_test_url, seed_authorized_spine, truncate_spine

TEST_URL = configured_test_url()


def _alembic_cfg(url: str) -> Config:
    cfg = Config(str(_REPO / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate18PlanBindingTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_new_plan_persists_version_and_fingerprint(self) -> None:
        plan = plan_diagnostic_echo(
            "hyp-1", budget_id="budget-1", target_reference="target-1", message="ping"
        )
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            experiment = uow.experiments.get("exp-1")
            assert experiment is not None
            uow.experiment_plans.insert(
                experiment_plan_record_for(
                    experiment, plan, created_at=datetime(2026, 8, 17, tzinfo=timezone.utc)
                )
            )
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert stored is not None
        echo = load_capability_registry().get("diagnostic.echo")
        assert echo is not None
        self.assertEqual(stored.capability_version, echo.version)
        self.assertEqual(stored.capability_definition_fingerprint, echo.definition_fingerprint)
        self.assertIsNotNone(stored.capability_version)

    def test_legacy_null_binding_is_readable_without_backfill(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        assert self.engine is not None
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO experiment_plan ("
                    "experiment_id, research_run_id, hypothesis_id, required_capability, "
                    "action, target_reference, side_effect_level, arguments, requested_budget_id, "
                    "expected_observation, disconfirming_observation, evaluation_strategy, created_at"
                    ") VALUES ("
                    "'exp-1', 'run-1', 'hyp-1', 'diagnostic.echo', 'echo', 'target-1', 0, "
                    "CAST(:args AS jsonb), 'budget-1', 'echoed value matches input', "
                    "'no result or mismatched value', 'diagnostic.echo.v1', :created)"
                ),
                {
                    "args": '{"message":"ping"}',
                    "created": datetime(2026, 8, 17, tzinfo=timezone.utc),
                },
            )
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert stored is not None
        self.assertIsNone(stored.capability_version)
        self.assertIsNone(stored.capability_definition_fingerprint)
        loaded = experiment_plan_from_record(stored)
        view = capability_view_for_plan(loaded)
        echo = load_capability_registry().get("diagnostic.echo")
        assert echo is not None
        self.assertEqual(view.definition_fingerprint, echo.definition_fingerprint)
        with PostgresUnitOfWork(self.engine) as uow:
            again = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert again is not None
        self.assertIsNone(again.capability_definition_fingerprint)

    def test_bound_aaa_plan_does_not_dispatch_under_bbb(self) -> None:
        echo = load_capability_registry().get("diagnostic.echo")
        assert echo is not None
        plan = plan_diagnostic_echo(
            "hyp-1", budget_id="budget-1", target_reference="target-1", message="ping"
        )
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            experiment = uow.experiments.get("exp-1")
            assert experiment is not None
            record = experiment_plan_record_for(
                experiment, plan, created_at=datetime(2026, 8, 17, tzinfo=timezone.utc)
            )
            record = replace(record, capability_definition_fingerprint="a" * 64)
            uow.experiment_plans.insert(record)
            uow.commit()
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert stored is not None
        self.assertEqual(stored.capability_definition_fingerprint, "a" * 64)
        loaded = experiment_plan_from_record(stored)
        view = capability_view_for_plan(loaded)
        echo = load_capability_registry().get("diagnostic.echo")
        assert echo is not None
        decision = evaluate_execution(
            ExecutionRequest(
                authorization_source=AuthorizationSourceView(
                    "as-1", "prog-1", AuthorizationSourceState.ACTIVE
                ),
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
                    ambiguous=False,
                ),
                issued_budget=IssuedBudget("budget-1", 10, 10, 10_000, 1),
                budget_usage=BudgetUsage(0, 0, 0, 0),
                requested_budget_id="budget-1",
                side_effect_level=0,
                requested_subject="target-1",
                capability=view,
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.DEFINITION_FINGERPRINT_MISMATCH)
        with PostgresUnitOfWork(self.engine) as uow:
            unchanged = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert unchanged is not None
        self.assertEqual(unchanged.capability_definition_fingerprint, "a" * 64)

    def test_legacy_below_current_minimum_fails_closed(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.commit()
        assert self.engine is not None
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO experiment_plan ("
                    "experiment_id, research_run_id, hypothesis_id, required_capability, "
                    "action, target_reference, side_effect_level, arguments, requested_budget_id, "
                    "expected_observation, disconfirming_observation, evaluation_strategy, created_at"
                    ") VALUES ("
                    "'exp-1', 'run-1', 'hyp-1', 'http.state_transition', 'probe', 'target-1', 0, "
                    "CAST(:args AS jsonb), 'budget-1', 'x', 'y', 'http.state_transition.v1', :created)"
                ),
                {
                    "args": '{"authorized_origin":"http://127.0.0.1:9","actor":"a","resource_id":"r1","transition":"submit","area":"workflow"}',
                    "created": datetime(2026, 8, 17, tzinfo=timezone.utc),
                },
            )
        with PostgresUnitOfWork(self.engine) as uow:
            stored = uow.experiment_plans.get("exp-1")
            uow.rollback()
        assert stored is not None
        with self.assertRaises(CapabilityBindingError) as ctx:
            capability_view_for_plan(experiment_plan_from_record(stored))
        self.assertEqual(ctx.exception.reason_code, "RISK_UNDERSTATEMENT")

    def test_a19_a20_round_trip(self) -> None:
        assert TEST_URL is not None
        cfg = _alembic_cfg(TEST_URL)
        try:
            command.downgrade(cfg, "a19_001_http_state_class")
            assert self.engine is not None
            with self.engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                has_fp = connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'experiment_plan' "
                        "AND column_name = 'capability_definition_fingerprint'"
                    )
                ).scalar()
            self.assertEqual(version, "a19_001_http_state_class")
            self.assertIsNone(has_fp)
            command.upgrade(cfg, "a20_001_capability_plan_binding")
            command.downgrade(cfg, "a19_001_http_state_class")
            command.upgrade(cfg, "a20_001_capability_plan_binding")
            with self.engine.connect() as connection:
                version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                has_fp = connection.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'experiment_plan' "
                        "AND column_name = 'capability_definition_fingerprint'"
                    )
                ).scalar()
            self.assertEqual(version, "a20_001_capability_plan_binding")
            self.assertIsNotNone(has_fp)
        finally:
            command.upgrade(cfg, "head")


if __name__ == "__main__":
    unittest.main()
