from __future__ import annotations

import json
import unittest

from zest.integrations.oast.interactsh import (
    INTERACTSH_PROVIDER_ADAPTER_ID,
    MAX_INTERACTSH_JSONL_BYTES,
    InteractshAdapterError,
    normalize_interactsh_jsonl,
)


def _event(**overrides: object) -> str:
    value: dict[str, object] = {
        "protocol": "dns",
        "unique-id": "abc123def456ghi",
        "full-id": "abc123def456ghi.oast.example.test",
        "q-type": "A",
        "raw-request": "RAW REQUEST",
        "raw-response": "RAW RESPONSE",
        "remote-address": "203.0.113.10",
        "timestamp": "2026-08-25T19:30:00.123456789Z",
    }
    value.update(overrides)
    return json.dumps(value, separators=(",", ":"))


class InteractshOastAdapterTests(unittest.TestCase):
    def test_normalizes_dns_without_persisting_raw_material(self) -> None:
        line = _event(
            **{
                "raw-request": "Authorization: Bearer SHOULD_NEVER_PERSIST",
                "raw-response": "Set-Cookie: secret=SHOULD_NEVER_PERSIST",
            }
        )

        delivery = normalize_interactsh_jsonl(
            line,
            correlation_id="correlation-1",
        )

        self.assertEqual(delivery.correlation_id, "correlation-1")
        self.assertEqual(
            delivery.provider_adapter_id,
            INTERACTSH_PROVIDER_ADAPTER_ID,
        )
        self.assertEqual(delivery.normalized_payload["protocol"], "dns")
        self.assertEqual(delivery.normalized_payload["q_type"], "A")

        serialized = json.dumps(delivery.normalized_payload)
        self.assertNotIn("raw-request", serialized)
        self.assertNotIn("raw-response", serialized)
        self.assertNotIn("SHOULD_NEVER_PERSIST", serialized)

        self.assertEqual(len(delivery.provider_event_id), 64)
        self.assertEqual(len(delivery.delivery_id), 64)
        self.assertEqual(len(delivery.normalized_digest), 64)
        self.assertIsNotNone(delivery.received_at.utcoffset())

    def test_https_is_supported_as_distinct_provider_protocol(self) -> None:
        line = _event(
            protocol="https",
            **{
                "q-type": "",
                "unique-id": "abc.example.test",
                "full-id": "abc.example.test",
            },
        )

        delivery = normalize_interactsh_jsonl(
            line,
            correlation_id="correlation-1",
        )

        self.assertEqual(delivery.normalized_payload["protocol"], "https")
        self.assertNotIn("q_type", delivery.normalized_payload)

    def test_replay_has_stable_provider_and_delivery_identity(self) -> None:
        line = _event()

        first = normalize_interactsh_jsonl(line, correlation_id="correlation-1")
        replay = normalize_interactsh_jsonl(line, correlation_id="correlation-1")
        other = normalize_interactsh_jsonl(line, correlation_id="correlation-2")

        self.assertEqual(first.provider_event_id, replay.provider_event_id)
        self.assertEqual(first.delivery_id, replay.delivery_id)
        self.assertEqual(first.normalized_digest, replay.normalized_digest)

        self.assertEqual(first.provider_event_id, other.provider_event_id)
        self.assertNotEqual(first.delivery_id, other.delivery_id)

    def test_raw_difference_changes_provider_event_but_not_normalized_digest(self) -> None:
        first = normalize_interactsh_jsonl(
            _event(**{"raw-request": "request-one"}),
            correlation_id="correlation-1",
        )
        second = normalize_interactsh_jsonl(
            _event(**{"raw-request": "request-two"}),
            correlation_id="correlation-1",
        )

        self.assertNotEqual(first.provider_event_id, second.provider_event_id)
        self.assertEqual(first.normalized_digest, second.normalized_digest)

    def test_malformed_or_ambiguous_provider_input_fails_closed(self) -> None:
        with self.assertRaises(InteractshAdapterError):
            normalize_interactsh_jsonl(
                '{"protocol":"dns","protocol":"http"}',
                correlation_id="correlation-1",
            )

        with self.assertRaises(InteractshAdapterError):
            normalize_interactsh_jsonl(
                _event(protocol="smtp"),
                correlation_id="correlation-1",
            )

        with self.assertRaises(InteractshAdapterError):
            normalize_interactsh_jsonl(
                _event(timestamp="2026-08-25T19:30:00"),
                correlation_id="correlation-1",
            )

        with self.assertRaises(InteractshAdapterError):
            normalize_interactsh_jsonl(
                _event(**{"q-type": ""}),
                correlation_id="correlation-1",
            )

    def test_input_size_is_bounded_before_provider_admission(self) -> None:
        oversized = "{" + ("x" * MAX_INTERACTSH_JSONL_BYTES) + "}"

        with self.assertRaises(InteractshAdapterError):
            normalize_interactsh_jsonl(
                oversized,
                correlation_id="correlation-1",
            )


if __name__ == "__main__":
    unittest.main()
