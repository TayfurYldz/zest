"""SD-G6 rate-limit enforcement integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
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
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.program_research_context import ProgramPolicyView
from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuditEventRecord,
    BudgetConsumptionRecord,
    ExecutionAttemptRecord,
    ExperimentRecord,
    ProgramPolicyRecord,
    RateLimitProfileRecord,
)
from zest.research.planning import plan_diagnostic_echo
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG6RateLimitIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)

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
            uow.rate_limit_profiles.insert(
                RateLimitProfileRecord(
                    profile_id="rl-1",
                    program_id="prog-1",
                    max_requests_per_window=1,
                    window_seconds=3600,
                    created_at=NOW,
                )
            )
            uow.experiments.insert(
                ExperimentRecord(
                    experiment_id="exp-2",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    budget_id="budget-1",
                    execution_state="PLANNED",
                    created_at=NOW,
                )
            )
            uow.commit()

    def test_program_request_query_and_profile_lock(self) -> None:
        with PostgresUnitOfWork(self.engine) as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id="ad-rate-2",
                    occurred_at=NOW,
                    actor_id="control-plane",
                    actor_type="CONTROL_PLANE",
                    event_type="EXECUTION_DECISION",
                    subject_type="experiment",
                    subject_id="exp-2",
                    payload={"decision": "ALLOW"},
                )
            )

            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id="ea-rate-2",
                    request_id="req-rate-2",
                    experiment_id="exp-2",
                    research_run_id="run-1",
                    correlation_id="corr-rate-2",
                    worker_capability="http.transaction",
                    action="read",
                    target_reference="target-1",
                    budget_id="budget-1",
                    side_effect_level=0,
                    authorization_decision_reference=(
                        "ad-rate-2"
                    ),
                    state="AUTHORIZED",
                    created_at=NOW,
                    authorized_at=NOW,
                )
            )

            uow.budget_consumptions.insert(
                BudgetConsumptionRecord(
                    consumption_id="cons-rate-2",
                    budget_id="budget-1",
                    research_run_id="run-1",
                    resource_type="REQUEST",
                    amount=1,
                    unit="count",
                    occurred_at=NOW,
                    provenance="sd-g6-rate-limit",
                    experiment_id="exp-2",
                    request_id="req-rate-2",
                )
            )

            uow.commit()

        with PostgresUnitOfWork(self.engine) as uow:
            profile = (
                uow.rate_limit_profiles
                .get_for_program_for_update(
                    "prog-1"
                )
            )

            self.assertIsNotNone(profile)

            reservations = (
                uow.budget_consumptions
                .list_program_request_reservations(
                    "prog-1",
                    window_start=(
                        NOW
                        - timedelta(seconds=3600)
                    ),
                    window_end=NOW,
                    worker_capabilities=(
                        "http.transaction",
                    ),
                )
            )

            self.assertEqual(
                len(reservations),
                1,
            )
            self.assertEqual(
                reservations[0].amount,
                1,
            )
            self.assertEqual(
                reservations[0].request_id,
                "req-rate-2",
            )

            uow.rollback()



if __name__ == "__main__":
    unittest.main()
