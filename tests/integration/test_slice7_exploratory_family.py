"""Slice 7 exploratory execution + human-gated family promotion.

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
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from zest.application.execute_exploratory_hypothesis import (
    ExecuteExploratoryHypothesis,
    ExecuteExploratoryHypothesisCommand,
)
from zest.application.promote_exploratory_family import (
    PromoteExploratoryFamily,
    PromoteExploratoryFamilyCommand,
)
from zest.core.enums import ActorType, ApprovalDecision, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.research.exploratory import ExploratorySignalKind
from zest.research.orchestration import OrchestrationBounds
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()
EXPLORATORY_FAMILY_NAME = "Unmapped Response Shape Coupling"


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _bounds() -> OrchestrationBounds:
    return OrchestrationBounds(
        max_cycles=2,
        max_experiments=2,
        max_model_calls=0,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )


def _draft(factory: PostgresUnitOfWorkFactory):
    return DraftExploratoryHypothesis(factory, clock=FixedClock()).execute(
        DraftExploratoryHypothesisCommand(
            research_run_id="run-1",
            proposed_family_name=EXPLORATORY_FAMILY_NAME,
            proposed_family_rationale="Lab-only response-shape coupling is not a registered family.",
            signals=(
                ExploratorySignalInput(
                    signal_id="sig-1",
                    kind=ExploratorySignalKind.LAB_ZERO_DAY_STYLE_ANOMALY.value,
                    description="A lab-only zero-day-style behavior changed the response shape.",
                    source_refs=("change-1",),
                    target_node_kind="ACTION",
                    attributes={"lab_fixture": "zero_day_style"},
                ),
            ),
            correlation_id="corr-slice7-pg",
        )
    )


def _enabled_names(engine) -> set[str]:
    with PostgresUnitOfWork(engine) as uow:
        names = {record.name for record in uow.hunter_families.list_enabled()}
        uow.rollback()
    return names


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class Slice7ExploratoryPostgresTests(unittest.TestCase):
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
            uow.commit()

    def test_execute_does_not_write_permanent_registry(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        drafted = _draft(factory)
        seed_names = {family["name"] for family in SEED_FAMILIES}
        port = RecordingWorkerPort()
        result = ExecuteExploratoryHypothesis(
            factory, port, clock=FixedClock()
        ).execute(
            ExecuteExploratoryHypothesisCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                budget_id="budget-1",
                target_reference="target-1",
                scope=_allow_scope(),
                bounds=_bounds(),
            )
        )
        self.assertEqual(result.compiler_outcome, "COMPILED")
        self.assertIsNotNone(result.assessment_id)
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(_enabled_names(self.engine), seed_names)
        self.assertNotIn(EXPLORATORY_FAMILY_NAME, _enabled_names(self.engine))

        reloaded = ExecuteExploratoryHypothesis(
            PostgresUnitOfWorkFactory(self.engine),
            RecordingWorkerPort(),
            clock=FixedClock(),
        ).execute(
            ExecuteExploratoryHypothesisCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                budget_id="budget-1",
                target_reference="target-1",
                scope=_allow_scope(),
                bounds=_bounds(),
            )
        )
        self.assertEqual(reloaded.compiler_reason, "ALREADY_ASSESSED")
        self.assertEqual(_enabled_names(self.engine), seed_names)

    def test_promotion_requires_human_approval_then_persists(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        drafted = _draft(factory)
        denied = PromoteExploratoryFamily(factory, clock=FixedClock()).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="control-plane",
                actor_type=ActorType.CONTROL_PLANE,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertFalse(denied.promoted)
        self.assertEqual(denied.reason_code, "APPROVAL_INVALID_ACTOR")
        self.assertNotIn(EXPLORATORY_FAMILY_NAME, _enabled_names(self.engine))

        promoted = PromoteExploratoryFamily(factory, clock=FixedClock()).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertTrue(promoted.promoted)
        self.assertIn(EXPLORATORY_FAMILY_NAME, _enabled_names(self.engine))
        again = PromoteExploratoryFamily(
            PostgresUnitOfWorkFactory(self.engine), clock=FixedClock()
        ).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertEqual(again.reason_code, "ALREADY_PROMOTED")
        self.assertEqual(again.family_id, promoted.family_id)


if __name__ == "__main__":
    unittest.main()
