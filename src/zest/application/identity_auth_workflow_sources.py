"""Direct Authentication / Authorization / Workflow research work sources."""

from __future__ import annotations

from datetime import datetime

from zest.application.identity import new_opaque_id
from zest.application.research_identity_catalog import (
    active_session_for_identity,
    load_research_identity_catalog,
)
from zest.application.research_work_sources import HarvestResult, ResearchWorkSource
from zest.data.records import OpportunitySelectionCandidateRecord
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)

AUTHENTICATION_SOURCE_SYSTEM = "AUTHENTICATION"
AUTHORIZATION_SOURCE_SYSTEM = "AUTHORIZATION"
WORKFLOW_SOURCE_SYSTEM = "WORKFLOW"
AUTH_STRATEGY_VERSION = "authentication.opportunity.v1"
AUTHZ_STRATEGY_VERSION = "authorization.differential.opportunity.v1"
WORKFLOW_STRATEGY_VERSION = "workflow.state_transition.opportunity.v1"


def authz_context_signature(origin: str, actor: str, own_object: str, cross_object: str) -> str:
    return f"authz:{origin}:{actor}:{own_object}:{cross_object}"


def workflow_context_signature(
    origin: str, actor: str, resource_id: str, transition: str
) -> str:
    return f"workflow:{origin}:{actor}:{resource_id}:{transition}"


def authz_proposed_direction(origin: str, actor: str, own_object: str, cross_object: str) -> str:
    return (
        f"Execute native authorization differential for actor {actor} "
        f"own {own_object} cross {cross_object} at {origin}."
    )


def workflow_proposed_direction(
    origin: str, actor: str, resource_id: str, transition: str
) -> str:
    return (
        f"Execute native state transition {transition} for actor {actor} "
        f"resource {resource_id} at {origin}."
    )


class AuthenticationWorkSource:
    source_id = AUTHENTICATION_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        catalog = load_research_identity_catalog(uow, research_run_id)
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        origin = _authorized_origin(uow, research_run_id)
        needed = _identities_needed_for_object_or_workflow(uow, research_run_id, catalog)
        for identity in catalog.identities:
            if identity.identity_id not in needed:
                continue
            session = active_session_for_identity(
                uow,
                research_run_id=research_run_id,
                identity_id=identity.identity_id,
                origin=origin or identity.target_reference,
                now=now,
            )
            if session is not None:
                continue
            record = _authentication_candidate(
                research_run_id, identity, origin or identity.target_reference, now
            )
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


class AuthorizationWorkSource:
    source_id = AUTHORIZATION_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        catalog = load_research_identity_catalog(uow, research_run_id)
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        for probe in _authz_probes_from_facts(uow, research_run_id):
            if probe["ownership"] == "UNKNOWN":
                continue
            identity = catalog.identity_by_actor(probe["actor"])
            if identity is None:
                _record_missing_identity(uow, research_run_id, probe, now)
                continue
            origin = probe["authorized_origin"]
            session = active_session_for_identity(
                uow,
                research_run_id=research_run_id,
                identity_id=identity.identity_id,
                origin=origin,
                now=now,
            )
            if session is None:
                continue
            record = _authz_candidate(
                research_run_id,
                probe,
                now,
                source_system=AUTHORIZATION_SOURCE_SYSTEM,
            )
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


class WorkflowWorkSource:
    source_id = WORKFLOW_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        catalog = load_research_identity_catalog(uow, research_run_id)
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        for probe in _workflow_probes_from_facts(uow, research_run_id):
            identity = catalog.identity_by_actor(probe["actor"])
            if identity is None:
                _record_missing_identity(uow, research_run_id, probe, now)
                continue
            session = active_session_for_identity(
                uow,
                research_run_id=research_run_id,
                identity_id=identity.identity_id,
                origin=probe["authorized_origin"],
                now=now,
            )
            if session is None:
                continue
            record = _workflow_candidate(
                research_run_id,
                probe,
                now,
                source_system=WORKFLOW_SOURCE_SYSTEM,
            )
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


def hunter_native_authz_or_workflow_candidate(
    *,
    research_run_id: str,
    family_name: str,
    attributes: dict,
    now: datetime,
    source_system: str,
) -> OpportunitySelectionCandidateRecord | None:
    origin = str(attributes.get("authorized_origin") or attributes.get("origin") or "")
    if family_name == "OBJECT_AUTHORIZATION":
        actor = str(attributes.get("actor") or "")
        own_object = str(attributes.get("own_object") or "")
        cross_object = str(attributes.get("cross_object") or "")
        if not all((origin, actor, own_object, cross_object)):
            return None
        return _authz_candidate(
            research_run_id,
            {
                "authorized_origin": origin,
                "actor": actor,
                "own_object": own_object,
                "cross_object": cross_object,
                "mode": str(attributes.get("mode") or "vulnerable"),
                "ownership": "KNOWN",
            },
            now,
            source_system=source_system,
        )
    if family_name == "WORKFLOW_STATE_TRANSITION":
        actor = str(attributes.get("actor") or "")
        resource_id = str(attributes.get("resource_id") or "")
        transition = str(attributes.get("transition") or "approve")
        if not all((origin, actor, resource_id, transition)):
            return None
        return _workflow_candidate(
            research_run_id,
            {
                "authorized_origin": origin,
                "actor": actor,
                "resource_id": resource_id,
                "transition": transition,
                "area": str(attributes.get("area") or "workflow"),
            },
            now,
            source_system=source_system,
        )
    return None


