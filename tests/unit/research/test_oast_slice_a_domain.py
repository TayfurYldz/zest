"""Focused OAST-1 domain tests; no network or provider calls."""

from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

import pathsetup  # noqa: F401

from research_os.research.oast.types import (
    OastCallbackDelivery,
    OastCorrelation,
)
from research_os.research.types import ResearchInputError


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
DIGEST = hashlib.sha256(b"normalized").hexdigest()


class OastSliceADomainTests(unittest.TestCase):
    def test_correlation_requires_identity_and_bounded_window(self) -> None:
        correlation = OastCorrelation(
            correlation_id="corr-1",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="https://example.test",
            identity_id="identity-1",
            armed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(correlation.identity_id, "identity-1")
        with self.assertRaises(ResearchInputError):
            OastCorrelation(
                correlation_id="corr-2",
                attempt_id="attempt-2",
                experiment_id="exp-2",
                research_run_id="run-1",
                target_reference="https://example.test",
                identity_id="",
                armed_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
        with self.assertRaises(ResearchInputError):
            OastCorrelation(
                correlation_id="corr-3",
                attempt_id="attempt-3",
                experiment_id="exp-3",
                research_run_id="run-1",
                target_reference="https://example.test",
                identity_id="identity-1",
                armed_at=NOW,
                expires_at=NOW,
            )

    def test_callback_delivery_is_provider_neutral_at_correlation_layer(self) -> None:
        correlation_fields = set(OastCorrelation.__dataclass_fields__)
        self.assertNotIn("provider_adapter_id", correlation_fields)
        delivery = OastCallbackDelivery(
            delivery_id="delivery-1",
            correlation_id="corr-1",
            provider_adapter_id="provider-neutral-adapter",
            provider_event_id="event-1",
            received_at=NOW,
            normalized_payload={"kind": "dns", "name": "oast.example"},
            normalized_digest=DIGEST,
        )
        self.assertEqual(delivery.normalized_digest, DIGEST)

    def test_callback_requires_event_digest_and_bounded_non_secret_payload(self) -> None:
        with self.assertRaises(ResearchInputError):
            OastCallbackDelivery(
                delivery_id="delivery-1",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="",
                received_at=NOW,
                normalized_payload={"kind": "dns"},
                normalized_digest=DIGEST,
            )
        with self.assertRaises(ResearchInputError):
            OastCallbackDelivery(
                delivery_id="delivery-2",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="event-2",
                received_at=NOW,
                normalized_payload={"nested": {"token": "must-not-persist"}},
                normalized_digest=DIGEST,
            )
        with self.assertRaises(ResearchInputError):
            OastCallbackDelivery(
                delivery_id="delivery-3",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="event-3",
                received_at=NOW,
                normalized_payload={"kind": "dns"},
                normalized_digest="not-a-sha256",
            )
        with self.assertRaises(ResearchInputError):
            OastCallbackDelivery(
                delivery_id="delivery-4",
                correlation_id="corr-1",
                provider_adapter_id="adapter",
                provider_event_id="event-4",
                received_at=NOW,
                normalized_payload={"blob": "x" * 17000},
                normalized_digest=DIGEST,
            )


if __name__ == "__main__":
    unittest.main()
