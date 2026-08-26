from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.executor_fabric_assessment import (
    AssessExecutorFabricExperiment,
    AssessExecutorFabricExperimentCommand,
)
from zest.data.records import (
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    ExperimentRecord,
    WorkerResultRecord,
)
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import seed_spine

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_experiment(
    store: _Store,
    *,
    capability: str = "browser.page",
    action: str = "navigate",
    status: str = "SUCCEEDED",
    raw_result: dict | None = None,
    diagnostics: dict | None = None,
    side_effect_level: int = 0,
) -> None:
    seed_spine(store)
    store.experiments["exp-1"] = ExperimentRecord(
        experiment_id="exp-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        execution_state="EXECUTION_SUCCEEDED",
        created_at=NOW,
    )
    store.experiment_plans["exp-1"] = ExperimentPlanRecord(
        experiment_id="exp-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        required_capability=capability,
        action=action,
        target_reference="target-1",
        side_effect_level=side_effect_level,
        arguments={"authorized_origin": "http://127.0.0.1:1", "path": "/"},
        requested_budget_id="budget-1",
        expected_observation="response returned",
        disconfirming_observation="response did not return",
        evaluation_strategy=f"{capability}.v1",
        created_at=NOW,
        capability_version="v1",
        capability_definition_fingerprint="f" * 64,
    )
    store.execution_attempts["attempt-1"] = ExecutionAttemptRecord(
        attempt_id="attempt-1",
        request_id="request-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        correlation_id="corr-1",
        worker_capability=capability,
        action=action,
        target_reference="target-1",
        budget_id="budget-1",
        side_effect_level=side_effect_level,
        authorization_decision_reference="authz-1",
        state="COMPLETED",
        created_at=NOW,
        authorized_at=NOW,
        dispatch_started_at=NOW,
        completed_at=NOW,
    )
    store.execution_attempts_by_request["request-1"] = "attempt-1"
    store.worker_results["wr-1"] = WorkerResultRecord(
        worker_result_id="wr-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        request_id="request-1",
        correlation_id="corr-1",
        worker_capability=capability,
        action=action,
        authorization_decision_reference="authz-1",
        budget_id="budget-1",
        side_effect_level=side_effect_level,
        contract_version="v1",
        worker_id="worker-1",
        status=status,
        received_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        raw_result=raw_result or {"snapshot_digest": "abc123", "self_authorized": False},
        diagnostics=diagnostics,
        raw_artifact_descriptors=[
            {"kind": "screenshot", "path": "/tmp/browser-sensitive-shot.png"},
            {"kind": "trace", "path": "/tmp/browser-sensitive-trace.zip"},
        ],
    )
    store.worker_results_by_request["request-1"] = "wr-1"


class ExecutorFabricAssessmentTests(unittest.TestCase):
    def test_browser_ledger_is_environment_sensitive_but_fabric_passes(self) -> None:
        store = _Store()
        _seed_experiment(store)
        use_case = AssessExecutorFabricExperiment(FakeUnitOfWorkFactory(store))

        result = use_case.execute(AssessExecutorFabricExperimentCommand("exp-1"))

        self.assertEqual(result.assessment_status, "PASS")
        self.assertEqual(result.assessment["replay_class"], "ENVIRONMENT_SENSITIVE")
        self.assertEqual(result.assessment["capability_surface"], ("browser.page",))
        self.assertEqual(result.assessment["invariants"]["self_authorized_count"], 0)
        self.assertIn("WORKER_SELF_AUTHORIZATION_ABSENT", result.reason_codes)
        blob = str(result.assessment).lower()
        self.assertNotIn("browser-sensitive-shot", blob)
        self.assertNotIn("browser-sensitive-trace", blob)

    def test_redirect_follow_violation_fails_assessment(self) -> None:
        store = _Store()
        _seed_experiment(
            store,
            capability="http.transaction",
            action="read",
            status="SUCCEEDED",
            raw_result={"status": 302, "self_authorized": False},
            diagnostics={
                "redirect": True,
                "followed": True,
                "requires_core_re_evaluation": False,
            },
        )

        result = AssessExecutorFabricExperiment(FakeUnitOfWorkFactory(store)).execute(
            AssessExecutorFabricExperimentCommand("exp-1")
        )

        self.assertEqual(result.assessment_status, "FAIL")
        self.assertIn("VIOLATION_REDIRECT_DID_NOT_STOP", result.reason_codes)
        self.assertIn("VIOLATION_REDIRECT_FOLLOWED", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
