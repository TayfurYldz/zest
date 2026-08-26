"""Chain engine. Explicit multi-step research composition, not an LLM story or exploit graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.research.planning import (
    DIAGNOSTIC_DISCONFIRMING_OBSERVATION,
    plan_diagnostic_echo,
)
from zest.research.target_model import TargetEpistemicStatus, TargetObservationView
from zest.research.types import ExperimentPlan, ResearchInputError

CHAIN_STRATEGY_VERSION = "chain.diagnostic.echo.v1"
DIAGNOSTIC_CHAIN_CAPABILITY = "CAN_OBSERVE_ECHO"
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_BRANCHING = 1
DEFAULT_MAX_GENERATED_CHAINS = 2
FORBIDDEN_CHAIN_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "vulnerability",
        "exploit",
        "idor",
        "confidence",
        "evidence",
        "candidate",
        "finding",
        "authorization",
        "token",
        "session_token",
        "password",
    }
)


class ChainNodeKind(Enum):
    OBSERVATION = "OBSERVATION"
    CAPABILITY = "CAPABILITY"
    STATE = "STATE"
    STATE_TRANSITION = "STATE_TRANSITION"
    INVARIANT = "INVARIANT"
    EXPERIMENT = "EXPERIMENT"
    HYPOTHESIS = "HYPOTHESIS"


class ChainEdgeKind(Enum):
    PRODUCES = "PRODUCES"
    ENABLES = "ENABLES"
    REQUIRES = "REQUIRES"
    TRANSITIONS_TO = "TRANSITIONS_TO"
    CONTRADICTS = "CONTRADICTS"
    SATISFIES_PRECONDITION = "SATISFIES_PRECONDITION"


class ChainOutcome(Enum):
    ADMITTED = "ADMITTED"
    REJECTED_MISSING_PRECONDITION = "REJECTED_MISSING_PRECONDITION"
    REJECTED_UNSUPPORTED_CAUSAL_LEAP = "REJECTED_UNSUPPORTED_CAUSAL_LEAP"
    REJECTED_BROKEN_PROVENANCE = "REJECTED_BROKEN_PROVENANCE"
    REJECTED_CROSS_RUN = "REJECTED_CROSS_RUN"
    REJECTED_CYCLE = "REJECTED_CYCLE"
    REJECTED_LIMIT = "REJECTED_LIMIT"
    REJECTED_POLICY_CONFLICT = "REJECTED_POLICY_CONFLICT"


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
    found = FORBIDDEN_CHAIN_KEYS.intersection(payload.keys())
    if found:
        raise ResearchInputError(f"{field_name} must not contain {sorted(found)}")
    return dict(payload)


@dataclass(frozen=True)
class ChainSearchLimits:
    """Explicit search bounds. 0 means no allowance. Negative is invalid."""

    max_depth: int = DEFAULT_MAX_DEPTH
    max_branching: int = DEFAULT_MAX_BRANCHING
    max_generated_chains: int = DEFAULT_MAX_GENERATED_CHAINS

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_depth", _require_non_negative_int(self.max_depth, "max_depth"))
        object.__setattr__(
            self, "max_branching", _require_non_negative_int(self.max_branching, "max_branching")
        )
        object.__setattr__(
            self,
            "max_generated_chains",
            _require_non_negative_int(self.max_generated_chains, "max_generated_chains"),
        )


@dataclass(frozen=True)
class ChainStep:
    """One chain node. Capability is context-bound. Epistemic status is not a promotion rank."""

    step_index: int
    node_kind: ChainNodeKind
    source_ref: str
    epistemic_status: TargetEpistemicStatus
    state_signature: str
    side_effect_level: int
    statement: str
    incoming_edge: ChainEdgeKind | None = None
    capability: str | None = None
    experiment_id: str | None = None
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.step_index, int) or isinstance(self.step_index, bool) or self.step_index < 0:
            raise ResearchInputError("step_index must be a non-negative int")
        if not isinstance(self.node_kind, ChainNodeKind):
            raise ResearchInputError("node_kind must be a ChainNodeKind")
        object.__setattr__(self, "source_ref", _require_text(self.source_ref, "source_ref"))
        if not isinstance(self.epistemic_status, TargetEpistemicStatus):
            raise ResearchInputError("epistemic_status must be a TargetEpistemicStatus")
        object.__setattr__(
            self, "state_signature", _require_text(self.state_signature, "state_signature")
        )
        if self.side_effect_level not in (0, 1, 2, 3):
            raise ResearchInputError("side_effect_level must be 0, 1, 2, or 3")
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        if self.incoming_edge is not None and not isinstance(self.incoming_edge, ChainEdgeKind):
            raise ResearchInputError("incoming_edge must be a ChainEdgeKind or None")
        if self.capability is not None:
            object.__setattr__(self, "capability", _require_text(self.capability, "capability"))
        if self.experiment_id is not None:
            object.__setattr__(
                self, "experiment_id", _require_text(self.experiment_id, "experiment_id")
            )
        if self.attributes is None:
            object.__setattr__(self, "attributes", {})
        else:
            object.__setattr__(self, "attributes", _reject_forbidden(self.attributes, "attributes"))

    @property
    def visit_key(self) -> tuple[str, str, str]:
        return (self.node_kind.value, self.source_ref, self.state_signature)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "node_kind": self.node_kind.value,
            "source_ref": self.source_ref,
            "epistemic_status": self.epistemic_status.value,
            "state_signature": self.state_signature,
            "side_effect_level": self.side_effect_level,
            "statement": self.statement,
            "incoming_edge": None if self.incoming_edge is None else self.incoming_edge.value,
            "capability": self.capability,
            "experiment_id": self.experiment_id,
            "attributes": dict(self.attributes or {}),
        }


def chain_structural_identity(steps: tuple[ChainStep, ...]) -> str:
    payload = [
        {
            "node_kind": step.node_kind.value,
            "source_ref": step.source_ref,
            "capability": step.capability,
            "state_signature": step.state_signature,
            "incoming_edge": None if step.incoming_edge is None else step.incoming_edge.value,
        }
        for step in steps
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChainHypothesis:
    """Ordered research composition. Not Evidence, Candidate, Finding, or an exploit."""

    chain_id: str
    research_run_id: str
    steps: tuple[ChainStep, ...]
    source_refs: tuple[str, ...]
    preconditions: tuple[str, ...]
    expected_resulting_capability: str
    unresolved_assumptions: tuple[str, ...]
    falsification_points: tuple[str, ...]
    strategy_version: str
    structural_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", _require_text(self.chain_id, "chain_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.steps, tuple) or len(self.steps) < 2:
            raise ResearchInputError("steps must contain at least two ChainStep values")
        object.__setattr__(self, "source_refs", _require_ids(self.source_refs, "source_refs"))
        object.__setattr__(
            self,
            "expected_resulting_capability",
            _require_text(self.expected_resulting_capability, "expected_resulting_capability"),
        )
        object.__setattr__(
            self,
            "structural_identity",
            _require_text(self.structural_identity, "structural_identity"),
        )
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )

    @property
    def depth(self) -> int:
        return len(self.steps) - 1

    @property
    def inferred_step_count(self) -> int:
        return sum(
            1
            for step in self.steps
            if step.epistemic_status
            in {TargetEpistemicStatus.INFERRED, TargetEpistemicStatus.HYPOTHESIZED}
        )

    @property
    def supported_step_count(self) -> int:
        return sum(
            1
            for step in self.steps
            if step.epistemic_status
            in {TargetEpistemicStatus.OBSERVED, TargetEpistemicStatus.DERIVED}
        )

    @property
    def max_side_effect_level(self) -> int:
        return max(step.side_effect_level for step in self.steps)

    def descriptive_features(self) -> dict[str, Any]:
        """Unweighted descriptors for a later Exploration Policy. Not a priority score."""

        return {
            "depth": self.depth,
            "unresolved_assumptions": len(self.unresolved_assumptions),
            "supported_steps": self.supported_step_count,
            "inferred_steps": self.inferred_step_count,
            "side_effect_requirement": self.max_side_effect_level,
            "evidence_coverage": 0,
            "novelty_composition": self.depth >= 1,
            "not_a_priority_score": True,
        }


@dataclass(frozen=True)
class ChainDecision:
    outcome: ChainOutcome
    reason_codes: tuple[str, ...]
    hypothesis: ChainHypothesis | None

    @property
    def admitted(self) -> bool:
        return self.outcome is ChainOutcome.ADMITTED


def _state_signature(view: TargetObservationView) -> str:
    return (
        f"input={view.submitted_input or ''}|action={view.action}|"
        f"resource={view.resource_handle}|actor={view.actor_handle}"
    )


def admit_chain_hypothesis(
    draft: ChainHypothesis,
    *,
    research_run_id: str,
    resolvable_source_ids: frozenset[str],
    limits: ChainSearchLimits | None = None,
) -> ChainDecision:
    """Admit a chain hypothesis. Sequence is not causality. No Core bypass."""

    limits = limits or ChainSearchLimits()
    if draft.research_run_id != research_run_id:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_CROSS_RUN,
            reason_codes=("CROSS_RUN_SOURCE",),
            hypothesis=None,
        )
    if limits.max_generated_chains == 0 or limits.max_depth == 0:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_LIMIT,
            reason_codes=("SEARCH_LIMIT_ZERO",),
            hypothesis=None,
        )
    if draft.depth > limits.max_depth:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_LIMIT,
            reason_codes=("MAX_DEPTH_EXCEEDED",),
            hypothesis=None,
        )
    expected_indexes = tuple(range(len(draft.steps)))
    actual_indexes = tuple(step.step_index for step in draft.steps)
    if actual_indexes != expected_indexes:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=("STEP_ORDER_INVALID",),
            hypothesis=None,
        )
    missing = [ref for ref in draft.source_refs if ref not in resolvable_source_ids]
    if missing:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=("HALLUCINATED_SOURCE",),
            hypothesis=None,
        )
    visited: set[tuple[str, str, str]] = set()
    previous: ChainStep | None = None
    for step in draft.steps:
        if step.visit_key in visited:
            return ChainDecision(
                outcome=ChainOutcome.REJECTED_CYCLE,
                reason_codes=("TRIVIAL_CYCLE",),
                hypothesis=None,
            )
        visited.add(step.visit_key)
        lowered = step.statement.lower()
        if "exploit" in lowered or "is a vulnerability" in lowered or "vulnerability class" in lowered:
            return ChainDecision(
                outcome=ChainOutcome.REJECTED_POLICY_CONFLICT,
                reason_codes=("POLICY_OR_EXPLOIT_CLAIM",),
                hypothesis=None,
            )
        if previous is None:
            previous = step
            continue
        if step.incoming_edge is None:
            return ChainDecision(
                outcome=ChainOutcome.REJECTED_UNSUPPORTED_CAUSAL_LEAP,
                reason_codes=("MISSING_EDGE",),
                hypothesis=None,
            )
        if step.incoming_edge is ChainEdgeKind.PRODUCES:
            if previous.experiment_id is None or step.experiment_id != previous.experiment_id:
                return ChainDecision(
                    outcome=ChainOutcome.REJECTED_UNSUPPORTED_CAUSAL_LEAP,
                    reason_codes=("PRODUCES_REQUIRES_SAME_EXPERIMENT",),
                    hypothesis=None,
                )
        if (
            step.incoming_edge is ChainEdgeKind.ENABLES
            and previous.node_kind
            not in {
                ChainNodeKind.CAPABILITY,
                ChainNodeKind.OBSERVATION,
                ChainNodeKind.STATE,
            }
        ):
            return ChainDecision(
                outcome=ChainOutcome.REJECTED_MISSING_PRECONDITION,
                reason_codes=("ENABLES_WITHOUT_CAPABILITY_OR_OBSERVATION",),
                hypothesis=None,
            )
        previous = step
    if chain_structural_identity(draft.steps) != draft.structural_identity:
        return ChainDecision(
            outcome=ChainOutcome.REJECTED_BROKEN_PROVENANCE,
            reason_codes=("STRUCTURAL_IDENTITY_MISMATCH",),
            hypothesis=None,
        )
    return ChainDecision(
        outcome=ChainOutcome.ADMITTED,
        reason_codes=("CHAIN_HYPOTHESIS_ADMITTED", "NOT_AN_EXPLOIT"),
        hypothesis=draft,
    )


def compose_diagnostic_echo_chains(
    research_run_id: str,
    views: tuple[TargetObservationView, ...],
    *,
    chain_id_prefix: str,
    invariant_id: str | None = None,
    limits: ChainSearchLimits | None = None,
    inferred_intermediate: ChainStep | None = None,
) -> tuple[ChainDecision, ...]:
    """Bounded deterministic diagnostic composition. Does not dispatch a Worker."""

    limits = limits or ChainSearchLimits()
    run_id = _require_text(research_run_id, "research_run_id")
    if limits.max_generated_chains == 0 or limits.max_depth == 0:
        return (
            ChainDecision(
                outcome=ChainOutcome.REJECTED_LIMIT,
                reason_codes=("SEARCH_LIMIT_ZERO",),
                hypothesis=None,
            ),
        )
    ordered = tuple(
        sorted(
            (view for view in views if view.research_run_id == run_id),
            key=lambda item: item.observation_id,
        )
    )
    if len(ordered) < 2:
        return (
            ChainDecision(
                outcome=ChainOutcome.REJECTED_MISSING_PRECONDITION,
                reason_codes=("TWO_DIAGNOSTIC_STEPS_REQUIRED",),
                hypothesis=None,
            ),
        )
    if any(view.research_run_id != run_id for view in views):
        return (
            ChainDecision(
                outcome=ChainOutcome.REJECTED_CROSS_RUN,
                reason_codes=("CROSS_RUN_SOURCE",),
                hypothesis=None,
            ),
        )
    pair_count = min(len(ordered) - 1, limits.max_branching, limits.max_generated_chains)
    decisions: list[ChainDecision] = []
    for index in range(pair_count):
        baseline = ordered[index]
        variant = ordered[index + 1]
        steps = _diagnostic_pair_steps(
            baseline,
            variant,
            invariant_id=invariant_id,
            inferred_intermediate=inferred_intermediate if index == 0 else None,
        )
        draft = ChainHypothesis(
            chain_id=f"{chain_id_prefix}:{index + 1}",
            research_run_id=run_id,
            steps=steps,
            source_refs=tuple(step.source_ref for step in steps),
            preconditions=("diagnostic.echo capability is available",),
            expected_resulting_capability=DIAGNOSTIC_CHAIN_CAPABILITY,
            unresolved_assumptions=(
                "echo correspondence is a plumbing expectation, not authorization",
            ),
            falsification_points=(DIAGNOSTIC_DISCONFIRMING_OBSERVATION,),
            strategy_version=CHAIN_STRATEGY_VERSION,
            structural_identity=chain_structural_identity(steps),
        )
        decisions.append(
            admit_chain_hypothesis(
                draft,
                research_run_id=run_id,
                resolvable_source_ids=frozenset(draft.source_refs),
                limits=limits,
            )
        )
    return tuple(decisions)


def _diagnostic_pair_steps(
    baseline: TargetObservationView,
    variant: TargetObservationView,
    *,
    invariant_id: str | None,
    inferred_intermediate: ChainStep | None,
) -> tuple[ChainStep, ...]:
    baseline_sig = _state_signature(baseline)
    variant_sig = _state_signature(variant)
    steps: list[ChainStep] = [
        ChainStep(
            step_index=0,
            node_kind=ChainNodeKind.OBSERVATION,
            source_ref=baseline.observation_id,
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            state_signature=baseline_sig,
            side_effect_level=0,
            statement=f"Diagnostic observation {baseline.observation_id} was produced.",
            experiment_id=baseline.experiment_id,
        ),
        ChainStep(
            step_index=1,
            node_kind=ChainNodeKind.CAPABILITY,
            source_ref=f"cap:echo:{baseline.observation_id}",
            epistemic_status=TargetEpistemicStatus.DERIVED,
            state_signature=baseline_sig,
            side_effect_level=0,
            statement="Derived CAN_OBSERVE_ECHO under the baseline diagnostic input.",
            incoming_edge=ChainEdgeKind.PRODUCES,
            capability=DIAGNOSTIC_CHAIN_CAPABILITY,
            experiment_id=baseline.experiment_id,
            attributes={"not_global_permission": True},
        ),
        ChainStep(
            step_index=2,
            node_kind=ChainNodeKind.EXPERIMENT,
            source_ref=variant.experiment_id,
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            state_signature=variant_sig,
            side_effect_level=0,
            statement=f"Diagnostic experiment {variant.experiment_id} executed a second echo.",
            incoming_edge=ChainEdgeKind.ENABLES,
            experiment_id=variant.experiment_id,
        ),
        ChainStep(
            step_index=3,
            node_kind=ChainNodeKind.OBSERVATION,
            source_ref=variant.observation_id,
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            state_signature=variant_sig,
            side_effect_level=0,
            statement=f"Diagnostic observation {variant.observation_id} was produced.",
            incoming_edge=ChainEdgeKind.PRODUCES,
            experiment_id=variant.experiment_id,
        ),
    ]
    if inferred_intermediate is not None:
        inserted = ChainStep(
            step_index=2,
            node_kind=inferred_intermediate.node_kind,
            source_ref=inferred_intermediate.source_ref,
            epistemic_status=inferred_intermediate.epistemic_status,
            state_signature=inferred_intermediate.state_signature,
            side_effect_level=inferred_intermediate.side_effect_level,
            statement=inferred_intermediate.statement,
            incoming_edge=ChainEdgeKind.TRANSITIONS_TO,
            capability=inferred_intermediate.capability,
            experiment_id=baseline.experiment_id,
            attributes=dict(inferred_intermediate.attributes or {}),
        )
        rest = []
        for step in steps[2:]:
            rest.append(
                ChainStep(
                    step_index=step.step_index + 1,
                    node_kind=step.node_kind,
                    source_ref=step.source_ref,
                    epistemic_status=step.epistemic_status,
                    state_signature=step.state_signature,
                    side_effect_level=step.side_effect_level,
                    statement=step.statement,
                    incoming_edge=step.incoming_edge,
                    capability=step.capability,
                    experiment_id=step.experiment_id,
                    attributes=dict(step.attributes or {}),
                )
            )
        steps = steps[:2] + [inserted] + rest
    if invariant_id is not None:
        steps.append(
            ChainStep(
                step_index=len(steps),
                node_kind=ChainNodeKind.INVARIANT,
                source_ref=invariant_id,
                epistemic_status=TargetEpistemicStatus.HYPOTHESIZED,
                state_signature=variant_sig,
                side_effect_level=0,
                statement="Invariant hypothesis compared across diagnostic inputs.",
                incoming_edge=ChainEdgeKind.SATISFIES_PRECONDITION,
                experiment_id=variant.experiment_id,
                attributes={"not_a_vulnerability": True, "not_a_finding": True},
            )
        )
    return tuple(steps)


def experiment_plan_for_chain_step(
    step: ChainStep,
    *,
    hypothesis_id: str,
    budget_id: str,
    target_reference: str,
    message: str = "ping",
) -> ExperimentPlan:
    """Research plan from a chain step. Does not authorize or dispatch.

    Side effect is determined by the capability action, not the chain step claim.
    """

    return plan_diagnostic_echo(
        hypothesis_id,
        budget_id=budget_id,
        target_reference=target_reference,
        message=message,
    )
