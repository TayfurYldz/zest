"""ImpactGraph domain types. Impact claims are not findings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from zest.research.types import ResearchInputError


class ImpactKind(Enum):
    """Category of security impact a chain node may claim.

    The claim must be supported by the demonstrated capabilities of the
    referenced proofs (see capability_map.py).
    """

    DATA_READ = "DATA_READ"
    DATA_WRITE = "DATA_WRITE"
    AUTH_BYPASS = "AUTH_BYPASS"
    STATE_CORRUPTION = "STATE_CORRUPTION"
    ACCOUNT_TAKEOVER_PATH = "ACCOUNT_TAKEOVER_PATH"
    EXTERNAL_CALLBACK = "EXTERNAL_CALLBACK"


class ImpactRelation(Enum):
    ENABLES = "ENABLES"
    ESCALATES = "ESCALATES"
    CONFIRMS = "CONFIRMS"


@dataclass(frozen=True)
class ChainValidation:
    """Result of validating an ImpactChain."""

    valid: bool
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise ResearchInputError("valid must be a bool")
        if not isinstance(self.reason_codes, tuple):
            raise ResearchInputError("reason_codes must be a tuple")


@dataclass(frozen=True)
class ProofRecord:
    """Resolved proof metadata. No raw secrets or full payloads."""

    proof_id: str
    research_run_id: str
    target_reference: str
    demonstrated_capabilities: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "proof_id", _require_text(self.proof_id, "proof_id")
        )
        object.__setattr__(
            self,
            "research_run_id",
            _require_text(self.research_run_id, "research_run_id"),
        )
        object.__setattr__(
            self,
            "target_reference",
            _require_text(self.target_reference, "target_reference"),
        )
        if not isinstance(self.demonstrated_capabilities, frozenset):
            raise ResearchInputError("demonstrated_capabilities must be a frozenset")


@runtime_checkable
class ProofResolver(Protocol):
    """Port: resolve a proof_id to metadata for a specific research run.

    Research layer does not implement this. The expected_run_id parameter lets
    the validator reject proofs that belong to a different run (cross-run
    provenance is not allowed).
    """

    def resolve(self, proof_id: str, expected_run_id: str) -> ProofRecord | None: ...


class ImpactGraphError(ValueError):
    """Invalid impact graph construction or validation."""


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_id(value: object, field_name: str) -> str:
    return _require_text(value, field_name)


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    cleaned: list[str] = []
    for index, item in enumerate(value):
        cleaned.append(_require_id(item, f"{field_name}[{index}]"))
    return tuple(cleaned)
