from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.executor_replay_manifest import (
    BuildExecutorReplayManifest,
    BuildExecutorReplayManifestCommand,
)
from zest.data.records import (
    ExecutionAttemptRecord,
    ExperimentRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import seed_spine

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _seed_attempt(
    store: _Store,
    *,
    capability: str = "http.transaction",
    action: str = "get",
    side_effect_level: int = 0,
    with_result: bool = True,
    status: str = "SUCCEEDED",
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
    if with_result:
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
            raw_result={
                "status_code": 200,
                "body": "sensitive-body-value",
            },
            diagnostics={"message": "sensitive-diagnostic-value"},
            raw_artifact_descriptors=[
                {"kind": "trace", "path": "sensitive-artifact-path"}
            ],
        )
        store.worker_results_by_request["request-1"] = "wr-1"
        store.observations["obs-1"] = ObservationRecord(
            observation_id="obs-1",
            worker_result_id="wr-1",
            observation_kind="HTTP_RESPONSE",
            payload={"status_code": 200, "body": "sensitive-observation-value"},
            normalization_version="http.response.v1",
            observed_at=NOW,
            created_at=NOW,
        )


class ExecutorReplayManifestTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_secret_free(self) -> None:
        store = _Store()
        _seed_attempt(store)
        use_case = BuildExecutorReplayManifest(FakeUnitOfWorkFactory(store))

        first = use_case.execute(BuildExecutorReplayManifestCommand("exp-1"))
        second = use_case.execute(BuildExecutorReplayManifestCommand("exp-1"))

        self.assertEqual(first.manifest_hash, second.manifest_hash)
        self.assertEqual(first.replay_class, "DETERMINISTIC_REPLAY")
        self.assertEqual(first.reason_codes, ("REPLAY_MANIFEST_READY",))
        blob = str(first.manifest).lower()
        self.assertNotIn("sensitive-body-value", blob)
        self.assertNotIn("sensitive-diagnostic-value", blob)
        self.assertNotIn("sensitive-artifact-path", blob)
        self.assertNotIn("sensitive-observation-value", blob)
        worker_result = first.manifest["attempts"][0]["worker_result"]
        self.assertIsNotNone(worker_result["raw_result_digest"])
        self.assertIsNotNone(worker_result["diagnostics_digest"])
        self.assertIsNotNone(worker_result["artifact_descriptor_digest"])
        observation = first.manifest["attempts"][0]["observations"][0]
        self.assertIsNotNone(observation["payload_digest"])

    def test_missing_worker_result_is_not_replayable(self) -> None:
        store = _Store()
        _seed_attempt(store, with_result=False)

        result = BuildExecutorReplayManifest(FakeUnitOfWorkFactory(store)).execute(
            BuildExecutorReplayManifestCommand("exp-1")
        )

        self.assertEqual(result.replay_class, "NOT_REPLAYABLE")
        self.assertEqual(result.reason_codes, ("WORKER_RESULT_MISSING",))

    def test_browser_result_is_environment_sensitive(self) -> None:
        store = _Store()
        _seed_attempt(store, capability="browser.page", action="navigate")

        result = BuildExecutorReplayManifest(FakeUnitOfWorkFactory(store)).execute(
            BuildExecutorReplayManifestCommand("exp-1")
        )

        self.assertEqual(result.replay_class, "ENVIRONMENT_SENSITIVE")
        self.assertEqual(result.reason_codes, ("BROWSER_STATE_ENVIRONMENT_SENSITIVE",))

    def test_stateful_side_effect_is_environment_sensitive(self) -> None:
        store = _Store()
        _seed_attempt(store, action="post", side_effect_level=1)

        result = BuildExecutorReplayManifest(FakeUnitOfWorkFactory(store)).execute(
            BuildExecutorReplayManifestCommand("exp-1")
        )

        self.assertEqual(result.replay_class, "ENVIRONMENT_SENSITIVE")
        self.assertEqual(
            result.reason_codes,
            ("STATEFUL_SIDE_EFFECT_ENVIRONMENT_SENSITIVE",),
        )


if __name__ == "__main__":
    unittest.main()
