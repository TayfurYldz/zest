"""Direct OAST research-work source. Does not dispatch Workers or admit Evidence."""

from __future__ import annotations

from datetime import datetime, timedelta

from zest.application.coverage.debt_view import rebuild_coverage_graph
from zest.application.identity import new_opaque_id
from zest.core.enums import ActorType, ScopeClassification
from zest.data.records import AuditEventRecord, OpportunitySelectionCandidateRecord
from zest.research.discovery.graph import AttackSurfaceNodeKind
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.oast.semantics import (
    FAMILY_SSRF,
    FAMILY_WEBHOOK,
    FAMILY_XSS,
    FAMILY_XXE,
    OAST_CALLBACK_EVALUATION_STRATEGY,
    OAST_EXECUTABLE_FAMILIES,
)
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)

OAST_SOURCE_SYSTEM = "OAST"
OAST_STRATEGY_VERSION = "oast.interaction.opportunity.v1"
OAST_HARVEST_BOUND = 20
OAST_TOKEN_BOUND = "OAST_TOKEN_BOUND"
OAST_DEFAULT_TTL = timedelta(minutes=15)
URL_SINK_PARAMS = frozenset(
    {
        "url",
        "dest",
        "uri",
        "webhook",
        "callback",
        "fetch",
        "redirect",
        "next",
        "target",
        "endpoint",
        "host",
    }
)
XML_SINK_PARAMS = frozenset({"xml", "document"})
HTML_SINK_FLAGS = frozenset({"html_sink", "script_sink", "xss_sink"})


def oast_context_signature(family_id: str, node_key: str, identity_id: str, sink: str) -> str:
    return f"oast:{family_id}:{node_key}:{identity_id}:{sink}"


def oast_structural_identity(
    family_id: str, node_key: str, identity_id: str, sink: str
) -> str:
    direction = (
        f"Arm an OAST callback for family {family_id} on {node_key} "
        f"sink {sink} identity {identity_id}."
    )
    return opportunity_structural_identity(
        kind=OpportunityKind.OAST_INTERACTION,
        source_refs=(family_id, node_key, identity_id, sink),
        context_signature=oast_context_signature(family_id, node_key, identity_id, sink),
        proposed_direction=direction,
    )


class OastWorkSource:
    source_id = OAST_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime):
        from zest.application.research_work_sources import HarvestResult

        seen = _seen_identities(uow, research_run_id)
        existing = sum(
            1
            for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
            if item.opportunity_kind == OpportunityKind.OAST_INTERACTION.value
        )
        graph = rebuild_coverage_graph(uow, research_run_id, SURFACE_DISCOVERY_STRATEGY_VERSION)
        created = 0
        skipped = 0
        if existing >= OAST_HARVEST_BOUND:
            return HarvestResult(self.source_id, created, skipped)
        for node in graph.nodes:
            if created + existing >= OAST_HARVEST_BOUND:
                break
            classified = classify_oast_need(node)
            if classified is None:
                continue
            family_id, sink, native_se, requires_session = classified
            identity_id = node.identity_ids[0] if node.identity_ids else "ANONYMOUS"
            record = oast_candidate(
                research_run_id,
                family_id=family_id,
                node_key=node.canonical_key,
                identity_id=identity_id,
                sink=sink,
                native_se=native_se,
                requires_session=requires_session,
                now=now,
                source_system=OAST_SOURCE_SYSTEM,
                callback_id=new_opaque_id(),
                expires_at=now + OAST_DEFAULT_TTL,
            )
            if record.structural_identity in seen:
                skipped += 1
                continue
            _write_token_bound_audit(uow, research_run_id, record, now)
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


def hunter_native_oast_candidate(
    *,
    research_run_id: str,
    node,
    now: datetime,
    source_system: str,
) -> OpportunitySelectionCandidateRecord | None:
    classified = classify_oast_need(node)
    if classified is None:
        return None
    family_id, sink, native_se, requires_session = classified
    identity_id = node.identity_ids[0] if node.identity_ids else "ANONYMOUS"
    return oast_candidate(
        research_run_id,
        family_id=family_id,
        node_key=node.canonical_key,
        identity_id=identity_id,
        sink=sink,
        native_se=native_se,
        requires_session=requires_session,
        now=now,
        source_system=source_system,
        callback_id=new_opaque_id(),
        expires_at=now + OAST_DEFAULT_TTL,
    )


