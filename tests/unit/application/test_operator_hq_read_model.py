from __future__ import annotations

import unittest
from unittest import mock

from dataclasses import dataclass
from datetime import datetime, timezone

import pathsetup  # noqa: F401

from zest.application.operator_hq_read_model import (
    MAX_ITEMS_PER_COLLECTION,
    _BoundedRepositoryProxy,
    _bundle,
    _safe_value,
)


@dataclass(frozen=True)
class DemoRecord:
    record_id: str
    created_at: datetime
    payload: dict[str, object]


class OperatorHqReadModelTests(unittest.TestCase):
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