def _authentication_candidate(
    research_run_id: str, identity, origin: str, now: datetime
) -> OpportunitySelectionCandidateRecord:
    direction = f"Establish authenticated session for identity {identity.identity_id}."
    context = f"authentication:{identity.identity_id}:{origin}"
    kind = OpportunityKind.AUTHENTICATION
    identity_key = opportunity_structural_identity(
        kind=kind,
        source_refs=(identity.identity_id, origin),
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=AUTHENTICATION_SOURCE_SYSTEM,
        opportunity_kind=kind.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(identity.identity_id, origin, identity.authentication_profile_reference),
        proposed_direction=direction,
        unresolved_question="Does this identity have a usable bound session?",
        expected_information_value_description="session binding for dependent research",
        assumptions=(
            "authentication_establishes_research_state",
            "not_a_vulnerability",
            f"identity_id:{identity.identity_id}",
            f"credential_scheme:{identity.credential_reference.scheme}",
            f"credential_name:{identity.credential_reference.name}",
        ),
        dimensions=OpportunityDimensions(
            expected_information_value=OrdinalLevel.HIGH,
            security_relevance_potential=OrdinalLevel.LOW,
            novelty_composition=OrdinalLevel.LOW,
            unresolved_uncertainty=OrdinalLevel.HIGH,
            chain_potential=OrdinalLevel.HIGH,
            evidence_coverage=OrdinalLevel.LOW,
            execution_cost=OrdinalLevel.LOW,
            side_effect_requirement=0,
            duplicate_risk=OrdinalLevel.LOW,
            previous_failed_attempts=0,
        ).to_mapping(),
        context_signature=context,
        structural_identity=identity_key,
        strategy_version=AUTH_STRATEGY_VERSION,
        created_at=now,
    )


def _authz_candidate(research_run_id, probe, now, *, source_system: str):
    origin = probe["authorized_origin"]
    actor = probe["actor"]
    own_object = probe["own_object"]
    cross_object = probe["cross_object"]
    direction = authz_proposed_direction(origin, actor, own_object, cross_object)
    context = authz_context_signature(origin, actor, own_object, cross_object)
    kind = OpportunityKind.AUTHORIZATION_DIFFERENTIAL
    identity = opportunity_structural_identity(
        kind=kind,
        source_refs=(origin, actor, own_object, cross_object),
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=source_system,
        opportunity_kind=kind.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(origin, actor, own_object, cross_object, probe.get("mode") or "vulnerable"),
        proposed_direction=direction,
        unresolved_question="Does object authorization hold across identities?",
        expected_information_value_description="authorization differential observation",
        assumptions=(
            "not_inferred_ownership" if probe.get("ownership") != "KNOWN" else "ownership_from_observation",
            f"mode:{probe.get('mode') or 'vulnerable'}",
        ),
        dimensions=OpportunityDimensions(
            expected_information_value=OrdinalLevel.HIGH,
            security_relevance_potential=OrdinalLevel.HIGH,
            novelty_composition=OrdinalLevel.MEDIUM,
            unresolved_uncertainty=OrdinalLevel.HIGH,
            chain_potential=OrdinalLevel.MEDIUM,
            evidence_coverage=OrdinalLevel.LOW,
            execution_cost=OrdinalLevel.LOW,
            side_effect_requirement=0,
            duplicate_risk=OrdinalLevel.LOW,
            previous_failed_attempts=0,
        ).to_mapping(),
        context_signature=context,
        structural_identity=identity,
        strategy_version=AUTHZ_STRATEGY_VERSION,
        created_at=now,
    )


def _workflow_candidate(research_run_id, probe, now, *, source_system: str):
    origin = probe["authorized_origin"]
    actor = probe["actor"]
    resource_id = probe["resource_id"]
    transition = probe["transition"]
    direction = workflow_proposed_direction(origin, actor, resource_id, transition)
    context = workflow_context_signature(origin, actor, resource_id, transition)
    kind = OpportunityKind.WORKFLOW_STATE_TRANSITION
    identity = opportunity_structural_identity(
        kind=kind,
        source_refs=(origin, actor, resource_id, transition),
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=source_system,
        opportunity_kind=kind.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(
            origin,
            actor,
            resource_id,
            transition,
            probe.get("area") or "workflow",
        ),
        proposed_direction=direction,
        unresolved_question="Does the workflow transition enforce role and sequence?",
        expected_information_value_description="state transition observation",
        assumptions=(f"area:{probe.get('area') or 'workflow'}",),
        dimensions=OpportunityDimensions(
            expected_information_value=OrdinalLevel.HIGH,
            security_relevance_potential=OrdinalLevel.HIGH,
            novelty_composition=OrdinalLevel.MEDIUM,
            unresolved_uncertainty=OrdinalLevel.HIGH,
            chain_potential=OrdinalLevel.MEDIUM,
            evidence_coverage=OrdinalLevel.LOW,
            execution_cost=OrdinalLevel.LOW,
            side_effect_requirement=1,
            duplicate_risk=OrdinalLevel.LOW,
            previous_failed_attempts=0,
        ).to_mapping(),
        context_signature=context,
        structural_identity=identity,
        strategy_version=WORKFLOW_STRATEGY_VERSION,
        created_at=now,
    )


