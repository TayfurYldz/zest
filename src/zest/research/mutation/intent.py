"""Convert a MutationVariant into a bound ExperimentIntent. Not authorization."""

from __future__ import annotations

from zest.research.compiler import ExperimentIntent
from zest.research.mutation.types import MutationVariant
from zest.research.types import ResearchInputError


def mutation_variant_to_intent(
    variant: MutationVariant,
    *,
    budget_id: str,
    expected_observation: str = "mutation variant produced distinguishable response",
    disconfirming_observation: str = "mutation variant produced identical or denied response",
    evaluation_strategy: str = "mutation.engine.v1",
) -> ExperimentIntent:
    """Bind a mutation variant to an ExperimentIntent for compile_experiment_intent."""
    if not isinstance(variant, MutationVariant):
        raise ResearchInputError("variant must be a MutationVariant")
    return ExperimentIntent(
        hypothesis_id=variant.variant_id,
        capability_id=variant.capability_id,
        action=variant.action,
        target_reference=variant.target_reference,
        arguments=dict(variant.arguments),
        requested_budget_id=budget_id,
        expected_observation=expected_observation,
        disconfirming_observation=disconfirming_observation,
        evaluation_strategy=evaluation_strategy,
        target_type="opaque_reference",
    )
