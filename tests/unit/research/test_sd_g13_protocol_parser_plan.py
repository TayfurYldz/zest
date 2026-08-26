from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.research.protocol import build_protocol_parser_plan
from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError


def _family(family_id: str) -> HunterFamilyView:
    row = next(item for item in SEED_FAMILIES if item["family_id"] == family_id)
    return HunterFamilyView(
        family_id=str(row["family_id"]),
        name=str(row["name"]),
        target_node_kinds=tuple(str(item) for item in row["target_node_kinds"]),
        preconditions=dict(row["preconditions"]),
        claim_template=str(row["claim_template"]),
        evidence_requirements=dict(row["evidence_requirements"]),
        validation_tier=str(row["validation_tier"]),
        enabled=bool(row["enabled"]),
        version=int(row["version"]),
    )


class SDG13ProtocolParserPlanTests(unittest.TestCase):
    def test_smuggling_plan_is_deterministic_and_se3_ready(self) -> None:
        family = _family("hf-http-smuggling-desync")

        first = build_protocol_parser_plan(family)
        second = build_protocol_parser_plan(family)

        self.assertEqual(first.plan_hash, second.plan_hash)
        self.assertEqual(first.lane, "http_request_smuggling_desync")
        self.assertGreaterEqual(len(first.steps), 8)
        self.assertEqual(
            first.dimensions,
            ("frontend_protocol", "backend_protocol", "normalization_boundary"),
        )
        self.assertIn("reverse_proxy", first.required_surface_signals)
        self.assertIn("single_parser_control", first.controls)

    def test_cache_plan_preserves_cache_and_proxy_dimensions(self) -> None:
        plan = build_protocol_parser_plan(_family("hf-cache-poison-deception"))

        self.assertEqual(plan.lane, "http_cache_poisoning_deception")
        self.assertGreaterEqual(len(plan.steps), 8)
        self.assertEqual(
            plan.dimensions,
            ("cache_key_dimension", "cache_behavior", "proxy_layer"),
        )
        self.assertIn("edge_cache", plan.required_surface_signals)
        self.assertIn("vary_control", plan.controls)

    def test_unknown_protocol_dimension_fails_closed(self) -> None:
        family = HunterFamilyView(
            family_id="hf-bad-protocol",
            name="BAD_PROTOCOL",
            target_node_kinds=("HTTP_OPERATION",),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template="bad {canonical_key}",
            evidence_requirements={
                "protocol_lane": "bad",
                "required_surface_signals": ["reverse_proxy"],
                "required_controls": ["negative_control"],
                "required_protocol_dimensions": ["unknown_protocol_dimension"],
            },
            validation_tier="V3",
            enabled=True,
            version=1,
        )

        with self.assertRaises(ResearchInputError):
            build_protocol_parser_plan(family)

    def test_too_small_step_cap_fails_closed(self) -> None:
        with self.assertRaises(ResearchInputError):
            build_protocol_parser_plan(_family("hf-http-smuggling-desync"), max_steps=7)


if __name__ == "__main__":
    unittest.main()
