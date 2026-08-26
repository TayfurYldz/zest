"""Untrusted structured Generator/Falsifier outputs. Not Hypothesis truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from zest.research.output_contracts import (
    ACCEPTED_NOVELTY_BASIS,
    FALSIFIER_CONTRACT,
    FORBIDDEN_AUTHORITY_KEYS,
    GENERATOR_CONTRACT,
    NoveltyBasis,
)
from zest.research.types import ResearchInputError

PROPOSAL_KEYS = GENERATOR_CONTRACT.allowed_keys
CHALLENGE_KEYS = FALSIFIER_CONTRACT.allowed_keys


class ProposalAuthorityError(ResearchInputError):
    """Structured output tried to claim policy, Evidence, Finding, or authority."""


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResearchInputError(f"{field_name} must be a string or None")
    stripped = value.strip()
    return stripped or None


def _text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ResearchInputError(f"{field_name} must be a list of strings")
    if not isinstance(value, (list, tuple)):
        raise ResearchInputError(f"{field_name} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ResearchInputError(f"{field_name}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


def _reject_authority_keys(raw: Mapping[str, object], label: str) -> None:
    found = FORBIDDEN_AUTHORITY_KEYS.intersection(raw.keys())
    if found:
        raise ProposalAuthorityError(
            f"{label} must not claim authority via keys: {sorted(found)}"
        )


def _require_mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ResearchInputError(f"{label} must be a mapping")
    return raw


def _unknown_keys(raw: Mapping[str, object], allowed: frozenset[str]) -> frozenset[str]:
    return frozenset(raw.keys()) - allowed


def parse_novelty_basis(value: object) -> tuple[NoveltyBasis, str | None]:
    if value is None:
        return NoveltyBasis.UNCLASSIFIED, None
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError("novelty_basis must be a string")
    token = value.strip()
    if token not in ACCEPTED_NOVELTY_BASIS:
        raise ResearchInputError("novelty_basis is not a known advisory class")
    return NoveltyBasis(token), token


@dataclass(frozen=True)
class HypothesisProposal:
    """Generator output. Not a persisted Hypothesis and not Evidence."""

    proposed_claim: str
    rationale: str
    source_references: tuple[str, ...]
    assumptions: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    suggested_disconfirming_test: str
    suggested_capability: str
    expected_security_relevance: str | None = None
    novelty_basis: NoveltyBasis = NoveltyBasis.UNCLASSIFIED
    model_claimed_novelty: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "proposed_claim": self.proposed_claim,
            "rationale": self.rationale,
            "source_references": list(self.source_references),
            "assumptions": list(self.assumptions),
            "unresolved_questions": list(self.unresolved_questions),
            "suggested_disconfirming_test": self.suggested_disconfirming_test,
            "suggested_capability": self.suggested_capability,
            "expected_security_relevance": self.expected_security_relevance,
            "novelty_basis": self.novelty_basis.value,
            "model_claimed_novelty": self.model_claimed_novelty,
        }


@dataclass(frozen=True)
class HypothesisChallenge:
    """Falsifier output. Not truth, Evidence, or a Finding decision."""

    alternative_explanations: tuple[str, ...]
    missing_preconditions: tuple[str, ...]
    contradictory_source_references: tuple[str, ...]
    required_negative_controls: tuple[str, ...]
    reasons_not_to_test: tuple[str, ...]
    proposed_disconfirming_observation: str
    ambiguity: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "alternative_explanations": list(self.alternative_explanations),
            "missing_preconditions": list(self.missing_preconditions),
            "contradictory_source_references": list(self.contradictory_source_references),
            "required_negative_controls": list(self.required_negative_controls),
            "reasons_not_to_test": list(self.reasons_not_to_test),
            "proposed_disconfirming_observation": self.proposed_disconfirming_observation,
            "ambiguity": self.ambiguity,
        }


def parse_hypothesis_proposal(raw: object) -> HypothesisProposal:
    mapping = _require_mapping(raw, "HypothesisProposal")
    _reject_authority_keys(mapping, "HypothesisProposal")
    unknown = _unknown_keys(mapping, PROPOSAL_KEYS)
    if unknown:
        raise ResearchInputError(
            f"HypothesisProposal has unsupported keys: {sorted(unknown)}"
        )
    normalized_novelty, claimed_novelty = parse_novelty_basis(mapping.get("novelty_basis"))
    return HypothesisProposal(
        proposed_claim=_require_text(mapping.get("proposed_claim"), "proposed_claim"),
        rationale=_require_text(mapping.get("rationale"), "rationale"),
        source_references=_text_tuple(mapping.get("source_references"), "source_references"),
        assumptions=_text_tuple(mapping.get("assumptions"), "assumptions"),
        unresolved_questions=_text_tuple(
            mapping.get("unresolved_questions"), "unresolved_questions"
        ),
        suggested_disconfirming_test=_require_text(
            mapping.get("suggested_disconfirming_test"), "suggested_disconfirming_test"
        ),
        suggested_capability=_require_text(
            mapping.get("suggested_capability"), "suggested_capability"
        ),
        expected_security_relevance=_optional_text(
            mapping.get("expected_security_relevance"), "expected_security_relevance"
        ),
        novelty_basis=normalized_novelty,
        model_claimed_novelty=claimed_novelty,
    )


def parse_hypothesis_challenge(raw: object) -> HypothesisChallenge:
    mapping = _require_mapping(raw, "HypothesisChallenge")
    _reject_authority_keys(mapping, "HypothesisChallenge")
    unknown = _unknown_keys(mapping, CHALLENGE_KEYS)
    if unknown:
        raise ResearchInputError(
            f"HypothesisChallenge has unsupported keys: {sorted(unknown)}"
        )
    return HypothesisChallenge(
        alternative_explanations=_text_tuple(
            mapping.get("alternative_explanations"), "alternative_explanations"
        ),
        missing_preconditions=_text_tuple(
            mapping.get("missing_preconditions"), "missing_preconditions"
        ),
        contradictory_source_references=_text_tuple(
            mapping.get("contradictory_source_references"),
            "contradictory_source_references",
        ),
        required_negative_controls=_text_tuple(
            mapping.get("required_negative_controls"), "required_negative_controls"
        ),
        reasons_not_to_test=_text_tuple(
            mapping.get("reasons_not_to_test"), "reasons_not_to_test"
        ),
        proposed_disconfirming_observation=_require_text(
            mapping.get("proposed_disconfirming_observation"),
            "proposed_disconfirming_observation",
        ),
        ambiguity=_optional_text(mapping.get("ambiguity"), "ambiguity"),
    )
