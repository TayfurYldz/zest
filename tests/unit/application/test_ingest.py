from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.ingest_worker_invocation import (
    IngestCompletedWorkerInvocation,
    IngestionStatus,
    observation_id_for,
    worker_result_id_for,
)
from zest.application.transition_a.diagnostic_echo import (
    DIAGNOSTIC_ECHO_NORMALIZER_VERSION,
)
from zest.data.records import (
    AuthorizationSourceRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.worker_requests import valid_worker_request

CREATED_AT = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 8, 16, 20, 0, tzinfo=timezone.utc)
COMPLETED_AT = datetime(2026, 8, 16, 20, 0, 1, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _seed(store: _Store, *, experiment_id: str = "exp-1", run_id: str = "run-1") -> None:
    now = CREATED_AT
    store.programs["prog-1"] = ProgramRecord(program_id="prog-1", created_at=now)
    store.authorization_sources["as-1"] = AuthorizationSourceRecord(
        authorization_source_id="as-1",
        program_id="prog-1",
        state="ACTIVE",
        provenance_reference="letter-1",
        created_at=now,
    )
    store.research_runs[run_id] = ResearchRunRecord(
        research_run_id=run_id,
        program_id="prog-1",
        authorization_source_id="as-1",
        initiated_by_actor_id="operator-1",
        initiated_by_actor_type="HUMAN_OPERATOR",
        started_at=now,
    )
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id=run_id,
        max_requests=1,
        max_tool_calls=1,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=now,
    )
    store.hypotheses["hyp-1"] = HypothesisRecord(
        hypothesis_id="hyp-1",
        research_run_id=run_id,
        claim="diagnostic path",
        created_at=now,
    )
    store.experiments[experiment_id] = ExperimentRecord(
        experiment_id=experiment_id,
        research_run_id=run_id,
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        execution_state="RUNNING",
        created_at=now,
    )


def _completed(request, *, status: str = "SUCCEEDED", raw_result=None, **result_fields):
    result = {
        "contract_version": "v1",
        "correlation": request["correlation"],
        "worker_id": "local-python-diagnostic",
        "status": status,
        "started_at": "2026-08-16T20:00:00Z",
        "completed_at": "2026-08-16T20:00:01Z",
        "raw_result": raw_result
        if raw_result is not None
        else {"echoed": "ping", "capability": "diagnostic.echo"},
    }
    result.update(result_fields)
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        worker_result=result,
        exit_code=0,
    )


def _use_case(store: _Store | None = None, fail_on: str | None = None):
    factory = FakeUnitOfWorkFactory(store=store or _Store(), fail_on=fail_on)
    if store is None:
        _seed(factory.store)
    return IngestCompletedWorkerInvocation(factory, clock=FixedClock()), factory


