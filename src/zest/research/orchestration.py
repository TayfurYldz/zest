"""Bounded autonomous research policy. Not execution authority and not a Finding.

Autonomous != unbounded. A cycle is observe → reason → select → plan →
authorize → execute → evaluate → remember under explicit limits.
The model cannot recursively spawn agents. Finding existence is not an
orchestration state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from zest.research.types import ResearchInputError

ORCHESTRATION_POLICY_VERSION = "orchestration.bounded.v1"

TERMINAL_ORCHESTRATION_STATES = frozenset(
    {
        "COMPLETED",
        "BUDGET_EXHAUSTED",
        "FAILED_OPERATIONAL",
    }
)


class OrchestrationState(Enum):
    """Durable controller state for one ResearchRun. Not a vulnerability verdict."""

    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    COMPLETED = "COMPLETED"
    FAILED_OPERATIONAL = "FAILED_OPERATIONAL"


class StopReason(Enum):
    """Explicit stop. Finding creation is not an automatic stop unless policy says so."""

    COMPLETED_NO_MORE_OPPORTUNITIES = "COMPLETED_NO_MORE_OPPORTUNITIES"
    COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK = (
        "COMPLETED_UNDER_CURRENT_AUTHORITY_WITH_BLOCKED_WORK"
    )
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MAX_CYCLES_REACHED = "MAX_CYCLES_REACHED"
    MAX_DURATION_REACHED = "MAX_DURATION_REACHED"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"
    NO_COMPATIBLE_RUNTIME = "NO_COMPATIBLE_RUNTIME"
    CORE_BLOCKED = "CORE_BLOCKED"
    OPERATOR_PAUSED = "OPERATOR_PAUSED"
    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    OPERATIONAL_FAILURE = "OPERATIONAL_FAILURE"
    UNKNOWN_OUTCOME_REQUIRES_REVIEW = "UNKNOWN_OUTCOME_REQUIRES_REVIEW"
    CONTENT_POLICY_BLOCKED = "CONTENT_POLICY_BLOCKED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    CANCELLED = "CANCELLED"


class OrchestrationPhase(Enum):
    """Durable cycle phase. Not AuditEvent workflow state and not a Finding."""

    CYCLE_READY = "CYCLE_READY"
    OPPORTUNITY_SELECTED = "OPPORTUNITY_SELECTED"
    HYPOTHESIS_ADMITTED = "HYPOTHESIS_ADMITTED"
    EXPERIMENT_PLANNED = "EXPERIMENT_PLANNED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    ATTEMPT_AUTHORIZED = "ATTEMPT_AUTHORIZED"
    DISPATCHING = "DISPATCHING"
    WORKER_RESULT_RECORDED = "WORKER_RESULT_RECORDED"
    TRANSITION_A_COMPLETE = "TRANSITION_A_COMPLETE"
    ASSESSMENT_COMPLETE = "ASSESSMENT_COMPLETE"
    TRANSITION_B_COMPLETE = "TRANSITION_B_COMPLETE"
    CYCLE_COMPLETE = "CYCLE_COMPLETE"


ORCHESTRATION_PHASES = tuple(item.value for item in OrchestrationPhase)


class CycleOutcome(Enum):
    """What the controller should do after one cycle. Not Core ALLOW."""

    CONTINUE = "CONTINUE"
    PAUSE = "PAUSE"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class NextCycleAction(Enum):
    """Policy choice before a cycle body. Not a Worker dispatch."""

    STOP = "STOP"
    BOOTSTRAP_DIAGNOSTIC = "BOOTSTRAP_DIAGNOSTIC"
    USE_SELECTED_OPPORTUNITY = "USE_SELECTED_OPPORTUNITY"
    CONTINUE_SURFACE_DISCOVERY = "CONTINUE_SURFACE_DISCOVERY"
    NO_SELECTION_THIS_TICK = "NO_SELECTION_THIS_TICK"


def _require_non_negative(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ResearchInputError(f"{name} must be a non-negative int; 0 is not unlimited")
    return value


def _require_side_effect_ceiling(value: int) -> int:
    if value not in (0, 1, 2, 3):
        raise ResearchInputError("side_effect_ceiling must be 0, 1, 2, or 3")
    return value


@dataclass(frozen=True)
class OrchestrationBounds:
    """Hard limits for one ResearchRun. 0 = no allowance. Negative is invalid."""

    max_cycles: int
    max_experiments: int
    max_model_calls: int
    max_worker_invocations: int
    max_elapsed_ms: int
    max_selected_opportunities: int
    max_runtime_fallback: int
    side_effect_ceiling: int
    allow_repeated_control_experiments: bool = False

    def __post_init__(self) -> None:
        _require_non_negative("max_cycles", self.max_cycles)
        _require_non_negative("max_experiments", self.max_experiments)
        _require_non_negative("max_model_calls", self.max_model_calls)
        _require_non_negative("max_worker_invocations", self.max_worker_invocations)
        _require_non_negative("max_elapsed_ms", self.max_elapsed_ms)
        _require_non_negative("max_selected_opportunities", self.max_selected_opportunities)
        _require_non_negative("max_runtime_fallback", self.max_runtime_fallback)
        _require_side_effect_ceiling(self.side_effect_ceiling)
        if not isinstance(self.allow_repeated_control_experiments, bool):
            raise ResearchInputError("allow_repeated_control_experiments must be bool")


@dataclass(frozen=True)
class OrchestrationUsage:
    cycles_completed: int
    experiments_executed: int
    model_calls: int
    worker_invocations: int
    elapsed_ms: int
    opportunities_selected: int
    runtime_fallbacks: int
    worker_requests: int = 0
    execution_time_ms: int = 0
    artifact_bytes: int = 0

    def __post_init__(self) -> None:
        _require_non_negative("cycles_completed", self.cycles_completed)
        _require_non_negative("experiments_executed", self.experiments_executed)
        _require_non_negative("model_calls", self.model_calls)
        _require_non_negative("worker_invocations", self.worker_invocations)
        _require_non_negative("elapsed_ms", self.elapsed_ms)
        _require_non_negative("opportunities_selected", self.opportunities_selected)
        _require_non_negative("runtime_fallbacks", self.runtime_fallbacks)
        _require_non_negative("worker_requests", self.worker_requests)
        _require_non_negative("execution_time_ms", self.execution_time_ms)
        _require_non_negative("artifact_bytes", self.artifact_bytes)


@dataclass(frozen=True)
class BoundCheck:
    allowed: bool
    stop_reason: StopReason | None
    reason: str


def check_orchestration_bounds(
    bounds: OrchestrationBounds,
    usage: OrchestrationUsage,
) -> BoundCheck:
    """Hard-stop evaluation. Does not authorize execution."""

    if not isinstance(bounds, OrchestrationBounds):
        raise ResearchInputError("bounds is required")
    if not isinstance(usage, OrchestrationUsage):
        raise ResearchInputError("usage is required")
    if usage.cycles_completed >= bounds.max_cycles:
        return BoundCheck(False, StopReason.MAX_CYCLES_REACHED, "max_cycles reached")
    if usage.elapsed_ms >= bounds.max_elapsed_ms:
        return BoundCheck(False, StopReason.MAX_DURATION_REACHED, "max_elapsed_ms reached")
    if usage.experiments_executed >= bounds.max_experiments:
        return BoundCheck(False, StopReason.BUDGET_EXHAUSTED, "max_experiments reached")
    if usage.model_calls >= bounds.max_model_calls:
        return BoundCheck(False, StopReason.BUDGET_EXHAUSTED, "max_model_calls reached")
    if usage.worker_invocations >= bounds.max_worker_invocations:
        return BoundCheck(False, StopReason.BUDGET_EXHAUSTED, "max_worker_invocations reached")
    if usage.runtime_fallbacks > bounds.max_runtime_fallback:
        return BoundCheck(False, StopReason.NO_COMPATIBLE_RUNTIME, "runtime fallback exhausted")
    return BoundCheck(True, None, "within bounds")


def next_cycle_action(
    *,
    bounds: OrchestrationBounds,
    usage: OrchestrationUsage,
    selected_count: int,
    hypothesis_count: int,
    unknown_outcome_open: bool,
    runnable_discovery_frontier_count: int = 0,
) -> tuple[NextCycleAction, StopReason | None]:
    """Choose the next bounded action. Does not dispatch a Worker.

    `selected_count == 0` is no selection this tick, not research exhaustion.
    Runnable discovery frontier remains discovery-owned work.
    Global exhaustion is decided by the work inventory, not this function.
    """

    if unknown_outcome_open:
        return NextCycleAction.STOP, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW
    bound = check_orchestration_bounds(bounds, usage)
    if not bound.allowed:
        return NextCycleAction.STOP, bound.stop_reason
    if runnable_discovery_frontier_count > 0:
        return NextCycleAction.CONTINUE_SURFACE_DISCOVERY, None
    if selected_count > 0:
        if selected_count > bounds.max_selected_opportunities:
            return NextCycleAction.STOP, StopReason.BUDGET_EXHAUSTED
        return NextCycleAction.USE_SELECTED_OPPORTUNITY, None
    if hypothesis_count == 0:
        return NextCycleAction.BOOTSTRAP_DIAGNOSTIC, None
    if bounds.allow_repeated_control_experiments:
        return NextCycleAction.BOOTSTRAP_DIAGNOSTIC, None
    return NextCycleAction.NO_SELECTION_THIS_TICK, None


def cycle_outcome_for_stop(reason: StopReason) -> CycleOutcome:
    if reason is StopReason.REQUIRE_HUMAN_REVIEW:
        return CycleOutcome.REQUIRE_HUMAN_REVIEW
    if reason is StopReason.OPERATOR_PAUSED:
        return CycleOutcome.PAUSE
    if reason is StopReason.CANCELLED:
        return CycleOutcome.COMPLETE
    if reason in {
        StopReason.CORE_BLOCKED,
        StopReason.NO_COMPATIBLE_RUNTIME,
        StopReason.CONTENT_POLICY_BLOCKED,
        StopReason.OPERATIONAL_FAILURE,
        StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
        StopReason.AUTH_REQUIRED,
        StopReason.RATE_LIMITED,
    }:
        return CycleOutcome.BLOCKED
    return CycleOutcome.COMPLETE


def orchestration_state_for_stop(reason: StopReason) -> OrchestrationState:
    if reason is StopReason.BUDGET_EXHAUSTED:
        return OrchestrationState.BUDGET_EXHAUSTED
    if reason is StopReason.REQUIRE_HUMAN_REVIEW:
        return OrchestrationState.WAITING_HUMAN
    if reason is StopReason.OPERATOR_PAUSED:
        return OrchestrationState.PAUSED
    if reason in {
        StopReason.OPERATIONAL_FAILURE,
        StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW,
    }:
        return OrchestrationState.FAILED_OPERATIONAL
    if reason is StopReason.CANCELLED:
        return OrchestrationState.COMPLETED
    if reason in {
        StopReason.CORE_BLOCKED,
        StopReason.NO_COMPATIBLE_RUNTIME,
        StopReason.CONTENT_POLICY_BLOCKED,
        StopReason.AUTH_REQUIRED,
        StopReason.RATE_LIMITED,
    }:
        return OrchestrationState.BLOCKED
    return OrchestrationState.COMPLETED


IMMUTABLE_ORCHESTRATION_CONFIG_KEYS = (
    "research_run_id",
    "budget_id",
    "target_reference",
    "research_question",
    "policy_version",
    "routing_policy_version",
    "scope_fingerprint",
    "max_cycles",
    "max_experiments",
    "max_model_calls",
    "max_worker_invocations",
    "max_elapsed_ms",
    "max_selected_opportunities",
    "max_runtime_fallback",
    "side_effect_ceiling",
    "allow_repeated_control_experiments",
)


def canonical_orchestration_config(payload: Mapping[str, object]) -> dict[str, object]:
    """Deterministic immutable control payload. No secrets."""

    missing = [key for key in IMMUTABLE_ORCHESTRATION_CONFIG_KEYS if key not in payload]
    if missing:
        raise ResearchInputError(f"orchestration config missing keys: {missing}")
    canonical: dict[str, object] = {}
    for key in IMMUTABLE_ORCHESTRATION_CONFIG_KEYS:
        canonical[key] = payload[key]
    return canonical


def orchestration_config_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = canonical_orchestration_config(payload)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bounds_from_config(payload: Mapping[str, object]) -> OrchestrationBounds:
    canonical = canonical_orchestration_config(payload)
    return OrchestrationBounds(
        max_cycles=int(canonical["max_cycles"]),
        max_experiments=int(canonical["max_experiments"]),
        max_model_calls=int(canonical["max_model_calls"]),
        max_worker_invocations=int(canonical["max_worker_invocations"]),
        max_elapsed_ms=int(canonical["max_elapsed_ms"]),
        max_selected_opportunities=int(canonical["max_selected_opportunities"]),
        max_runtime_fallback=int(canonical["max_runtime_fallback"]),
        side_effect_ceiling=int(canonical["side_effect_ceiling"]),
        allow_repeated_control_experiments=bool(
            canonical["allow_repeated_control_experiments"]
        ),
    )

