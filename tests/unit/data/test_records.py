from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.core.enums import ActorType, AuthorizationSourceState
from zest.data.errors import PersistenceInputError
from zest.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ObservationRecord,
    ProgramRecord,
    ResearchRunRecord,
    WorkerResultRecord,
)


def _now() -> datetime:
    return datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class RecordValidationTests(unittest.TestCase):
    def test_program_rejects_empty_id(self) -> None:
        with self.assertRaises(PersistenceInputError):
            ProgramRecord(program_id="", created_at=_now())

    def test_naive_datetime_rejected(self) -> None:
        with self.assertRaises(PersistenceInputError):
            ProgramRecord(program_id="p1", created_at=datetime(2026, 8, 16))

    def test_authorization_state_must_match_core(self) -> None:
        record = AuthorizationSourceRecord(
            authorization_source_id="as1",
            program_id="p1",
            state=AuthorizationSourceState.ACTIVE.value,
            provenance_reference="letter-1",
            created_at=_now(),
        )
        self.assertEqual(record.state, "ACTIVE")
        with self.assertRaises(PersistenceInputError):
            AuthorizationSourceRecord(
                authorization_source_id="as1",
                program_id="p1",
                state="MAYBE",
                provenance_reference="letter-1",
                created_at=_now(),
            )

    def test_effective_window_rejected_when_until_before_from(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(PersistenceInputError):
            AuthorizationSourceRecord(
                authorization_source_id="as1",
                program_id="p1",
                state="ACTIVE",
                provenance_reference="letter-1",
                created_at=_now(),
                effective_from=start,
                effective_until=end,
            )

    def test_model_is_not_an_actor_type(self) -> None:
        with self.assertRaises(PersistenceInputError):
            ResearchRunRecord(
                research_run_id="run1",
                program_id="p1",
                authorization_source_id="as1",
                initiated_by_actor_id="gpt",
                initiated_by_actor_type="MODEL",
                started_at=_now(),
            )
        self.assertIn(ActorType.HUMAN_OPERATOR.value, {"HUMAN_OPERATOR"})

    def test_negative_budget_rejected_and_zero_is_not_unlimited(self) -> None:
        with self.assertRaises(PersistenceInputError):
            IssuedBudgetRecord(
                budget_id="b1",
                research_run_id="run1",
                max_requests=-1,
                max_tool_calls=0,
                max_runtime_ms=0,
                max_concurrency=0,
                issued_at=_now(),
            )
        zero = IssuedBudgetRecord(
            budget_id="b1",
            research_run_id="run1",
            max_requests=0,
            max_tool_calls=0,
            max_runtime_ms=0,
            max_concurrency=0,
            issued_at=_now(),
        )
        self.assertEqual(zero.max_requests, 0)

    def test_hypothesis_is_not_a_finding_status_carrier(self) -> None:
        record = HypothesisRecord(
            hypothesis_id="h1",
            research_run_id="run1",
            claim="owner-only mutation",
            created_at=_now(),
        )
        self.assertFalse(hasattr(record, "confidence"))
        self.assertFalse(hasattr(record, "finding_status"))

    def test_experiment_rejects_unknown_state(self) -> None:
        with self.assertRaises(PersistenceInputError):
            ExperimentRecord(
                experiment_id="e1",
                research_run_id="run1",
                hypothesis_id="h1",
                budget_id="b1",
                execution_state="HYPOTHESIS_REJECTED",
                created_at=_now(),
            )

    def test_worker_result_rejects_secret_keys_in_payload(self) -> None:
        with self.assertRaises(PersistenceInputError):
            WorkerResultRecord(
                worker_result_id="wr1",
                experiment_id="e1",
                research_run_id="run1",
                request_id="req1",
                correlation_id="corr1",
                worker_capability="diagnostic.echo",
                action="echo",
                authorization_decision_reference="authz1",
                budget_id="b1",
                side_effect_level=0,
                contract_version="v1",
                worker_id="worker-1",
                status="SUCCEEDED",
                received_at=_now(),
                raw_result={"password": "nope"},
            )

    def test_observation_requires_worker_result_provenance(self) -> None:
        record = ObservationRecord(
            observation_id="o1",
            worker_result_id="wr1",
            observation_kind="http_response",
            payload={"status": 200},
            normalization_version="a3-test",
            observed_at=_now(),
            created_at=_now(),
        )
        self.assertEqual(record.worker_result_id, "wr1")
        self.assertFalse(hasattr(record, "severity"))

    def test_execution_attempt_rejects_unknown_state_and_is_not_evidence(self) -> None:
        from zest.data.records import ExecutionAttemptRecord

        record = ExecutionAttemptRecord(
            attempt_id="ea:req-1",
            request_id="req-1",
            experiment_id="exp1",
            research_run_id="run1",
            correlation_id="corr1",
            worker_capability="diagnostic.echo",
            action="echo",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="ae:exec:req-1",
            state="AUTHORIZED",
            created_at=_now(),
        )
        self.assertEqual(record.state, "AUTHORIZED")
        self.assertFalse(hasattr(record, "evidence_id"))
        with self.assertRaises(PersistenceInputError):
            ExecutionAttemptRecord(
                attempt_id="ea:req-1",
                request_id="req-1",
                experiment_id="exp1",
                research_run_id="run1",
                correlation_id="corr1",
                worker_capability="diagnostic.echo",
                action="echo",
                target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                authorization_decision_reference="ae:exec:req-1",
                state="RETRYING",
                created_at=_now(),
            )

    def test_audit_event_rejects_secret_keys(self) -> None:
        with self.assertRaises(PersistenceInputError):
            AuditEventRecord(
                audit_event_id="ae1",
                occurred_at=_now(),
                actor_id="op1",
                actor_type="HUMAN_OPERATOR",
                event_type="research_run_started",
                subject_type="research_run",
                subject_id="run1",
                payload={"api_key": "nope"},
            )


class TargetModelRecordTests(unittest.TestCase):
    def test_inference_cannot_be_observed(self) -> None:
        from zest.data.records import TargetInferenceRecord

        with self.assertRaises(PersistenceInputError):
            TargetInferenceRecord(
                inference_id="inf-1",
                research_run_id="run-1",
                kind="RELATIONSHIP",
                epistemic_status="OBSERVED",
                opaque_ref="maybe-related",
                statement="Actor handle may be related to the diagnostic resource.",
                source_refs=("obs-1",),
                attributes={},
                strategy_version="target.model.diagnostic.echo.v1",
                created_at=_now(),
            )

    def test_session_token_is_rejected(self) -> None:
        from zest.data.records import TargetInferenceRecord

        with self.assertRaises(PersistenceInputError):
            TargetInferenceRecord(
                inference_id="inf-1",
                research_run_id="run-1",
                kind="RELATIONSHIP",
                epistemic_status="INFERRED",
                opaque_ref="maybe-related",
                statement="Actor handle may be related to the diagnostic resource.",
                source_refs=("obs-1",),
                attributes={"session_token": "secret"},
                strategy_version="target.model.diagnostic.echo.v1",
                created_at=_now(),
            )


if __name__ == "__main__":
    unittest.main()