class IngestCompletedWorkerInvocationTests(unittest.TestCase):
    def test_diagnostic_success_ingests_one_observation(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(request, _completed(request))
        self.assertEqual(outcome.status, IngestionStatus.INGESTED)
        self.assertEqual(outcome.worker_result_id, worker_result_id_for("req-1"))
        self.assertEqual(
            outcome.observation_ids,
            (
                observation_id_for(
                    "req-1", "diagnostic.echo", DIAGNOSTIC_ECHO_NORMALIZER_VERSION
                ),
            ),
        )
        observation = factory.store.observations[outcome.observation_ids[0]]
        self.assertEqual(observation.payload, {"echoed": "ping"})
        self.assertEqual(observation.observed_at, COMPLETED_AT)
        self.assertEqual(observation.created_at, CREATED_AT)
        self.assertNotEqual(observation.observed_at, observation.created_at)
        self.assertFalse(hasattr(observation, "severity"))
        self.assertIn("ae:wr:req-1", factory.store.audit_events)
        self.assertEqual(
            factory.store.audit_events["ae:wr:req-1"].event_type,
            "WORKER_RESULT_INGESTED",
        )

    def test_replay_is_already_ingested_and_does_not_duplicate_observation(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        first = use_case.execute(request, _completed(request))
        second = use_case.execute(request, _completed(request))
        self.assertEqual(first.status, IngestionStatus.INGESTED)
        self.assertEqual(second.status, IngestionStatus.ALREADY_INGESTED)
        self.assertEqual(len(factory.store.observations), 1)
        self.assertEqual(len(factory.store.worker_results), 1)

    def test_equal_payloads_with_distinct_request_ids_are_not_collapsed(self) -> None:
        store = _Store()
        _seed(store)
        factory = FakeUnitOfWorkFactory(store=store)
        use_case = IngestCompletedWorkerInvocation(factory, clock=FixedClock())
        first_request = valid_worker_request()
        second_request = valid_worker_request()
        second_request["correlation"] = {
            **first_request["correlation"],
            "request_id": "req-2",
        }
        first = use_case.execute(first_request, _completed(first_request))
        second = use_case.execute(second_request, _completed(second_request))
        self.assertEqual(first.status, IngestionStatus.INGESTED)
        self.assertEqual(second.status, IngestionStatus.INGESTED)
        self.assertEqual(len(store.observations), 2)
        self.assertNotEqual(first.worker_result_id, second.worker_result_id)

    def test_blocked_result_persists_worker_result_without_observation(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(request, _completed(request, status="BLOCKED"))
        self.assertEqual(outcome.status, IngestionStatus.NO_OBSERVATION)
        self.assertEqual(len(factory.store.worker_results), 1)
        self.assertEqual(len(factory.store.observations), 0)

    def test_invalid_invocation_does_not_persist(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(
            request,
            WorkerInvocationOutcome(
                invocation_status=InvocationStatus.PROCESS_FAILED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                exit_code=1,
            ),
        )
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)
        self.assertEqual(len(factory.store.worker_results), 0)
        self.assertEqual(len(factory.store.observations), 0)

    def test_correlation_mismatch_rejected(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        completed = _completed(request)
        assert completed.worker_result is not None
        tampered = dict(completed.worker_result)
        tampered["correlation"] = {
            **request["correlation"],
            "correlation_id": "other",
        }
        outcome = use_case.execute(
            request,
            WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=tampered,
                exit_code=0,
            ),
        )
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)
        self.assertEqual(len(factory.store.worker_results), 0)

    def test_experiment_mismatch_rejected(self) -> None:
        store = _Store()
        _seed(store, experiment_id="exp-other")
        use_case, _factory = _use_case(store=store)
        request = valid_worker_request()
        outcome = use_case.execute(request, _completed(request))
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)

    def test_unsupported_normalizer_rejected(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        request["worker_capability"] = "not.a.scanner"
        request["action"] = "scan"
        outcome = use_case.execute(request, _completed(request))
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)
        self.assertEqual(len(factory.store.worker_results), 0)

    def test_malformed_payload_rejected(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(
            request, _completed(request, raw_result={"nope": True})
        )
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)
        self.assertEqual(len(factory.store.worker_results), 0)

    def test_injected_observation_failure_rolls_back(self) -> None:
        store = _Store()
        _seed(store)
        use_case, factory = _use_case(store=store, fail_on="observations")
        request = valid_worker_request()
        with self.assertRaises(Exception):
            use_case.execute(request, _completed(request))
        self.assertEqual(len(factory.store.worker_results), 0)
        self.assertEqual(len(factory.store.observations), 0)
        self.assertEqual(len(factory.store.audit_events), 0)

    def test_process_success_alone_cannot_create_observation(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(
            request,
            WorkerInvocationOutcome(
                invocation_status=InvocationStatus.PROTOCOL_ERROR,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                reason="not a result",
            ),
        )
        self.assertEqual(outcome.status, IngestionStatus.REJECTED_INVALID_INVOCATION)
        self.assertEqual(len(factory.store.observations), 0)

    def test_observation_is_not_a_finding(self) -> None:
        use_case, factory = _use_case()
        request = valid_worker_request()
        outcome = use_case.execute(request, _completed(request))
        observation = factory.store.observations[outcome.observation_ids[0]]
        self.assertFalse(hasattr(observation, "finding_id"))
        self.assertNotIn("finding", observation.payload)
        self.assertNotIn("evidence", observation.payload)


if __name__ == "__main__":
    unittest.main()
