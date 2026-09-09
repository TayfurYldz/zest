"""Run-scoped compiler adapter for registry-external exploratory hypotheses.

Reuses `ExperimentCompilerRegistry` generic planner. Never binds a permanent
HunterFamily compiler. Never authorizes, never dispatches, never writes the
hunter_family registry. Model output is not Evidence.
"""

from __future__ import annotations

from zest.research.compiler_registry import (
    COMPILER_GENERIC_PLANNER,
    CompilerRequest,
    CompilerResult,
    ExperimentCompilerRegistry,
)
from zest.research.exploratory import (
    ExploratoryHypothesisDraft,
    assert_ephemeral_registry_binding,
)
from zest.research.planning import (
    DIAGNOSTIC_CLAIM,
    DIAGNOSTIC_DISCONFIRMING_OBSERVATION,
)
from zest.research.proposals import HypothesisChallenge, HypothesisProposal
from zest.research.types import ResearchInputError
from zest.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY

EXPLORATORY_COMPILER_ADAPTER_VERSION = "exploratory.ephemeral.compile.v1"
EXPLORATORY_ECHO_MESSAGE = "ping"


def compile_exploratory_hypothesis(
    draft: ExploratoryHypothesisDraft,
    *,
    hypothesis_id: str,
    budget_id: str,
    target_reference: str,
    message: str = EXPLORATORY_ECHO_MESSAGE,
    registry: ExperimentCompilerRegistry | None = None,
) -> CompilerResult:
    """Compile an exploratory draft onto diagnostic.echo via the generic planner.

    `family_name` is intentionally omitted so known-family compilers cannot
    capture a registry-external idea even if the proposed name later collides.
    """

    assert_ephemeral_registry_binding(draft)
    if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
        raise ResearchInputError("hypothesis_id must be a non-empty string")
    if not isinstance(budget_id, str) or not budget_id.strip():
        raise ResearchInputError("budget_id must be a non-empty string")
    if not isinstance(target_reference, str) or not target_reference.strip():
        raise ResearchInputError("target_reference must be a non-empty string")
    if not isinstance(message, str) or not message.strip():
        raise ResearchInputError("message must be a non-empty string")

    proposal, challenge = exploratory_proposal_and_challenge(draft)
    compilers = registry or ExperimentCompilerRegistry()
    result = compilers.compile(
        CompilerRequest(
            hypothesis_id=hypothesis_id.strip(),
            budget_id=budget_id.strip(),
            target_reference=target_reference.strip(),
            family_id=None,
            family_name=None,
            proposal=proposal,
            challenge=challenge,
            arguments={"message": message.strip()},
        )
    )
    if result.compiled and result.plan is not None:
        if result.plan.required_capability != DIAGNOSTIC_ECHO_CAPABILITY:
            raise ResearchInputError("exploratory compile cannot select a non-diagnostic capability")
        if result.compiler_id != COMPILER_GENERIC_PLANNER:
            raise ResearchInputError("exploratory compile must use the generic planner")
    return result


def exploratory_proposal_and_challenge(
    draft: ExploratoryHypothesisDraft,
) -> tuple[HypothesisProposal, HypothesisChallenge]:
    """Deterministic proposal/challenge for the generic planner. Not model output."""

    assert_ephemeral_registry_binding(draft)
    proposal = HypothesisProposal(
        proposed_claim=draft.hypothesis_claim or DIAGNOSTIC_CLAIM,
        rationale=draft.proposed_family_rationale,
        source_references=draft.source_refs,
        assumptions=("exploratory draft is not a HunterFamily", "side_effect_estimate:0"),
        unresolved_questions=("does the diagnostic control loop round-trip?",),
        suggested_disconfirming_test="submit a value and observe mismatch",
        suggested_capability=DIAGNOSTIC_ECHO_CAPABILITY,
        expected_security_relevance=None,
        novelty_basis=draft.novelty_basis,
        model_claimed_novelty=draft.model_claimed_novelty,
    )
    challenge = HypothesisChallenge(
        alternative_explanations=("anomaly is environmental rather than repeatable",),
        missing_preconditions=(),
        contradictory_source_references=(),
        required_negative_controls=("repeat diagnostic echo",),
        reasons_not_to_test=(),
        proposed_disconfirming_observation=DIAGNOSTIC_DISCONFIRMING_OBSERVATION,
        ambiguity="exploratory execution is not a vulnerability verdict",
    )
    return proposal, challenge
