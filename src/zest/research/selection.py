"""Deterministic multi-hypothesis experiment selection. Not authorization. Not a Finding.

No numeric confidence, weighted score, or vulnerability probability.
Research may rank ExperimentOptions. Core still authorizes execution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.assessment import AssessmentOutcome
from zest.research.exploration import (
    FORBIDDEN_OPPORTUNITY_KEYS,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    SelectionOutcome,
)
from zest.research.compiler import ExperimentIntent, compile_experiment_intent
from zest.research.planning import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM,
    HTTP_STATE_TRANSITION_CLAIM,
)
from zest.research.types import ExperimentPlan, ResearchInputError

# Imported lazily inside families_for_node to avoid cycles at module load.
# from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.tools.capabilities import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
)
from zest.tools.registry import WORKER_EXECUTOR_CLASS, load_capability_registry

RESEARCH_SELECTION_STRATEGY_VERSION = "research.selection.v1"
HUNTER_FAMILY_REGISTRY_VERSION = "hunter_family.registry.v1"
OBJECT_OBSERVATION_KIND = "HTTP_AUTHORIZATION_DIFFERENTIAL"
WORKFLOW_OBSERVATION_KIND = "HTTP_STATE_TRANSITION_AUTHORIZATION"
FORBIDDEN_OPTION_KEYS = FORBIDDEN_OPPORTUNITY_KEYS | frozenset(
    {
        "expected_vulnerable",
        "ground_truth",
        "correct_answer",
        "scenario_expected_class",
        "scenario_id",
        "fixture_kind",
        "expected_class",
        "leakage_canary",
        "R01",
        "R02",
        "R03",
    }
)
LIVE_LIFECYCLES = frozenset(
    {"ACTIVE", "SUPPORTED", "NEEDS_MORE_CONTEXT"}
)
OBJECT_HYPOTHESIS_ORIGIN = "human-seed-object-authorization"
WORKFLOW_HYPOTHESIS_ORIGIN = "human-seed-workflow-authorization"


class HypothesisFamily(Enum):
    OBJECT_AUTHORIZATION = "OBJECT_AUTHORIZATION"
    WORKFLOW_STATE_TRANSITION = "WORKFLOW_STATE_TRANSITION"
    EXPOSED_API_SPEC = "EXPOSED_API_SPEC"
    UNPROTECTED_HOSTNAME = "UNPROTECTED_HOSTNAME"
    TECH_KNOWN_CVE_SURFACE = "TECH_KNOWN_CVE_SURFACE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class HunterFamilyView:
    """Read-only registry view used by the research-layer resolver.

    The authoritative append-only rows live in the data layer; this is a
    language-neutral projection passed into research selection.
    """

    family_id: str
    name: str
    target_node_kinds: tuple[str, ...]
    preconditions: Mapping[str, Any]
    claim_template: str
    evidence_requirements: Mapping[str, Any]
    validation_tier: str
    enabled: bool
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_node_kinds", tuple(self.target_node_kinds))
        object.__setattr__(self, "preconditions", dict(self.preconditions))
        object.__setattr__(self, "evidence_requirements", dict(self.evidence_requirements))


class HypothesisLifecycle(Enum):
    """Derived from the latest append-only assessment. Not a confidence score."""

    ACTIVE = "ACTIVE"
    SUPPORTED = "SUPPORTED"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"
    DEPRIORITIZED = "DEPRIORITIZED"
    FALSIFIED = "FALSIFIED"


class ExperimentPurpose(Enum):
    OBJECT_CROSS_PROBE = "OBJECT_CROSS_PROBE"
    OBJECT_CONTROL_PROBE = "OBJECT_CONTROL_PROBE"
    OBJECT_INDEPENDENT_REPRODUCTION = "OBJECT_INDEPENDENT_REPRODUCTION"
    WORKFLOW_TRANSITION_PROBE = "WORKFLOW_TRANSITION_PROBE"
    WORKFLOW_CONTROL_PROBE = "WORKFLOW_CONTROL_PROBE"
    WORKFLOW_INDEPENDENT_REPRODUCTION = "WORKFLOW_INDEPENDENT_REPRODUCTION"


class DiscriminationLevel(Enum):
    HIGH_DISCRIMINATION = "HIGH_DISCRIMINATION"
    MEDIUM_DISCRIMINATION = "MEDIUM_DISCRIMINATION"
    LOW_DISCRIMINATION = "LOW_DISCRIMINATION"


class ResearchStopReason(Enum):
    NO_ACTIVE_HYPOTHESES = "NO_ACTIVE_HYPOTHESES"
    NO_AUTHORIZED_DISCRIMINATING_EXPERIMENT = "NO_AUTHORIZED_DISCRIMINATING_EXPERIMENT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SUFFICIENT_EVIDENCE_FOR_VERIFICATION = "SUFFICIENT_EVIDENCE_FOR_VERIFICATION"
    ALL_HYPOTHESES_FALSIFIED = "ALL_HYPOTHESES_FALSIFIED"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"
    OPERATIONAL_INCONCLUSIVE = "OPERATIONAL_INCONCLUSIVE"
    MAX_CYCLES_REACHED = "MAX_CYCLES_REACHED"
    OPERATOR_PAUSED = "OPERATOR_PAUSED"
    CORE_BLOCKED = "CORE_BLOCKED"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchInputError(f"{field_name} must be a tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_OPTION_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


def family_for_claim(claim: str) -> HypothesisFamily:
    text = _require_text(claim, "claim")
    if text == HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM:
        return HypothesisFamily.OBJECT_AUTHORIZATION
    if text == HTTP_STATE_TRANSITION_CLAIM:
        return HypothesisFamily.WORKFLOW_STATE_TRANSITION
    return HypothesisFamily.UNKNOWN


def families_for_node(
    node: "AttackSurfaceNode",
    graph: "AttackSurfaceGraph",
    registry: tuple[HunterFamilyView, ...],
) -> tuple[HunterFamilyView, ...]:
    """Return enabled families whose preconditions match the node and graph.

    Empty result is normal; it never falls back to UNKNOWN spam.
    """
    # Lazy import keeps the module DAG acyclic.
    from zest.research.discovery.graph import AttackSurfaceNode

    if not isinstance(node, AttackSurfaceNode):
        raise ResearchInputError("node must be an AttackSurfaceNode")
    matched: list[HunterFamilyView] = []
    node_kind_value = node.kind.value
    scope_value = node.scope_classification.value
    for family in registry:
        if not family.enabled:
            continue
        if node_kind_value not in family.target_node_kinds:
            continue
        preconditions = family.preconditions
        required_scope = preconditions.get("scope_classification")
        if required_scope is not None and required_scope != scope_value:
            continue
        absent_edge_kind = preconditions.get("absent_edge_kind")
        if absent_edge_kind is not None and _node_has_edge_kind(node, graph, absent_edge_kind):
            continue
        required_edge_kind = preconditions.get("required_edge_kind")
        if required_edge_kind is not None and not _node_has_edge_kind(node, graph, required_edge_kind):
            continue
        required_attribute_any = preconditions.get("required_attribute_any")
        if required_attribute_any is not None and not _node_has_required_attribute_any(
            node, required_attribute_any
        ):
            continue
        matched.append(family)
    return tuple(matched)


def _node_has_edge_kind(
    node: "AttackSurfaceNode", graph: "AttackSurfaceGraph", edge_kind_value: str
) -> bool:
    """True if any edge touching the node has the given kind value."""
    for edge in graph.edges:
        if edge.kind.value != edge_kind_value:
            continue
        if edge.from_node_id == node.node_id or edge.to_node_id == node.node_id:
            return True
    return False


def _node_has_required_attribute_any(
    node: "AttackSurfaceNode", required_attribute_any: object
) -> bool:
    """Require at least one allowed value for every named node attribute."""
    if not isinstance(required_attribute_any, Mapping) or not required_attribute_any:
        return False
    attributes = node.attributes or {}
    for key, allowed in required_attribute_any.items():
        if not isinstance(key, str) or not key.strip():
            return False
        allowed_values = _string_set(allowed)
        if not allowed_values:
            return False
        node_values = _string_set(attributes.get(key))
        if not node_values.intersection(allowed_values):
            return False
    return True


def _string_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, frozenset, set)):
        return {str(item) for item in value if str(item).strip()}
    return {str(value)}


def claim_from_template(
    node: "AttackSurfaceNode",
    family: HunterFamilyView,
    *,
    extra_context: Mapping[str, str] | None = None,
) -> str:
    """Deterministic claim from family template and node attributes.

    Claim text intentionally contains no severity/confidence/finding language.
    Optional extra_context allows callers (e.g., SD-G9 identity binding) to
    inject additional template placeholders without mutating the node.
    """
    attributes = dict(node.attributes or {})
    attributes["canonical_key"] = node.canonical_key
    if extra_context is not None:
        attributes.update(extra_context)
    try:
        return family.claim_template.format(**attributes)
    except KeyError as exc:
        raise ResearchInputError(
            f"claim template for {family.family_id} missing placeholder {exc}"
        ) from exc


def lifecycle_from_assessments(
    outcomes: tuple[str, ...],
    *,
    competing_supported: bool,
    remaining_untested_context: bool = False,
) -> HypothesisLifecycle:
    """Latest assessment wins. Historical rows remain immutable."""

    if not outcomes:
        return HypothesisLifecycle.ACTIVE
    latest = outcomes[-1]
    if latest == AssessmentOutcome.CONTRADICTS_PREDICTION.value:
        if remaining_untested_context:
            return HypothesisLifecycle.ACTIVE
        return HypothesisLifecycle.FALSIFIED
    if latest == AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value:
        return HypothesisLifecycle.SUPPORTED
    if latest == AssessmentOutcome.NEEDS_MORE_CONTEXT.value:
        return HypothesisLifecycle.NEEDS_MORE_CONTEXT
    if latest == AssessmentOutcome.EXECUTION_UNUSABLE.value:
        return HypothesisLifecycle.NEEDS_MORE_CONTEXT
    if latest == AssessmentOutcome.INCONCLUSIVE.value:
        if competing_supported:
            return HypothesisLifecycle.DEPRIORITIZED
        return HypothesisLifecycle.NEEDS_MORE_CONTEXT
    return HypothesisLifecycle.ACTIVE


@dataclass(frozen=True)
class ObjectProbeContext:
    actor: str
    own_object: str
    cross_object: str
    verification_actor: str | None = None
    verification_own_object: str | None = None
    verification_cross_object: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor", _require_text(self.actor, "actor"))
        object.__setattr__(self, "own_object", _require_text(self.own_object, "own_object"))
        object.__setattr__(
            self, "cross_object", _require_text(self.cross_object, "cross_object")
        )


@dataclass(frozen=True)
class WorkflowProbeContext:
    actor: str
    resource_id: str
    transition: str = "approve"
    verification_actor: str | None = None
    verification_resource_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor", _require_text(self.actor, "actor"))
        object.__setattr__(
            self, "resource_id", _require_text(self.resource_id, "resource_id")
        )
        object.__setattr__(
            self, "transition", _require_text(self.transition, "transition")
        )


def object_origin_reference(context: ObjectProbeContext) -> str:
    return (
        f"{OBJECT_HYPOTHESIS_ORIGIN}:{context.actor}:"
        f"{context.own_object}:{context.cross_object}"
    )


def workflow_origin_reference(context: WorkflowProbeContext) -> str:
    return (
        f"{WORKFLOW_HYPOTHESIS_ORIGIN}:{context.actor}:"
        f"{context.resource_id}:{context.transition}"
    )


def origin_binds_object_context(origin_reference: str, context: ObjectProbeContext) -> bool:
    if not origin_reference or origin_reference == OBJECT_HYPOTHESIS_ORIGIN:
        return True
    return origin_reference == object_origin_reference(context)


def origin_binds_workflow_context(
    origin_reference: str, context: WorkflowProbeContext
) -> bool:
    if not origin_reference or origin_reference == WORKFLOW_HYPOTHESIS_ORIGIN:
        return True
    return origin_reference == workflow_origin_reference(context)


@dataclass(frozen=True)
class ObservedResearchFact:
    observation_id: str
    observation_kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observation_id", _require_text(self.observation_id, "observation_id")
        )
        object.__setattr__(
            self,
            "observation_kind",
            _require_text(self.observation_kind, "observation_kind"),
        )
        object.__setattr__(self, "payload", _reject_forbidden(self.payload, "payload"))


@dataclass(frozen=True)
class PortfolioHypothesis:
    hypothesis_id: str
    claim: str
    family: HypothesisFamily
    lifecycle: HypothesisLifecycle
    observation_ids: tuple[str, ...]
    assessment_outcomes: tuple[str, ...]
    origin_reference: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(self, "claim", _require_text(self.claim, "claim"))
        if not isinstance(self.family, HypothesisFamily):
            raise ResearchInputError("family must be a HypothesisFamily")
        if not isinstance(self.lifecycle, HypothesisLifecycle):
            raise ResearchInputError("lifecycle must be a HypothesisLifecycle")
        object.__setattr__(
            self, "observation_ids", _require_ids(self.observation_ids, "observation_ids")
            if self.observation_ids
            else ()
        )
        object.__setattr__(self, "assessment_outcomes", tuple(self.assessment_outcomes))
        if self.origin_reference:
            object.__setattr__(
                self,
                "origin_reference",
                _require_text(self.origin_reference, "origin_reference"),
            )


@dataclass(frozen=True)
class ResearchPortfolio:
    hypotheses: tuple[PortfolioHypothesis, ...]

    def by_family(self, family: HypothesisFamily) -> tuple[PortfolioHypothesis, ...]:
        return tuple(item for item in self.hypotheses if item.family is family)

    def live(self) -> tuple[PortfolioHypothesis, ...]:
        return tuple(item for item in self.hypotheses if item.lifecycle.value in LIVE_LIFECYCLES)

    def open_investigation(self) -> tuple[PortfolioHypothesis, ...]:
        return tuple(
            item
            for item in self.hypotheses
            if item.lifecycle
            in {HypothesisLifecycle.ACTIVE, HypothesisLifecycle.NEEDS_MORE_CONTEXT}
        )


def build_portfolio(
    *,
    hypotheses: tuple[tuple[str, str], ...],
    assessments_by_hypothesis: Mapping[str, tuple[str, ...]],
    observation_ids_by_hypothesis: Mapping[str, tuple[str, ...]],
    remaining_untested_by_hypothesis: Mapping[str, bool] | None = None,
    origin_reference_by_hypothesis: Mapping[str, str] | None = None,
) -> ResearchPortfolio:
    families = tuple(family_for_claim(claim) for _hypothesis_id, claim in hypotheses)
    supported_families = set()
    origin_refs = origin_reference_by_hypothesis or {}
    for (hypothesis_id, claim), family in zip(hypotheses, families):
        outcomes = assessments_by_hypothesis.get(hypothesis_id, ())
        if outcomes and outcomes[-1] == AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value:
            supported_families.add(family)
    items: list[PortfolioHypothesis] = []
    for (hypothesis_id, claim), family in zip(hypotheses, families):
        outcomes = assessments_by_hypothesis.get(hypothesis_id, ())
        competing = any(
            other is not family and other in supported_families for other in supported_families
        )
        remaining = bool(
            (remaining_untested_by_hypothesis or {}).get(hypothesis_id, False)
        )
        items.append(
            PortfolioHypothesis(
                hypothesis_id=hypothesis_id,
                claim=claim,
                family=family,
                lifecycle=lifecycle_from_assessments(
                    outcomes,
                    competing_supported=competing,
                    remaining_untested_context=remaining,
                ),
                observation_ids=observation_ids_by_hypothesis.get(hypothesis_id, ()),
                assessment_outcomes=outcomes,
                origin_reference=origin_refs.get(hypothesis_id, ""),
            )
        )
    return ResearchPortfolio(hypotheses=tuple(items))


@dataclass(frozen=True)
class ExperimentOption:
    """Candidate next experiment. Not Core ALLOW and not hidden ground truth."""

    option_id: str
    hypothesis_id: str
    hypothesis_ids: tuple[str, ...]
    purpose: ExperimentPurpose
    required_capability: str
    requested_observation: str
    expected_supporting_observation: str
    expected_disconfirming_observation: str
    required_negative_control: str
    unresolved_facts: tuple[str, ...]
    estimated_request_cost: int
    side_effect_level: int
    can_falsify_live: bool
    distinguishes_competing_count: int
    resolves_missing_fact: bool
    provides_missing_negative_control: bool
    authorized_origin: str
    target_reference: str
    in_authorized_origin: bool
    context_signature: str
    structural_identity: str
    plan_arguments: Mapping[str, Any]
    discrimination: DiscriminationLevel
    observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "option_id", _require_text(self.option_id, "option_id"))
        object.__setattr__(
            self, "hypothesis_id", _require_text(self.hypothesis_id, "hypothesis_id")
        )
        object.__setattr__(
            self, "hypothesis_ids", _require_ids(self.hypothesis_ids, "hypothesis_ids")
        )
        if not isinstance(self.purpose, ExperimentPurpose):
            raise ResearchInputError("purpose must be an ExperimentPurpose")
        object.__setattr__(
            self,
            "required_capability",
            _require_text(self.required_capability, "required_capability"),
        )
        for name in (
            "requested_observation",
            "expected_supporting_observation",
            "expected_disconfirming_observation",
            "required_negative_control",
            "authorized_origin",
            "target_reference",
            "context_signature",
            "structural_identity",
        ):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        object.__setattr__(
            self, "unresolved_facts", tuple(self.unresolved_facts)
        )
        if (
            not isinstance(self.estimated_request_cost, int)
            or isinstance(self.estimated_request_cost, bool)
            or self.estimated_request_cost < 1
        ):
            raise ResearchInputError("estimated_request_cost must be >= 1")
        if self.side_effect_level not in (0, 1, 2, 3):
            raise ResearchInputError("side_effect_level must be 0, 1, 2, or 3")
        if not isinstance(self.discrimination, DiscriminationLevel):
            raise ResearchInputError("discrimination must be a DiscriminationLevel")
        object.__setattr__(
            self, "plan_arguments", _reject_forbidden(self.plan_arguments, "plan_arguments")
        )
        object.__setattr__(
            self,
            "observation_ids",
            _require_ids(self.observation_ids, "observation_ids")
            if self.observation_ids
            else (),
        )
        lowered = " ".join(
            [
                self.requested_observation,
                self.expected_supporting_observation,
                self.expected_disconfirming_observation,
            ]
        ).lower()
        if "scenario_id" in lowered or "leakage_canary" in lowered:
            raise ResearchInputError("experiment option must not carry evaluation labels")

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_ids": list(self.hypothesis_ids),
            "purpose": self.purpose.value,
            "required_capability": self.required_capability,
            "requested_observation": self.requested_observation,
            "unresolved_facts": list(self.unresolved_facts),
            "estimated_request_cost": self.estimated_request_cost,
            "side_effect_level": self.side_effect_level,
            "can_falsify_live": self.can_falsify_live,
            "distinguishes_competing_count": self.distinguishes_competing_count,
            "resolves_missing_fact": self.resolves_missing_fact,
            "provides_missing_negative_control": self.provides_missing_negative_control,
            "in_authorized_origin": self.in_authorized_origin,
            "context_signature": self.context_signature,
            "structural_identity": self.structural_identity,
            "discrimination": self.discrimination.value,
            "observation_ids": list(self.observation_ids),
            "not_a_priority_score": True,
            "not_confidence": True,
        }


def experiment_option_identity(
    *,
    capability: str,
    purpose: ExperimentPurpose,
    origin: str,
    actor: str,
    resource: str,
    operation: str,
) -> str:
    payload = {
        "capability": capability,
        "purpose": purpose.value,
        "origin": origin,
        "actor": actor,
        "resource": resource,
        "operation": operation,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def context_signature_for(*, family: str, origin: str, resource: str) -> str:
    payload = {"family": family, "origin": origin, "resource": resource}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def discrimination_for(
    *,
    can_falsify_live: bool,
    distinguishes_competing_count: int,
    resolves_missing_fact: bool,
    provides_missing_negative_control: bool,
) -> DiscriminationLevel:
    if distinguishes_competing_count >= 2 and (can_falsify_live or resolves_missing_fact):
        return DiscriminationLevel.HIGH_DISCRIMINATION
    if can_falsify_live and (resolves_missing_fact or provides_missing_negative_control):
        return DiscriminationLevel.HIGH_DISCRIMINATION
    if can_falsify_live or resolves_missing_fact or provides_missing_negative_control:
        return DiscriminationLevel.MEDIUM_DISCRIMINATION
    return DiscriminationLevel.LOW_DISCRIMINATION


def selector_key(option: ExperimentOption) -> tuple[object, ...]:
    """Lexicographic rank. Lower is better. Not a weighted score."""

    return (
        0 if option.in_authorized_origin else 1,
        0 if option.can_falsify_live else 1,
        -option.distinguishes_competing_count,
        0 if option.resolves_missing_fact else 1,
        0 if option.provides_missing_negative_control else 1,
        option.side_effect_level,
        option.estimated_request_cost,
        0 if option.discrimination is DiscriminationLevel.HIGH_DISCRIMINATION else 1,
        0 if option.discrimination is DiscriminationLevel.MEDIUM_DISCRIMINATION else 1,
        option.purpose.value,
        option.required_capability,
        option.context_signature,
        option.structural_identity,
    )


@dataclass(frozen=True)
class ExperimentSelectionDecision:
    outcome: SelectionOutcome
    reason_codes: tuple[str, ...]
    option: ExperimentOption

    @property
    def selected(self) -> bool:
        return self.outcome is SelectionOutcome.SELECT


def select_next_experiment(
    options: tuple[ExperimentOption, ...],
    *,
    executed_identities: frozenset[str] = frozenset(),
    negative_context_signatures: frozenset[str] = frozenset(),
) -> tuple[ExperimentSelectionDecision, ...]:
    """Choose one next experiment. Does not authorize or dispatch."""

    ranked = sorted(options, key=selector_key)
    decisions: list[ExperimentSelectionDecision] = []
    selected_id: str | None = None
    for option in ranked:
        if not option.in_authorized_origin:
            decisions.append(
                ExperimentSelectionDecision(
                    SelectionOutcome.BLOCKED_POLICY,
                    ("UNAUTHORIZED_ORIGIN", "NOT_AUTHORIZATION"),
                    option,
                )
            )
            continue
        if option.structural_identity in executed_identities:
            decisions.append(
                ExperimentSelectionDecision(
                    SelectionOutcome.SKIP_DUPLICATE,
                    ("EQUIVALENT_EXPERIMENT_UNCHANGED_CONTEXT",),
                    option,
                )
            )
            continue
        if (
            option.context_signature in negative_context_signatures
            and option.purpose
            in {
                ExperimentPurpose.OBJECT_CROSS_PROBE,
                ExperimentPurpose.WORKFLOW_TRANSITION_PROBE,
            }
        ):
            decisions.append(
                ExperimentSelectionDecision(
                    SelectionOutcome.SKIP_LOW_INFORMATION,
                    ("NEGATIVE_KNOWLEDGE_SAME_CONTEXT",),
                    option,
                )
            )
            continue
        if selected_id is None:
            selected_id = option.option_id
            decisions.append(
                ExperimentSelectionDecision(
                    SelectionOutcome.SELECT,
                    (
                        "LEXICOGRAPHIC_SELECTION",
                        "FALSIFY_LIVE" if option.can_falsify_live else "NOT_FALSIFIER",
                        "DISTINGUISHES_COMPETING"
                        if option.distinguishes_competing_count > 1
                        else "SINGLE_HYPOTHESIS",
                        "RESOLVES_MISSING_FACT"
                        if option.resolves_missing_fact
                        else "NO_MISSING_FACT",
                        "NEGATIVE_CONTROL"
                        if option.provides_missing_negative_control
                        else "CONTROL_NOT_REQUIRED",
                        f"SIDE_EFFECT_{option.side_effect_level}",
                        f"COST_{option.estimated_request_cost}",
                        option.discrimination.value,
                        "NOT_AUTHORIZATION",
                    ),
                    option,
                )
            )
            continue
        decisions.append(
            ExperimentSelectionDecision(
                SelectionOutcome.DEFER,
                ("NOT_SELECTED_THIS_CYCLE",),
                option,
            )
        )
    return tuple(decisions)


def _object_state(
    observations: tuple[ObservedResearchFact, ...],
    context: ObjectProbeContext,
    origin: str,
) -> dict[str, Any]:
    matching = [
        item
        for item in sorted(observations, key=lambda row: row.observation_id)
        if item.observation_kind == OBJECT_OBSERVATION_KIND
        and str(item.payload.get("actor") or "") == context.actor
        and str(item.payload.get("own_object") or "") == context.own_object
        and str(item.payload.get("cross_object") or "") == context.cross_object
        and str(item.payload.get("authorized_origin") or origin) == origin
    ]
    if not matching:
        return {
            "observed": False,
            "owner_proven": False,
            "public": False,
            "delegated": False,
            "secure_denied": False,
            "status_only": False,
            "observation_ids": (),
            "unresolved": (
                "cross_object_owner",
                "secure_control",
                "unauthenticated_control",
            ),
        }
    payload = matching[-1].payload
    owner = payload.get("cross_object_request_object_owner")
    owner_proven = isinstance(owner, str) and owner == context.cross_object
    visibility = payload.get("cross_object_request_visibility")
    readers = payload.get("cross_object_request_authorized_readers")
    public = isinstance(visibility, str) and visibility.strip().upper() == "PUBLIC"
    delegated = isinstance(readers, list) and context.actor in readers
    secure_denied = payload.get("secure_control_status") == 403
    status_only = payload.get("cross_object_request_status") == 200 and not owner_proven
    unresolved: list[str] = []
    if not owner_proven:
        unresolved.append("cross_object_owner")
    if not secure_denied:
        unresolved.append("secure_control")
    return {
        "observed": True,
        "owner_proven": owner_proven,
        "public": public,
        "delegated": delegated,
        "secure_denied": secure_denied,
        "status_only": status_only,
        "observation_ids": tuple(item.observation_id for item in matching),
        "unresolved": tuple(unresolved),
    }


def _workflow_state(
    observations: tuple[ObservedResearchFact, ...],
    context: WorkflowProbeContext,
    origin: str,
) -> dict[str, Any]:
    matching = [
        item
        for item in sorted(observations, key=lambda row: row.observation_id)
        if item.observation_kind == WORKFLOW_OBSERVATION_KIND
        and str(item.payload.get("actor") or "") == context.actor
        and str(item.payload.get("resource_id") or "") == context.resource_id
        and str(item.payload.get("authorized_origin") or origin) == origin
    ]
    if not matching:
        return {
            "observed": False,
            "post_state": None,
            "state_changed": False,
            "control_denied": False,
            "status_only": False,
            "observation_ids": (),
            "unresolved": ("pre_state", "post_state", "control_path"),
        }
    payload = matching[-1].payload
    post_state = payload.get("post_state")
    state_changed = bool(payload.get("state_changed"))
    control_denied = payload.get("control_status") in {401, 403, 409}
    status_only = payload.get("response_status") == 200 and (
        not isinstance(post_state, str) or not state_changed
    )
    unresolved: list[str] = []
    if not isinstance(post_state, str):
        unresolved.append("post_state")
    if not control_denied:
        unresolved.append("control_path")
    return {
        "observed": True,
        "post_state": post_state,
        "state_changed": state_changed,
        "control_denied": control_denied,
        "status_only": status_only,
        "observation_ids": tuple(item.observation_id for item in matching),
        "unresolved": tuple(unresolved),
    }


def object_context_is_observed(
    observations: tuple[ObservedResearchFact, ...],
    context: ObjectProbeContext,
    origin: str,
) -> bool:
    return bool(_object_state(observations, context, origin)["observed"])


def workflow_context_is_observed(
    observations: tuple[ObservedResearchFact, ...],
    context: WorkflowProbeContext,
    origin: str,
) -> bool:
    return bool(_workflow_state(observations, context, origin)["observed"])


def identity_from_plan_arguments(
    *,
    capability: str,
    arguments: Mapping[str, Any],
    target_reference: str,
) -> str | None:
    if capability == HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY:
        mode = str(arguments.get("mode") or "vulnerable")
        actor = str(arguments.get("actor") or "")
        own_object = str(arguments.get("own_object") or "")
        cross_object = str(arguments.get("cross_object") or "")
        if mode == "secure_only":
            purpose = ExperimentPurpose.OBJECT_CONTROL_PROBE
        else:
            purpose = ExperimentPurpose.OBJECT_CROSS_PROBE
        return experiment_option_identity(
            capability=capability,
            purpose=purpose,
            origin=target_reference,
            actor=actor,
            resource=f"{own_object}:{cross_object}",
            operation=mode,
        )
    if capability == HTTP_STATE_TRANSITION_CAPABILITY:
        area = str(arguments.get("area") or "workflow")
        actor = str(arguments.get("actor") or "")
        resource_id = str(arguments.get("resource_id") or "")
        transition = str(arguments.get("transition") or "")
        purpose = (
            ExperimentPurpose.WORKFLOW_CONTROL_PROBE
            if area == "control"
            else ExperimentPurpose.WORKFLOW_TRANSITION_PROBE
        )
        return experiment_option_identity(
            capability=capability,
            purpose=purpose,
            origin=target_reference,
            actor=actor,
            resource=resource_id,
            operation=f"{area}:{transition}",
        )
    return None


def propose_experiment_options(
    *,
    portfolio: ResearchPortfolio,
    observations: tuple[ObservedResearchFact, ...],
    authorized_origin: str,
    candidate_origins: tuple[str, ...],
    object_contexts: tuple[ObjectProbeContext, ...],
    workflow_contexts: tuple[WorkflowProbeContext, ...],
    id_prefix: str,
) -> tuple[ExperimentOption, ...]:
    origin = _require_text(authorized_origin, "authorized_origin")
    live_families = {item.family for item in portfolio.live() if item.family is not HypothesisFamily.UNKNOWN}
    competing = len(live_families)
    options: list[ExperimentOption] = []
    counter = 0

    def _next_id() -> str:
        nonlocal counter
        counter += 1
        return f"{id_prefix}-{counter:02d}"

    for hypothesis in portfolio.hypotheses:
        if hypothesis.family is HypothesisFamily.OBJECT_AUTHORIZATION:
            if hypothesis.lifecycle is HypothesisLifecycle.DEPRIORITIZED:
                continue
            for context in object_contexts:
                if not origin_binds_object_context(hypothesis.origin_reference, context):
                    continue
                for target in candidate_origins or (origin,):
                    options.extend(
                        _object_options(
                            hypothesis=hypothesis,
                            context=context,
                            observations=observations,
                            authorized_origin=origin,
                            target_reference=target,
                            competing=competing,
                            next_id=_next_id,
                        )
                    )
        if hypothesis.family is HypothesisFamily.WORKFLOW_STATE_TRANSITION:
            if hypothesis.lifecycle is HypothesisLifecycle.DEPRIORITIZED:
                continue
            if hypothesis.lifecycle is HypothesisLifecycle.FALSIFIED:
                continue
            for context in workflow_contexts:
                if not origin_binds_workflow_context(hypothesis.origin_reference, context):
                    continue
                for target in candidate_origins or (origin,):
                    options.extend(
                        _workflow_options(
                            hypothesis=hypothesis,
                            context=context,
                            observations=observations,
                            authorized_origin=origin,
                            target_reference=target,
                            competing=competing,
                            next_id=_next_id,
                        )
                    )
    return tuple(options)


def _object_options(
    *,
    hypothesis: PortfolioHypothesis,
    context: ObjectProbeContext,
    observations: tuple[ObservedResearchFact, ...],
    authorized_origin: str,
    target_reference: str,
    competing: int,
    next_id,
) -> list[ExperimentOption]:
    state = _object_state(observations, context, authorized_origin)
    authorized = target_reference == authorized_origin
    resource = f"{context.own_object}:{context.cross_object}"
    signature = context_signature_for(
        family=HypothesisFamily.OBJECT_AUTHORIZATION.value,
        origin=target_reference,
        resource=resource,
    )
    open_investigation = hypothesis.lifecycle in {
        HypothesisLifecycle.ACTIVE,
        HypothesisLifecycle.NEEDS_MORE_CONTEXT,
    }
    items: list[ExperimentOption] = []
    if open_investigation:
        items.append(
            _make_object_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=context,
                purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
                mode="vulnerable",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=signature,
                resource=resource,
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=not state["observed"] or bool(state["unresolved"]),
                provides_missing_negative_control=False,
                unresolved=state["unresolved"] or ("object_authorization_semantics",),
                observation_ids=state["observation_ids"],
            )
        )
        items.append(
            _make_object_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=context,
                purpose=ExperimentPurpose.OBJECT_CONTROL_PROBE,
                mode="secure_only",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=signature,
                resource=resource,
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=False,
                provides_missing_negative_control=not state["secure_denied"],
                unresolved=("secure_control",)
                if not state["secure_denied"]
                else ("negative_control_already_present",),
                observation_ids=state["observation_ids"],
            )
        )
    if hypothesis.lifecycle is HypothesisLifecycle.SUPPORTED and context.verification_actor:
        items.append(
            _make_object_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=ObjectProbeContext(
                    actor=context.verification_actor,
                    own_object=context.verification_own_object or context.verification_actor,
                    cross_object=context.verification_cross_object or context.cross_object,
                ),
                purpose=ExperimentPurpose.OBJECT_INDEPENDENT_REPRODUCTION,
                mode="vulnerable",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=context_signature_for(
                    family=HypothesisFamily.OBJECT_AUTHORIZATION.value,
                    origin=target_reference,
                    resource=(
                        f"{context.verification_own_object or context.verification_actor}:"
                        f"{context.verification_cross_object or context.cross_object}"
                    ),
                ),
                resource=(
                    f"{context.verification_own_object or context.verification_actor}:"
                    f"{context.verification_cross_object or context.cross_object}"
                ),
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=False,
                provides_missing_negative_control=False,
                unresolved=("independent_reproduction",),
                observation_ids=state["observation_ids"],
            )
        )
    return items


def _workflow_options(
    *,
    hypothesis: PortfolioHypothesis,
    context: WorkflowProbeContext,
    observations: tuple[ObservedResearchFact, ...],
    authorized_origin: str,
    target_reference: str,
    competing: int,
    next_id,
) -> list[ExperimentOption]:
    state = _workflow_state(observations, context, authorized_origin)
    authorized = target_reference == authorized_origin
    signature = context_signature_for(
        family=HypothesisFamily.WORKFLOW_STATE_TRANSITION.value,
        origin=target_reference,
        resource=context.resource_id,
    )
    open_investigation = hypothesis.lifecycle in {
        HypothesisLifecycle.ACTIVE,
        HypothesisLifecycle.NEEDS_MORE_CONTEXT,
    }
    items: list[ExperimentOption] = []
    if open_investigation:
        items.append(
            _make_workflow_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=context,
                purpose=ExperimentPurpose.WORKFLOW_TRANSITION_PROBE,
                area="workflow",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=signature,
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=not state["observed"] or bool(state["unresolved"]),
                provides_missing_negative_control=False,
                unresolved=state["unresolved"] or ("workflow_authorization_semantics",),
                observation_ids=state["observation_ids"],
            )
        )
        items.append(
            _make_workflow_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=context,
                purpose=ExperimentPurpose.WORKFLOW_CONTROL_PROBE,
                area="control",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=signature,
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=False,
                provides_missing_negative_control=not state["control_denied"],
                unresolved=("control_path",)
                if not state["control_denied"]
                else ("negative_control_already_present",),
                observation_ids=state["observation_ids"],
            )
        )
    if hypothesis.lifecycle is HypothesisLifecycle.SUPPORTED and context.verification_actor:
        items.append(
            _make_workflow_option(
                option_id=next_id(),
                hypothesis=hypothesis,
                context=WorkflowProbeContext(
                    actor=context.verification_actor,
                    resource_id=context.verification_resource_id or context.resource_id,
                    transition=context.transition,
                ),
                purpose=ExperimentPurpose.WORKFLOW_INDEPENDENT_REPRODUCTION,
                area="workflow",
                authorized_origin=authorized_origin,
                target_reference=target_reference,
                in_authorized_origin=authorized,
                context_signature=context_signature_for(
                    family=HypothesisFamily.WORKFLOW_STATE_TRANSITION.value,
                    origin=target_reference,
                    resource=context.verification_resource_id or context.resource_id,
                ),
                competing=competing,
                can_falsify_live=True,
                resolves_missing_fact=False,
                provides_missing_negative_control=False,
                unresolved=("independent_reproduction",),
                observation_ids=state["observation_ids"],
            )
        )
    return items


def _make_object_option(
    *,
    option_id: str,
    hypothesis: PortfolioHypothesis,
    context: ObjectProbeContext,
    purpose: ExperimentPurpose,
    mode: str,
    authorized_origin: str,
    target_reference: str,
    in_authorized_origin: bool,
    context_signature: str,
    resource: str,
    competing: int,
    can_falsify_live: bool,
    resolves_missing_fact: bool,
    provides_missing_negative_control: bool,
    unresolved: tuple[str, ...],
    observation_ids: tuple[str, ...],
) -> ExperimentOption:
    identity = experiment_option_identity(
        capability=HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
        purpose=(
            ExperimentPurpose.OBJECT_CONTROL_PROBE
            if mode == "secure_only"
            else ExperimentPurpose.OBJECT_CROSS_PROBE
        ),
        origin=target_reference,
        actor=context.actor,
        resource=resource,
        operation=mode,
    )
    return ExperimentOption(
        option_id=option_id,
        hypothesis_id=hypothesis.hypothesis_id,
        hypothesis_ids=(hypothesis.hypothesis_id,),
        purpose=purpose,
        required_capability=HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
        requested_observation="object authorization differential including owner and controls",
        expected_supporting_observation=(
            "cross-object read returns the foreign owner's object while the secure control denies"
        ),
        expected_disconfirming_observation=(
            "cross-object access is denied, public, delegated, or lacks foreign owner proof"
        ),
        required_negative_control="secure_only object control",
        unresolved_facts=unresolved,
        estimated_request_cost=4,
        side_effect_level=0,
        can_falsify_live=can_falsify_live,
        distinguishes_competing_count=competing,
        resolves_missing_fact=resolves_missing_fact,
        provides_missing_negative_control=provides_missing_negative_control,
        authorized_origin=authorized_origin,
        target_reference=target_reference,
        in_authorized_origin=in_authorized_origin,
        context_signature=context_signature,
        structural_identity=identity,
        plan_arguments={
            "authorized_origin": authorized_origin,
            "actor": context.actor,
            "own_object": context.own_object,
            "cross_object": context.cross_object,
            "mode": mode,
        },
        discrimination=discrimination_for(
            can_falsify_live=can_falsify_live,
            distinguishes_competing_count=competing,
            resolves_missing_fact=resolves_missing_fact,
            provides_missing_negative_control=provides_missing_negative_control,
        ),
        observation_ids=observation_ids,
    )


def _make_workflow_option(
    *,
    option_id: str,
    hypothesis: PortfolioHypothesis,
    context: WorkflowProbeContext,
    purpose: ExperimentPurpose,
    area: str,
    authorized_origin: str,
    target_reference: str,
    in_authorized_origin: bool,
    context_signature: str,
    competing: int,
    can_falsify_live: bool,
    resolves_missing_fact: bool,
    provides_missing_negative_control: bool,
    unresolved: tuple[str, ...],
    observation_ids: tuple[str, ...],
) -> ExperimentOption:
    identity = experiment_option_identity(
        capability=HTTP_STATE_TRANSITION_CAPABILITY,
        purpose=(
            ExperimentPurpose.WORKFLOW_CONTROL_PROBE
            if area == "control"
            else ExperimentPurpose.WORKFLOW_TRANSITION_PROBE
        ),
        origin=target_reference,
        actor=context.actor,
        resource=context.resource_id,
        operation=f"{area}:{context.transition}",
    )
    return ExperimentOption(
        option_id=option_id,
        hypothesis_id=hypothesis.hypothesis_id,
        hypothesis_ids=(hypothesis.hypothesis_id,),
        purpose=purpose,
        required_capability=HTTP_STATE_TRANSITION_CAPABILITY,
        requested_observation="authoritative pre/post workflow state and control denial",
        expected_supporting_observation=(
            "unauthorized actor changes authoritative workflow state while the control path denies"
        ),
        expected_disconfirming_observation=(
            "transition is denied or authoritative post-state is unchanged"
        ),
        required_negative_control="control-area workflow denial",
        unresolved_facts=unresolved,
        estimated_request_cost=4,
        side_effect_level=1,
        can_falsify_live=can_falsify_live,
        distinguishes_competing_count=competing,
        resolves_missing_fact=resolves_missing_fact,
        provides_missing_negative_control=provides_missing_negative_control,
        authorized_origin=authorized_origin,
        target_reference=target_reference,
        in_authorized_origin=in_authorized_origin,
        context_signature=context_signature,
        structural_identity=identity,
        plan_arguments={
            "authorized_origin": authorized_origin,
            "actor": context.actor,
            "resource_id": context.resource_id,
            "transition": context.transition,
            "area": area,
        },
        discrimination=discrimination_for(
            can_falsify_live=can_falsify_live,
            distinguishes_competing_count=competing,
            resolves_missing_fact=resolves_missing_fact,
            provides_missing_negative_control=provides_missing_negative_control,
        ),
        observation_ids=observation_ids,
    )


def plan_from_option(option: ExperimentOption, *, budget_id: str) -> ExperimentPlan:
    registry = load_capability_registry()
    definition = registry.get(option.required_capability)
    if definition is None or definition.executor_class != WORKER_EXECUTOR_CLASS:
        raise ResearchInputError("unknown or unsupported capability cannot be planned")
    if len(definition.actions) != 1:
        raise ResearchInputError("capability action is ambiguous")
    action_id = next(iter(definition.actions))
    return compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id=option.hypothesis_id,
            capability_id=option.required_capability,
            action=action_id,
            target_reference=option.target_reference,
            arguments=dict(option.plan_arguments),
            requested_budget_id=budget_id,
            expected_observation=option.expected_supporting_observation,
            disconfirming_observation=option.expected_disconfirming_observation,
            evaluation_strategy=f"{option.required_capability}.v1",
            requested_side_effect=option.side_effect_level,
        )
    )


def opportunity_kind_for(purpose: ExperimentPurpose) -> OpportunityKind:
    if purpose in {
        ExperimentPurpose.OBJECT_CONTROL_PROBE,
        ExperimentPurpose.WORKFLOW_CONTROL_PROBE,
    }:
        return OpportunityKind.CONTROL_EXPERIMENT
    if purpose in {
        ExperimentPurpose.OBJECT_INDEPENDENT_REPRODUCTION,
        ExperimentPurpose.WORKFLOW_INDEPENDENT_REPRODUCTION,
    }:
        return OpportunityKind.INVARIANT_CHALLENGE
    return OpportunityKind.HYPOTHESIS_FOLLOWUP


def opportunity_mode_for(purpose: ExperimentPurpose) -> OpportunityMode:
    if purpose in {
        ExperimentPurpose.OBJECT_CROSS_PROBE,
        ExperimentPurpose.WORKFLOW_TRANSITION_PROBE,
    }:
        return OpportunityMode.EXPLORATION
    return OpportunityMode.EXPLOITATION


def stop_reason_for_portfolio(
    portfolio: ResearchPortfolio,
    *,
    selected: ExperimentOption | None,
    budget_exhausted: bool,
    max_cycles_reached: bool,
    operational: bool,
) -> ResearchStopReason | None:
    if operational:
        return ResearchStopReason.OPERATIONAL_INCONCLUSIVE
    if budget_exhausted:
        return ResearchStopReason.BUDGET_EXHAUSTED
    if max_cycles_reached:
        return ResearchStopReason.MAX_CYCLES_REACHED
    if selected is not None:
        return None
    open_items = portfolio.open_investigation()
    supported = tuple(
        item for item in portfolio.hypotheses if item.lifecycle is HypothesisLifecycle.SUPPORTED
    )
    falsified = tuple(
        item for item in portfolio.hypotheses if item.lifecycle is HypothesisLifecycle.FALSIFIED
    )
    if open_items:
        return ResearchStopReason.NEEDS_MORE_CONTEXT
    if supported:
        return ResearchStopReason.SUFFICIENT_EVIDENCE_FOR_VERIFICATION
    if falsified and not open_items and not supported:
        if len(falsified) == len(portfolio.hypotheses):
            return ResearchStopReason.ALL_HYPOTHESES_FALSIFIED
        return ResearchStopReason.NO_ACTIVE_HYPOTHESES
    if not portfolio.live():
        return ResearchStopReason.NO_ACTIVE_HYPOTHESES
    return ResearchStopReason.NO_AUTHORIZED_DISCRIMINATING_EXPERIMENT
