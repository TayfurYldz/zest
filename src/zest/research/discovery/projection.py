"""Deterministic projection of one Observation or ControlEvent into facts/frontier.

Does not persist, authorize, or dispatch. Does not create Findings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from zest.research.discovery.canonical import (
    canonical_key,
    instance_token_from_segment,
    path_segments,
)
from zest.research.discovery.config import DiscoveryRunConfig
from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.frontier import FrontierEvent, FrontierItem
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    ControlEventKind,
    DiscoveryFactKind,
    DiscoveryGoalKind,
    DiscoverySourcePlane,
    FrontierEventKind,
)
from zest.research.target_model import TargetEpistemicStatus
from zest.research.types import ResearchInputError

BROWSER_PAGE_CAPABILITY = "browser.page"
BROWSER_PAGE_OBSERVE_ACTION = "observe"
HTTP_TRANSACTION_CAPABILITY = "http.transaction"
HTTP_TRANSACTION_READ_ACTION = "read"


@dataclass(frozen=True)
class ControlView:
    tag: str
    name: str
    role: str
    input_type: str
    href_scheme: str = ""
    href_origin: str = ""
    href_path: str = ""


@dataclass(frozen=True)
class NetworkEventView:
    event_id: str
    method: str
    path: str
    normalized_target: str
    redirect: bool
    representability: str
    status_code: int | None = None


@dataclass(frozen=True)
class ObservationView:
    observation_id: str
    research_run_id: str
    observation_kind: str
    identity_id: str
    target_reference: str
    normalized_origin: str
    normalized_path: str
    worker_result_id: str
    session_context_id: str | None = None
    http_method: str | None = None
    snapshot_fingerprint: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    json_value_kind: str | None = None
    json_top_level_keys: tuple[str, ...] = ()
    controls: tuple[ControlView, ...] = ()
    network_events: tuple[NetworkEventView, ...] = ()
    form_known: bool = False
    main_observation_present: bool = True
    containment_degraded: bool = False
    blocked_external_dependency_count: int = 0
    coverage_complete: bool = True


@dataclass(frozen=True)
class ControlEventView:
    control_event_id: str
    research_run_id: str
    event_kind: ControlEventKind
    identity_id: str
    target_reference: str
    worker_result_id: str
    normalized_origin: str | None = None
    location_origin: str | None = None
    location_path: str | None = None
    session_context_id: str | None = None


@dataclass(frozen=True)
class WorkflowCausalBinding:
    pre_state_fact_id: str
    experiment_plan_id: str
    execution_attempt_id: str
    actor_identity_id: str
    post_observation_id: str
    object_handle: str | None = None


@dataclass(frozen=True)
class ProposedFact:
    fact: DiscoveryFact
    is_new_semantic: bool


@dataclass(frozen=True)
class ProjectionDelta:
    facts: tuple[ProposedFact, ...]
    frontier_items: tuple[FrontierItem, ...]
    frontier_events: tuple[FrontierEvent, ...]
    workflow_transition_ready: bool
    resolve_transition_frontier: FrontierItem | None


def seed_inspect_path_frontier(config: DiscoveryRunConfig, *, frontier_id: str) -> FrontierItem:
    """Seed does not create an OBSERVED DiscoveryFact."""

    signature = canonical_key(
        "INSPECT_PATH",
        config.normalized_origin,
        config.normalized_path,
        ANONYMOUS_IDENTITY_ID,
    )
    return FrontierItem(
        frontier_id=frontier_id,
        research_run_id=config.research_run_id,
        goal_kind=DiscoveryGoalKind.INSPECT_PATH,
        candidate_origin=config.normalized_origin,
        candidate_path=config.normalized_path,
        identity_id=ANONYMOUS_IDENTITY_ID,
        proposed_capability=BROWSER_PAGE_CAPABILITY,
        proposed_action=BROWSER_PAGE_OBSERVE_ACTION,
        expected_side_effect=0,
        budget_class=0,
        structural_signature=signature,
        dedupe_identity=signature,
        strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
        scope_hint="in_scope_seed",
    )


def project_observation_view(
    view: ObservationView,
    *,
    existing_canonical_keys: frozenset[str],
    fact_id_for_key: Mapping[str, str],
    allocate_id,
    existing_frontier_dedupes: frozenset[str] = frozenset(),
    workflow: WorkflowCausalBinding | None = None,
) -> ProjectionDelta:
    source = DiscoveryFactSourceView(
        source_plane=DiscoverySourcePlane.OBSERVATION,
        observation_id=view.observation_id,
    )
    facts: list[ProposedFact] = []
    frontier: list[FrontierItem] = []
    events: list[FrontierEvent] = []

    def _fact(
        kind: DiscoveryFactKind,
        key: str,
        *,
        epistemic: TargetEpistemicStatus,
        origin: str | None = None,
        path: str | None = None,
        method: str | None = None,
        attributes: dict[str, Any] | None = None,
        fact_id: str | None = None,
    ) -> DiscoveryFact:
        resolved_id = fact_id or fact_id_for_key.get(key) or allocate_id(f"fact:{key}")
        return DiscoveryFact(
            fact_id=resolved_id,
            research_run_id=view.research_run_id,
            fact_kind=kind,
            canonical_key=key,
            epistemic_status=epistemic,
            identity_id=view.identity_id,
            target_reference=view.target_reference,
            sources=(source,),
            session_context_id=view.session_context_id,
            normalized_origin=origin,
            normalized_path=path,
            http_method=method,
            attributes=attributes,
        )

    def _offer(fact: DiscoveryFact) -> None:
        if any(item.fact.canonical_key == fact.canonical_key for item in facts):
            return
        facts.append(
            ProposedFact(fact=fact, is_new_semantic=fact.canonical_key not in existing_canonical_keys)
        )

    origin_key = canonical_key("ORIGIN", view.normalized_origin)
    _offer(
        _fact(
            DiscoveryFactKind.ORIGIN,
            origin_key,
            epistemic=TargetEpistemicStatus.OBSERVED,
            origin=view.normalized_origin,
            path="/",
        )
    )
    path_key = canonical_key("EXACT_PATH", view.normalized_origin, view.normalized_path)
    _offer(
        _fact(
            DiscoveryFactKind.EXACT_PATH,
            path_key,
            epistemic=TargetEpistemicStatus.OBSERVED,
            origin=view.normalized_origin,
            path=view.normalized_path,
        )
    )

    if view.observation_kind in {"BROWSER_PAGE_STATE", "HTTP_TRANSACTION"} and view.http_method:
        op_key = canonical_key(
            "HTTP_OPERATION", view.normalized_origin, view.http_method, view.normalized_path
        )
        _offer(
            _fact(
                DiscoveryFactKind.HTTP_OPERATION,
                op_key,
                epistemic=TargetEpistemicStatus.OBSERVED,
                origin=view.normalized_origin,
                path=view.normalized_path,
                method=view.http_method,
            )
        )

    if view.observation_kind == "BROWSER_PAGE_STATE":
        page_key = canonical_key(
            "PAGE_STATE",
            view.normalized_origin,
            view.normalized_path,
            view.snapshot_fingerprint or "",
        )
        _offer(
            _fact(
                DiscoveryFactKind.PAGE_STATE,
                page_key,
                epistemic=TargetEpistemicStatus.OBSERVED,
                origin=view.normalized_origin,
                path=view.normalized_path,
                attributes=_page_state_attributes(view),
            )
        )
        workflow_key = canonical_key(
            "WORKFLOW_STATE", view.normalized_origin, view.normalized_path, view.identity_id
        )
        _offer(
            _fact(
                DiscoveryFactKind.WORKFLOW_STATE,
                workflow_key,
                epistemic=TargetEpistemicStatus.OBSERVED,
                origin=view.normalized_origin,
                path=view.normalized_path,
            )
        )
        for control in view.controls:
            control_key = canonical_key(
                "CONTROL",
                view.normalized_origin,
                view.normalized_path,
                control.tag,
                control.name,
                control.role,
                control.input_type,
            )
            _offer(
                _fact(
                    DiscoveryFactKind.CONTROL,
                    control_key,
                    epistemic=TargetEpistemicStatus.OBSERVED,
                    origin=view.normalized_origin,
                    path=view.normalized_path,
                    attributes={
                        "tag": control.tag,
                        "name": control.name,
                        "role": control.role,
                        "input_type": control.input_type,
                        **({"href_path": control.href_path} if control.href_path else {}),
                        **({"href_origin": control.href_origin} if control.href_origin else {}),
                    },
                )
            )
            boundary = _scope_boundary_from_control(view, control)
            if boundary is not None:
                boundary_origin, boundary_path, boundary_kind = boundary
                boundary_key = canonical_key(
                    "SCOPE_BOUNDARY_CANDIDATE",
                    boundary_kind,
                    boundary_origin or "",
                    boundary_path or "",
                    control_key,
                )
                _offer(
                    _fact(
                        DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE,
                        boundary_key,
                        epistemic=TargetEpistemicStatus.DERIVED,
                        origin=boundary_origin,
                        path=boundary_path,
                        attributes={
                            "control_event_kind": boundary_kind,
                            "not_observed": True,
                            "source_fact_kind": DiscoveryFactKind.CONTROL.value,
                        },
                    )
                )
            inspect_sig = canonical_key("INSPECT_CONTROL", control_key, view.identity_id)
            if inspect_sig not in existing_frontier_dedupes:
                item = FrontierItem(
                    frontier_id=allocate_id(inspect_sig),
                    research_run_id=view.research_run_id,
                    goal_kind=DiscoveryGoalKind.INSPECT_CONTROL,
                    candidate_origin=view.normalized_origin,
                    candidate_path=view.normalized_path,
                    identity_id=view.identity_id,
                    proposed_capability=BROWSER_PAGE_CAPABILITY,
                    proposed_action="interact",
                    expected_side_effect=1,
                    budget_class=1,
                    structural_signature=control_key,
                    dedupe_identity=inspect_sig,
                    scope_hint="in_scope_observed_control",
                    attributes={
                        "tag": control.tag,
                        "name": control.name,
                        "role": control.role,
                        "input_type": control.input_type,
                        **({"href_path": control.href_path} if control.href_path else {}),
                        **({"href_origin": control.href_origin} if control.href_origin else {}),
                    },
                )
                frontier.append(item)
        if view.form_known:
            form_key = canonical_key("FORM", view.normalized_origin, view.normalized_path)
            _offer(
                _fact(
                    DiscoveryFactKind.FORM,
                    form_key,
                    epistemic=TargetEpistemicStatus.OBSERVED,
                    origin=view.normalized_origin,
                    path=view.normalized_path,
                )
            )
        for event in view.network_events:
            if not _network_event_same_origin(view.normalized_origin, event.normalized_target):
                boundary_origin = _origin_of_target(event.normalized_target)
                boundary_key = canonical_key(
                    "SCOPE_BOUNDARY_CANDIDATE",
                    "OFF_ORIGIN_NETWORK_EVENT",
                    boundary_origin or event.normalized_target or event.path,
                    event.method,
                    event.path,
                    event.event_id,
                )
                _offer(
                    _fact(
                        DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE,
                        boundary_key,
                        epistemic=TargetEpistemicStatus.DERIVED,
                        origin=boundary_origin,
                        path=event.path,
                        method=event.method,
                        attributes={
                            "not_observed": True,
                            "source": "off_origin_network_event",
                            "main_observation_preserved": True,
                            "event_id": event.event_id,
                        },
                    )
                )
                continue
            op_key = canonical_key(
                "HTTP_OPERATION", view.normalized_origin, event.method, event.path
            )
            _offer(
                _fact(
                    DiscoveryFactKind.HTTP_OPERATION,
                    op_key,
                    epistemic=TargetEpistemicStatus.OBSERVED,
                    origin=view.normalized_origin,
                    path=event.path,
                    method=event.method,
                    attributes={"source": "browser_network_event", "event_id": event.event_id},
                )
            )
            event_path_key = canonical_key("EXACT_PATH", view.normalized_origin, event.path)
            _offer(
                _fact(
                    DiscoveryFactKind.EXACT_PATH,
                    event_path_key,
                    epistemic=TargetEpistemicStatus.OBSERVED,
                    origin=view.normalized_origin,
                    path=event.path,
                )
            )
            token = _instance_from_path(event.path)
            if token:
                inst_key = canonical_key(
                    "RESOURCE_INSTANCE_CANDIDATE", view.normalized_origin, event.path, token
                )
                _offer(
                    _fact(
                        DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE,
                        inst_key,
                        epistemic=TargetEpistemicStatus.DERIVED,
                        origin=view.normalized_origin,
                        path=event.path,
                        attributes={"instance_token": token},
                    )
                )
            char_sig = canonical_key(
                "CHARACTERIZE_HTTP_OPERATION", view.normalized_origin, event.method, event.path
            )
            if char_sig in existing_frontier_dedupes:
                continue
            item = FrontierItem(
                frontier_id=allocate_id(char_sig),
                research_run_id=view.research_run_id,
                goal_kind=DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION,
                candidate_origin=view.normalized_origin,
                candidate_path=event.path,
                identity_id=view.identity_id,
                proposed_capability=HTTP_TRANSACTION_CAPABILITY,
                proposed_action=HTTP_TRANSACTION_READ_ACTION
                if event.method in {"GET", "HEAD", "OPTIONS"}
                else "mutate",
                expected_side_effect=0 if event.method in {"GET", "HEAD", "OPTIONS"} else 1,
                budget_class=0 if event.method in {"GET", "HEAD", "OPTIONS"} else 1,
                structural_signature=char_sig,
                dedupe_identity=char_sig,
                scope_hint="in_scope_network_event",
                attributes={
                    "method": event.method,
                    "browser_event_id": event.event_id,
                    "auto_replay": False,
                },
            )
            frontier.append(item)

    if view.observation_kind == "HTTP_TRANSACTION":
        shape_key = canonical_key(
            "RESPONSE_SHAPE",
            view.normalized_origin,
            view.http_method or "",
            view.normalized_path,
            str(view.status_code or ""),
            view.content_type or "",
            view.json_value_kind or "",
        )
        _offer(
            _fact(
                DiscoveryFactKind.RESPONSE_SHAPE,
                shape_key,
                epistemic=TargetEpistemicStatus.OBSERVED,
                origin=view.normalized_origin,
                path=view.normalized_path,
                method=view.http_method,
                attributes={
                    "status_code": view.status_code,
                    "content_type": view.content_type,
                    "json_value_kind": view.json_value_kind,
                    "json_top_level_keys": list(view.json_top_level_keys),
                },
            )
        )
        token = _instance_from_path(view.normalized_path)
        if token:
            inst_key = canonical_key(
                "RESOURCE_INSTANCE_CANDIDATE",
                view.normalized_origin,
                view.normalized_path,
                token,
            )
            _offer(
                _fact(
                    DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE,
                    inst_key,
                    epistemic=TargetEpistemicStatus.DERIVED,
                    origin=view.normalized_origin,
                    path=view.normalized_path,
                    attributes={"instance_token": token},
                )
            )

    token = _instance_from_path(view.normalized_path)
    if token and view.observation_kind == "BROWSER_PAGE_STATE":
        inst_key = canonical_key(
            "RESOURCE_INSTANCE_CANDIDATE",
            view.normalized_origin,
            view.normalized_path,
            token,
        )
        _offer(
            _fact(
                DiscoveryFactKind.RESOURCE_INSTANCE_CANDIDATE,
                inst_key,
                epistemic=TargetEpistemicStatus.DERIVED,
                origin=view.normalized_origin,
                path=view.normalized_path,
                attributes={"instance_token": token},
            )
        )

    transition_ready = False
    resolve_item = None
    if view.observation_kind == "BROWSER_PAGE_STATE":
        if workflow is not None and _workflow_complete(workflow, view):
            transition_ready = True
            trans_key = canonical_key(
                "WORKFLOW_TRANSITION",
                workflow.pre_state_fact_id,
                workflow.execution_attempt_id,
                view.observation_id,
            )
            _offer(
                _fact(
                    DiscoveryFactKind.WORKFLOW_TRANSITION,
                    trans_key,
                    epistemic=TargetEpistemicStatus.DERIVED,
                    origin=view.normalized_origin,
                    path=view.normalized_path,
                    attributes={
                        "pre_state_fact_id": workflow.pre_state_fact_id,
                        "experiment_plan_id": workflow.experiment_plan_id,
                        "execution_attempt_id": workflow.execution_attempt_id,
                        "post_observation_id": view.observation_id,
                    },
                )
            )
        elif workflow is not None and not _workflow_complete(workflow, view):
            resolve_sig = canonical_key(
                "RESOLVE_TRANSITION_RESULT",
                view.normalized_origin,
                view.normalized_path,
                view.identity_id,
            )
            if resolve_sig not in existing_frontier_dedupes:
                resolve_item = FrontierItem(
                    frontier_id=allocate_id(resolve_sig),
                    research_run_id=view.research_run_id,
                    goal_kind=DiscoveryGoalKind.RESOLVE_TRANSITION_RESULT,
                    candidate_origin=view.normalized_origin,
                    candidate_path=view.normalized_path,
                    identity_id=view.identity_id,
                    proposed_capability=BROWSER_PAGE_CAPABILITY,
                    proposed_action=BROWSER_PAGE_OBSERVE_ACTION,
                    expected_side_effect=0,
                    budget_class=0,
                    structural_signature=resolve_sig,
                    dedupe_identity=resolve_sig,
                    scope_hint="in_scope_transition_unbound",
                )
                frontier.append(resolve_item)

    for item in frontier:
        events.append(
            FrontierEvent(
                event_id=allocate_id(f"created:{item.frontier_id}"),
                frontier_id=item.frontier_id,
                research_run_id=view.research_run_id,
                event_kind=FrontierEventKind.CREATED,
                sequence=1,
            )
        )
        events.append(
            FrontierEvent(
                event_id=allocate_id(f"eligible:{item.frontier_id}"),
                frontier_id=item.frontier_id,
                research_run_id=view.research_run_id,
                event_kind=FrontierEventKind.ELIGIBLE,
                sequence=2,
            )
        )

    return ProjectionDelta(
        facts=tuple(facts),
        frontier_items=tuple(frontier),
        frontier_events=tuple(events),
        workflow_transition_ready=transition_ready,
        resolve_transition_frontier=resolve_item if not transition_ready else None,
    )


def project_control_event(
    view: ControlEventView,
    *,
    existing_canonical_keys: frozenset[str],
    fact_id_for_key: Mapping[str, str],
    allocate_id,
) -> ProjectionDelta:
    source = DiscoveryFactSourceView(
        source_plane=DiscoverySourcePlane.CONTROL_EVENT,
        control_event_id=view.control_event_id,
    )
    key = canonical_key(
        "SCOPE_BOUNDARY_CANDIDATE",
        view.event_kind.value,
        view.location_origin or "",
        view.location_path or "",
        view.worker_result_id,
    )
    fact_id = fact_id_for_key.get(key) or allocate_id(f"fact:{key}")
    fact = DiscoveryFact(
        fact_id=fact_id,
        research_run_id=view.research_run_id,
        fact_kind=DiscoveryFactKind.SCOPE_BOUNDARY_CANDIDATE,
        canonical_key=key,
        epistemic_status=TargetEpistemicStatus.DERIVED,
        identity_id=view.identity_id,
        target_reference=view.target_reference,
        sources=(source,),
        session_context_id=view.session_context_id,
        normalized_origin=view.normalized_origin,
        normalized_path=view.location_path,
        attributes={"control_event_kind": view.event_kind.value, "not_observed": True},
    )
    return ProjectionDelta(
        facts=(ProposedFact(fact=fact, is_new_semantic=key not in existing_canonical_keys),),
        frontier_items=(),
        frontier_events=(),
        workflow_transition_ready=False,
        resolve_transition_frontier=None,
    )


def _scope_boundary_from_control(
    view: ObservationView,
    control: ControlView,
) -> tuple[str | None, str | None, str] | None:
    if control.href_scheme and control.href_scheme not in {"http", "https"}:
        return None, control.href_path or None, ControlEventKind.NEW_ORIGIN_BOUNDARY.value
    if not control.href_origin:
        return None
    if control.href_origin.rstrip("/") == view.normalized_origin.rstrip("/"):
        return None
    return control.href_origin, control.href_path or "/", ControlEventKind.NEW_ORIGIN_BOUNDARY.value


def _instance_from_path(normalized_path: str) -> str | None:
    segments = path_segments(normalized_path)
    if not segments:
        return None
    return instance_token_from_segment(segments[-1])


def _workflow_complete(binding: WorkflowCausalBinding, view: ObservationView) -> bool:
    return (
        binding.post_observation_id == view.observation_id
        and binding.actor_identity_id == view.identity_id
        and bool(binding.pre_state_fact_id)
        and bool(binding.experiment_plan_id)
        and bool(binding.execution_attempt_id)
    )


def _page_state_attributes(view: ObservationView) -> dict[str, Any] | None:
    attributes: dict[str, Any] = {
        "main_observation_present": view.main_observation_present,
    }
    if view.snapshot_fingerprint:
        attributes["snapshot_fingerprint"] = view.snapshot_fingerprint
    if view.containment_degraded or view.blocked_external_dependency_count:
        attributes["containment_degraded"] = True
        attributes["blocked_external_dependency_count"] = view.blocked_external_dependency_count
        attributes["page_complete"] = False
    if not view.coverage_complete:
        attributes["coverage_complete"] = False
        attributes["page_complete"] = False
    return attributes


def _origin_of_target(normalized_target: str) -> str | None:
    parsed = urlsplit(normalized_target or "")
    if not parsed.scheme or not parsed.hostname:
        return None
    default = 80 if parsed.scheme == "http" else 443
    origin = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port not in (None, default):
        origin += f":{parsed.port}"
    return origin


def _network_event_same_origin(page_origin: str, normalized_target: str) -> bool:
    event_origin = _origin_of_target(normalized_target)
    if event_origin is None:
        return False
    return event_origin.rstrip("/") == page_origin.rstrip("/")
