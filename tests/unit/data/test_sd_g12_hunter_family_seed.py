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
SD_G12_FAMILIES = frozenset(
    {
        "hf-sqli",
        "hf-ssti",
        "hf-lfi-rfi",
        "hf-mass-assignment",
        "hf-jwt-crypto",
        "hf-cors",
        "hf-graphql",
        "hf-dom-taint",
        "hf-ai-llm-target",
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


def _node(kind: AttackSurfaceNodeKind, key: str) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id=key,
        kind=kind,
        canonical_key=key,
        epistemic_status=TargetEpistemicStatus.OBSERVED,
        identity_ids=("ANONYMOUS", "alice"),
        provenance_refs=(),
        scope_classification=ScopeClassification.IN_SCOPE,
        attributes={"path": "/api/input", "technology": "api"},
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


class SDG12HunterFamilySeedTests(unittest.TestCase):
    def test_sd_g12_seed_rows_are_valid_append_only_family_records(self) -> None:
        rows = [row for row in SEED_FAMILIES if row["family_id"] in SD_G12_FAMILIES]
        self.assertEqual({row["family_id"] for row in rows}, SD_G12_FAMILIES)
        records = [_record(row) for row in rows]

        self.assertTrue(all(record.enabled for record in records))
        self.assertTrue(all(record.validation_tier == "V3" for record in records))
        self.assertTrue(
            all(record.preconditions["scope_classification"] == "IN_SCOPE" for record in records)
        )

    def test_sd_g12_seed_rows_do_not_smuggle_finding_or_severity_keys(self) -> None:
        for row in SEED_FAMILIES:
            self.assertEqual(_forbidden_keys(row), set(), row["family_id"])

    def test_injection_families_create_coverage_debt_for_input_surfaces(self) -> None:
        registry = tuple(
            _view(row) for row in SEED_FAMILIES if row["family_id"] in SD_G12_FAMILIES
        )
        operation = _node(AttackSurfaceNodeKind.HTTP_OPERATION, "op:https://example.test/api")
        graph = AttackSurfaceGraph("run-1", "sd-g12-test", (operation,), ())

        matrix = compute_coverage_debt(graph, registry, ())
        family_ids = {cell.family_id for cell in matrix.cells}

        self.assertTrue(SD_G12_FAMILIES - {"hf-dom-taint"} <= family_ids)
        self.assertEqual(matrix.total_debt, len(matrix.cells))
        self.assertTrue(all(cell.state.value == "UNTESTED" for cell in matrix.cells))
        self.assertEqual(
            len([cell for cell in matrix.cells if cell.family_id == "hf-ai-llm-target"]),
            2,
        )

    def test_dom_taint_family_targets_js_and_page_surfaces(self) -> None:
        registry = tuple(
            _view(row) for row in SEED_FAMILIES if row["family_id"] == "hf-dom-taint"
        )
        js_node = _node(AttackSurfaceNodeKind.JS_BUNDLE, "js:https://example.test/app.js")
        page_node = _node(AttackSurfaceNodeKind.PAGE_STATE, "page:https://example.test/app")
        graph = AttackSurfaceGraph("run-1", "sd-g12-test", (js_node, page_node), ())

        matrix = compute_coverage_debt(graph, registry, ())

        self.assertEqual({cell.family_id for cell in matrix.cells}, {"hf-dom-taint"})
        self.assertEqual(matrix.total_debt, 4)


if __name__ == "__main__":
    unittest.main()
