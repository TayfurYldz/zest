"""Mutation Engine types. Pure research-layer data; no authorization, no execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from zest.core.enums import ScopeClassification
from zest.research.discovery.graph import AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.types import ExperimentPlan, ResearchInputError

MAX_AUDIT_ARGUMENT_BYTES = 2048
FORBIDDEN_VARIANT_ARGUMENT_KEYS = frozenset(
    {
        "token",
        "secret",
        "password",
        "api_key",
        "private_key",
        "session",
        "cookie",
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value


@dataclass(frozen=True)
class MutationVariant:
    """Deterministic attack-variant plan. Not an ExperimentPlan and not Core ALLOW."""

    variant_id: str
    node_id: str
    family_id: str
    mutation_rule_id: str
    target_reference: str
    scope_classification: ScopeClassification
    capability_id: str
    action: str
    arguments: Mapping[str, Any]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "variant_id", _require_text(self.variant_id, "variant_id"))
        object.__setattr__(self, "node_id", _require_text(self.node_id, "node_id"))
        object.__setattr__(self, "family_id", _require_text(self.family_id, "family_id"))
        object.__setattr__(
            self, "mutation_rule_id", _require_text(self.mutation_rule_id, "mutation_rule_id")
        )
        object.__setattr__(
            self, "target_reference", _require_text(self.target_reference, "target_reference")
        )
        object.__setattr__(
            self, "capability_id", _require_text(self.capability_id, "capability_id")
        )
        object.__setattr__(self, "action", _require_text(self.action, "action"))
        if not isinstance(self.scope_classification, ScopeClassification):
            raise ResearchInputError("scope_classification must be a ScopeClassification")
        if not isinstance(self.arguments, Mapping):
            raise ResearchInputError("arguments must be a mapping")
        object.__setattr__(self, "arguments", dict(self.arguments))
        if not isinstance(self.provenance, Mapping):
            raise ResearchInputError("provenance must be a mapping")
        object.__setattr__(self, "provenance", dict(self.provenance))
        found = FORBIDDEN_VARIANT_ARGUMENT_KEYS.intersection(
            key.lower() for key in self.arguments.keys()
        )
        if found:
            raise ResearchInputError(f"arguments must not contain secret keys: {sorted(found)}")

    def to_public_summary(self) -> dict[str, Any]:
        """Size-bounded audit payload (D5). No secrets, no full body, no token values."""
        summary_arguments = {}
        for key, value in self.arguments.items():
            text = str(value)
            # Truncate individual values; never include a full response body or secret string.
            if len(text) > 256:
                text = text[:253] + "..."
            summary_arguments[key] = text
        payload = {
            "variant_id": self.variant_id,
            "node_id": self.node_id,
            "family_id": self.family_id,
            "mutation_rule_id": self.mutation_rule_id,
            "capability_id": self.capability_id,
            "action": self.action,
            "scope_classification": self.scope_classification.value,
            "arguments": summary_arguments,
            "provenance": dict(self.provenance),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        if len(encoded.encode("utf-8")) > MAX_AUDIT_ARGUMENT_BYTES:
            payload["arguments"] = {"_truncated": True, "_reason": "audit_size_limit"}
        return payload


@dataclass(frozen=True)
class MutationRule:
    """One deterministic transformation rule inside a mutation family."""

    rule_id: str
    family_id: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _require_text(self.rule_id, "rule_id"))
        object.__setattr__(self, "family_id", _require_text(self.family_id, "family_id"))
        object.__setattr__(self, "description", _require_text(self.description, "description"))


@runtime_checkable
class MutationFamily(Protocol):
    """Research-layer protocol for deterministic variant families."""

    @property
    def family_id(self) -> str: ...

    def generate(
        self,
        node: AttackSurfaceNode,
        provenance: Mapping[str, Any],
        variant_id_prefix: str,
    ) -> tuple[MutationVariant, ...]: ...
