"""Registry-external exploratory hypothesis drafts. Not Evidence or Finding."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.safe_data import SecretMaterialError, reject_secret_keys
from zest.research.proposals import NoveltyBasis
from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError

EXPLORATORY_HYPOTHESIS_STRATEGY_VERSION = "exploratory.hypothesis.registry_external.v1"
EXPLORATORY_DRAFTED_EVENT = "EXPLORATORY_HYPOTHESIS_DRAFTED"
EXPLORATORY_SUBJECT_TYPE = "exploratory_family_draft"
VALIDATION_GATES = ("HYPOTHESIZED", "V1", "V2", "V3", "FALSE_FINDING_ZERO")
FORBIDDEN_EXPLORATORY_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "exploit",
        "evidence",
        "candidate",
        "finding",
        "authorization",
        "confidence",
        "token",
        "session_token",
        "password",
        "raw_request",
        "raw_response",
        "body",
    }
)
FORBIDDEN_TRUTH_MARKERS = (
    "confirmed vulnerability",
    "this is a vulnerability",
    "declare this evidence",
    "declare this a finding",
    "confirmed exploit",
    "working exploit",
)


class ExploratorySignalKind(Enum):
    """Anomaly source class. Not a vulnerability class."""

    TEMPORAL_CHANGE = "TEMPORAL_CHANGE"
    COVERAGE_DEBT_INCREASE = "COVERAGE_DEBT_INCREASE"
    SCOPE_BOUNDARY_CANDIDATE = "SCOPE_BOUNDARY_CANDIDATE"
    RESPONSE_SHAPE_DRIFT = "RESPONSE_SHAPE_DRIFT"
    GRAPH_STRUCTURE_ANOMALY = "GRAPH_STRUCTURE_ANOMALY"
    IDENTITY_ANOMALY = "IDENTITY_ANOMALY"
    WORKFLOW_ANOMALY = "WORKFLOW_ANOMALY"
    LAB_ZERO_DAY_STYLE_ANOMALY = "LAB_ZERO_DAY_STYLE_ANOMALY"
    OTHER = "OTHER"


@dataclass(frozen=True)
class ExploratorySignal:
    """A sourced anomaly used to draft a registry-external family idea."""

    signal_id: str
    research_run_id: str
    kind: ExploratorySignalKind
    description: str
    source_refs: tuple[str, ...]
    target_node_kind: str
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _require_text(self.signal_id, "signal_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.kind, ExploratorySignalKind):
            raise ResearchInputError("kind must be an ExploratorySignalKind")
        description = _require_text(self.description, "description")
        _reject_truth_claims(description, "description")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self,
            "target_node_kind",
            _require_text(self.target_node_kind, "target_node_kind"),
        )
        object.__setattr__(
            self,
            "attributes",
            _reject_forbidden(self.attributes or {}, "attributes"),
        )


@dataclass(frozen=True)
class ExploratoryHypothesisDraft:
    """A human-reviewable family draft plus HYPOTHESIZED claim."""

    draft_id: str
    research_run_id: str
    hypothesis_claim: str
    proposed_family_name: str
    proposed_family_rationale: str
    source_refs: tuple[str, ...]
    signal_ids: tuple[str, ...]
    target_node_kinds: tuple[str, ...]
    structural_identity: str
    novelty_basis: NoveltyBasis = NoveltyBasis.UNCLASSIFIED
    model_claimed_novelty: str | None = None
    strategy_version: str = EXPLORATORY_HYPOTHESIS_STRATEGY_VERSION
    status: str = "HYPOTHESIZED"
    validation_gates: tuple[str, ...] = VALIDATION_GATES
    registry_external: bool = True
    requires_human_family_approval: bool = True
    may_write_hunter_registry: bool = False
    not_evidence: bool = True
    not_candidate: bool = True
    not_finding: bool = True
    not_impact_graph_edge: bool = True
    false_finding_required_zero: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _require_text(self.draft_id, "draft_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        claim = _require_text(self.hypothesis_claim, "hypothesis_claim")
        _reject_truth_claims(claim, "hypothesis_claim")
        object.__setattr__(self, "hypothesis_claim", claim)
        object.__setattr__(
            self,
            "proposed_family_name",
            _require_text(self.proposed_family_name, "proposed_family_name"),
        )
        rationale = _require_text(self.proposed_family_rationale, "proposed_family_rationale")
        _reject_truth_claims(rationale, "proposed_family_rationale")
        object.__setattr__(self, "proposed_family_rationale", rationale)
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(self, "signal_ids", _require_ids(self.signal_ids, "signal_ids"))
        object.__setattr__(
            self,
            "target_node_kinds",
            _require_ids(self.target_node_kinds, "target_node_kinds"),
        )
        object.__setattr__(
            self,
            "structural_identity",
            _require_text(self.structural_identity, "structural_identity"),
        )
        if not isinstance(self.novelty_basis, NoveltyBasis):
            raise ResearchInputError("novelty_basis must be NoveltyBasis")
        if self.model_claimed_novelty is not None:
            object.__setattr__(
                self,
                "model_claimed_novelty",
                _require_text(self.model_claimed_novelty, "model_claimed_novelty"),
            )
        if self.status != "HYPOTHESIZED":
            raise ResearchInputError("exploratory draft must start as HYPOTHESIZED")
        if self.validation_gates != VALIDATION_GATES:
            raise ResearchInputError("exploratory draft must retain V1/V2/V3 false-finding gates")
        if not (
            self.registry_external
            and self.requires_human_family_approval
            and not self.may_write_hunter_registry
            and self.not_evidence
            and self.not_candidate
            and self.not_finding
            and self.not_impact_graph_edge
            and self.false_finding_required_zero
        ):
            raise ResearchInputError("exploratory draft cannot bypass registry or finding gates")

    def to_audit_payload(self, *, hypothesis_id: str) -> dict[str, Any]:
        return {
            "hypothesis_id": hypothesis_id,
            "hypothesis_claim": self.hypothesis_claim,
            "proposed_family_name": self.proposed_family_name,
            "proposed_family_rationale": self.proposed_family_rationale,
            "source_refs": list(self.source_refs),
            "signal_ids": list(self.signal_ids),
            "target_node_kinds": list(self.target_node_kinds),
            "structural_identity": self.structural_identity,
            "novelty_basis": self.novelty_basis.value,
            "model_claimed_novelty": self.model_claimed_novelty,
            "strategy_version": self.strategy_version,
            "status": self.status,
            "validation_gates": list(self.validation_gates),
            "registry_external": self.registry_external,
            "requires_human_family_approval": self.requires_human_family_approval,
            "may_write_hunter_registry": self.may_write_hunter_registry,
            "not_evidence": self.not_evidence,
            "not_candidate": self.not_candidate,
            "not_finding": self.not_finding,
            "not_impact_graph_edge": self.not_impact_graph_edge,
            "false_finding_required_zero": self.false_finding_required_zero,
        }


def assert_ephemeral_registry_binding(draft: ExploratoryHypothesisDraft) -> None:
    """Run-scoped drafts never authorize a permanent HunterFamily write."""

    if draft.may_write_hunter_registry:
        raise ResearchInputError("exploratory draft cannot authorize hunter registry write")
    if not draft.registry_external or not draft.requires_human_family_approval:
        raise ResearchInputError("exploratory draft cannot bypass registry or finding gates")


def exploratory_draft_from_audit(
    *,
    draft_id: str,
    research_run_id: str,
    payload: Mapping[str, Any],
) -> ExploratoryHypothesisDraft:
    """Rebuild a draft from the SD-G16 audit payload. Tampered flags fail closed."""

    if not isinstance(payload, Mapping):
        raise ResearchInputError("exploratory audit payload must be a mapping")
    if payload.get("may_write_hunter_registry") is not False:
        raise ResearchInputError("exploratory draft cannot authorize hunter registry write")
    novelty_raw = payload.get("novelty_basis")
    try:
        novelty = (
            NoveltyBasis(novelty_raw)
            if isinstance(novelty_raw, str)
            else NoveltyBasis.UNCLASSIFIED
        )
    except ValueError as exc:
        raise ResearchInputError("novelty_basis must be NoveltyBasis") from exc
    claimed = payload.get("model_claimed_novelty")
    return ExploratoryHypothesisDraft(
        draft_id=draft_id,
        research_run_id=research_run_id,
        hypothesis_claim=_require_text(payload.get("hypothesis_claim"), "hypothesis_claim"),
        proposed_family_name=_require_text(
            payload.get("proposed_family_name"), "proposed_family_name"
        ),
        proposed_family_rationale=_require_text(
            payload.get("proposed_family_rationale"), "proposed_family_rationale"
        ),
        source_refs=_ids_from_payload(payload.get("source_refs"), "source_refs"),
        signal_ids=_ids_from_payload(payload.get("signal_ids"), "signal_ids"),
        target_node_kinds=_ids_from_payload(
            payload.get("target_node_kinds"), "target_node_kinds"
        ),
        structural_identity=_require_text(
            payload.get("structural_identity"), "structural_identity"
        ),
        novelty_basis=novelty,
        model_claimed_novelty=(
            _require_text(claimed, "model_claimed_novelty") if claimed is not None else None
        ),
        strategy_version=_require_text(
            payload.get("strategy_version") or EXPLORATORY_HYPOTHESIS_STRATEGY_VERSION,
            "strategy_version",
        ),
        status=_require_text(payload.get("status"), "status"),
        validation_gates=_ids_from_payload(payload.get("validation_gates"), "validation_gates"),
        registry_external=bool(payload.get("registry_external")),
        requires_human_family_approval=bool(payload.get("requires_human_family_approval")),
        may_write_hunter_registry=bool(payload.get("may_write_hunter_registry")),
        not_evidence=bool(payload.get("not_evidence")),
        not_candidate=bool(payload.get("not_candidate")),
        not_finding=bool(payload.get("not_finding")),
        not_impact_graph_edge=bool(payload.get("not_impact_graph_edge")),
        false_finding_required_zero=bool(payload.get("false_finding_required_zero")),
    )


def _ids_from_payload(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return _require_ids(value, field_name)
    if isinstance(value, list):
        return _require_ids(tuple(value), field_name)
    raise ResearchInputError(f"{field_name} must be a non-empty tuple")


def draft_registry_external_hypothesis(
    *,
    draft_id: str,
    research_run_id: str,
    proposed_family_name: str,
    proposed_family_rationale: str,
    signals: tuple[ExploratorySignal, ...],
    registry: tuple[HunterFamilyView, ...],
    model_claimed_novelty: str | None = None,
) -> ExploratoryHypothesisDraft:
    """Create a sourced draft only when it is outside the enabled registry."""

    run_id = _require_text(research_run_id, "research_run_id")
    if not signals:
        raise ResearchInputError("at least one exploratory signal is required")
    for signal in signals:
        if signal.research_run_id != run_id:
            raise ResearchInputError("exploratory signal is cross-run")
    family_name = _require_text(proposed_family_name, "proposed_family_name")
    _reject_registered_family_overlap(family_name, signals, registry)

    source_refs = tuple(
        dict.fromkeys(ref for signal in signals for ref in signal.source_refs)
    )
    signal_ids = tuple(sorted(signal.signal_id for signal in signals))
    target_node_kinds = tuple(sorted({signal.target_node_kind for signal in signals}))
    structural_identity = exploratory_structural_identity(
        proposed_family_name=family_name,
        source_refs=source_refs,
        signal_ids=signal_ids,
        target_node_kinds=target_node_kinds,
    )
    claim = (
        f"Explore whether {family_name} describes repeatable target-specific behavior "
        f"on {', '.join(target_node_kinds)} surfaces before any registry admission."
    )
    return ExploratoryHypothesisDraft(
        draft_id=draft_id,
        research_run_id=run_id,
        hypothesis_claim=claim,
        proposed_family_name=family_name,
        proposed_family_rationale=proposed_family_rationale,
        source_refs=source_refs,
        signal_ids=signal_ids,
        target_node_kinds=target_node_kinds,
        structural_identity=structural_identity,
        novelty_basis=NoveltyBasis.UNCLASSIFIED,
        model_claimed_novelty=model_claimed_novelty,
    )


def exploratory_structural_identity(
    *,
    proposed_family_name: str,
    source_refs: tuple[str, ...],
    signal_ids: tuple[str, ...],
    target_node_kinds: tuple[str, ...],
) -> str:
    payload = {
        "proposed_family_name": proposed_family_name.strip().lower(),
        "source_refs": sorted(source_refs),
        "signal_ids": sorted(signal_ids),
        "target_node_kinds": sorted(target_node_kinds),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reject_registered_family_overlap(
    proposed_family_name: str,
    signals: tuple[ExploratorySignal, ...],
    registry: tuple[HunterFamilyView, ...],
) -> None:
    proposed = proposed_family_name.strip().lower()
    enabled = [family for family in registry if family.enabled]
    registered_tokens = {
        family.family_id.strip().lower()
        for family in enabled
    } | {
        family.name.strip().lower()
        for family in enabled
    }
    if proposed in registered_tokens:
        raise ResearchInputError("exploratory family draft already exists in registry")
    matched_family_ids = {
        value.strip().lower()
        for signal in signals
        for key, value in signal.attributes.items()
        if key == "matched_family_id" and isinstance(value, str) and value.strip()
    }
    if matched_family_ids.intersection(registered_tokens):
        raise ResearchInputError("exploratory signal is already covered by registry")


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    try:
        cleaned = reject_secret_keys(payload, field_name)
    except SecretMaterialError as exc:
        raise ResearchInputError(str(exc)) from exc
    found = FORBIDDEN_EXPLORATORY_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    for key, value in cleaned.items():
        _require_text(str(key), f"{field_name} key")
        if isinstance(value, str):
            _reject_truth_claims(value, f"{field_name}.{key}")
    return dict(cleaned)


def _reject_truth_claims(value: str, field_name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in FORBIDDEN_TRUTH_MARKERS):
        raise ResearchInputError(f"{field_name} must not claim vulnerability truth")


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ResearchInputError(f"{field_name} must be a non-empty tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()
