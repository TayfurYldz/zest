from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationResolution,
)
from zest.application.orchestration_config import fingerprint_for_start
from zest.data.records import ExecutionAttemptRecord, ResearchOrchestrationRecord
from zest.research.orchestration import OrchestrationBounds
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _attempt(state: str, *, side_effect_level: int = 0) -> ExecutionAttemptRecord:
    return ExecutionAttemptRecord(
        attempt_id="ea-1",
        request_id="req-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        correlation_id="corr-1",
        worker_capability="diagnostic.echo",
        action="echo",
        target_reference="target-1",
        budget_id="budget-1",
        side_effect_level=side_effect_level,
        authorization_decision_reference="ae-1",
        state=state,
        created_at=CREATED_AT,
        authorized_at=CREATED_AT,
    )


class ReconcileResearchRunTests(unittest.TestCase):
    def test_authorized_never_dispatched_is_safe_for_level0(self) -> None:
        store = _Store()
        seed_spine(store)
        store.execution_attempts["ea-1"] = _attempt("AUTHORIZED")
        result = ReconcileResearchRun(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            ReconcileResearchRunCommand("run-1")
        )
        self.assertEqual(result.items[0].resolution, ReconciliationResolution.SAFE_TO_RETRY)

    def test_dispatching_is_unknown_and_not_safe_retry(self) -> None:
        store = _Store()
        seed_spine(store)
        store.execution_attempts["ea-1"] = _attempt("DISPATCHING")
        result = ReconcileResearchRun(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            ReconcileResearchRunCommand("run-1")
        )
        self.assertEqual(result.items[0].resolution, ReconciliationResolution.UNKNOWN_OUTCOME)

    def test_side_effectful_unknown_requires_human(self) -> None:
        store = _Store()
        seed_spine(store)
        store.execution_attempts["ea-1"] = _attempt("UNKNOWN_OUTCOME", side_effect_level=2)
        result = ReconcileResearchRun(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            ReconcileResearchRunCommand("run-1")
        )
        self.assertEqual(result.items[0].resolution, ReconciliationResolution.REQUIRE_HUMAN_REVIEW)

    def test_stale_running_orchestration(self) -> None:
        store = _Store()
        seed_spine(store)
        bounds = OrchestrationBounds(
            max_cycles=3,
            max_experiments=3,
            max_model_calls=12,
            max_worker_invocations=3,
            max_elapsed_ms=60_000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
            allow_repeated_control_experiments=False,
        )
        fingerprint = fingerprint_for_start(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            research_question="q",
            policy_version="orchestration.bounded.v1",
            bounds=bounds,
            routing_policy_version=None,
            scope_fp=None,
        )
        store.research_orchestrations["run-1"] = ResearchOrchestrationRecord(
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
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            checkpoint_at=CREATED_AT,
            budget_id="budget-1",
            target_reference="target-1",
            research_question="q",
            configuration_fingerprint=fingerprint,
            current_phase="CYCLE_READY",
        )
        result = ReconcileResearchRun(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            ReconcileResearchRunCommand("run-1", stale_running=True)
        )
        self.assertTrue(
            any(
                item.resolution is ReconciliationResolution.MARK_OPERATIONAL_FAILURE
                for item in result.items
            )
        )


if __name__ == "__main__":
    unittest.main()
