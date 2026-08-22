"""Exploration / exploitation policy. Priority is not truth, authorization, or Evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from research_os.research.types import ResearchInputError

EXPLORATION_STRATEGY_VERSION = "exploration.diagnostic.echo.v1"
FORBIDDEN_OPPORTUNITY_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "idor",
        "confidence",
        "evidence",
        "candidate",
        "finding",
        "authorization",
        "token",
        "session_token",
        "password",
        "priority_score",
        "weighted_score",
    }
)
COST_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


class OpportunityKind(Enum):
    """Research workflow category. Not a vulnerability class."""

    HYPOTHESIS_FOLLOWUP = "HYPOTHESIS_FOLLOWUP"
    DIFFERENTIAL_FOLLOWUP = "DIFFERENTIAL_FOLLOWUP"
    INVARIANT_CHALLENGE = "INVARIANT_CHALLENGE"
    CHAIN_EXTENSION = "CHAIN_EXTENSION"
    NEGATIVE_KNOWLEDGE_REVISIT = "NEGATIVE_KNOWLEDGE_REVISIT"
    UNRESOLVED_TARGET_RELATION = "UNRESOLVED_TARGET_RELATION"
    CONTROL_EXPERIMENT = "CONTROL_EXPERIMENT"
    SURFACE_DISCOVERY = "SURFACE_DISCOVERY"
    HUNTER_COVERAGE_GAP = "HUNTER_COVERAGE_GAP"
    REGISTRY_EXTERNAL_EXPLORATORY = "REGISTRY_EXTERNAL_EXPLORATORY"
    OTHER = "OTHER"


class OpportunityMode(Enum):
    EXPLOITATION = "EXPLOITATION"
    EXPLORATION = "EXPLORATION"


class OrdinalLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SelectionOutcome(Enum):
    SELECT = "SELECT"
    DEFER = "DEFER"
    SKIP_DUPLICATE = "SKIP_DUPLICATE"
    SKIP_LOW_INFORMATION = "SKIP_LOW_INFORMATION"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    NEEDS_MORE_CONTEXT = "NEEDS_MORE_CONTEXT"


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_ids(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise ResearchInputError(f"{field_name} must be a non-empty tuple")
    return tuple(_require_text(item, f"{field_name}[{index}]") for index, item in enumerate(value))


def _require_non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ResearchInputError(f"{field_name} must be >= 0; 0 means no allowance")
    return value


def _reject_forbidden(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ResearchInputError(f"{field_name} must be a mapping")
    found = FORBIDDEN_OPPORTUNITY_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


def _require_ordinal(value: object, field_name: str) -> OrdinalLevel:
    if not isinstance(value, OrdinalLevel):
        raise ResearchInputError(f"{field_name} must be an OrdinalLevel")
    return value


@dataclass(frozen=True)
class OpportunityDimensions:
    """Independent ordinal descriptors. Not a weighted priority score."""

    expected_information_value: OrdinalLevel
    security_relevance_potential: OrdinalLevel
    novelty_composition: OrdinalLevel
    unresolved_uncertainty: OrdinalLevel
    chain_potential: OrdinalLevel
    evidence_coverage: OrdinalLevel
    execution_cost: OrdinalLevel
    side_effect_requirement: int
    duplicate_risk: OrdinalLevel
    previous_failed_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_information_value",
            _require_ordinal(self.expected_information_value, "expected_information_value"),
        )
        object.__setattr__(
            self,
            "security_relevance_potential",
            _require_ordinal(
                self.security_relevance_potential, "security_relevance_potential"
            ),
        )
        object.__setattr__(
            self,
            "novelty_composition",
            _require_ordinal(self.novelty_composition, "novelty_composition"),
        )
        object.__setattr__(
            self,
            "unresolved_uncertainty",
            _require_ordinal(self.unresolved_uncertainty, "unresolved_uncertainty"),
        )
        object.__setattr__(
            self, "chain_potential", _require_ordinal(self.chain_potential, "chain_potential")
        )
        object.__setattr__(
            self,
            "evidence_coverage",
            _require_ordinal(self.evidence_coverage, "evidence_coverage"),
        )
        object.__setattr__(
            self, "execution_cost", _require_ordinal(self.execution_cost, "execution_cost")
        )
        object.__setattr__(
            self, "duplicate_risk", _require_ordinal(self.duplicate_risk, "duplicate_risk")
        )
        if self.side_effect_requirement not in (0, 1, 2, 3):
            raise ResearchInputError("side_effect_requirement must be 0, 1, 2, or 3")
        if (
            not isinstance(self.previous_failed_attempts, int)
            or isinstance(self.previous_failed_attempts, bool)
            or self.previous_failed_attempts < 0
        ):
            raise ResearchInputError("previous_failed_attempts must be >= 0")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "expected_information_value": self.expected_information_value.value,
            "security_relevance_potential": self.security_relevance_potential.value,
            "novelty_composition": self.novelty_composition.value,
            "unresolved_uncertainty": self.unresolved_uncertainty.value,
            "chain_potential": self.chain_potential.value,
            "evidence_coverage": self.evidence_coverage.value,
            "execution_cost": self.execution_cost.value,
            "side_effect_requirement": self.side_effect_requirement,
            "duplicate_risk": self.duplicate_risk.value,
            "previous_failed_attempts": self.previous_failed_attempts,
            "not_a_priority_score": True,
            "not_confidence": True,
        }


@dataclass(frozen=True)
class ResearchPolicyBudget:
    """Bounded selection allowance. 0 means no allowance. Not Core IssuedBudget."""

    max_selected: int = 4
    max_exploratory: int = 1
    max_chain_extensions: int = 1
    max_estimated_cost_rank: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_selected", _require_non_negative_int(self.max_selected, "max_selected")
        )
        object.__setattr__(
            self,
            "max_exploratory",
            _require_non_negative_int(self.max_exploratory, "max_exploratory"),
        )
        object.__setattr__(
            self,
            "max_chain_extensions",
            _require_non_negative_int(self.max_chain_extensions, "max_chain_extensions"),
        )
        object.__setattr__(
            self,
            "max_estimated_cost_rank",
            _require_non_negative_int(
                self.max_estimated_cost_rank, "max_estimated_cost_rank"
            ),
        )
        if self.max_estimated_cost_rank > 3:
            raise ResearchInputError("max_estimated_cost_rank must be 0..3 (0 means none)")


@dataclass(frozen=True)
class NegativeKnowledge:
    """Context-bound failed/contradicted history. Not a permanent blacklist."""

    structural_identity: str
    context_signature: str
    strategy_version: str
    assessment_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structural_identity",
            _require_text(self.structural_identity, "structural_identity"),
        )
        object.__setattr__(
            self, "context_signature", _require_text(self.context_signature, "context_signature")
        )
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )


@dataclass(frozen=True)
class ResearchOpportunity:
    """A possible next research direction. Not Hypothesis truth and not authorization."""

    opportunity_id: str
    research_run_id: str
    opportunity_kind: OpportunityKind
    mode: OpportunityMode
    source_refs: tuple[str, ...]
    proposed_direction: str
    unresolved_question: str
    expected_information_value_description: str
    assumptions: tuple[str, ...]
    dimensions: OpportunityDimensions
    context_signature: str
    novelty_composition_marker: bool
    prior_attempt_refs: tuple[str, ...]
    strategy_version: str
    structural_identity: str
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "opportunity_id", _require_text(self.opportunity_id, "opportunity_id")
        )
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.opportunity_kind, OpportunityKind):
            raise ResearchInputError("opportunity_kind must be an OpportunityKind")
        if not isinstance(self.mode, OpportunityMode):
            raise ResearchInputError("mode must be an OpportunityMode")
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self, "proposed_direction", _require_text(self.proposed_direction, "proposed_direction")
        )
        object.__setattr__(
            self,
            "unresolved_question",
            _require_text(self.unresolved_question, "unresolved_question"),
        )
        object.__setattr__(
            self,
            "expected_information_value_description",
            _require_text(
                self.expected_information_value_description,
                "expected_information_value_description",
            ),
        )
        object.__setattr__(
            self,
            "assumptions",
            tuple(
                _require_text(item, f"assumptions[{index}]")
                for index, item in enumerate(self.assumptions)
            ),
        )
        if not isinstance(self.dimensions, OpportunityDimensions):
            raise ResearchInputError("dimensions must be OpportunityDimensions")
        object.__setattr__(
            self, "context_signature", _require_text(self.context_signature, "context_signature")
        )
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )
        object.__setattr__(
            self,
            "structural_identity",
            _require_text(self.structural_identity, "structural_identity"),
        )
        object.__setattr__(
            self,
            "prior_attempt_refs",
            tuple(
                _require_text(item, f"prior_attempt_refs[{index}]")
                for index, item in enumerate(self.prior_attempt_refs)
            ),
        )
        if self.attributes is None:
            object.__setattr__(self, "attributes", {})
        else:
            object.__setattr__(self, "attributes", _reject_forbidden(self.attributes, "attributes"))
        lowered = f"{self.proposed_direction} {self.unresolved_question}".lower()
        if "vulnerability" in lowered or "exploit" in lowered:
            raise ResearchInputError("opportunity must not claim a vulnerability or exploit")

    @property
    def diversity_key(self) -> tuple[str, tuple[str, ...], str]:
        return (self.opportunity_kind.value, tuple(sorted(self.source_refs)), self.context_signature)


def dimensions_from_mapping(mapping: Mapping[str, Any]) -> OpportunityDimensions:
    """Reconstruct OpportunityDimensions from a persisted mapping.

    Exact inverse of `OpportunityDimensions.to_mapping()` for the ten real
    fields; the two descriptive markers `to_mapping()` adds
    (`not_a_priority_score`/`not_confidence`) are not real fields and are
    ignored on the way back in. Used to round-trip dimensions that were
    computed once by a producer and persisted, without that producer or its
    reader duplicating OpportunityDimensions' own validation.
    """

    def _ordinal(field_name: str) -> OrdinalLevel:
        value = mapping.get(field_name)
        if not isinstance(value, str):
            raise ResearchInputError(f"{field_name} must be a string ordinal level")
        try:
            return OrdinalLevel(value)
        except ValueError as exc:
            raise ResearchInputError(f"{field_name} is not a recognized OrdinalLevel") from exc

    return OpportunityDimensions(
        expected_information_value=_ordinal("expected_information_value"),
        security_relevance_potential=_ordinal("security_relevance_potential"),
        novelty_composition=_ordinal("novelty_composition"),
        unresolved_uncertainty=_ordinal("unresolved_uncertainty"),
        chain_potential=_ordinal("chain_potential"),
        evidence_coverage=_ordinal("evidence_coverage"),
        execution_cost=_ordinal("execution_cost"),
        side_effect_requirement=mapping.get("side_effect_requirement"),
        duplicate_risk=_ordinal("duplicate_risk"),
        previous_failed_attempts=mapping.get("previous_failed_attempts"),
    )


def opportunity_structural_identity(
    *,
    kind: OpportunityKind,
    source_refs: tuple[str, ...],
    context_signature: str,
    proposed_direction: str,
) -> str:
    payload = {
        "kind": kind.value,
        "source_refs": list(source_refs),
        "context_signature": context_signature,
        "proposed_direction": proposed_direction,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResearchSelectionDecision:
    """Policy choice of what to investigate next. Not Core ALLOW."""

    outcome: SelectionOutcome
    reason_codes: tuple[str, ...]
    opportunity: ResearchOpportunity

    @property
    def selected(self) -> bool:
        return self.outcome is SelectionOutcome.SELECT


def _same_context_negative(
    opportunity: ResearchOpportunity, negatives: tuple[NegativeKnowledge, ...]
) -> bool:
    for item in negatives:
        if (
            item.structural_identity == opportunity.structural_identity
            and item.context_signature == opportunity.context_signature
            and item.strategy_version == opportunity.strategy_version
        ):
            return True
    return False


def select_research_opportunities(
    opportunities: tuple[ResearchOpportunity, ...],
    *,
    research_run_id: str,
    budget: ResearchPolicyBudget | None = None,
    negative_knowledge: tuple[NegativeKnowledge, ...] = (),
    previously_selected_identities: frozenset[str] = frozenset(),
) -> tuple[ResearchSelectionDecision, ...]:
    """One bounded selection cycle. Does not execute, authorize, or loop."""

    budget = budget or ResearchPolicyBudget()
    run_id = _require_text(research_run_id, "research_run_id")
    seen_identities: set[str] = set(previously_selected_identities)
    decisions: list[ResearchSelectionDecision] = []
    selected: list[ResearchOpportunity] = []
    selected_diversity: set[tuple[str, tuple[str, ...], str]] = set()
    exploratory_count = 0
    chain_count = 0

    def _block(opportunity: ResearchOpportunity, outcome: SelectionOutcome, *codes: str) -> None:
        decisions.append(
            ResearchSelectionDecision(
                outcome=outcome, reason_codes=codes, opportunity=opportunity
            )
        )

    if budget.max_selected == 0:
        for opportunity in opportunities:
            _block(opportunity, SelectionOutcome.BLOCKED_BUDGET, "MAX_SELECTED_ZERO")
        return tuple(decisions)

    pending: list[ResearchOpportunity] = []
    for opportunity in opportunities:
        if opportunity.research_run_id != run_id:
            _block(opportunity, SelectionOutcome.BLOCKED_POLICY, "CROSS_RUN_SOURCE")
            continue
        if opportunity.structural_identity in seen_identities:
            _block(opportunity, SelectionOutcome.SKIP_DUPLICATE, "EXACT_STRUCTURAL_DUPLICATE")
            continue
        if opportunity.diversity_key in selected_diversity:
            _block(opportunity, SelectionOutcome.SKIP_DUPLICATE, "EQUIVALENT_SOURCE_CONTEXT")
            continue
        if _same_context_negative(opportunity, negative_knowledge):
            _block(
                opportunity,
                SelectionOutcome.SKIP_LOW_INFORMATION,
                "NEGATIVE_KNOWLEDGE_SAME_CONTEXT",
            )
            continue
        cost_rank = COST_RANK[opportunity.dimensions.execution_cost.value]
        if budget.max_estimated_cost_rank == 0 or cost_rank > budget.max_estimated_cost_rank:
            _block(opportunity, SelectionOutcome.BLOCKED_BUDGET, "COST_CLASS_NOT_ALLOWED")
            continue
        if opportunity.dimensions.side_effect_requirement == 3:
            _block(opportunity, SelectionOutcome.BLOCKED_POLICY, "LEVEL_3_NOT_SELECTABLE")
            continue
        pending.append(opportunity)
        seen_identities.add(opportunity.structural_identity)

    exploratory = tuple(
        item for item in pending if item.mode is OpportunityMode.EXPLORATION
    )
    exploitative = tuple(
        item for item in pending if item.mode is OpportunityMode.EXPLOITATION
    )

    def _try_select(opportunity: ResearchOpportunity) -> bool:
        nonlocal exploratory_count, chain_count
        if len(selected) >= budget.max_selected:
            return False
        if (
            opportunity.mode is OpportunityMode.EXPLORATION
            and exploratory_count >= budget.max_exploratory
        ):
            return False
        if (
            opportunity.opportunity_kind is OpportunityKind.CHAIN_EXTENSION
            and chain_count >= budget.max_chain_extensions
        ):
            return False
        if opportunity.diversity_key in selected_diversity:
            return False
        selected.append(opportunity)
        selected_diversity.add(opportunity.diversity_key)
        if opportunity.mode is OpportunityMode.EXPLORATION:
            exploratory_count += 1
        if opportunity.opportunity_kind is OpportunityKind.CHAIN_EXTENSION:
            chain_count += 1
        decisions.append(
            ResearchSelectionDecision(
                outcome=SelectionOutcome.SELECT,
                reason_codes=("SELECTED_FOR_PLANNING", "NOT_AUTHORIZATION"),
                opportunity=opportunity,
            )
        )
        return True

    for opportunity in exploratory:
        if not _try_select(opportunity):
            if budget.max_exploratory == 0:
                _block(opportunity, SelectionOutcome.BLOCKED_BUDGET, "EXPLORATION_SLOT_ZERO")
            elif exploratory_count >= budget.max_exploratory:
                _block(opportunity, SelectionOutcome.DEFER, "EXPLORATION_SLOT_EXHAUSTED")
            elif len(selected) >= budget.max_selected:
                _block(opportunity, SelectionOutcome.DEFER, "SELECTION_CAPACITY_EXHAUSTED")
            elif (
                opportunity.opportunity_kind is OpportunityKind.CHAIN_EXTENSION
                and chain_count >= budget.max_chain_extensions
            ):
                _block(opportunity, SelectionOutcome.BLOCKED_BUDGET, "CHAIN_EXTENSION_LIMIT")
            else:
                _block(opportunity, SelectionOutcome.DEFER, "NOT_SELECTED")
    for opportunity in exploitative:
        if not _try_select(opportunity):
            if len(selected) >= budget.max_selected:
                _block(opportunity, SelectionOutcome.DEFER, "SELECTION_CAPACITY_EXHAUSTED")
            elif (
                opportunity.opportunity_kind is OpportunityKind.CHAIN_EXTENSION
                and chain_count >= budget.max_chain_extensions
            ):
                _block(opportunity, SelectionOutcome.BLOCKED_BUDGET, "CHAIN_EXTENSION_LIMIT")
            else:
                _block(opportunity, SelectionOutcome.DEFER, "NOT_SELECTED")
    return tuple(decisions)


def _diagnostic_dimensions(
    *,
    information: OrdinalLevel,
    novelty: OrdinalLevel,
    uncertainty: OrdinalLevel,
    chain_potential: OrdinalLevel,
    duplicate_risk: OrdinalLevel,
    failed_attempts: int = 0,
) -> OpportunityDimensions:
    return OpportunityDimensions(
        expected_information_value=information,
        security_relevance_potential=OrdinalLevel.LOW,
        novelty_composition=novelty,
        unresolved_uncertainty=uncertainty,
        chain_potential=chain_potential,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=0,
        duplicate_risk=duplicate_risk,
        previous_failed_attempts=failed_attempts,
    )


def _opportunity(
    *,
    opportunity_id: str,
    research_run_id: str,
    kind: OpportunityKind,
    mode: OpportunityMode,
    source_refs: tuple[str, ...],
    direction: str,
    question: str,
    information: str,
    context_signature: str,
    dimensions: OpportunityDimensions,
    assumptions: tuple[str, ...] = ("diagnostic.echo is plumbing, not authorization",),
    prior_attempt_refs: tuple[str, ...] = (),
    novelty: bool = False,
) -> ResearchOpportunity:
    identity = opportunity_structural_identity(
        kind=kind,
        source_refs=source_refs,
        context_signature=context_signature,
        proposed_direction=direction,
    )
    return ResearchOpportunity(
        opportunity_id=opportunity_id,
        research_run_id=research_run_id,
        opportunity_kind=kind,
        mode=mode,
        source_refs=source_refs,
        proposed_direction=direction,
        unresolved_question=question,
        expected_information_value_description=information,
        assumptions=assumptions,
        dimensions=dimensions,
        context_signature=context_signature,
        novelty_composition_marker=novelty,
        prior_attempt_refs=prior_attempt_refs,
        strategy_version=EXPLORATION_STRATEGY_VERSION,
        structural_identity=identity,
        attributes={"not_a_vulnerability": True, "not_authorization": True},
    )


@dataclass(frozen=True)
class DiagnosticOpportunitySources:
    """Structured Research state for deterministic opportunity generation."""

    differential_ids: tuple[str, ...] = ()
    invariant_ids: tuple[str, ...] = ()
    chain_ids: tuple[str, ...] = ()
    change_event_ids: tuple[str, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    negative_knowledge: tuple[NegativeKnowledge, ...] = ()


def propose_diagnostic_opportunities(
    research_run_id: str,
    sources: DiagnosticOpportunitySources,
    *,
    id_prefix: str,
) -> tuple[ResearchOpportunity, ...]:
    """Deterministic diagnostic opportunities. Not a live-model planner."""

    run_id = _require_text(research_run_id, "research_run_id")
    prefix = _require_text(id_prefix, "id_prefix")
    items: list[ResearchOpportunity] = []
    index = 0
    for differential_id in sources.differential_ids:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:diff:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.DIFFERENTIAL_FOLLOWUP,
                mode=OpportunityMode.EXPLOITATION,
                source_refs=(differential_id,),
                direction="Reproduce the controlled diagnostic input difference.",
                question="Does the diagnostic echo still differ by submitted input?",
                information="Closing a controlled diagnostic difference is high information.",
                context_signature=f"differential:{differential_id}",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.HIGH,
                    novelty=OrdinalLevel.LOW,
                    uncertainty=OrdinalLevel.MEDIUM,
                    chain_potential=OrdinalLevel.LOW,
                    duplicate_risk=OrdinalLevel.LOW,
                ),
            )
        )
    for invariant_id in sources.invariant_ids:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:inv:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.INVARIANT_CHALLENGE,
                mode=OpportunityMode.EXPLORATION,
                source_refs=(invariant_id,),
                direction="Challenge the diagnostic input/output correspondence invariant.",
                question="Does diagnostic.echo still correspond across a new input?",
                information="An untested invariant boundary is informative even if weak.",
                context_signature=f"invariant:{invariant_id}",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.HIGH,
                    novelty=OrdinalLevel.MEDIUM,
                    uncertainty=OrdinalLevel.HIGH,
                    chain_potential=OrdinalLevel.MEDIUM,
                    duplicate_risk=OrdinalLevel.LOW,
                ),
                novelty=True,
            )
        )
    for chain_id in sources.chain_ids:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:chain:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.CHAIN_EXTENSION,
                mode=OpportunityMode.EXPLORATION,
                source_refs=(chain_id,),
                direction="Extend the diagnostic echo chain under a second input.",
                question="Does a second diagnostic step still compose without a causal leap?",
                information="An unresolved chain assumption is worth a bounded extension.",
                context_signature=f"chain:{chain_id}",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.MEDIUM,
                    novelty=OrdinalLevel.HIGH,
                    uncertainty=OrdinalLevel.HIGH,
                    chain_potential=OrdinalLevel.HIGH,
                    duplicate_risk=OrdinalLevel.LOW,
                ),
                novelty=True,
            )
        )
    for change_event_id in sources.change_event_ids:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:change:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.DIFFERENTIAL_FOLLOWUP,
                mode=OpportunityMode.EXPLORATION,
                source_refs=(change_event_id,),
                direction="Investigate a diagnostic ChangeEvent as a TIME-backed difference.",
                question="What diagnostic behavior changed between snapshot t1 and t2?",
                information="A temporal change can reopen research without rewriting history.",
                context_signature=f"change:{change_event_id}",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.HIGH,
                    novelty=OrdinalLevel.MEDIUM,
                    uncertainty=OrdinalLevel.HIGH,
                    chain_potential=OrdinalLevel.LOW,
                    duplicate_risk=OrdinalLevel.LOW,
                ),
                novelty=True,
            )
        )
    for hypothesis_id in sources.hypothesis_ids:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:hyp:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.HYPOTHESIS_FOLLOWUP,
                mode=OpportunityMode.EXPLOITATION,
                source_refs=(hypothesis_id,),
                direction="Continue the existing diagnostic hypothesis with a control echo.",
                question="Does a repeated diagnostic echo still round-trip?",
                information="A control experiment can close remaining diagnostic ambiguity.",
                context_signature=f"hypothesis:{hypothesis_id}",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.MEDIUM,
                    novelty=OrdinalLevel.LOW,
                    uncertainty=OrdinalLevel.LOW,
                    chain_potential=OrdinalLevel.LOW,
                    duplicate_risk=OrdinalLevel.MEDIUM,
                ),
            )
        )
    for negative in sources.negative_knowledge:
        index += 1
        items.append(
            _opportunity(
                opportunity_id=f"{prefix}:neg:{index}",
                research_run_id=run_id,
                kind=OpportunityKind.NEGATIVE_KNOWLEDGE_REVISIT,
                mode=OpportunityMode.EXPLORATION,
                source_refs=(negative.assessment_ref or negative.structural_identity,),
                direction="Revisit a previously contradicted diagnostic direction under new context.",
                question="Is the earlier contradiction still applicable after context change?",
                information="Changed context can make a failed test informative again.",
                context_signature=f"revisit:{negative.context_signature}:new",
                dimensions=_diagnostic_dimensions(
                    information=OrdinalLevel.MEDIUM,
                    novelty=OrdinalLevel.MEDIUM,
                    uncertainty=OrdinalLevel.HIGH,
                    chain_potential=OrdinalLevel.LOW,
                    duplicate_risk=OrdinalLevel.LOW,
                    failed_attempts=1,
                ),
                prior_attempt_refs=(negative.assessment_ref,) if negative.assessment_ref else (),
                novelty=True,
            )
        )
    return tuple(items)
