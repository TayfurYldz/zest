"""Metamorphic variant alignment. Hidden meaning must not drift with surface edits."""

from __future__ import annotations

from zest.benchmark.errors import BenchmarkError
from zest.benchmark.leakage import model_visible_blob
from zest.benchmark.scenarios import BenchmarkScenario, context_from_visible


def assert_variant_aligned(parent: BenchmarkScenario, variant: BenchmarkScenario) -> None:
    if variant.variant_of != parent.scenario_id:
        raise BenchmarkError(
            f"{variant.identity} variant_of must be {parent.scenario_id}"
        )
    if variant.category is not parent.category:
        raise BenchmarkError(f"{variant.identity} category drifted from parent")
    if not variant.variant_kind:
        raise BenchmarkError(f"{variant.identity} requires variant_kind")
    parent_expected = set(parent.hidden_evaluation.expected_admission_outcomes)
    variant_expected = set(variant.hidden_evaluation.expected_admission_outcomes)
    if parent_expected and variant_expected and not parent_expected.intersection(variant_expected):
        raise BenchmarkError(
            f"{variant.identity} expected admission family is incompatible with parent"
        )
    parent_ids = {item.observation_id for item in parent.visible_input.observations}
    variant_ids = {item.observation_id for item in variant.visible_input.observations}
    if variant.variant_kind in {"opaque_id_rename", "reorder_and_rename"} and parent_ids.intersection(variant_ids):
        raise BenchmarkError(
            f"{variant.identity} id rename/reorder leaked parent observation ids"
        )
    context = context_from_visible(variant.visible_input)
    blob = model_visible_blob(context)
    if parent.hidden_evaluation.leakage_canary in blob:
        raise BenchmarkError(f"{variant.identity} contains parent leakage canary")
    if variant.hidden_evaluation.leakage_canary in blob:
        raise BenchmarkError(f"{variant.identity} leaked its own hidden canary")
