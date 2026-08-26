"""Runtime routing policy. Selects a configured runtime for a reasoning role.

Does not decide vulnerability truth, scope, authorization, Evidence, Candidate, or Finding.
Does not let a model choose itself. Does not compute a magic aggregate score.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cmp_to_key
from typing import Any

from zest.research.model_port import ModelRole
from zest.research.model_runtime import (
    ModelPriceClass,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeOutcome,
    reject_secret_keys,
)
from zest.research.types import ResearchInputError

ROUTING_POLICY_VERSION = "runtime.routing.v1"
UNRESTRICTED_CAPABILITY_MARKERS = frozenset(
    {"*", "all", "unrestricted", "shell", "yolo", "danger-full-access", "any"}
)
FORBIDDEN_ROUTING_KEYS = frozenset(
    {
        "winner",
        "model_score",
        "weighted_score",
        "quality_score",
        "aggregate_score",
        "api_key",
        "token",
        "password",
        "secret",
    }
)

# SD-G4: task class → price class.  "none" means no model call (monitoring mode).
TASK_PRICE_CLASS_POLICY: dict[str, ModelPriceClass | str] = {
    "monitoring": "none",
    "generator": ModelPriceClass.CHEAP,
    "falsifier": ModelPriceClass.CHEAP,
    "finding_proposal_qa": ModelPriceClass.EXPENSIVE,
}


class LocalityConstraint(Enum):
    LOCAL_REQUIRED = "LOCAL_REQUIRED"
    REMOTE_ALLOWED = "REMOTE_ALLOWED"


class CandidateLocality(Enum):
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class RoutingOutcome(Enum):
    SELECT = "SELECT"
    NO_COMPATIBLE_RUNTIME = "NO_COMPATIBLE_RUNTIME"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    UNAVAILABLE = "UNAVAILABLE"
    REQUIRE_OPERATOR_SELECTION = "REQUIRE_OPERATOR_SELECTION"


class RoutingReasonCode(Enum):
    SELECTED_AFTER_HARD_FILTERS = "SELECTED_AFTER_HARD_FILTERS"
    SELECTED_BY_OPERATOR_ORDER = "SELECTED_BY_OPERATOR_ORDER"
    SELECTED_BY_QUALITY_ORDER = "SELECTED_BY_QUALITY_ORDER"
    TIE_REQUIRES_OPERATOR = "TIE_REQUIRES_OPERATOR"
    ZERO_RUNTIME_ALLOWANCE = "ZERO_RUNTIME_ALLOWANCE"
    ZERO_FALLBACK_ALLOWANCE = "ZERO_FALLBACK_ALLOWANCE"
    NO_CANDIDATES = "NO_CANDIDATES"
    UNAVAILABLE = "UNAVAILABLE"
    WRONG_RUNTIME_CLASS = "WRONG_RUNTIME_CLASS"
    AGENT_NOT_PERMITTED_FOR_INFERENCE_ROLE = "AGENT_NOT_PERMITTED_FOR_INFERENCE_ROLE"
    UNRESTRICTED_CAPABILITY = "UNRESTRICTED_CAPABILITY"
    MISSING_STRUCTURED_OUTPUT = "MISSING_STRUCTURED_OUTPUT"
    OPERATOR_PROHIBITED = "OPERATOR_PROHIBITED"
    LOCALITY_MISMATCH = "LOCALITY_MISMATCH"
    MISSING_AUTHENTICATION = "MISSING_AUTHENTICATION"
    CAPABILITY_EXPOSURE_TOO_BROAD = "CAPABILITY_EXPOSURE_TOO_BROAD"
    SIDE_EFFECT_CAPABILITY_ON_REASONING_PATH = "SIDE_EFFECT_CAPABILITY_ON_REASONING_PATH"
    STRIX_IS_NOT_MODEL_RUNTIME = "STRIX_IS_NOT_MODEL_RUNTIME"
    CONTENT_POLICY_BLOCK_NO_BYPASS = "CONTENT_POLICY_BLOCK_NO_BYPASS"
    FALLBACK_EXHAUSTED = "FALLBACK_EXHAUSTED"
    ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"
    ALREADY_ATTEMPTED = "ALREADY_ATTEMPTED"
    CHEAP_CLASS_SELECTED = "CHEAP_CLASS_SELECTED"
    EXPENSIVE_CLASS_SELECTED = "EXPENSIVE_CLASS_SELECTED"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    MONITORING_CLASS_DISABLED = "MONITORING_CLASS_DISABLED"


def _require_non_negative(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ResearchInputError(f"{name} must be a non-negative int; 0 is no allowance")
    return value


@dataclass(frozen=True)
class RoutingBudget:
    """Bounds routing attempts. 0 means none. Negative is invalid."""

    max_runtime_attempts: int
    max_fallback_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_runtime_attempts",
            _require_non_negative("max_runtime_attempts", self.max_runtime_attempts),
        )
        object.__setattr__(
            self,
            "max_fallback_attempts",
            _require_non_negative("max_fallback_attempts", self.max_fallback_attempts),
        )


@dataclass(frozen=True)
class RuntimeQualityObservation:
    """Benchmark-derived observations. Not a scalar model score and not Evidence."""

    grounding_safety_hard_failures: int = 0
    research_usefulness_failures: int = 0
    falsifier_quality_failures: int = 0
    instability_events: int = 0
    latency_ms: int | None = None
    cost_amount: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "grounding_safety_hard_failures",
            "research_usefulness_failures",
            "falsifier_quality_failures",
            "instability_events",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ResearchInputError(f"{name} must be a non-negative int")
        if self.latency_ms is not None and (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or self.latency_ms < 0
        ):
            raise ResearchInputError("latency_ms must be a non-negative int when set")
        if self.cost_amount is not None and (
            not isinstance(self.cost_amount, (int, float))
            or isinstance(self.cost_amount, bool)
            or self.cost_amount < 0
        ):
            raise ResearchInputError("cost_amount must be a non-negative number when set")

    def ordered_key(self) -> tuple[int, int, int, int]:
        return (
            self.grounding_safety_hard_failures,
            self.research_usefulness_failures,
            self.falsifier_quality_failures,
            self.instability_events,
        )


@dataclass(frozen=True)
class RuntimeCandidate:
    """One configured runtime. Not a vendor ranking and not Core authority."""

    identity: ModelRuntimeIdentity
    available: bool
    authenticated: bool
    structured_output_compatible: bool
    allowed_capabilities: tuple[str, ...] = ()
    side_effect_capabilities: tuple[str, ...] = ()
    locality: CandidateLocality = CandidateLocality.REMOTE
    operator_prohibited: bool = False
    quality: RuntimeQualityObservation | None = None
    is_strix: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ModelRuntimeIdentity):
            raise ResearchInputError("identity must be a ModelRuntimeIdentity")
        if not isinstance(self.locality, CandidateLocality):
            raise ResearchInputError("locality must be a CandidateLocality")
        if not isinstance(self.allowed_capabilities, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.allowed_capabilities
        ):
            raise ResearchInputError("allowed_capabilities must be a tuple of non-empty strings")
        if not isinstance(self.side_effect_capabilities, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.side_effect_capabilities
        ):
            raise ResearchInputError("side_effect_capabilities must be a tuple of non-empty strings")


@dataclass(frozen=True)
class RoutingRequest:
    """Structured routing input. Not a prose prompt and not an LLM self-vote."""

    role: ModelRole
    candidates: tuple[RuntimeCandidate, ...]
    budget: RoutingBudget
    required_runtime_class: RuntimeClass = RuntimeClass.INFERENCE_RUNTIME
    structured_output_required: bool = True
    locality: LocalityConstraint = LocalityConstraint.REMOTE_ALLOWED
    allow_agent_runtime: bool = False
    operator_prohibited_adapter_ids: tuple[str, ...] = ()
    operator_preference_order: tuple[str, ...] = ()
    require_operator_on_tie: bool = True
    attempted_adapter_ids: tuple[str, ...] = ()
    runtime_attempts_used: int = 0
    fallback_attempts_used: int = 0
    task_class: str | None = None
    escalation_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, ModelRole):
            raise ResearchInputError("role must be a ModelRole")
        if not isinstance(self.candidates, tuple):
            raise ResearchInputError("candidates must be a tuple")
        if not isinstance(self.budget, RoutingBudget):
            raise ResearchInputError("budget must be a RoutingBudget")
        if not isinstance(self.required_runtime_class, RuntimeClass):
            raise ResearchInputError("required_runtime_class must be a RuntimeClass")
        if not isinstance(self.locality, LocalityConstraint):
            raise ResearchInputError("locality must be a LocalityConstraint")
        if self.runtime_attempts_used < 0 or self.fallback_attempts_used < 0:
            raise ResearchInputError("attempt counters must be non-negative")
        if self.task_class is not None and (
            not isinstance(self.task_class, str) or not self.task_class.strip()
        ):
            raise ResearchInputError("task_class must be a non-empty string when set")
        if self.escalation_reason is not None and (
            not isinstance(self.escalation_reason, str) or not self.escalation_reason.strip()
        ):
            raise ResearchInputError("escalation_reason must be a non-empty string when set")


@dataclass(frozen=True)
class FilterRecord:
    adapter_id: str
    reason_code: RoutingReasonCode

    def to_mapping(self) -> dict[str, str]:
        return {"adapter_id": self.adapter_id, "reason_code": self.reason_code.value}


@dataclass(frozen=True)
class RuntimeSelectionDecision:
    """Routing outcome. Not vulnerability truth and not a universal winner."""

    outcome: RoutingOutcome
    policy_version: str
    role: ModelRole
    reason_codes: tuple[str, ...]
    selected_identity: ModelRuntimeIdentity | None = None
    considered_identities: tuple[ModelRuntimeIdentity, ...] = ()
    filtered: tuple[FilterRecord, ...] = ()
    attempted_adapter_ids: tuple[str, ...] = ()
    runtime_attempts_used: int = 0
    fallback_attempts_used: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RoutingOutcome):
            raise ResearchInputError("outcome must be a RoutingOutcome")
        if self.policy_version != ROUTING_POLICY_VERSION:
            raise ResearchInputError("policy_version must be the locked routing policy version")
        if self.selected_identity is not None and self.outcome is not RoutingOutcome.SELECT:
            raise ResearchInputError("selected_identity is only valid on SELECT")
        if self.outcome is RoutingOutcome.SELECT and self.selected_identity is None:
            raise ResearchInputError("SELECT requires selected_identity")

    @property
    def selected(self) -> bool:
        return self.outcome is RoutingOutcome.SELECT

    def to_mapping(self) -> dict[str, Any]:
        payload = {
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
            "role": self.role.value,
            "reason_codes": list(self.reason_codes),
            "selected_runtime": None
            if self.selected_identity is None
            else self.selected_identity.to_mapping(),
            "considered_runtimes": [item.to_mapping() for item in self.considered_identities],
            "filtered": [item.to_mapping() for item in self.filtered],
            "attempted_adapter_ids": list(self.attempted_adapter_ids),
            "runtime_attempts_used": self.runtime_attempts_used,
            "fallback_attempts_used": self.fallback_attempts_used,
            "no_aggregate_model_score": True,
            "no_automatic_winner": True,
            "not_authorization": True,
            "not_evidence": True,
            "not_finding": True,
            "contains_secrets": False,
        }
        reject_secret_keys(payload, "runtime_selection_decision")
        found = FORBIDDEN_ROUTING_KEYS.intersection(key.lower() for key in payload)
        if found:
            raise ResearchInputError(f"routing decision must not contain {sorted(found)}")
        return payload


def _capability_unrestricted(capabilities: tuple[str, ...]) -> bool:
    return bool({item.lower() for item in capabilities} & UNRESTRICTED_CAPABILITY_MARKERS)


def _hard_filter(candidate: RuntimeCandidate, request: RoutingRequest) -> RoutingReasonCode | None:
    if candidate.is_strix:
        return RoutingReasonCode.STRIX_IS_NOT_MODEL_RUNTIME
    if candidate.identity.adapter_id in request.attempted_adapter_ids:
        return RoutingReasonCode.ALREADY_ATTEMPTED
    if candidate.operator_prohibited or candidate.identity.adapter_id in request.operator_prohibited_adapter_ids:
        return RoutingReasonCode.OPERATOR_PROHIBITED
    if not candidate.available:
        return RoutingReasonCode.UNAVAILABLE
    if not candidate.authenticated:
        return RoutingReasonCode.MISSING_AUTHENTICATION
    if request.locality is LocalityConstraint.LOCAL_REQUIRED and candidate.locality is not CandidateLocality.LOCAL:
        return RoutingReasonCode.LOCALITY_MISMATCH
    if candidate.identity.runtime_class is RuntimeClass.AGENT_RUNTIME:
        if not request.allow_agent_runtime or request.required_runtime_class is RuntimeClass.INFERENCE_RUNTIME:
            return RoutingReasonCode.AGENT_NOT_PERMITTED_FOR_INFERENCE_ROLE
        if not candidate.allowed_capabilities:
            return RoutingReasonCode.CAPABILITY_EXPOSURE_TOO_BROAD
        if _capability_unrestricted(candidate.allowed_capabilities) or _capability_unrestricted(
            candidate.side_effect_capabilities
        ):
            return RoutingReasonCode.UNRESTRICTED_CAPABILITY
        if candidate.side_effect_capabilities:
            return RoutingReasonCode.SIDE_EFFECT_CAPABILITY_ON_REASONING_PATH
    elif candidate.identity.runtime_class is not request.required_runtime_class:
        return RoutingReasonCode.WRONG_RUNTIME_CLASS
    if _capability_unrestricted(candidate.allowed_capabilities):
        return RoutingReasonCode.UNRESTRICTED_CAPABILITY
    if request.structured_output_required and not candidate.structured_output_compatible:
        return RoutingReasonCode.MISSING_STRUCTURED_OUTPUT
    return None


def _quality_better(left: RuntimeCandidate, right: RuntimeCandidate) -> int:
    """Negative if left is better. Hard-filter survivors only. Not a weighted score."""

    left_obs = left.quality or RuntimeQualityObservation()
    right_obs = right.quality or RuntimeQualityObservation()
    left_key = left_obs.ordered_key()
    right_key = right_obs.ordered_key()
    if left_key != right_key:
        return -1 if left_key < right_key else 1
    if left_obs.latency_ms is not None and right_obs.latency_ms is not None and left_obs.latency_ms != right_obs.latency_ms:
        return -1 if left_obs.latency_ms < right_obs.latency_ms else 1
    if (
        left_obs.cost_amount is not None
        and right_obs.cost_amount is not None
        and left_obs.cost_amount != right_obs.cost_amount
    ):
        return -1 if left_obs.cost_amount < right_obs.cost_amount else 1
    if left.identity.adapter_id != right.identity.adapter_id:
        return -1 if left.identity.adapter_id < right.identity.adapter_id else 1
    return 0


def _decision(
    *,
    outcome: RoutingOutcome,
    request: RoutingRequest,
    reason_codes: tuple[str, ...],
    selected: ModelRuntimeIdentity | None = None,
    considered: tuple[ModelRuntimeIdentity, ...] = (),
    filtered: tuple[FilterRecord, ...] = (),
    runtime_attempts_used: int | None = None,
    fallback_attempts_used: int | None = None,
    attempted_adapter_ids: tuple[str, ...] | None = None,
) -> RuntimeSelectionDecision:
    return RuntimeSelectionDecision(
        outcome=outcome,
        policy_version=ROUTING_POLICY_VERSION,
        role=request.role,
        reason_codes=reason_codes,
        selected_identity=selected,
        considered_identities=considered,
        filtered=filtered,
        attempted_adapter_ids=request.attempted_adapter_ids
        if attempted_adapter_ids is None
        else attempted_adapter_ids,
        runtime_attempts_used=request.runtime_attempts_used
        if runtime_attempts_used is None
        else runtime_attempts_used,
        fallback_attempts_used=request.fallback_attempts_used
        if fallback_attempts_used is None
        else fallback_attempts_used,
    )


def _desired_price_class(request: RoutingRequest) -> ModelPriceClass:
    if request.escalation_reason is not None:
        return ModelPriceClass.EXPENSIVE
    if request.task_class is not None and request.task_class in TASK_PRICE_CLASS_POLICY:
        policy_class = TASK_PRICE_CLASS_POLICY[request.task_class]
        if isinstance(policy_class, ModelPriceClass):
            return policy_class
    return ModelPriceClass.CHEAP


def select_runtime(request: RoutingRequest) -> RuntimeSelectionDecision:
    """Hard filters first, then ordered preference. The model does not vote."""

    considered = tuple(item.identity for item in request.candidates)
    if request.budget.max_runtime_attempts == 0:
        return _decision(
            outcome=RoutingOutcome.UNAVAILABLE,
            request=request,
            reason_codes=(RoutingReasonCode.ZERO_RUNTIME_ALLOWANCE.value,),
            considered=considered,
        )
    if request.runtime_attempts_used >= request.budget.max_runtime_attempts:
        return _decision(
            outcome=RoutingOutcome.UNAVAILABLE,
            request=request,
            reason_codes=(RoutingReasonCode.ATTEMPT_BUDGET_EXHAUSTED.value,),
            considered=considered,
        )
    if not request.candidates:
        return _decision(
            outcome=RoutingOutcome.NO_COMPATIBLE_RUNTIME,
            request=request,
            reason_codes=(RoutingReasonCode.NO_CANDIDATES.value,),
        )

    if request.task_class is not None and request.task_class in TASK_PRICE_CLASS_POLICY:
        policy_class = TASK_PRICE_CLASS_POLICY[request.task_class]
        if policy_class == "none":
            return _decision(
                outcome=RoutingOutcome.BLOCKED_POLICY,
                request=request,
                reason_codes=(RoutingReasonCode.MONITORING_CLASS_DISABLED.value,),
                considered=considered,
            )

    survivors: list[RuntimeCandidate] = []
    filtered: list[FilterRecord] = []
    for candidate in request.candidates:
        reason = _hard_filter(candidate, request)
        if reason is not None:
            filtered.append(FilterRecord(candidate.identity.adapter_id, reason))
            continue
        survivors.append(candidate)

    if not survivors:
        all_unavailable = bool(filtered) and all(
            item.reason_code is RoutingReasonCode.UNAVAILABLE for item in filtered
        )
        outcome = (
            RoutingOutcome.UNAVAILABLE
            if all_unavailable
            else RoutingOutcome.NO_COMPATIBLE_RUNTIME
        )
        codes = tuple(dict.fromkeys(item.reason_code.value for item in filtered)) or (
            RoutingReasonCode.NO_CANDIDATES.value,
        )
        return _decision(
            outcome=outcome,
            request=request,
            reason_codes=codes,
            considered=considered,
            filtered=tuple(filtered),
        )

    desired_class = _desired_price_class(request)
    price_filtered: list[FilterRecord] = []
    price_survivors: list[RuntimeCandidate] = []
    for candidate in survivors:
        if candidate.identity.price_class is not desired_class:
            price_filtered.append(
                FilterRecord(candidate.identity.adapter_id, RoutingReasonCode.WRONG_RUNTIME_CLASS)
            )
            continue
        price_survivors.append(candidate)

    if not price_survivors:
        all_blocked = price_filtered and all(
            item.reason_code is RoutingReasonCode.WRONG_RUNTIME_CLASS for item in price_filtered
        )
        outcome = (
            RoutingOutcome.BLOCKED_POLICY
            if all_blocked and desired_class is ModelPriceClass.EXPENSIVE
            else RoutingOutcome.NO_COMPATIBLE_RUNTIME
        )
        codes = tuple(dict.fromkeys(item.reason_code.value for item in price_filtered)) or (
            RoutingReasonCode.NO_CANDIDATES.value,
        )
        return _decision(
            outcome=outcome,
            request=request,
            reason_codes=codes,
            considered=considered,
            filtered=tuple(filtered + price_filtered),
        )

    survivors = price_survivors
    filtered.extend(price_filtered)

    if request.operator_preference_order:
        order = {adapter_id: index for index, adapter_id in enumerate(request.operator_preference_order)}
        preferred = [item for item in survivors if item.identity.adapter_id in order]
        if preferred:
            chosen = sorted(preferred, key=lambda item: order[item.identity.adapter_id])[0]
            if request.escalation_reason is not None:
                price_reason = RoutingReasonCode.ESCALATION_REQUIRED
            elif desired_class is ModelPriceClass.EXPENSIVE:
                price_reason = RoutingReasonCode.EXPENSIVE_CLASS_SELECTED
            else:
                price_reason = RoutingReasonCode.CHEAP_CLASS_SELECTED
            return _decision(
                outcome=RoutingOutcome.SELECT,
                request=request,
                reason_codes=(
                    RoutingReasonCode.SELECTED_AFTER_HARD_FILTERS.value,
                    RoutingReasonCode.SELECTED_BY_OPERATOR_ORDER.value,
                    price_reason.value,
                ),
                selected=chosen.identity,
                considered=considered,
                filtered=tuple(filtered),
                runtime_attempts_used=request.runtime_attempts_used + 1,
            )

    ranked = sorted(survivors, key=cmp_to_key(_quality_better))
    best = ranked[0]
    tied = [
        item
        for item in ranked
        if item.identity.adapter_id != best.identity.adapter_id
        and _quality_equal(item, best)
    ]
    if tied and request.require_operator_on_tie:
        return _decision(
            outcome=RoutingOutcome.REQUIRE_OPERATOR_SELECTION,
            request=request,
            reason_codes=(RoutingReasonCode.TIE_REQUIRES_OPERATOR.value,),
            considered=tuple(item.identity for item in ranked),
            filtered=tuple(filtered),
        )
    if request.escalation_reason is not None:
        price_reason = RoutingReasonCode.ESCALATION_REQUIRED
    elif desired_class is ModelPriceClass.EXPENSIVE:
        price_reason = RoutingReasonCode.EXPENSIVE_CLASS_SELECTED
    else:
        price_reason = RoutingReasonCode.CHEAP_CLASS_SELECTED
    return _decision(
        outcome=RoutingOutcome.SELECT,
        request=request,
        reason_codes=(
            RoutingReasonCode.SELECTED_AFTER_HARD_FILTERS.value,
            RoutingReasonCode.SELECTED_BY_QUALITY_ORDER.value,
            price_reason.value,
        ),
        selected=best.identity,
        considered=considered,
        filtered=tuple(filtered),
        runtime_attempts_used=request.runtime_attempts_used + 1,
    )


def _quality_equal(left: RuntimeCandidate, right: RuntimeCandidate) -> bool:
    left_obs = left.quality or RuntimeQualityObservation()
    right_obs = right.quality or RuntimeQualityObservation()
    if left_obs.ordered_key() != right_obs.ordered_key():
        return False
    if left_obs.latency_ms is not None and right_obs.latency_ms is not None and left_obs.latency_ms != right_obs.latency_ms:
        return False
    if (
        left_obs.cost_amount is not None
        and right_obs.cost_amount is not None
        and left_obs.cost_amount != right_obs.cost_amount
    ):
        return False
    return True


def reconsider_runtime(
    request: RoutingRequest,
    previous: RuntimeSelectionDecision,
    outcome: RuntimeOutcome,
) -> RuntimeSelectionDecision:
    """Bounded fallback. CONTENT_POLICY_BLOCKED does not hop to evade a safeguard."""

    attempted = previous.attempted_adapter_ids
    if previous.selected_identity is not None:
        attempted = tuple(
            dict.fromkeys((*attempted, previous.selected_identity.adapter_id))
        )
    if outcome is RuntimeOutcome.CONTENT_POLICY_BLOCKED:
        return _decision(
            outcome=RoutingOutcome.BLOCKED_POLICY,
            request=request,
            reason_codes=(RoutingReasonCode.CONTENT_POLICY_BLOCK_NO_BYPASS.value,),
            considered=previous.considered_identities,
            filtered=previous.filtered,
            runtime_attempts_used=previous.runtime_attempts_used,
            fallback_attempts_used=previous.fallback_attempts_used,
            attempted_adapter_ids=attempted,
        )
    if outcome is RuntimeOutcome.ESCALATION_NEEDED:
        escalation_retry = RoutingRequest(
            role=request.role,
            candidates=request.candidates,
            budget=request.budget,
            required_runtime_class=request.required_runtime_class,
            structured_output_required=request.structured_output_required,
            locality=request.locality,
            allow_agent_runtime=request.allow_agent_runtime,
            operator_prohibited_adapter_ids=request.operator_prohibited_adapter_ids,
            operator_preference_order=request.operator_preference_order,
            require_operator_on_tie=request.require_operator_on_tie,
            attempted_adapter_ids=attempted,
            runtime_attempts_used=previous.runtime_attempts_used,
            fallback_attempts_used=previous.fallback_attempts_used,
            task_class=request.task_class,
            escalation_reason="cheap_model_returned_escalation_needed",
        )
        return select_runtime(escalation_retry)
    if request.budget.max_fallback_attempts == 0:
        return _decision(
            outcome=RoutingOutcome.UNAVAILABLE,
            request=request,
            reason_codes=(RoutingReasonCode.ZERO_FALLBACK_ALLOWANCE.value,),
            considered=previous.considered_identities,
            filtered=previous.filtered,
            runtime_attempts_used=previous.runtime_attempts_used,
            fallback_attempts_used=previous.fallback_attempts_used,
        )
    if previous.fallback_attempts_used >= request.budget.max_fallback_attempts:
        return _decision(
            outcome=RoutingOutcome.UNAVAILABLE,
            request=request,
            reason_codes=(RoutingReasonCode.FALLBACK_EXHAUSTED.value,),
            considered=previous.considered_identities,
            filtered=previous.filtered,
            runtime_attempts_used=previous.runtime_attempts_used,
            fallback_attempts_used=previous.fallback_attempts_used,
        )
    retry = RoutingRequest(
        role=request.role,
        candidates=request.candidates,
        budget=request.budget,
        required_runtime_class=request.required_runtime_class,
        structured_output_required=request.structured_output_required,
        locality=request.locality,
        allow_agent_runtime=request.allow_agent_runtime,
        operator_prohibited_adapter_ids=request.operator_prohibited_adapter_ids,
        operator_preference_order=request.operator_preference_order,
        require_operator_on_tie=request.require_operator_on_tie,
        attempted_adapter_ids=attempted,
        runtime_attempts_used=previous.runtime_attempts_used,
        fallback_attempts_used=previous.fallback_attempts_used + 1,
        task_class=request.task_class,
        escalation_reason=request.escalation_reason,
    )
    return select_runtime(retry)
