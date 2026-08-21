"""Registry-external exploratory research compiler. Not diagnostic.echo plumbing.

Reuses Authorization/StateTransition/Mutation/Protocol compilers when typed
fields are present. Model query/body/header/raw-wire keys are stripped and
never become Worker authority. Missing semantics fail closed.
"""

from __future__ import annotations

from typing import Any, Mapping

from research_os.research.compiler_registry import (
    COMPILER_GENERIC_PLANNER,
    AuthorizationDifferentialCompiler,
    CompilerOutcome,
    CompilerRequest,
    CompilerResult,
    MutationMatrixCellCompiler,
    ProtocolStepCompiler,
    StateTransitionCompiler,
    _blocked,
)
from research_os.research.exploratory import (
    ExploratoryHypothesisDraft,
    assert_ephemeral_registry_binding,
)
from research_os.research.exploratory_compile import exploratory_proposal_and_challenge
from research_os.research.types import ResearchInputError
from research_os.tools.capabilities import DIAGNOSTIC_ECHO_CAPABILITY

EXPLORATORY_RESEARCH_COMPILER_VERSION = "exploratory.research.compile.v1"
MODEL_PAYLOAD_KEYS = frozenset(
    {
        "query",
        "body",
        "headers",
        "content_type",
        "raw",
        "raw_body",
        "raw_request",
        "raw_response",
        "wire",
        "framing_bytes",
        "payload",
    }
)
AUTHZ_KEYS = (
    "authorized_origin",
    "origin",
    "actor",
    "own_object",
    "cross_object",
    "mode",
)
STATE_KEYS = (
    "authorized_origin",
    "origin",
    "actor",
    "resource_id",
    "transition",
    "area",
)
MUTATION_KEYS = (
    "cell_id",
    "selected_cell_id",
    "control",
    "authorized_origin",
    "origin",
    "path",
    "dimension_values",
    "family_name",
)
PROTOCOL_KEYS = (
    "step_id",
    "selected_step_id",
    "control",
    "authorized_origin",
    "origin",
    "path",
    "protocol_lane",
    "lane",
    "dimension_values",
    "family_name",
    "framing_profile",
)


def compile_exploratory_research(
    draft: ExploratoryHypothesisDraft,
    *,
    hypothesis_id: str,
    budget_id: str,
    target_reference: str,
    compile_arguments: Mapping[str, Any] | None = None,
) -> CompilerResult:
    """Compile a registry-external hypothesis onto a real non-diagnostic compiler."""

    assert_ephemeral_registry_binding(draft)
    if not isinstance(hypothesis_id, str) or not hypothesis_id.strip():
        raise ResearchInputError("hypothesis_id must be a non-empty string")
    if not isinstance(budget_id, str) or not budget_id.strip():
        raise ResearchInputError("budget_id must be a non-empty string")
    if not isinstance(target_reference, str) or not target_reference.strip():
        raise ResearchInputError("target_reference must be a non-empty string")

    arguments = _compiler_arguments(draft, compile_arguments)
    proposal, challenge = exploratory_proposal_and_challenge(draft)
    request = CompilerRequest(
        hypothesis_id=hypothesis_id.strip(),
        budget_id=budget_id.strip(),
        target_reference=target_reference.strip(),
        family_id=None,
        family_name=_family_hint(arguments),
        proposal=proposal,
        challenge=challenge,
        arguments=arguments,
    )
    result = _select_compiler(request)
    if result.compiled and result.plan is not None:
        if result.plan.required_capability == DIAGNOSTIC_ECHO_CAPABILITY:
            raise ResearchInputError(
                "exploratory research compile cannot select diagnostic.echo"
            )
        if result.compiler_id == COMPILER_GENERIC_PLANNER:
            raise ResearchInputError(
                "exploratory research compile cannot fall back to the generic planner"
            )
    return result


def _compiler_arguments(
    draft: ExploratoryHypothesisDraft,
    compile_arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    payload = draft.model_claimed_novelty
    _ = payload
    for key in ("source_refs",):
        _ = key
    incoming = dict(compile_arguments or {})
    for key, value in incoming.items():
        if key in MODEL_PAYLOAD_KEYS:
            continue
        merged[key] = value
    return merged


def _family_hint(arguments: Mapping[str, Any]) -> str | None:
    raw = arguments.get("family_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _select_compiler(request: CompilerRequest) -> CompilerResult:
    arguments = request.arguments
    if _has_text(arguments, "actor") and _has_text(arguments, "own_object") and _has_text(
        arguments, "cross_object"
    ) and (_has_text(arguments, "authorized_origin") or _has_text(arguments, "origin")):
        return AuthorizationDifferentialCompiler().compile(request)
    if _has_text(arguments, "actor") and _has_text(arguments, "resource_id") and _has_text(
        arguments, "transition"
    ) and (_has_text(arguments, "authorized_origin") or _has_text(arguments, "origin")):
        return StateTransitionCompiler().compile(request)
    if _has_text(arguments, "cell_id") or _has_text(arguments, "selected_cell_id"):
        return MutationMatrixCellCompiler().compile(request)
    if _has_text(arguments, "step_id") or _has_text(arguments, "selected_step_id"):
        return ProtocolStepCompiler().compile(request)
    _ = AUTHZ_KEYS, STATE_KEYS, MUTATION_KEYS, PROTOCOL_KEYS
    return _blocked(
        "exploratory.research.v1",
        CompilerOutcome.BLOCKED_MISSING_SEMANTICS,
        "EXPLORATORY_RESEARCH_MISSING_TYPED_SEMANTICS",
        family_name=request.family_name,
    )


def _has_text(arguments: Mapping[str, Any], key: str) -> bool:
    value = arguments.get(key)
    return isinstance(value, str) and bool(value.strip())