def oast_candidate(
    research_run_id: str,
    *,
    family_id: str,
    node_key: str,
    identity_id: str,
    sink: str,
    native_se: int,
    requires_session: bool,
    now: datetime,
    source_system: str,
    callback_id: str,
    expires_at: datetime,
) -> OpportunitySelectionCandidateRecord:
    direction = (
        f"Arm an OAST callback for family {family_id} on {node_key} "
        f"sink {sink} identity {identity_id}."
    )
    context = oast_context_signature(family_id, node_key, identity_id, sink)
    identity = opportunity_structural_identity(
        kind=OpportunityKind.OAST_INTERACTION,
        source_refs=(family_id, node_key, identity_id, sink),
        context_signature=context,
        proposed_direction=direction,
    )
    dimensions = OpportunityDimensions(
        expected_information_value=OrdinalLevel.HIGH,
        security_relevance_potential=OrdinalLevel.HIGH,
        novelty_composition=OrdinalLevel.MEDIUM,
        unresolved_uncertainty=OrdinalLevel.HIGH,
        chain_potential=OrdinalLevel.MEDIUM,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=native_se,
        duplicate_risk=OrdinalLevel.LOW,
        previous_failed_attempts=0,
    ).to_mapping()
    dimensions["oast_callback_id"] = callback_id
    dimensions["oast_expires_at"] = expires_at.isoformat()
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=source_system,
        opportunity_kind=OpportunityKind.OAST_INTERACTION.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(family_id, node_key, identity_id, sink),
        proposed_direction=direction,
        unresolved_question="Does the intended sink produce a correlated out-of-band callback?",
        expected_information_value_description=(
            f"family={family_id}; sink={sink}; se={native_se}"
        ),
        assumptions=(
            "oast_callback_is_not_automatically_a_finding",
            f"native_side_effect:{native_se}",
            f"requires_session:{str(requires_session).lower()}",
            "callback_channel:http",
            f"callback_id:{callback_id}",
            f"expires_at:{expires_at.isoformat()}",
        ),
        dimensions=dimensions,
        context_signature=context,
        structural_identity=identity,
        strategy_version=OAST_STRATEGY_VERSION,
        created_at=now,
    )


def classify_oast_need(node) -> tuple[str, str, int, bool] | None:
    if node.scope_classification is not ScopeClassification.IN_SCOPE:
        return None
    attrs = dict(node.attributes or {})
    requires_session = bool(attrs.get("requires_session") or attrs.get("auth_required"))
    if node.kind is AttackSurfaceNodeKind.WORKFLOW_TRANSITION:
        for key in ("webhook", "callback", "url"):
            if attrs.get(key):
                return FAMILY_WEBHOOK, key, 1, requires_session
        return None
    if node.kind not in {
        AttackSurfaceNodeKind.HTTP_OPERATION,
        AttackSurfaceNodeKind.EXACT_PATH,
        AttackSurfaceNodeKind.FORM,
        AttackSurfaceNodeKind.API_SPEC,
    }:
        return None
    if attrs.get("command_sink") or attrs.get("deserialization_sink"):
        return None
    content_type = str(attrs.get("content_type") or "").lower()
    params = _query_params(attrs)
    for flag in HTML_SINK_FLAGS:
        if attrs.get(flag):
            sink = next(iter(params), "q")
            return FAMILY_XSS, sink, 0, requires_session
    if "xml" in content_type:
        sink = next((item for item in params if item in XML_SINK_PARAMS), "xml")
        return FAMILY_XXE, sink, 1, requires_session
    for param in params:
        lowered = param.lower()
        if lowered in XML_SINK_PARAMS:
            return FAMILY_XXE, param, 1, requires_session
        if lowered in URL_SINK_PARAMS:
            family = FAMILY_WEBHOOK if lowered in {"webhook", "callback"} else FAMILY_SSRF
            se = 1 if family == FAMILY_WEBHOOK and str(attrs.get("method") or "GET").upper() not in {
                "GET",
                "HEAD",
                "OPTIONS",
            } else 0
            return family, param, se, requires_session
    return None


def classify_oast_need_from_attrs(
    *,
    kind: AttackSurfaceNodeKind,
    attributes: dict,
    identity_ids: tuple[str, ...] = (),
    canonical_key: str = "",
    scope_classification=ScopeClassification.IN_SCOPE,
):
    node = type(
        "OastNodeView",
        (),
        {
            "kind": kind,
            "attributes": attributes,
            "identity_ids": identity_ids,
            "canonical_key": canonical_key,
            "scope_classification": scope_classification,
        },
    )()
    return classify_oast_need(node)


def _query_params(attrs: dict) -> tuple[str, ...]:
    params = attrs.get("query_params") or attrs.get("query") or ()
    if isinstance(params, str):
        return (params,)
    if isinstance(params, (list, tuple)):
        return tuple(str(item) for item in params if str(item).strip())
    return ()


def _write_token_bound_audit(uow, research_run_id: str, record, now: datetime) -> None:
    callback_id = (record.dimensions or {}).get("oast_callback_id")
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id="control-plane:oast-source",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=OAST_TOKEN_BOUND,
            subject_type="research_run",
            subject_id=research_run_id,
            correlation_id=str(callback_id) if callback_id else record.candidate_id,
            payload={
                "callback_id": callback_id,
                "family_id": record.source_refs[0] if record.source_refs else None,
                "node_key": record.source_refs[1] if len(record.source_refs) > 1 else None,
                "identity_id": record.source_refs[2] if len(record.source_refs) > 2 else None,
                "sink": record.source_refs[3] if len(record.source_refs) > 3 else None,
                "expires_at": (record.dimensions or {}).get("oast_expires_at"),
                "not_evidence": True,
            },
        )
    )


def _seen_identities(uow, research_run_id: str) -> set[str]:
    return {
        item.structural_identity
        for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
    } | {
        item.structural_identity
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
    }
