"""GATE 22 surface discovery. Research proposes; Core authorizes; Workers execute.

AttackSurfaceGraph is a rebuildable projection. FrontierItem is not authorization.
"""

from zest.research.discovery.canonical import (
    UUID_SEGMENT_RE,
    admit_route_templates,
    canonical_key,
    instance_token_from_segment,
    path_segments,
    route_template_from_paths,
)
from zest.research.discovery.config import (
    DiscoveryBounds,
    DiscoveryRunConfig,
    discovery_config_fingerprint,
)
from zest.research.discovery.context_pack import pack_surface_discovery_context
from zest.research.discovery.control_resolve import (
    ControlResolution,
    ControlResolutionOutcome,
    DurableControlSignature,
    LiveControlView,
    resolve_control_ref,
)
from zest.research.discovery.facts import (
    DiscoveryFact,
    DiscoveryFactSourceView,
    object_instance_from_numeric_path_rejected,
)
from zest.research.discovery.frontier import (
    FrontierEvent,
    FrontierItem,
    legal_frontier_transition,
    next_selection_generation,
    select_eligible_frontier,
)
from zest.research.discovery.graph import (
    AttackSurfaceEdge,
    AttackSurfaceGraph,
    AttackSurfaceNode,
    rebuild_attack_surface_graph,
)
from zest.research.discovery.inference import (
    DiscoveryInference,
    DiscoveryInferenceDecision,
    DiscoveryInferenceDraft,
    admit_discovery_inference,
)
from zest.research.discovery.projection import (
    ProjectionDelta,
    project_control_event,
    project_observation_view,
    seed_inspect_path_frontier,
)
from zest.research.discovery.selection import (
    SurfaceDiscoveryOpportunity,
    SurfaceDiscoverySelectionDecision,
    select_surface_discovery_opportunities,
)
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    ControlEventKind,
    DiscoveryFactKind,
    DiscoveryGoalKind,
    DiscoveryInferenceKind,
    DiscoverySourcePlane,
    FrontierEventKind,
    FrontierState,
)
from zest.research.target_model import TargetEpistemicStatus

__all__ = [
    "ANONYMOUS_IDENTITY_ID",
    "AttackSurfaceEdge",
    "AttackSurfaceGraph",
    "AttackSurfaceNode",
    "ControlEventKind",
    "ControlResolution",
    "DurableControlSignature",
    "DiscoveryBounds",
    "DiscoveryFact",
    "DiscoveryFactKind",
    "DiscoveryFactSourceView",
    "DiscoveryGoalKind",
    "DiscoveryInference",
    "DiscoveryInferenceDecision",
    "DiscoveryInferenceDraft",
    "DiscoveryInferenceKind",
    "DiscoveryRunConfig",
    "DiscoverySourcePlane",
    "DurableControlSignature",
    "FrontierEvent",
    "FrontierEventKind",
    "FrontierItem",
    "FrontierState",
    "LiveControlView",
    "ProjectionDelta",
    "SURFACE_DISCOVERY_STRATEGY_VERSION",
    "SurfaceDiscoveryOpportunity",
    "SurfaceDiscoverySelectionDecision",
    "TargetEpistemicStatus",
    "UUID_SEGMENT_RE",
    "admit_discovery_inference",
    "admit_route_templates",
    "canonical_key",
    "discovery_config_fingerprint",
    "instance_token_from_segment",
    "legal_frontier_transition",
    "next_selection_generation",
    "object_instance_from_numeric_path_rejected",
    "pack_surface_discovery_context",
    "path_segments",
    "project_control_event",
    "project_observation_view",
    "rebuild_attack_surface_graph",
    "resolve_control_ref",
    "route_template_from_paths",
    "seed_inspect_path_frontier",
    "select_eligible_frontier",
    "select_surface_discovery_opportunities",
]
