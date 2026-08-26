from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from zest.application.admit_oast_callback import AdmitOastCallback
from zest.application.arm_oast_correlation import (
    ArmOastCorrelation,
    OastCorrelationArmError,
)
from zest.data.records import (
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    HypothesisRecord,
    ResearchRunRecord,
)
from zest.research.oast.types import OastCallbackDelivery
from tests.support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _seed(store: _Store, *, identity_id: str | None = "identity-1") -> None:
    store.research_runs["run-1"] = ResearchRunRecord(
        research_run_id="run-1",
        program_id="program-1",
        authorization_source_id="auth-1",
        initiated_by_actor_id="operator-1",
        initiated_by_actor_type="HUMAN_OPERATOR",
        started_at=NOW,
    )
    store.hypotheses["hyp-1"] = HypothesisRecord(
        hypothesis_id="hyp-1",
        research_run_id="run-1",
        claim="external callback may be observed",
        created_at=NOW,
        identity_id=identity_id,
    )
    store.experiment_plans["exp-1"] = ExperimentPlanRecord(
        experiment_id="exp-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        required_capability="oast",
        action="arm-correlation",
        target_reference="https://example.test",
        side_effect_level=0,
        arguments={"identity_id": identity_id} if identity_id else {},
        requested_budget_id="budget-1",
        expected_observation="callback",
        disconfirming_observation="no callback",
        evaluation_strategy="deterministic",
        created_at=NOW,
    )
    store.execution_attempts["attempt-1"] = ExecutionAttemptRecord(
        attempt_id="attempt-1",
        request_id="request-1",
        experiment_id="exp-1",
        research_run_id="run-1",
        correlation_id="worker-correlation-1",
        worker_capability="oast",
        action="arm-correlation",
        target_reference="https://example.test",
        budget_id="budget-1",
        side_effect_level=0,
        authorization_decision_reference="decision-1",
        state="AUTHORIZED",
        created_at=NOW,
        authorized_at=NOW,
    )


def _delivery(correlation_id: str, event: str, payload: dict) -> OastCallbackDelivery:
    digest = hashlib.sha256(
        __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OastCallbackDelivery(
        delivery_id=f"delivery-{event}",
        correlation_id=correlation_id,
        provider_adapter_id="provider-neutral-test",
        provider_event_id=event,
        received_at=NOW + timedelta(minutes=1),
        normalized_payload=payload,
        normalized_digest=digest,
    )


class OastSliceBAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _Store()
        _seed(self.store)
        self.factory = FakeUnitOfWorkFactory(self.store)
        self.correlation = ArmOastCorrelation(
            self.factory, clock=lambda: NOW
        ).execute("attempt-1")

    def test_arm_derives_identity_and_reuses_existing_correlation(self) -> None:
        again = ArmOastCorrelation(self.factory, clock=lambda: NOW).execute("attempt-1")
        self.assertTrue(again.existing)
        self.assertEqual(again.correlation_id, self.correlation.correlation_id)
        self.assertEqual(again.target_reference, "https://example.test")
        self.assertEqual(again.identity_id, "identity-1")
        self.assertEqual(again.expires_at - again.armed_at, timedelta(minutes=15))

    def test_missing_identity_fails_closed(self) -> None:
        store = _Store()
        _seed(store, identity_id=None)
        with self.assertRaises(OastCorrelationArmError):
            ArmOastCorrelation(FakeUnitOfWorkFactory(store), clock=lambda: NOW).execute(
                "attempt-1"
            )

    def test_replay_and_distinct_deliveries_have_one_authoritative_admission(self) -> None:
        admit = AdmitOastCallback(self.factory, clock=lambda: NOW)
        first = _delivery(self.correlation.correlation_id, "event-1", {"kind": "dns"})
        result = admit.execute(first, scope_classification="OUT_OF_SCOPE", identity_id="attacker")
        self.assertTrue(result.admitted)

        replay = admit.execute(first)
        self.assertTrue(replay.admitted)
        self.assertEqual(replay.fact_id, result.fact_id)

        second = _delivery(self.correlation.correlation_id, "event-2", {"kind": "other"})
        second_result = admit.execute(second)
        self.assertTrue(second_result.admitted)
        self.assertEqual(second_result.fact_id, result.fact_id)

        duplicate_digest = _delivery(self.correlation.correlation_id, "event-3", {"kind": "dns"})
        duplicate_result = admit.execute(duplicate_digest)
        self.assertTrue(duplicate_result.admitted)
        self.assertEqual(duplicate_result.fact_id, result.fact_id)

        self.assertEqual(len(self.store.oast_callback_deliveries), 2)
        self.assertEqual(len(self.store.oast_admissions), 1)
        self.assertEqual(len(self.store.sensor_observations), 1)
        self.assertEqual(len(self.store.discovery_facts), 1)
        fact = next(iter(self.store.discovery_facts.values()))
        self.assertEqual(fact.attributes["scope_classification"], "UNKNOWN")
        self.assertEqual(fact.attributes["oast_semantics"], "correlated external callback observed")
        self.assertEqual(self.store.evidence, {})
        self.assertEqual(self.store.candidates, {})
        self.assertEqual(self.store.findings, {})

    def test_unknown_and_future_callbacks_are_not_authoritative(self) -> None:
        admit = AdmitOastCallback(self.factory, clock=lambda: NOW)
        unknown = _delivery("missing-correlation", "event-unknown", {"kind": "dns"})
        self.assertEqual(admit.execute(unknown).reason_code, "OAST_CORRELATION_NOT_FOUND")

        future = _delivery(self.correlation.correlation_id, "event-future", {"kind": "dns"})
        future = OastCallbackDelivery(
            delivery_id=future.delivery_id,
            correlation_id=future.correlation_id,
            provider_adapter_id=future.provider_adapter_id,
            provider_event_id=future.provider_event_id,
            received_at=NOW - timedelta(minutes=1),
            normalized_payload=future.normalized_payload,
            normalized_digest=future.normalized_digest,
        )
        self.assertEqual(admit.execute(future).reason_code, "OAST_CORRELATION_NOT_ARMED")



if __name__ == "__main__":
    unittest.main()
