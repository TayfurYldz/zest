"""TX B: project one Observation or ControlEvent and commit a receipt in the same TX."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from zest.application.identity import new_opaque_id
from zest.data.records import (
    ControlEventRecord,
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    DiscoveryInferenceRecord,
    DiscoveryInferenceSourceRecord,
    DiscoveryProjectionReceiptRecord,
    FrontierEventRecord,
    FrontierItemRecord,
    FrontierSourceRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from zest.data.unit_of_work import UnitOfWork
from zest.platform.url_normalize import normalize_url
from zest.research.discovery.facts import DiscoveryFact
from zest.research.discovery.frontier import FrontierEvent, FrontierItem
from zest.research.discovery.projection import (
    ControlEventView,
    ControlView,
    NetworkEventView,
    ObservationView,
    WorkflowCausalBinding,
    project_control_event,
    project_observation_view,
)
from zest.research.discovery.templates import admit_route_template_inferences
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    ControlEventKind,
    DiscoverySourcePlane,
)
from zest.research.target_model import TargetEpistemicStatus


def project_observation(
    uow: UnitOfWork,
    observation: ObservationRecord,
    *,
    created_at: datetime,
    identity_id: str = ANONYMOUS_IDENTITY_ID,
    target_reference: str = "target-1",
    session_context_id: str | None = None,
    workflow: WorkflowCausalBinding | None = None,
) -> None:
    run_id = _run_id_for_observation(uow, observation)
    if uow.discovery_projection_receipts.has_observation(run_id, observation.observation_id):
        return
    resolved_identity = _identity_for(uow, observation, identity_id)
    resolved_session = session_context_id or _session_for(uow, observation)
    resolved_workflow = workflow or _workflow_binding(uow, observation, resolved_identity)
    view = observation_view(
        observation,
        research_run_id=run_id,
        identity_id=resolved_identity,
        target_reference=target_reference,
        session_context_id=resolved_session,
    )
    facts = uow.discovery_facts.list_for_research_run(run_id)
    existing_keys = frozenset(item.canonical_key for item in facts)
    fact_ids = {item.canonical_key: item.fact_id for item in facts}
    existing_dedupes = frozenset(
        item.dedupe_identity for item in uow.frontier_items.list_for_research_run(run_id)
    )
    delta = project_observation_view(
        view,
        existing_canonical_keys=existing_keys,
        fact_id_for_key=fact_ids,
        allocate_id=lambda _label: new_opaque_id(),
        existing_frontier_dedupes=existing_dedupes,
        workflow=resolved_workflow,
    )
    _persist_delta(uow, delta, created_at=created_at, observation_id=observation.observation_id)
    _admit_templates(uow, run_id, resolved_identity, created_at)
    uow.discovery_projection_receipts.insert(
        DiscoveryProjectionReceiptRecord(
            receipt_id=new_opaque_id(),
            research_run_id=run_id,
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            source_plane=DiscoverySourcePlane.OBSERVATION.value,
            created_at=created_at,
            observation_id=observation.observation_id,
        )
    )


def project_control(
    uow: UnitOfWork,
    event: ControlEventRecord,
    *,
    created_at: datetime,
) -> None:
    if uow.discovery_projection_receipts.has_control_event(event.research_run_id, event.control_event_id):
        return
    facts = uow.discovery_facts.list_for_research_run(event.research_run_id)
    existing_keys = frozenset(item.canonical_key for item in facts)
    fact_ids = {item.canonical_key: item.fact_id for item in facts}
    delta = project_control_event(
        ControlEventView(
            control_event_id=event.control_event_id,
            research_run_id=event.research_run_id,
            event_kind=ControlEventKind(event.event_kind),
            identity_id=event.identity_id,
            target_reference=event.target_reference,
            worker_result_id=event.worker_result_id,
            normalized_origin=None,
            location_origin=event.location_origin,
            location_path=event.location_path,
            session_context_id=event.session_context_id,
        ),
        existing_canonical_keys=existing_keys,
        fact_id_for_key=fact_ids,
        allocate_id=lambda _label: new_opaque_id(),
    )
    _persist_delta(uow, delta, created_at=created_at, control_event_id=event.control_event_id)
    uow.discovery_projection_receipts.insert(
        DiscoveryProjectionReceiptRecord(
            receipt_id=new_opaque_id(),
            research_run_id=event.research_run_id,
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            source_plane=DiscoverySourcePlane.CONTROL_EVENT.value,
            created_at=created_at,
            control_event_id=event.control_event_id,
        )
    )


def reconcile_missing_projections(
    uow: UnitOfWork,
    research_run_id: str,
    *,
    created_at: datetime,
    identity_id: str = ANONYMOUS_IDENTITY_ID,
    target_reference: str = "target-1",
) -> int:
    """Replay TX B for sources without receipts. Does not redispatch Workers."""

    projected = 0
    for observation in uow.observations.list_for_research_run(research_run_id):
        if not uow.discovery_projection_receipts.has_observation(
            research_run_id, observation.observation_id
        ):
            project_observation(
                uow,
                observation,
                created_at=created_at,
                identity_id=identity_id,
                target_reference=target_reference,
            )
            projected += 1
    for event in uow.control_events.list_for_research_run(research_run_id):
        if not uow.discovery_projection_receipts.has_control_event(
            research_run_id, event.control_event_id
        ):
            project_control(uow, event, created_at=created_at)
            projected += 1
    return projected


def observation_view(
    observation: ObservationRecord,
    *,
    research_run_id: str,
    identity_id: str,
    target_reference: str,
    session_context_id: str | None,
) -> ObservationView:
    payload = dict(observation.payload)
    origin, path = _origin_path(payload)
    controls = tuple(
        ControlView(
            tag=str(item.get("tag") or ""),
            name=str(item.get("name") or ""),
            role=str(item.get("role") or ""),
            input_type=str(item.get("input_type") or ""),
            href_scheme=str(item.get("href_scheme") or ""),
            href_origin=str(item.get("href_origin") or ""),
            href_path=str(item.get("href_path") or ""),
        )
        for item in payload.get("controls") or []
        if isinstance(item, Mapping)
    )
    events = tuple(
        NetworkEventView(
            event_id=str(item.get("event_id")),
            method=str(item.get("method")),
            path=str(item.get("path") or "/"),
            normalized_target=str(item.get("normalized_target") or ""),
            redirect=bool(item.get("redirect")),
            representability=str(item.get("representability") or "NOT_REPRESENTABLE"),
            status_code=item.get("status_code") if isinstance(item.get("status_code"), int) else None,
        )
        for item in payload.get("network_events") or []
        if isinstance(item, Mapping)
    )
    keys = payload.get("json_top_level_keys") or ()
    return ObservationView(
        observation_id=observation.observation_id,
        research_run_id=research_run_id,
        observation_kind=observation.observation_kind,
        identity_id=identity_id,
        target_reference=target_reference,
        normalized_origin=origin,
        normalized_path=path,
        worker_result_id=observation.worker_result_id,
        session_context_id=session_context_id,
        http_method=str(payload["method"]) if isinstance(payload.get("method"), str) else None,
        snapshot_fingerprint=payload.get("snapshot_fingerprint")
        if isinstance(payload.get("snapshot_fingerprint"), str)
        else None,
        status_code=payload.get("status_code") if isinstance(payload.get("status_code"), int) else None,
        content_type=payload.get("content_type") if isinstance(payload.get("content_type"), str) else None,
        json_value_kind=payload.get("json_value_kind")
        if isinstance(payload.get("json_value_kind"), str)
        else None,
        json_top_level_keys=tuple(keys) if isinstance(keys, list) else (),
        controls=controls,
        network_events=events,
        form_known=any(item.tag.lower() == "form" or item.input_type == "submit" for item in controls),
        main_observation_present=True,
        containment_degraded=bool(payload.get("page_degraded_by_blocked_external_dependency")),
        blocked_external_dependency_count=_blocked_dependency_count(payload),
        coverage_complete=payload.get("coverage_complete") is not False,
    )


def _blocked_dependency_count(payload: Mapping[str, Any]) -> int:
    explicit = payload.get("blocked_external_dependency_count")
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit >= 0:
        return explicit
    boundaries = payload.get("blocked_boundaries")
    if isinstance(boundaries, list):
        return len(boundaries)
    return 0


def _identity_for(uow: UnitOfWork, observation: ObservationRecord, fallback: str) -> str:
    result = uow.worker_results.get(observation.worker_result_id)
    if result is None:
        return fallback
    plan = uow.experiment_plans.get(result.experiment_id)
    if plan is None:
        return fallback
    identity = plan.arguments.get("identity_id")
    if isinstance(identity, str) and identity.strip():
        return identity
    session_ref = plan.arguments.get("session_context_reference")
    if isinstance(session_ref, str) and session_ref.strip():
        session = uow.session_contexts.get(session_ref)
        if session is not None and session.identity_id.strip():
            return session.identity_id
    return fallback


def _session_for(uow: UnitOfWork, observation: ObservationRecord) -> str | None:
    result = uow.worker_results.get(observation.worker_result_id)
    if result is None:
        return None
    plan = uow.experiment_plans.get(result.experiment_id)
    if plan is None:
        return None
    session_ref = plan.arguments.get("session_context_reference")
    if isinstance(session_ref, str) and session_ref.strip():
        return session_ref
    return None


def _workflow_binding(
    uow: UnitOfWork, observation: ObservationRecord, identity_id: str
) -> WorkflowCausalBinding | None:
    result = uow.worker_results.get(observation.worker_result_id)
    if result is None or result.action != "interact":
        return None
    attempts = [
        item
        for item in uow.execution_attempts.list_for_research_run(result.research_run_id)
        if item.experiment_id == result.experiment_id
    ]
    attempt_id = attempts[-1].attempt_id if attempts else ""
    payload = observation.payload if isinstance(observation.payload, Mapping) else {}
    origin, path = _origin_path(payload)
    pre_id = ""
    for fact in uow.discovery_facts.list_for_research_run(result.research_run_id):
        if fact.fact_kind != "PAGE_STATE":
            continue
        if fact.normalized_origin != origin or fact.normalized_path != path:
            continue
        sources = uow.discovery_fact_sources.list_for_fact(fact.fact_id)
        if any(item.observation_id == observation.observation_id for item in sources):
            continue
        pre_id = fact.fact_id
    object_handle = path if path else None
    return WorkflowCausalBinding(
        pre_state_fact_id=pre_id,
        experiment_plan_id=result.experiment_id,
        execution_attempt_id=attempt_id,
        actor_identity_id=identity_id,
        post_observation_id=observation.observation_id,
        object_handle=object_handle,
    )


def _origin_path(payload: Mapping[str, Any]) -> tuple[str, str]:
    raw = payload.get("normalized_url") or payload.get("authorized_origin")
    path = payload.get("path")
    if isinstance(raw, str) and raw.startswith("http"):
        candidate = normalize_url(raw)
        if candidate.normalization_error is None and candidate.normalized_host:
            origin = _format_origin(candidate.normalized_scheme, candidate.normalized_host, candidate.normalized_port)
            return origin, candidate.scope_match_path or candidate.raw_path or "/"
    if isinstance(raw, str) and isinstance(path, str):
        return raw, path
    parsed = urlsplit(str(raw or ""))
    origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{parsed.hostname}"
    return origin, path if isinstance(path, str) else (parsed.path or "/")


def _format_origin(scheme: str | None, host: str | None, port: int | None) -> str:
    if scheme is None or host is None:
        raise ValueError("normalized origin is incomplete")
    default = 80 if scheme == "http" else 443
    if port in (None, default):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _run_id_for_observation(uow: UnitOfWork, observation: ObservationRecord) -> str:
    result = uow.worker_results.get(observation.worker_result_id)
    if result is None:
        raise RuntimeError("observation worker_result is missing")
    return result.research_run_id


def _persist_delta(
    uow: UnitOfWork,
    delta,
    *,
    created_at: datetime,
    observation_id: str | None = None,
    control_event_id: str | None = None,
) -> None:
    for proposed in delta.facts:
        fact = proposed.fact
        existing = uow.discovery_facts.get_by_canonical(fact.research_run_id, fact.canonical_key)
        if existing is None:
            uow.discovery_facts.insert(_fact_record(fact, created_at))
            fact_id = fact.fact_id
        else:
            fact_id = existing.fact_id
        source = fact.sources[0]
        linked = uow.discovery_fact_sources.list_for_fact(fact_id)
        if any(
            item.observation_id == source.observation_id
            and item.control_event_id == source.control_event_id
            and item.source_fact_id == source.source_fact_id
            and item.source_inference_id == source.source_inference_id
            for item in linked
        ):
            continue
        uow.discovery_fact_sources.insert(
            DiscoveryFactSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=fact.research_run_id,
                fact_id=fact_id,
                created_at=created_at,
                observation_id=source.observation_id,
                control_event_id=source.control_event_id,
                source_fact_id=source.source_fact_id,
                source_inference_id=source.source_inference_id,
            )
        )
    superseded_new: set[str] = set()
    for item in delta.frontier_items:
        existing_items = [
            row
            for row in uow.frontier_items.list_for_research_run(item.research_run_id)
            if row.dedupe_identity == item.dedupe_identity
        ]
        if existing_items:
            if any(row.frontier_id == item.frontier_id for row in existing_items):
                continue
            canonical_id = sorted(existing_items, key=lambda row: row.frontier_id)[0].frontier_id
            record = _frontier_record(item, created_at)
            uow.frontier_items.insert(
                replace(record, current_state="SUPERSEDED", state_version=2)
            )
            uow.frontier_sources.insert(
                FrontierSourceRecord(
                    source_row_id=new_opaque_id(),
                    research_run_id=item.research_run_id,
                    frontier_id=item.frontier_id,
                    created_at=created_at,
                    observation_id=observation_id,
                    control_event_id=control_event_id,
                )
            )
            uow.frontier_events.insert(
                FrontierEventRecord(
                    event_id=new_opaque_id(),
                    frontier_id=item.frontier_id,
                    research_run_id=item.research_run_id,
                    event_kind="CREATED",
                    sequence=1,
                    created_at=created_at,
                )
            )
            uow.frontier_events.insert(
                FrontierEventRecord(
                    event_id=new_opaque_id(),
                    frontier_id=item.frontier_id,
                    research_run_id=item.research_run_id,
                    event_kind="SUPERSEDED",
                    sequence=2,
                    created_at=created_at,
                    reason_code=f"DEDUPLICATED:{canonical_id}",
                )
            )
            superseded_new.add(item.frontier_id)
            continue
        uow.frontier_items.insert(_frontier_record(item, created_at))
        uow.frontier_sources.insert(
            FrontierSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=item.research_run_id,
                frontier_id=item.frontier_id,
                created_at=created_at,
                observation_id=observation_id,
                control_event_id=control_event_id,
            )
        )
    for event in delta.frontier_events:
        if event.frontier_id in superseded_new:
            continue
        existing = uow.frontier_events.list_for_frontier(event.frontier_id)
        if any(row.event_kind == event.event_kind.value and row.sequence == event.sequence for row in existing):
            continue
        if not uow.frontier_items.get(event.frontier_id):
            continue
        uow.frontier_events.insert(_event_record(event, created_at))


def _admit_templates(uow: UnitOfWork, research_run_id: str, identity_id: str, created_at: datetime) -> None:
    from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
    from zest.research.discovery.types import DiscoveryFactKind, DiscoverySourcePlane

    records = uow.discovery_facts.list_for_research_run(research_run_id)
    facts = []
    for record in records:
        sources = tuple(
            DiscoveryFactSourceView(
                source_plane=(
                    DiscoverySourcePlane.OBSERVATION
                    if item.observation_id
                    else DiscoverySourcePlane.CONTROL_EVENT
                    if item.control_event_id
                    else None
                ),
                observation_id=item.observation_id,
                control_event_id=item.control_event_id,
                source_fact_id=item.source_fact_id,
                source_inference_id=item.source_inference_id,
            )
            for item in uow.discovery_fact_sources.list_for_fact(record.fact_id)
        )
        if not sources:
            continue
        facts.append(
            DiscoveryFact(
                fact_id=record.fact_id,
                research_run_id=record.research_run_id,
                fact_kind=DiscoveryFactKind(record.fact_kind),
                canonical_key=record.canonical_key,
                epistemic_status=TargetEpistemicStatus(record.epistemic_status),
                identity_id=record.identity_id,
                target_reference=record.target_reference,
                sources=sources,
                session_context_id=record.session_context_id,
                normalized_origin=record.normalized_origin,
                normalized_path=record.normalized_path,
                http_method=record.http_method,
                attributes=record.attributes,
            )
        )
    inferences = admit_route_template_inferences(
        tuple(facts),
        research_run_id=research_run_id,
        identity_id=identity_id,
        allocate_id=lambda _label: new_opaque_id(),
    )
    existing = {item.canonical_key for item in uow.discovery_inferences.list_for_research_run(research_run_id)}
    for inference in inferences:
        if inference.canonical_key in existing:
            continue
        uow.discovery_inferences.insert(
            DiscoveryInferenceRecord(
                inference_id=inference.inference_id,
                research_run_id=inference.research_run_id,
                inference_kind=inference.inference_kind.value,
                canonical_key=inference.canonical_key,
                epistemic_status=inference.epistemic_status.value,
                identity_id=inference.identity_id,
                created_at=created_at,
                attributes=inference.attributes,
            )
        )
        for fact_id in inference.source_fact_ids:
            uow.discovery_inference_sources.insert(
                DiscoveryInferenceSourceRecord(
                    source_row_id=new_opaque_id(),
                    research_run_id=research_run_id,
                    inference_id=inference.inference_id,
                    created_at=created_at,
                    source_fact_id=fact_id,
                )
            )


def _fact_record(fact: DiscoveryFact, created_at: datetime) -> DiscoveryFactRecord:
    return DiscoveryFactRecord(
        fact_id=fact.fact_id,
        research_run_id=fact.research_run_id,
        fact_kind=fact.fact_kind.value,
        canonical_key=fact.canonical_key,
        epistemic_status=fact.epistemic_status.value,
        identity_id=fact.identity_id,
        target_reference=fact.target_reference,
        created_at=created_at,
        session_context_id=fact.session_context_id,
        normalized_origin=fact.normalized_origin,
        normalized_path=fact.normalized_path,
        http_method=fact.http_method,
        attributes=fact.attributes,
    )


def _frontier_record(item: FrontierItem, created_at: datetime) -> FrontierItemRecord:
    return FrontierItemRecord(
        frontier_id=item.frontier_id,
        research_run_id=item.research_run_id,
        strategy_version=item.strategy_version,
        goal_kind=item.goal_kind.value,
        candidate_origin=item.candidate_origin,
        candidate_path=item.candidate_path,
        identity_id=item.identity_id,
        proposed_capability=item.proposed_capability,
        proposed_action=item.proposed_action,
        expected_side_effect=item.expected_side_effect,
        budget_class=item.budget_class,
        structural_signature=item.structural_signature,
        dedupe_identity=item.dedupe_identity,
        created_at=created_at,
        session_context_id=item.session_context_id,
        scope_hint=item.scope_hint,
        attributes=item.attributes,
        current_state="ELIGIBLE",
        state_version=2,
    )


def _event_record(event: FrontierEvent, created_at: datetime) -> FrontierEventRecord:
    return FrontierEventRecord(
        event_id=event.event_id,
        frontier_id=event.frontier_id,
        research_run_id=event.research_run_id,
        event_kind=event.event_kind.value,
        sequence=event.sequence,
        created_at=created_at,
        selection_generation=event.selection_generation,
        execution_attempt_id=event.execution_attempt_id,
        reason_code=event.reason_code,
    )