def _seen_identities(uow, research_run_id: str) -> set[str]:
    return {
        item.structural_identity
        for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
    } | {
        item.structural_identity
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
    }


def _authorized_origin(uow, research_run_id: str) -> str:
    orch = uow.research_orchestrations.get(research_run_id)
    if orch is not None and orch.target_reference:
        return str(orch.target_reference).rstrip("/")
    facts = uow.discovery_facts.list_for_research_run(research_run_id)
    for fact in facts:
        origin = fact.normalized_origin or (fact.attributes or {}).get("authorized_origin")
        if isinstance(origin, str) and origin.strip():
            return origin.strip().rstrip("/")
    return ""


def _authz_probes_from_facts(uow, research_run_id: str) -> list[dict]:
    probes = []
    for fact in uow.discovery_facts.list_for_research_run(research_run_id):
        attrs = dict(fact.attributes or {})
        origin = attrs.get("authorized_origin") or fact.normalized_origin
        actor = attrs.get("actor")
        own_object = attrs.get("own_object")
        cross_object = attrs.get("cross_object")
        if not isinstance(origin, str) or not origin.strip():
            continue
        if fact.fact_kind not in {"HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"}:
            continue
        if not isinstance(actor, str) or not actor.strip():
            continue
        ownership = "KNOWN"
        if not isinstance(own_object, str) or not own_object.strip():
            ownership = "UNKNOWN"
            own_object = ""
        if not isinstance(cross_object, str) or not cross_object.strip():
            ownership = "UNKNOWN"
            cross_object = ""
        probes.append(
            {
                "authorized_origin": origin.strip().rstrip("/"),
                "actor": actor.strip(),
                "own_object": str(own_object).strip(),
                "cross_object": str(cross_object).strip(),
                "mode": str(attrs.get("mode") or "vulnerable"),
                "ownership": ownership,
            }
        )
    return probes


def _workflow_probes_from_facts(uow, research_run_id: str) -> list[dict]:
    probes = []
    for fact in uow.discovery_facts.list_for_research_run(research_run_id):
        if fact.fact_kind not in {"WORKFLOW_TRANSITION", "WORKFLOW_STATE", "CONTROL"}:
            continue
        attrs = dict(fact.attributes or {})
        origin = attrs.get("authorized_origin") or fact.normalized_origin
        actor = attrs.get("actor")
        resource_id = attrs.get("resource_id")
        transition = attrs.get("transition") or "approve"
        if not all(
            isinstance(item, str) and item.strip()
            for item in (origin, actor, resource_id, transition)
        ):
            continue
        probes.append(
            {
                "authorized_origin": origin.strip().rstrip("/"),
                "actor": actor.strip(),
                "resource_id": resource_id.strip(),
                "transition": transition.strip(),
                "area": str(attrs.get("area") or "workflow"),
            }
        )
    return probes


def _identities_needed_for_object_or_workflow(uow, research_run_id, catalog) -> set[str]:
    needed: set[str] = set()
    for probe in _authz_probes_from_facts(uow, research_run_id):
        identity = catalog.identity_by_actor(probe["actor"])
        if identity is not None:
            needed.add(identity.identity_id)
    for probe in _workflow_probes_from_facts(uow, research_run_id):
        identity = catalog.identity_by_actor(probe["actor"])
        if identity is not None:
            needed.add(identity.identity_id)
    return needed


def _record_missing_identity(uow, research_run_id: str, probe: dict, now: datetime) -> None:
    from zest.core.enums import ActorType
    from zest.data.records import AuditEventRecord

    existing = [
        item
        for item in uow.audit_events.list_for_subject("research_run", research_run_id)
        if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        and (item.payload or {}).get("reason_codes") == ["MISSING_IDENTITY_PRECONDITION"]
        and (item.payload or {}).get("actor") == probe.get("actor")
    ]
    if existing:
        return
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id="control-plane:identity-auth-workflow-source",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type="RESEARCH_WORK_MISSING_PRECONDITION",
            subject_type="research_run",
            subject_id=research_run_id,
            payload={
                "reason_codes": ["MISSING_IDENTITY_PRECONDITION"],
                "actor": probe.get("actor"),
                "selected_engine": "AUTHORIZATION",
                "not_fabricated_identity": True,
            },
        )
    )
