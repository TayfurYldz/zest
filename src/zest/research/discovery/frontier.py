"""Frontier proposals and reconstructible lifecycle. Not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.discovery.types import (
    FORBIDDEN_DISCOVERY_KEYS,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    DiscoveryGoalKind,
    FrontierEventKind,
    FrontierState,
)
from zest.research.types import ResearchInputError

GOAL_PRIORITY = (
    DiscoveryGoalKind.INSPECT_PATH,
    DiscoveryGoalKind.INSPECT_SPA_PATH,
    DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY,
    DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
    DiscoveryGoalKind.INSPECT_CONTROL,
    DiscoveryGoalKind.RESOLVE_TRANSITION_RESULT,
    DiscoveryGoalKind.RESOLVE_OBJECT_TYPE,
)

LEGAL_TRANSITIONS: dict[FrontierEventKind, frozenset[FrontierEventKind]] = {
    FrontierEventKind.CREATED: frozenset(
        {
            FrontierEventKind.ELIGIBLE,
            FrontierEventKind.BLOCKED_SCOPE,
            FrontierEventKind.BLOCKED_AUTH,
            FrontierEventKind.BLOCKED_BUDGET,
            FrontierEventKind.SUPERSEDED,
        }
    ),
    FrontierEventKind.ELIGIBLE: frozenset(
        {
            FrontierEventKind.SELECTED,
            FrontierEventKind.BLOCKED_SCOPE,
            FrontierEventKind.BLOCKED_AUTH,
            FrontierEventKind.BLOCKED_BUDGET,
            FrontierEventKind.SUPERSEDED,
            FrontierEventKind.NO_NEW_INFORMATION,
        }
    ),
    FrontierEventKind.SELECTED: frozenset(
        {
            FrontierEventKind.OBSERVED,
            FrontierEventKind.NO_NEW_INFORMATION,
            FrontierEventKind.FAILED_TRANSIENT,
            FrontierEventKind.FAILED_TERMINAL,
            FrontierEventKind.AWAITING_REAUTHORIZATION,
            FrontierEventKind.BLOCKED_SCOPE,
            FrontierEventKind.BLOCKED_AUTH,
            FrontierEventKind.BLOCKED_BUDGET,
            FrontierEventKind.SUPERSEDED,
        }
    ),
    FrontierEventKind.FAILED_TRANSIENT: frozenset(
        {
            FrontierEventKind.ELIGIBLE,
            FrontierEventKind.FAILED_TERMINAL,
            FrontierEventKind.SUPERSEDED,
        }
    ),
    FrontierEventKind.AWAITING_REAUTHORIZATION: frozenset(
        {
            FrontierEventKind.ELIGIBLE,
            FrontierEventKind.BLOCKED_AUTH,
            FrontierEventKind.SUPERSEDED,
        }
    ),
    FrontierEventKind.NO_NEW_INFORMATION: frozenset(
        {FrontierEventKind.ELIGIBLE, FrontierEventKind.SUPERSEDED}
    ),
    FrontierEventKind.OBSERVED: frozenset({FrontierEventKind.SUPERSEDED}),
    FrontierEventKind.BLOCKED_SCOPE: frozenset({FrontierEventKind.SUPERSEDED}),
    FrontierEventKind.BLOCKED_AUTH: frozenset(
        {FrontierEventKind.ELIGIBLE, FrontierEventKind.SUPERSEDED}
    ),
    FrontierEventKind.BLOCKED_BUDGET: frozenset({FrontierEventKind.SUPERSEDED}),
    FrontierEventKind.FAILED_TERMINAL: frozenset({FrontierEventKind.SUPERSEDED}),
    FrontierEventKind.SUPERSEDED: frozenset(),
}

TERMINAL_EVENT_KINDS = frozenset(
    {
        FrontierEventKind.OBSERVED,
        FrontierEventKind.BLOCKED_SCOPE,
        FrontierEventKind.BLOCKED_BUDGET,
        FrontierEventKind.FAILED_TERMINAL,
        FrontierEventKind.SUPERSEDED,
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_side_effect(value: object) -> int:
    if value not in (0, 1, 2, 3):
        raise ResearchInputError("budget_class/side_effect must be 0..3")
    return int(value)


@dataclass(frozen=True)
class FrontierItem:
    """Immutable proposed discovery work. Does not grant scope, budget, or session."""

    frontier_id: str
    research_run_id: str
    goal_kind: DiscoveryGoalKind
    candidate_origin: str
    candidate_path: str
    identity_id: str
    proposed_capability: str
    proposed_action: str
    expected_side_effect: int
    budget_class: int
    structural_signature: str
    dedupe_identity: str
    strategy_version: str = SURFACE_DISCOVERY_STRATEGY_VERSION
    session_context_id: str | None = None
    scope_hint: str | None = None
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frontier_id", _require_text(self.frontier_id, "frontier_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.goal_kind, DiscoveryGoalKind):
            raise ResearchInputError("goal_kind must be a DiscoveryGoalKind")
        object.__setattr__(
            self, "candidate_origin", _require_text(self.candidate_origin, "candidate_origin")
        )
        object.__setattr__(
            self, "candidate_path", _require_text(self.candidate_path, "candidate_path")
        )
        object.__setattr__(self, "identity_id", _require_text(self.identity_id, "identity_id"))
        object.__setattr__(
            self,
            "proposed_capability",
            _require_text(self.proposed_capability, "proposed_capability"),
        )
        object.__setattr__(
            self, "proposed_action", _require_text(self.proposed_action, "proposed_action")
        )
        object.__setattr__(self, "expected_side_effect", _require_side_effect(self.expected_side_effect))
        object.__setattr__(self, "budget_class", _require_side_effect(self.budget_class))
        object.__setattr__(
            self,
            "structural_signature",
            _require_text(self.structural_signature, "structural_signature"),
        )
        if self.structural_signature.startswith("el-") or "element_reference" in (
            self.attributes or {}
        ):
            raise ResearchInputError("FrontierItem must not persist ephemeral browser element refs")
        object.__setattr__(
            self, "dedupe_identity", _require_text(self.dedupe_identity, "dedupe_identity")
        )
        object.__setattr__(
            self, "strategy_version", _require_text(self.strategy_version, "strategy_version")
        )
        if self.strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
            raise ResearchInputError("FrontierItem strategy_version must be surface.discovery.v1")
        if self.attributes is not None:
            found = FORBIDDEN_DISCOVERY_KEYS.intersection(self.attributes.keys())
            if found:
                raise ResearchInputError(f"frontier attributes must not contain {sorted(found)}")
            object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class FrontierEvent:
    event_id: str
    frontier_id: str
    research_run_id: str
    event_kind: FrontierEventKind
    sequence: int
    selection_generation: int | None = None
    execution_attempt_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_text(self.event_id, "event_id"))
        object.__setattr__(self, "frontier_id", _require_text(self.frontier_id, "frontier_id"))
        object.__setattr__(
            self, "research_run_id", _require_text(self.research_run_id, "research_run_id")
        )
        if not isinstance(self.event_kind, FrontierEventKind):
            raise ResearchInputError("event_kind must be a FrontierEventKind")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise ResearchInputError("sequence must be a positive int")
        if self.event_kind is FrontierEventKind.SELECTED:
            if not isinstance(self.selection_generation, int) or self.selection_generation < 1:
                raise ResearchInputError("SELECTED requires selection_generation >= 1")
        if self.execution_attempt_id is not None:
            _require_text(self.execution_attempt_id, "execution_attempt_id")


def legal_frontier_transition(
    latest: FrontierEventKind | None, nxt: FrontierEventKind
) -> bool:
    if latest is None:
        return nxt is FrontierEventKind.CREATED
    allowed = LEGAL_TRANSITIONS.get(latest, frozenset())
    return nxt in allowed


def next_selection_generation(events: tuple[FrontierEvent, ...]) -> int:
    selected = [
        item.selection_generation
        for item in events
        if item.event_kind is FrontierEventKind.SELECTED and item.selection_generation is not None
    ]
    return (max(selected) if selected else 0) + 1


def latest_event(events: tuple[FrontierEvent, ...]) -> FrontierEvent | None:
    if not events:
        return None
    return sorted(events, key=lambda item: (item.sequence, item.event_id))[-1]


def reconstruct_state(events: tuple[FrontierEvent, ...]) -> FrontierState | None:
    latest = latest_event(events)
    if latest is None:
        return None
    return FrontierState(latest.event_kind.value)


def select_eligible_frontier(
    items: tuple[FrontierItem, ...],
    events_by_frontier: Mapping[str, tuple[FrontierEvent, ...]],
    *,
    max_side_effect: int = 1,
) -> FrontierItem | None:
    """Deterministic next item. SE2/SE3 are not selected by surface.discovery.v1."""

    ranked: list[tuple[int, int, str, FrontierItem]] = []
    for item in items:
        if item.budget_class > max_side_effect or item.expected_side_effect > max_side_effect:
            continue
        latest = latest_event(events_by_frontier.get(item.frontier_id, ()))
        if latest is None or latest.event_kind is not FrontierEventKind.ELIGIBLE:
            continue
        goal_rank = GOAL_PRIORITY.index(item.goal_kind)
        ranked.append((item.budget_class, goal_rank, item.dedupe_identity, item))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1], row[2], row[3].frontier_id))
    return ranked[0][3]
