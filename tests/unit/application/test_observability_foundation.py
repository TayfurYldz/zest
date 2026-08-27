from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pathsetup  # noqa: F401

from zest.application.observability import (
    SemanticPlane,
    activity_from_fault,
    project_effective_run_state,
)
from zest.data.records import (
    RunFaultClass,
    RunFaultComponent,
    RunFaultPhase,
    RunFaultRecord,
    RuntimeInstanceRecord,
    TargetContactStatus,
)
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _fault(*, fatal: bool = True) -> RunFaultRecord:
    return RunFaultRecord(
        fault_id="fault-1",
        research_run_id="run-1",
        component=RunFaultComponent.SUPERVISOR.value,
        phase=RunFaultPhase.TICK.value,
        fault_class=RunFaultClass.SUPERVISOR.value,
        fault_code="SUPERVISOR_TICK_FAILED",
        fatal=fatal,
        occurred_at=NOW,
        diagnostic_summary="worker failed with password=do-not-persist",
        runtime_instance_id="runtime-1",
    )


class RunFaultTests(unittest.TestCase):
    def test_fault_is_typed_sanitized_and_durable_in_unit_of_work(self) -> None:
        fault = _fault()
        self.assertNotIn("do-not-persist", fault.diagnostic_summary)
        self.assertIn("[REDACTED]", fault.diagnostic_summary)
        factory = FakeUnitOfWorkFactory(_Store())
        with factory.open() as uow:
            uow.run_faults.insert(fault)
            uow.commit()
        self.assertEqual(factory.store.run_faults["fault-1"], fault)

    def test_stale_running_is_runtime_fault_without_changing_persisted_state(self) -> None:
        orchestration = SimpleNamespace(
            state="RUNNING",
            owner_runtime_instance_id="runtime-1",
            lease_expires_at=NOW - timedelta(seconds=1),
        )
        runtime = RuntimeInstanceRecord(
            runtime_instance_id="runtime-1",
            host_identity="host-1",
            process_id="pid-1",
            engine_version="test",
            status="RUNNING",
            capabilities_summary={},
            started_at=NOW - timedelta(minutes=2),
            last_seen_at=NOW - timedelta(minutes=2),
        )
        projected = project_effective_run_state(
            orchestration,
            runtime,
            (_fault(),),
            now=NOW,
        )
        self.assertEqual(projected.persisted_state, "RUNNING")
        self.assertEqual(projected.effective_state, "RUNTIME_FAULT")
        self.assertIn("RUNTIME_HEARTBEAT_STALE", projected.reason_codes)
        self.assertIn("UNRESOLVED_SUPERVISOR_TICK_FAILED", projected.reason_codes)

    def test_fault_activity_is_typed_deterministic_and_not_evidence(self) -> None:
        activity = activity_from_fault(_fault(fatal=False))
        self.assertEqual(activity.plane, SemanticPlane.RUNTIME.value)
        self.assertEqual(activity.kind, "RUN_FAULT")
        self.assertEqual(activity.activity_id, "activity:fault-1")
        self.assertEqual(activity.summary, "material supervisor fault: SUPERVISOR_TICK_FAILED")
        self.assertEqual(activity.source_type, "run_fault")
        self.assertNotIn("evidence", activity.summary.lower())

    def test_attempt_contact_default_is_unknown(self) -> None:
        from zest.data.records import ExecutionAttemptRecord, ExecutionAttemptState

        attempt = ExecutionAttemptRecord(
            attempt_id="attempt-1",
            request_id="request-1",
            experiment_id="experiment-1",
            research_run_id="run-1",
            correlation_id="correlation-1",
            worker_capability="diagnostic.echo",
            action="echo",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="audit-1",
            state=ExecutionAttemptState.AUTHORIZED.value,
            created_at=NOW,
        )
        self.assertEqual(attempt.target_contact_status, TargetContactStatus.UNKNOWN.value)


if __name__ == "__main__":
    unittest.main()
