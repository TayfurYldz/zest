"""Mutation Engine unit tests. No network, no execution."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core.enums import ScopeClassification
from zest.research.compiler import compile_experiment_intent
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.mutation import (
    AuthHeaderVariationFamily,
    BoundaryValueFamily,
    ContentTypeConfusionFamily,
    IdOrTraversalCandidateFamily,
    MethodOverrideFamily,
    MutationEngine,
    ParamPollutionFamily,
    TypeJugglingFamily,
    mutate_for_node,
    mutation_variant_to_intent,
)
from zest.research.mutation.types import MutationVariant
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError


def _node(
    *,
    node_id: str = "op-1",
    kind: AttackSurfaceNodeKind = AttackSurfaceNodeKind.HTTP_OPERATION,
    canonical_key: str = "origin:http://example.com|path:/api/users|method:GET",
    scope_classification: ScopeClassification = ScopeClassification.IN_SCOPE,
    attributes: dict | None = None,
) -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id=node_id,
        kind=kind,
        canonical_key=canonical_key,
        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
        identity_ids=(),
        provenance_refs=("sensor_observation:so-1",),
        scope_classification=scope_classification,
        attributes=attributes
        or {
            "origin": "http://example.com",
            "path": "/api/users",
            "method": "GET",
            "query_params": ["id"],
        },
    )


def _graph() -> AttackSurfaceGraph:
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(_node(),),
        edges=(),
    )


class MutationEngineScopeTests(unittest.TestCase):
    def test_in_scope_node_generates_variants(self) -> None:
        variants = mutate_for_node(_node(), _graph(), variant_id_prefix="prefix")
        self.assertGreater(len(variants), 0)

    def test_unknown_scope_node_returns_empty(self) -> None:
        node = _node(scope_classification=ScopeClassification.UNKNOWN)
        variants = mutate_for_node(node, _graph(), variant_id_prefix="prefix")
        self.assertEqual(variants, ())

    def test_out_of_scope_node_returns_empty(self) -> None:
        node = _node(scope_classification=ScopeClassification.OUT_OF_SCOPE)
        variants = mutate_for_node(node, _graph(), variant_id_prefix="prefix")
        self.assertEqual(variants, ())

    def test_unsupported_kind_returns_empty(self) -> None:
        node = _node(kind=AttackSurfaceNodeKind.TECH)
        variants = mutate_for_node(node, _graph(), variant_id_prefix="prefix")
        self.assertEqual(variants, ())


class MutationFamilyDeterminismTests(unittest.TestCase):
    def test_param_pollution_deterministic(self) -> None:
        family = ParamPollutionFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_type_juggling_deterministic(self) -> None:
        family = TypeJugglingFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_boundary_value_deterministic(self) -> None:
        family = BoundaryValueFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_auth_header_variation_deterministic(self) -> None:
        family = AuthHeaderVariationFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_method_override_deterministic(self) -> None:
        family = MethodOverrideFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_content_type_confusion_deterministic(self) -> None:
        family = ContentTypeConfusionFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)

    def test_id_traversal_deterministic(self) -> None:
        family = IdOrTraversalCandidateFamily()
        node = _node()
        v1 = family.generate(node, {}, "prefix")
        v2 = family.generate(node, {}, "prefix")
        self.assertEqual(v1, v2)


class MutationVariantValidationTests(unittest.TestCase):
    def test_variant_carries_scope_classification(self) -> None:
        variants = mutate_for_node(_node(), _graph(), variant_id_prefix="prefix")
        for variant in variants:
            self.assertEqual(variant.scope_classification, ScopeClassification.IN_SCOPE)

    def test_variant_provenance_includes_node_id(self) -> None:
        variants = mutate_for_node(_node(), _graph(), variant_id_prefix="prefix")
        for variant in variants:
            self.assertEqual(variant.provenance["node_id"], "op-1")
            self.assertIn("family_id", variant.provenance)
            self.assertIn("mutation_rule_id", variant.provenance)

    def test_variant_rejects_secret_argument_keys(self) -> None:
        with self.assertRaises(ResearchInputError):
            MutationVariant(
                variant_id="v-1",
                node_id="op-1",
                family_id="test",
                mutation_rule_id="r-1",
                target_reference="target-1",
                scope_classification=ScopeClassification.IN_SCOPE,
                capability_id="http.transaction",
                action="read",
                arguments={"token": "leaked"},
                provenance={},
            )

    def test_public_summary_respects_size_limit(self) -> None:
        # Many large arguments force the overall audit payload over 2KB.
        arguments = {f"arg_{i}": "x" * 3000 for i in range(20)}
        variant = MutationVariant(
            variant_id="v-1",
            node_id="op-1",
            family_id="test",
            mutation_rule_id="r-1",
            target_reference="target-1",
            scope_classification=ScopeClassification.IN_SCOPE,
            capability_id="http.transaction",
            action="read",
            arguments=arguments,
            provenance={},
        )
        summary = variant.to_public_summary()
        self.assertTrue(summary["arguments"].get("_truncated"))


class MutationIntentCompilationTests(unittest.TestCase):
    def test_variant_compiles_to_experiment_plan(self) -> None:
        variants = mutate_for_node(_node(), _graph(), variant_id_prefix="prefix")
        self.assertTrue(variants)
        variant = variants[0]
        intent = mutation_variant_to_intent(variant, budget_id="budget-1")
        plan = compile_experiment_intent(intent)
        self.assertEqual(plan.required_capability, "http.transaction")
        self.assertEqual(plan.requested_budget_id, "budget-1")

    def test_engine_class_interface(self) -> None:
        engine = MutationEngine()
        variants = engine.mutate(_node(), _graph(), variant_id_prefix="prefix")
        self.assertGreater(len(variants), 0)


if __name__ == "__main__":
    unittest.main()
