from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.executor_replay_bundle import (
    BuildExecutorReplayBundle,
    BuildExecutorReplayBundleCommand,
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


def _seed_replayable_bundle(
    store: _Store,
    *,
    side_effect_level: int = 0,
    with_plan: bool = True,
) -> None:
    seed_spine(store)
    experiment = ExperimentRecord(
        experiment_id="exp-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        execution_state="EXECUTION_SUCCEEDED",
        created_at=NOW,
    )
    store.experiments["exp-1"] = experiment
    if with_plan:
        store.experiment_plans["exp-1"] = ExperimentPlanRecord(
            experiment_id="exp-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            required_capability="http.transaction",
            action="get",
            target_reference="target-1",
            side_effect_level=side_effect_level,
            arguments={
                "authorized_origin": "https://target.example",
                "path": "/private-sensitive-path",
                "header_value": "sensitive-plan-header-value",
            },
            requested_budget_id="budget-1",
            expected_observation="response returned",
            disconfirming_observation="response did not return",
            evaluation_strategy="http.transaction.v1",
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
        worker_capability="http.transaction",
        action="get",
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
        worker_capability="http.transaction",
        action="get",
        authorization_decision_reference="authz-1",
        budget_id="budget-1",
        side_effect_level=side_effect_level,
        contract_version="v1",
        worker_id="worker-1",
        status="SUCCEEDED",
        received_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        raw_result={
            "status_code": 200,
            "body": "sensitive-response-body",
            "body_marker": "hidden-session-value",
        },
        diagnostics={"message": "sensitive-diagnostic"},
        control_signal={"marker": "hidden-csrf-value"},
        raw_artifact_descriptors=[
            {
                "kind": "screenshot",
                "path": "/tmp/sensitive-screenshot.png",
                "marker": "hidden-cookie-value",
            },
            {"kind": "trace", "path": "/tmp/sensitive-trace.zip"},
        ],
    )
    store.worker_results_by_request["request-1"] = "wr-1"


class ExecutorReplayBundleTests(unittest.TestCase):
    def test_bundle_is_deterministic_secret_free_and_forbids_redispatch(self) -> None:
        store = _Store()
        _seed_replayable_bundle(store)
        use_case = BuildExecutorReplayBundle(FakeUnitOfWorkFactory(store))

        first = use_case.execute(BuildExecutorReplayBundleCommand("exp-1"))
        second = use_case.execute(BuildExecutorReplayBundleCommand("exp-1"))

        self.assertEqual(first.bundle_hash, second.bundle_hash)
        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(first.replay_class, "DETERMINISTIC_REPLAY")
        controls = first.bundle["replay_controls"]
        self.assertFalse(controls["auto_redispatch_allowed"])
        self.assertTrue(controls["requires_core_authorization"])
        self.assertTrue(controls["requires_redirect_reauthorization"])
        self.assertEqual(controls["worker_redispatch"], "FORBIDDEN_BY_BUNDLE")
        template = first.bundle["request_template"]
        self.assertEqual(template["template_state"], "PLAN_BOUND")
        self.assertEqual(template["required_capability"], "http.transaction")
        self.assertIsNotNone(template["argument_digest"])
        artifacts = first.bundle["artifact_descriptors"]
        self.assertEqual(tuple(item["artifact_kind"] for item in artifacts), ("screenshot", "trace"))
        self.assertTrue(all(item["descriptor_digest"] for item in artifacts))
        blob = str(first.bundle).lower()
        self.assertNotIn("sensitive-plan-header-value", blob)
        self.assertNotIn("private-sensitive-path", blob)
        self.assertNotIn("sensitive-response-body", blob)
        self.assertNotIn("hidden-session-value", blob)
        self.assertNotIn("hidden-csrf-value", blob)
        self.assertNotIn("sensitive-screenshot", blob)
        self.assertNotIn("sensitive-trace", blob)
        self.assertEqual(
            first.bundle["redaction_metadata"]["response_redactions"], 0
        )
        self.assertEqual(
            first.bundle["redaction_metadata"]["artifact_descriptor_redactions"], 0
        )

    def test_missing_plan_is_explicit_not_silent(self) -> None:
        store = _Store()
        _seed_replayable_bundle(store, with_plan=False)

        result = BuildExecutorReplayBundle(FakeUnitOfWorkFactory(store)).execute(
            BuildExecutorReplayBundleCommand("exp-1")
        )

        self.assertEqual(result.bundle["request_template"], {"template_state": "PLAN_MISSING"})
        self.assertEqual(result.replay_class, "DETERMINISTIC_REPLAY")

    def test_high_side_effect_bundle_requires_human_review(self) -> None:
        store = _Store()
        _seed_replayable_bundle(store, side_effect_level=2)
        store.worker_results["wr-1"] = replace(
            store.worker_results["wr-1"],
            side_effect_level=2,
        )

        result = BuildExecutorReplayBundle(FakeUnitOfWorkFactory(store)).execute(
            BuildExecutorReplayBundleCommand("exp-1")
        )

        self.assertEqual(result.replay_class, "HUMAN_REVIEW_REQUIRED")
        self.assertTrue(result.bundle["replay_controls"]["requires_human_review"])
        self.assertFalse(result.bundle["replay_controls"]["auto_redispatch_allowed"])


if __name__ == "__main__":
    unittest.main()
