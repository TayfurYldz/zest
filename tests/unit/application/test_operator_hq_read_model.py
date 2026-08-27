from __future__ import annotations

import json
import unittest
from unittest import mock

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pathsetup  # noqa: F401

from zest.application.operator_hq_read_model import (
    MAX_ITEMS_PER_COLLECTION,
    _BoundedRepositoryProxy,
    _bundle,
    _safe_value,
    build_hq_run_analysis,
)
from zest.data.records import (
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ResearchOrchestrationRecord,
    RunFaultClass,
    RunFaultComponent,
    RunFaultPhase,
    RunFaultRecord,
    TargetContactStatus,
)
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import seed_spine


CREATED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class DemoRecord:
    record_id: str
    created_at: datetime
    payload: dict[str, object]


class OperatorHqReadModelTests(unittest.TestCase):
    def test_failed_run_replay_is_authoritative_and_does_not_fabricate_evidence(self) -> None:
        store = _Store()
        seed_spine(store)
        store.research_orchestrations["run-1"] = ResearchOrchestrationRecord(
            research_run_id="run-1",
            state="FAILED_OPERATIONAL",
            cycle_number=1,
            last_phase="DISPATCHING",
            policy_version="orchestration.test.v1",
            max_cycles=1,
            max_experiments=1,
            max_model_calls=4,
            max_worker_invocations=4,
            max_elapsed_ms=60_000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
            allow_repeated_control_experiments=True,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
            checkpoint_at=CREATED_AT,
            budget_id="budget-1",
            target_reference="target-1",
            research_question="diagnostic failure replay",
            configuration_fingerprint="a" * 64,
            current_phase="DISPATCHING",
            last_attempt_id="attempt-1",
        )
        store.execution_attempts["attempt-1"] = ExecutionAttemptRecord(
            attempt_id="attempt-1",
            request_id="request-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="correlation-1",
            worker_capability="browser.fetch",
            action="fetch",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="audit-1",
            state=ExecutionAttemptState.FAILED.value,
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
            dispatch_started_at=CREATED_AT,
            completed_at=CREATED_AT,
            target_contact_status=TargetContactStatus.UNKNOWN.value,
        )
        store.run_faults["fault-1"] = RunFaultRecord(
            fault_id="fault-1",
            research_run_id="run-1",
            component=RunFaultComponent.EXECUTION.value,
            phase=RunFaultPhase.INVOCATION.value,
            fault_class=RunFaultClass.EXECUTION.value,
            fault_code="EXECUTION_FAILED",
            fatal=True,
            occurred_at=CREATED_AT,
            diagnostic_summary="browser invocation failed",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            attempt_id="attempt-1",
            request_id="request-1",
            capability="browser.fetch",
            action="fetch",
            correlation_id="correlation-1",
        )
        store.research_reasoning["reasoning-1"] = SimpleNamespace(
            reasoning_record_id="reasoning-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            role="generator",
            model_id="test-model",
            created_at=CREATED_AT,
            structured_output={"hidden_reasoning": "must not be exposed"},
        )

        payload = build_hq_run_analysis(FakeUnitOfWorkFactory(store), "run-1")
        encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual(payload["read_model_schema"], "hq.run.read-model.v1")
        self.assertEqual(payload["truth"]["persisted_lifecycle_state"], "FAILED_OPERATIONAL")
        self.assertTrue(payload["truth"]["human_attention_required"])
        self.assertEqual(
            payload["current_research_lineage"]["stages"]["execution_attempt"]["items"][0]["attempt_id"],
            "attempt-1",
        )
        self.assertEqual(
            payload["current_research_lineage"]["stages"]["worker_result"]["status"],
            "UNKNOWN",
        )
        self.assertEqual(payload["failure_inspector"]["fault"]["fault_id"], "fault-1")
        self.assertFalse(payload["failure_inspector"]["worker_result_present"])
        self.assertFalse(payload["failure_inspector"]["observation_present"])
        self.assertFalse(payload["failure_inspector"]["evidence_present"])
        self.assertEqual(
            payload["failure_inspector"]["target_contact_status"],
            TargetContactStatus.UNKNOWN.value,
        )
        self.assertEqual(payload["active_pipeline"][2]["status"], "PRESENT")
        self.assertEqual(payload["active_pipeline"][3]["record_id"], "attempt-1")
        self.assertEqual(payload["motors"]["browser_worker"]["last_outcome"], "FAILED")
        self.assertEqual(
            payload["failure_inspector"]["retry_classification"]["classification"],
            "UNSAFE_UNKNOWN_OUTCOME",
        )
        warning_codes = {item["code"] for item in payload["state_consistency_warnings"]}
        self.assertIn("NO_WORKER_RESULT", warning_codes)
        self.assertIn("FAILED_ATTEMPT_CONTACT_UNKNOWN", warning_codes)
        self.assertNotIn("hidden_reasoning", encoded)
        self.assertNotIn("structured_output", encoded)

    def test_missing_orchestration_keeps_operational_truth_unknown(self) -> None:
        store = _Store()
        seed_spine(store)

        payload = build_hq_run_analysis(FakeUnitOfWorkFactory(store), "run-1")

        self.assertEqual(payload["truth"]["persisted_lifecycle_state"], "UNKNOWN")
        self.assertEqual(payload["truth"]["effective_operational_state"], "UNKNOWN")
        self.assertEqual(payload["truth"]["runtime_liveness"], "UNKNOWN")
        self.assertIsNone(payload["failure_inspector"])
        self.assertEqual(payload["counters"]["response_received"], None)

    def test_research_intent_and_verification_projection_use_persisted_records_only(self) -> None:
        store = _Store()
        seed_spine(store)
        store.hypotheses["hyp-1"] = SimpleNamespace(
            hypothesis_id="hyp-1", research_run_id="run-1", claim="bounded claim",
            identity_id="identity-1", origin_reference="source-1", created_at=CREATED_AT,
        )
        store.research_opportunities["opp-1"] = SimpleNamespace(
            opportunity_id="opp-1", research_run_id="run-1", source_refs=("source-2",),
            proposed_direction="test persisted direction", opportunity_kind="SURFACE",
            created_at=CREATED_AT,
        )
        store.research_selections["selection-1"] = SimpleNamespace(
            selection_id="selection-1", research_run_id="run-1", opportunity_id="opp-1", outcome="SELECTED",
            reason_codes=("NOVEL",), created_at=CREATED_AT,
        )
        store.experiments["exp-1"] = SimpleNamespace(
            experiment_id="exp-1", research_run_id="run-1", hypothesis_id="hyp-1",
            execution_state="PLANNED", created_at=CREATED_AT,
        )
        store.experiment_plans["exp-1"] = SimpleNamespace(
            experiment_id="exp-1", expected_observation="expected", disconfirming_observation="disconfirming",
            required_capability="diagnostic.echo", action="echo", evaluation_strategy="diagnostic.echo.v1",
        )
        payload = build_hq_run_analysis(FakeUnitOfWorkFactory(store), "run-1")
        intent = payload["research_intent"]
        self.assertEqual(intent["hypothesis"]["claim"], "bounded claim")
        self.assertEqual(intent["selection_reason_codes"], ["NOVEL"])
        self.assertEqual(intent["expected_observation"], "expected")
        self.assertEqual(intent["next_direction"], "test persisted direction")
        self.assertEqual(payload["verification_chain"]["final_state"], "UNKNOWN")
        self.assertFalse(payload["observer"]["brief"]["source_event_ids"])

    def test_safe_value_serializes_dataclass_and_datetime(self) -> None:
        value = DemoRecord(
            record_id="demo-1",
            created_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
            payload={"state": "PASS"},
        )

        serialized = _safe_value(value)

        self.assertEqual(serialized["record_id"], "demo-1")
        self.assertEqual(
            serialized["created_at"],
            "2026-08-26T00:00:00+00:00",
        )
        self.assertEqual(serialized["payload"]["state"], "PASS")

    def test_raw_bytes_are_never_exposed(self) -> None:
        serialized = _safe_value(b"super-secret-binary-value")

        self.assertEqual(serialized["type"], "bytes")
        self.assertFalse(serialized["raw_exposed"])
        self.assertEqual(serialized["length"], 25)

    def test_bundle_is_bounded(self) -> None:
        records = list(range(MAX_ITEMS_PER_COLLECTION + 10))

        bundled = _bundle(records)

        self.assertEqual(
            bundled["count"],
            MAX_ITEMS_PER_COLLECTION + 10,
        )
        self.assertEqual(
            bundled["shown"],
            MAX_ITEMS_PER_COLLECTION,
        )
        self.assertTrue(bundled["truncated"])

    def test_nested_collections_are_bounded_and_mapping_order_is_deterministic(self) -> None:
        value = {
            "z": list(range(MAX_ITEMS_PER_COLLECTION + 10)),
            "a": {str(index): index for index in range(MAX_ITEMS_PER_COLLECTION + 10)},
        }

        serialized = _safe_value(value)

        self.assertEqual(list(serialized), ["a", "z"])
        self.assertEqual(len(serialized["a"]), MAX_ITEMS_PER_COLLECTION)
        self.assertEqual(len(serialized["z"]), MAX_ITEMS_PER_COLLECTION)

    def test_bounded_repository_proxy_passes_explicit_read_limit(self) -> None:
        repository = mock.Mock()
        repository.list_for_research_run.return_value = []

        _BoundedRepositoryProxy(repository).list_for_research_run("run-1")

        repository.list_for_research_run.assert_called_once_with(
            "run-1",
            limit=MAX_ITEMS_PER_COLLECTION,
        )


if __name__ == "__main__":
    unittest.main()
