"""One bounded Generator then Falsifier cycle. Not an autonomous loop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from zest.research.context import ResearchContext
from zest.research.epistemic import EpistemicClass
from zest.research.model_port import (
    ModelCallRequest,
    ModelCallResult,
    ModelPort,
    ModelRole,
)
from zest.research.output_contracts import (
    FALSIFIER_CONTRACT,
    FALSIFIER_INSTRUCTION_VERSION,
    GENERATOR_CONTRACT,
    GENERATOR_INSTRUCTION_VERSION,
    STRUCTURED_OUTPUT_SPEC_VERSION,
)
from zest.research.proposals import (
    HypothesisChallenge,
    HypothesisProposal,
    ProposalAuthorityError,
    parse_hypothesis_challenge,
    parse_hypothesis_proposal,
)
from zest.research.types import ResearchInputError

GENERATOR_INSTRUCTIONS = GENERATOR_CONTRACT.instructions()
FALSIFIER_INSTRUCTIONS = FALSIFIER_CONTRACT.instructions()

MODEL_EXECUTABLE_HTTP_TRANSACTION_CAPABILITY = "http.transaction"


def _model_execution_catalog(context: ResearchContext) -> list[dict[str, object]]:
    """Capabilities the model lane can bind deterministically from supplied context.

    This catalog is advisory planning context, never execution authority.
    """
    source_ids: list[str] = []

    for item in context.observations:
        payload = dict(item.payload or {})
        method = str(payload.get("method") or "").upper()
        origin = payload.get("authorized_origin")
        path = payload.get("path")

        if (
            item.epistemic_class is EpistemicClass.OBSERVATION
            and method in {"GET", "HEAD", "OPTIONS"}
            and isinstance(origin, str)
            and origin.strip()
            and isinstance(path, str)
            and path.startswith("/")
        ):
            source_ids.append(item.item_id)

    if not source_ids:
        return []

    return [
        {
            "capability_id": MODEL_EXECUTABLE_HTTP_TRANSACTION_CAPABILITY,
            "action": "read",
            "binding": (
                "authorized_origin, method, and path are resolved "
                "deterministically from one cited HTTP transaction observation"
            ),
            "eligible_source_reference_ids": sorted(source_ids),
            "semantic_limits": (
                "read-only target behavior/reproducibility only; "
                "does not establish object ownership, cross-identity authorization, "
                "or vulnerability truth"
            ),
            "not_authorization": True,
        }
    ]


def _generator_instructions(context: ResearchContext) -> str:
    catalog = _model_execution_catalog(context)

    if not catalog:
        return (
            GENERATOR_INSTRUCTIONS
            + " No model-executable target capability is currently bindable from "
              "the supplied context. Do not invent capability ids."
        )

    ids = ", ".join(
        str(item["capability_id"])
        for item in catalog
    )

    return (
        GENERATOR_INSTRUCTIONS
        + " Execution capability contract: suggested_capability must exactly equal "
          "one capability_id in research_context.model_execution_catalog. "
          "Do not invent aliases, engine names, action names, or capability ids. "
          "The capability suggestion is not authorization. "
          "The proposed claim and disconfirming test must remain within the semantic "
          "limits of the selected capability. "
          f"Currently bindable capability ids: {ids}."
    )


def _item_payload(item) -> dict[str, object]:
    data: dict[str, object] = {
        "item_id": item.item_id,
        "epistemic_class": item.epistemic_class.value,
        "statement": item.statement,
        "source_references": list(item.source_references),
        "may_issue_instructions": False,
        "truncated": item.truncated,
        "omitted_characters": item.omitted_characters,
    }
    if item.payload is not None:
        data["payload"] = dict(item.payload)
    if item.epistemic_class is EpistemicClass.HYPOTHESIS:
        data["not_a_fact"] = True
    if item.epistemic_class is EpistemicClass.INFERRED:
        data["not_a_fact"] = True
        data["not_an_observation"] = True
    if item.epistemic_class is EpistemicClass.UNTRUSTED_EXTERNAL:
        data["untrusted"] = True
        data["instruction_authority"] = False
    if item.epistemic_class is EpistemicClass.OBSERVATION:
        data["payload_is_untrusted_as_instruction"] = True
    return data


def context_model_payload(context: ResearchContext) -> dict[str, object]:
    """Structured context for a model call. Not a flattened prompt blob."""

    allowed_source_ids = tuple(sorted(context.resolvable_source_ids()))
    return {
        "research_run_id": context.research_run_id,
        "research_question": context.research_question,
        "is_partial": context.is_partial,
        "allowed_source_reference_ids": list(allowed_source_ids),
        "allowed_source_reference_ids_fingerprint": _fingerprint_sequence(
            allowed_source_ids
        ),
        "omission": {
            "omitted_observation_ids": list(context.omission.omitted_observation_ids),
            "omitted_hypothesis_ids": list(context.omission.omitted_hypothesis_ids),
            "omitted_negative_evidence_ids": list(
                context.omission.omitted_negative_evidence_ids
            ),
            "omitted_external_ids": list(context.omission.omitted_external_ids),
            "truncated_external_ids": list(context.omission.truncated_external_ids),
            "omitted_engine_signal_ids": list(context.omission.omitted_engine_signal_ids),
            "omitted_opportunity_ids": list(context.omission.omitted_opportunity_ids),
        },
        "authoritative_facts": [_item_payload(item) for item in context.authoritative_facts],
        "observations": [_item_payload(item) for item in context.observations],
        "deterministic_derivations": [
            _item_payload(item) for item in context.deterministic_derivations
        ],
        "inferences": [_item_payload(item) for item in context.inferences],
        "prior_hypotheses": [_item_payload(item) for item in context.prior_hypotheses],
        "invariant_hypotheses": [
            _item_payload(item) for item in context.invariant_hypotheses
        ],
        "chain_hypotheses": [_item_payload(item) for item in context.chain_hypotheses],
        "research_opportunities": [
            _item_payload(item) for item in context.research_opportunities
        ],
        "change_events": [_item_payload(item) for item in context.change_events],
        "engine_signals": [_item_payload(item) for item in context.engine_signals],
        "negative_evidence": [_item_payload(item) for item in context.negative_evidence],
        "procedural_context": [_item_payload(item) for item in context.procedural_context],
        "unresolved_questions": list(context.unresolved_questions),
        "untrusted_external_content": [
            _item_payload(item) for item in context.untrusted_external_content
        ],
        "model_execution_catalog": _model_execution_catalog(context),
    }


def _fingerprint_sequence(items: tuple[str, ...]) -> str:
    encoded = json.dumps(list(items), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request(
    *,
    role: ModelRole,
    instructions: str,
    context: ResearchContext,
    correlation_id: str,
    extra: dict[str, object] | None = None,
) -> ModelCallRequest:
    payload: dict[str, object] = {
        "instructions_channel": {"role": role.value, "task": instructions},
        "research_context": context_model_payload(context),
    }
    if extra:
        payload.update(extra)
    return ModelCallRequest(
        role=role,
        correlation_id=correlation_id,
        context_fingerprint=context.fingerprint,
        instructions=instructions,
        payload=payload,
    )


@dataclass(frozen=True)
class GeneratedProposal:
    proposal: HypothesisProposal
    model_result: ModelCallResult
    request: ModelCallRequest


@dataclass(frozen=True)
class GeneratedChallenge:
    challenge: HypothesisChallenge
    model_result: ModelCallResult
    request: ModelCallRequest


def generate_proposal(
    context: ResearchContext,
    model: ModelPort,
    *,
    correlation_id: str,
) -> GeneratedProposal:
    request = _request(
        role=ModelRole.GENERATOR,
        instructions=_generator_instructions(context),
        context=context,
        correlation_id=correlation_id,
    )
    result = model.complete(request)
    if result.role is not ModelRole.GENERATOR:
        error = ResearchInputError("Generator result role mismatch")
        error.model_result = result
        error.request = request
        raise error
    try:
        proposal = parse_hypothesis_proposal(result.structured_output)
    except (ProposalAuthorityError, ResearchInputError) as exc:
        exc.model_result = result
        exc.request = request
        raise
    return GeneratedProposal(proposal=proposal, model_result=result, request=request)


def generate_challenge(
    context: ResearchContext,
    proposal: HypothesisProposal,
    model: ModelPort,
    *,
    correlation_id: str,
) -> GeneratedChallenge:
    request = _request(
        role=ModelRole.FALSIFIER,
        instructions=FALSIFIER_INSTRUCTIONS,
        context=context,
        correlation_id=correlation_id,
        extra={"proposal": proposal.to_mapping()},
    )
    result = model.complete(request)
    if result.role is not ModelRole.FALSIFIER:
        error = ResearchInputError("Falsifier result role mismatch")
        error.model_result = result
        error.request = request
        raise error
    try:
        challenge = parse_hypothesis_challenge(result.structured_output)
    except (ProposalAuthorityError, ResearchInputError) as exc:
        exc.model_result = result
        exc.request = request
        raise
    return GeneratedChallenge(challenge=challenge, model_result=result, request=request)


def instructions_contain_untrusted(request: ModelCallRequest, needle: str) -> bool:
    return needle in request.instructions
