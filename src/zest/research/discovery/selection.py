"""Surface discovery selection. Isolated from exploration.diagnostic.echo.v1."""

from __future__ import annotations

from dataclasses import dataclass

from zest.research.discovery.frontier import FrontierItem
from zest.research.discovery.types import (
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    DiscoveryGoalKind,
)
from zest.research.exploration import (
    NegativeKnowledge,
    OpportunityKind,
    SelectionOutcome,
)
from zest.research.types import ResearchInputError


@dataclass(frozen=True)
class SurfaceDiscoveryOpportunity:
    """Next surface-discovery action. Not Core ALLOW and not a vulnerability claim."""

    opportunity_id: str
    research_run_id: str
    frontier_id: str
    goal_kind: DiscoveryGoalKind
    strategy_version: str
    structural_identity: str
    context_signature: str
    proposed_direction: str
    identity_id: str
    candidate_origin: str
    candidate_path: str
    budget_class: int

    def __post_init__(self) -> None:
        if self.strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
            raise ResearchInputError("surface discovery opportunity must use surface.discovery.v1")
        lowered = self.proposed_direction.lower()
        if "vulnerability" in lowered or "exploit" in lowered:
            raise ResearchInputError("opportunity must not claim a vulnerability or exploit")


@dataclass(frozen=True)
class SurfaceDiscoverySelectionDecision:
    outcome: SelectionOutcome
    reason_codes: tuple[str, ...]
    opportunity: SurfaceDiscoveryOpportunity

    @property
    def selected(self) -> bool:
        return self.outcome is SelectionOutcome.SELECT


def opportunity_from_frontier(item: FrontierItem) -> SurfaceDiscoveryOpportunity:
    return SurfaceDiscoveryOpportunity(
        opportunity_id=item.frontier_id,
        research_run_id=item.research_run_id,
        frontier_id=item.frontier_id,
        goal_kind=item.goal_kind,
        strategy_version=item.strategy_version,
        structural_identity=item.dedupe_identity,
        context_signature=f"{item.identity_id}:{item.candidate_origin}{item.candidate_path}",
        proposed_direction=f"Observe {item.goal_kind.value} at {item.candidate_path}",
        identity_id=item.identity_id,
        candidate_origin=item.candidate_origin,
        candidate_path=item.candidate_path,
        budget_class=item.budget_class,
    )


def select_surface_discovery_opportunities(
    opportunities: tuple[SurfaceDiscoveryOpportunity, ...],
    *,
    research_run_id: str,
    strategy_version: str,
    negative_knowledge: tuple[NegativeKnowledge, ...] = (),
    previously_selected_identities: frozenset[str] = frozenset(),
    max_selected: int = 1,
) -> tuple[SurfaceDiscoverySelectionDecision, ...]:
    """Deterministic SE0/SE1 surface selection. Does not execute or authorize."""

    if strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
        raise ResearchInputError("select_surface_discovery_opportunities requires surface.discovery.v1")
    if not isinstance(research_run_id, str) or not research_run_id.strip():
        raise ResearchInputError("research_run_id must be a non-empty string")
    decisions: list[SurfaceDiscoverySelectionDecision] = []
    selected = 0
    seen = set(previously_selected_identities)
    ordered = sorted(
        opportunities,
        key=lambda item: (
            item.budget_class,
            _goal_rank(item.goal_kind),
            0 if item.identity_id == "ANONYMOUS" else 1,
            item.structural_identity,
            item.opportunity_id,
        ),
    )
    for opportunity in ordered:
        if opportunity.research_run_id != research_run_id:
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.BLOCKED_POLICY, ("CROSS_RUN",), opportunity
                )
            )
            continue
        if opportunity.strategy_version != SURFACE_DISCOVERY_STRATEGY_VERSION:
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.BLOCKED_POLICY, ("STRATEGY_MISMATCH",), opportunity
                )
            )
            continue
        if opportunity.budget_class >= 2:
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.DEFER, ("SE2_SE3_NOT_SELECTED",), opportunity
                )
            )
            continue
        if opportunity.structural_identity in seen:
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.SKIP_DUPLICATE, ("ALREADY_SELECTED",), opportunity
                )
            )
            continue
        if _same_context_negative(opportunity, negative_knowledge):
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.BLOCKED_POLICY, ("CONTEXT_BOUND_NEGATIVE",), opportunity
                )
            )
            continue
        if selected >= max_selected or max_selected == 0:
            decisions.append(
                SurfaceDiscoverySelectionDecision(
                    SelectionOutcome.DEFER, ("SELECTION_BUDGET",), opportunity
                )
            )
            continue
        decisions.append(
            SurfaceDiscoverySelectionDecision(SelectionOutcome.SELECT, ("SELECTED",), opportunity)
        )
        seen.add(opportunity.structural_identity)
        selected += 1
    return tuple(decisions)


def _goal_rank(kind: DiscoveryGoalKind) -> int:
    order = (
        DiscoveryGoalKind.INSPECT_PATH,
        DiscoveryGoalKind.INSPECT_SPA_PATH,
        DiscoveryGoalKind.OBSERVE_UNDER_IDENTITY,
        DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
        DiscoveryGoalKind.INSPECT_CONTROL,
        DiscoveryGoalKind.RESOLVE_TRANSITION_RESULT,
        DiscoveryGoalKind.RESOLVE_OBJECT_TYPE,
    )
    return order.index(kind)


def _same_context_negative(
    opportunity: SurfaceDiscoveryOpportunity, negatives: tuple[NegativeKnowledge, ...]
) -> bool:
    for item in negatives:
        if (
            item.structural_identity == opportunity.structural_identity
            and item.context_signature == opportunity.context_signature
            and item.strategy_version == SURFACE_DISCOVERY_STRATEGY_VERSION
        ):
            return True
    return False


# Keep OpportunityKind import referenced so G17 kind remains a distinct identity.
SURFACE_DISCOVERY_OPPORTUNITY_KIND = OpportunityKind
