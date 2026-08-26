from __future__ import annotations

from datetime import datetime, timezone
import unittest

import pathsetup  # noqa: F401

from zest.data.errors import PersistenceConflictError, PersistenceInputError
from zest.data.records import (
    DiscoveryFactRecord,
    DiscoveryProjectionReceiptRecord,
    FrontierEventRecord,
)
from support.fake_unit_of_work import FakeUnitOfWork, _Store


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class DiscoveryRecordTests(unittest.TestCase):
    def test_inference_cannot_be_observed_on_fact(self) -> None:
        with self.assertRaises(PersistenceInputError):
            DiscoveryFactRecord(
                fact_id="f1",
                research_run_id="run-1",
                fact_kind="ORIGIN",
                canonical_key="k",
                epistemic_status="INFERRED",
                identity_id="ANONYMOUS",
                target_reference="t",
                created_at=NOW,
            )

    def test_receipt_requires_typed_plane(self) -> None:
        with self.assertRaises(PersistenceInputError):
            DiscoveryProjectionReceiptRecord(
                receipt_id="r1",
                research_run_id="run-1",
                strategy_version="surface.discovery.v1",
                source_plane="OBSERVATION",
                created_at=NOW,
                observation_id=None,
            )


class FakeFrontierClaimTests(unittest.TestCase):
    def test_concurrent_selected_generation_is_unique(self) -> None:
        store = _Store()
        uow = FakeUnitOfWork(store)
        first = FrontierEventRecord(
            event_id="e1",
            frontier_id="front-1",
            research_run_id="run-1",
            event_kind="SELECTED",
            sequence=3,
            created_at=NOW,
            selection_generation=1,
        )
        uow.frontier_events.insert(first)
        with self.assertRaises(PersistenceConflictError):
            uow.frontier_events.insert(
                FrontierEventRecord(
                    event_id="e2",
                    frontier_id="front-1",
                    research_run_id="run-1",
                    event_kind="SELECTED",
                    sequence=4,
                    created_at=NOW,
                    selection_generation=1,
                )
            )


if __name__ == "__main__":
    unittest.main()
