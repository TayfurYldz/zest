from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any, Mapping

import pathsetup  # noqa: F401

from zest.core.enums import ScopeClassification
from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.data.records import HunterFamilyRecord
from zest.research.coverage.debt import compute_coverage_debt
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind, FORBIDDEN_DISCOVERY_KEYS
from zest.research.selection import HunterFamilyView
from zest.research.target_model import TargetEpistemicStatus

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
SD_G13_FAMILIES = frozenset(
    {
        "hf-http-smuggling-desync",
        "hf-cache-poison-deception",
    }
)


def _record(row: Mapping[str, Any]) -> HunterFamilyRecord:
    return HunterFamilyRecord(
        family_id=str(row["family_id"]),
        name=str(row["name"]),
        target_node_kinds=tuple(str(item) for item in row["target_node_kinds"]),
        preconditions=dict(row["preconditions"]),
        claim_template=str(row["claim_template"]),
        evidence_requirements=dict(row["evidence_requirements"]),
        validation_tier=str(row["validation_tier"]),
        enabled=bool(row["enabled"]),
        version=int(row["version"]),
        created_at=NOW,
    )


def _view(row: Mapping[str, Any]) -> HunterFamilyView:
    record = _record(row)
    return HunterFamilyView(
        family_id=record.family_id,
        name=record.name,
        target_node_kinds=record.target_node_kinds,
        preconditions=record.preconditions,
        claim_template=record.claim_template,
        evidence_requirements=record.evidence_requirements,
        validation_tier=record.validation_tier,
        enabled=record.enabled,
        version=record.version,
    )


def _node(signals: tuple[str, ...]) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id="op-1",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="op:https://example.test/api",
        epistemic_status=TargetEpistemicStatus.OBSERVED,
        identity_ids=("ANONYMOUS",),
        provenance_refs=(),
        scope_classification=ScopeClassification.IN_SCOPE,
        attributes={
            "path": "/api",
            "method": "GET",
            "protocol_surface_signals": signals,
        },
    )


def _forbidden_keys(payload: object) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_DISCOVERY_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.update(_forbidden_keys(item))
    return found


class SDG13HunterFamilySeedTests(unittest.TestCase):
    def test_sd_g13_seed_rows_are_v3_and_surface_evidence_gated(self) -> None:
        rows = [row for row in SEED_FAMILIES if row["family_id"] in SD_G13_FAMILIES]
        self.assertEqual({row["family_id"] for row in rows}, SD_G13_FAMILIES)
        records = [_record(row) for row in rows]

        self.assertTrue(all(record.enabled for record in records))
        self.assertTrue(all(record.validation_tier == "V3" for record in records))
        self.assertTrue(
            all(record.preconditions["scope_classification"] == "IN_SCOPE" for record in records)
        )
        self.assertTrue(
            all("required_attribute_any" in record.preconditions for record in records)
        )

    def test_sd_g13_seed_rows_do_not_smuggle_finding_or_secret_keys(self) -> None:
        for row in SEED_FAMILIES:
            self.assertEqual(_forbidden_keys(row), set(), row["family_id"])

    def test_protocol_families_create_debt_only_when_surface_signals_exist(self) -> None:
        registry = tuple(
            _view(row) for row in SEED_FAMILIES if row["family_id"] in SD_G13_FAMILIES
        )
        supported = _node(("reverse_proxy", "edge_cache"))
        unsupported = _node(())

        supported_matrix = compute_coverage_debt(
            AttackSurfaceGraph("run-1", "sd-g13-test", (supported,), ()),
            registry,
            (),
        )
        unsupported_matrix = compute_coverage_debt(
            AttackSurfaceGraph("run-1", "sd-g13-test", (unsupported,), ()),
            registry,
            (),
        )

        self.assertEqual(
            {cell.family_id for cell in supported_matrix.cells},
            SD_G13_FAMILIES,
        )
        self.assertEqual(unsupported_matrix.total_debt, 0)


if __name__ == "__main__":
    unittest.main()
