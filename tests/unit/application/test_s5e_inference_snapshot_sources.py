from __future__ import annotations

import unittest
from datetime import datetime, timezone

from zest.application.discovery.snapshot_views import (
    _inference_from_record,
)
from zest.data.records import (
    DiscoveryInferenceRecord,
    DiscoveryInferenceSourceRecord,
)
from zest.research.discovery.types import DiscoveryInferenceKind
from zest.research.target_model import TargetEpistemicStatus

from support.fake_unit_of_work import FakeUnitOfWork, _Store


NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


class S5eInferenceSnapshotSourceTests(unittest.TestCase):
    def test_rehydrates_normalized_inference_provenance(self) -> None:
        store = _Store()
        uow = FakeUnitOfWork(store)

        record = DiscoveryInferenceRecord(
            inference_id="inf-1",
            research_run_id="run-1",
            inference_kind=DiscoveryInferenceKind.ROUTE_TEMPLATE.value,
            canonical_key="route-template:/api/orders/{id}",
            epistemic_status=TargetEpistemicStatus.INFERRED.value,
            identity_id="anonymous",
            created_at=NOW,
            attributes={
                "template_path": "/api/orders/{id}",
                "exact_paths": [
                    "/api/orders/101",
                    "/api/orders/202",
                    "/api/orders/303",
                ],
            },
        )

        uow.discovery_inferences.insert(record)

        uow.discovery_inference_sources.insert(
            DiscoveryInferenceSourceRecord(
                source_row_id="src-1",
                research_run_id="run-1",
                inference_id="inf-1",
                created_at=NOW,
                source_fact_id="fact-1",
            )
        )
        uow.discovery_inference_sources.insert(
            DiscoveryInferenceSourceRecord(
                source_row_id="src-2",
                research_run_id="run-1",
                inference_id="inf-1",
                created_at=NOW,
                source_inference_id="inf-parent",
            )
        )
        uow.discovery_inference_sources.insert(
            DiscoveryInferenceSourceRecord(
                source_row_id="src-3",
                research_run_id="run-1",
                inference_id="inf-1",
                created_at=NOW,
                observation_id="obs-1",
            )
        )

        inference = _inference_from_record(uow, record)

        self.assertEqual(inference.source_fact_ids, ("fact-1",))
        self.assertEqual(
            inference.source_inference_ids,
            ("inf-parent",),
        )
        self.assertEqual(
            inference.source_observation_ids,
            ("obs-1",),
        )


if __name__ == "__main__":
    unittest.main()
