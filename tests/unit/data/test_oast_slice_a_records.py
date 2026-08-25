"""Focused OAST-1 persistence-record tests."""

from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from research_os.data.errors import PersistenceInputError
from research_os.data.records import (
    OastCallbackDeliveryRecord,
    OastCorrelationRecord,
)


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
DIGEST = hashlib.sha256(b"normalized").hexdigest()


class OastSliceARecordTests(unittest.TestCase):
    def _correlation(self) -> OastCorrelationRecord:
        return OastCorrelationRecord(
            correlation_id="corr-1",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="https://example.test",
            identity_id="identity-1",
            armed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            created_at=NOW,
        )

    def test_correlation_record_requires_identity_and_temporal_order(self) -> None:
        self.assertEqual(self._correlation().attempt_id, "attempt-1")
        with self.assertRaises(PersistenceInputError):
            OastCorrelationRecord(
                correlation_id="corr-1",
                attempt_id="attempt-1",
                experiment_id="exp-1",
                research_run_id="run-1",
                target_reference="https://example.test",
                identity_id="",
                armed_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
                created_at=NOW,
            )
        with self.assertRaises(PersistenceInputError):
            OastCorrelationRecord(
                correlation_id="corr-1",
                attempt_id="attempt-1",
                experiment_id="exp-1",
                research_run_id="run-1",
                target_reference="https://example.test",
                identity_id="identity-1",
                armed_at=NOW,
                expires_at=NOW,
                created_at=NOW,
            )

    def test_delivery_record_rejects_secret_and_unbounded_payloads(self) -> None:
        with self.assertRaises(PersistenceInputError):
            OastCallbackDeliveryRecord(
                delivery_id="delivery-1",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="event-1",
                received_at=NOW,
                normalized_payload={"headers": {"authorization": "secret"}},
                normalized_digest=DIGEST,
            )
        with self.assertRaises(PersistenceInputError):
            OastCallbackDeliveryRecord(
                delivery_id="delivery-2",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="event-2",
                received_at=NOW,
                normalized_payload={"blob": "x" * 17000},
                normalized_digest=DIGEST,
            )


if __name__ == "__main__":
    unittest.main()
