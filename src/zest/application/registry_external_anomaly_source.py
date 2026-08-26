"""Propose registry-external identity-anomaly candidates into the shared pool.

Stops at OpportunitySelectionCandidate. Does not select next action, compile,
authorize, dispatch a Worker, or write HunterFamily. SelectResearchOpportunities
remains the sole reader that can admit a ResearchOpportunity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from zest.application.discovery.snapshot_views import (
    _fact_from_record,
    _inference_from_record,
)
from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.validation_audit import latest_tier_events
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import AuditEventRecord, OpportunitySelectionCandidateRecord
from zest.data.unit_of_work import UnitOfWork
from zest.research.discovery.graph import rebuild_attack_surface_graph
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)
from zest.research.validation.tier_gate import ValidationTier, ValidationTierOutcome
from zest.research.identity_anomaly import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_KIND,
    IDENTITY_ANOMALY_DIRECTION,
    IDENTITY_ANOMALY_QUESTION,
    IDENTITY_ANOMALY_STRATEGY_VERSION,
    IdentityAnomalyClass,
    IdentityAnomalyContext,
    classify_http_authorization_observation,
    classify_identity_differential,
    compiler_arguments_from_context,
    compiler_fields_from_authz_payload,
    owning_identity_families,
)
from zest.research.selection import HunterFamilyView

SOURCE_SYSTEM = "REGISTRY_EXTERNAL_ANOMALY"
CANDIDATES_PROPOSED_EVENT = "REGISTRY_EXTERNAL_ANOMALY_CANDIDATES_PROPOSED"
KNOWN_FAMILY_EVENT = "REGISTRY_EXTERNAL_ANOMALY_ROUTED_KNOWN_FAMILY"
SOURCE_REJECTED_EVENT = "REGISTRY_EXTERNAL_ANOMALY_SOURCE_REJECTED"
VALIDATION_TIER_EVENT = "REGISTRY_EXTERNAL_VALIDATION_TIER"
VALIDATION_ACTOR_ID = "control-plane:registry-external-anomaly"
GRAPH_STRATEGY_VERSION = "surface.discovery.v1"


@dataclass(frozen=True)
class RegistryExternalAnomalySourceResult:
    candidates_created: int
    skipped_duplicate: int
    skipped_known_family: int
    skipped_rejected: int
    identity_differential_ids: tuple[str, ...]


def admit_registry_external_anomaly_candidates(
    uow: UnitOfWork,
    *,
    research_run_id: str,
    now: datetime,
    actor_id: str,
) -> RegistryExternalAnomalySourceResult:
    """Scan durable identity sources and write PENDING candidates. No commit."""

    run = uow.research_runs.get(research_run_id)
    if run is None:
        raise ApplicationError("research run not found")

    registry = tuple(_family_view(record) for record in uow.hunter_families.list_enabled())
    graph = _load_graph(uow, research_run_id)
    owning_families = owning_identity_families(graph, registry)

    existing_candidates = uow.opportunity_selection_candidates.list_for_research_run(
        research_run_id
    )
    existing_opportunities = uow.research_opportunities.list_for_research_run(research_run_id)
    seen_identities = {
        item.structural_identity for item in existing_candidates
    } | {item.structural_identity for item in existing_opportunities}
    seen_context_signatures = {
        item.context_signature
        for item in existing_candidates
        if item.strategy_version == IDENTITY_ANOMALY_STRATEGY_VERSION
    } | {
        item.context_signature
        for item in existing_opportunities
        if item.strategy_version == IDENTITY_ANOMALY_STRATEGY_VERSION
    }
    covered_observation_ids: set[str] = set()
    identity_differential_ids: list[str] = []
    created = 0
    skipped_duplicate = 0
    skipped_known_family = 0
    skipped_rejected = 0

    for differential in uow.differential_observations.list_for_research_run(research_run_id):
        observation_ids = tuple(
            dict.fromkeys(
                differential.baseline_observation_ids
                + differential.variant_observation_ids
                + differential.source_refs
            )
        )
        unresolved, cross_run, payloads = _resolve_observations(
            uow, research_run_id, observation_ids
        )
        fields = _fields_from_observation_payloads(payloads)
        context = classify_identity_differential(
            research_run_id=research_run_id,
            differential_id=differential.differential_id,
            differential_run_id=differential.research_run_id,
            interpretation=differential.interpretation,
            changed_dimensions=differential.changed_dimensions,
            observation_ids=observation_ids,
            unresolved_observation_ids=unresolved,
            cross_run_observation_ids=cross_run,
            owning_family_ids=owning_families,
            **fields,
        )
        if context.classification in {
            IdentityAnomalyClass.REGISTRY_EXTERNAL,
            IdentityAnomalyClass.KNOWN_FAMILY,
            IdentityAnomalyClass.REJECT_NO_ANOMALY,
            IdentityAnomalyClass.REJECT_NOT_IDENTITY,
            IdentityAnomalyClass.REJECT_CROSS_RUN,
            IdentityAnomalyClass.REJECT_UNRESOLVED_SOURCE,
        } and context.classification is not IdentityAnomalyClass.REJECT_NOT_IDENTITY:
            identity_differential_ids.append(differential.differential_id)
        created, skipped_duplicate, skipped_known_family, skipped_rejected = _record_context(
            uow,
            context=context,
            now=now,
            actor_id=actor_id,
            seen_identities=seen_identities,
            seen_context_signatures=seen_context_signatures,
            created=created,
            skipped_duplicate=skipped_duplicate,
            skipped_known_family=skipped_known_family,
            skipped_rejected=skipped_rejected,
        )
        if context.classification is IdentityAnomalyClass.REGISTRY_EXTERNAL:
            covered_observation_ids.update(context.observation_ids)

    for observation in uow.observations.list_for_research_run(research_run_id):
        if observation.observation_kind != HTTP_AUTHORIZATION_DIFFERENTIAL_KIND:
            continue
        if observation.observation_id in covered_observation_ids:
            continue
        run_id = _observation_run_id(uow, observation.worker_result_id)
        context = classify_http_authorization_observation(
            research_run_id=research_run_id,
            observation_id=observation.observation_id,
            observation_kind=observation.observation_kind,
            observation_run_id=run_id,
            payload=dict(observation.payload),
            owning_family_ids=owning_families,
        )
        created, skipped_duplicate, skipped_known_family, skipped_rejected = _record_context(
            uow,
            context=context,
            now=now,
            actor_id=actor_id,
            seen_identities=seen_identities,
            seen_context_signatures=seen_context_signatures,
            created=created,
            skipped_duplicate=skipped_duplicate,
            skipped_known_family=skipped_known_family,
            skipped_rejected=skipped_rejected,
        )

    return RegistryExternalAnomalySourceResult(
        candidates_created=created,
        skipped_duplicate=skipped_duplicate,
        skipped_known_family=skipped_known_family,
        skipped_rejected=skipped_rejected,
        identity_differential_ids=tuple(dict.fromkeys(identity_differential_ids)),
    )


def record_identity_anomaly_validation_audit(
    uow: UnitOfWork,
    *,
    research_run_id: str,
    hypothesis_id: str,
    now: datetime,
    source_id: str,
) -> None:
    """Record V1–V3 PASSED after a consistent discriminating identity experiment.

    FindingProposal admission for HTTP_AUTHORIZATION_DIFFERENTIAL requires the
    SD-G10 tier chain. This is not a HunterFamily write and not a Finding.
    """

    existing = latest_tier_events(
        uow, research_run_id=research_run_id, hypothesis_id=hypothesis_id
    )
    v3 = existing.get(ValidationTier.V3)
    if v3 is not None and str(v3.payload.get("outcome")) == ValidationTierOutcome.PASSED.value:
        return
    for tier in ("V1", "V2", "V3"):
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=now,
                actor_id=VALIDATION_ACTOR_ID,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=VALIDATION_TIER_EVENT,
                subject_type="hypothesis",
                subject_id=hypothesis_id,
                correlation_id=research_run_id,
                payload={
                    "research_run_id": research_run_id,
                    "family_id": "registry-external",
                    "tier": tier,
                    "outcome": "PASSED",
                    "reason_code": "IDENTITY_ANOMALY_DISCRIMINATING_EXPERIMENT_PASSED",
                    "node_canonical_key": source_id,
                    "scope_state": "IN_SCOPE",
                    "not_hunter_family_write": True,
                    "not_a_finding": True,
                    "not_authorization": True,
                },
            )
        )


def load_identity_anomaly_context(
    uow: UnitOfWork, *, research_run_id: str, source_id: str
) -> IdentityAnomalyContext:
    """Reload one durable source and classify it. Missing source rejects."""

    registry = tuple(_family_view(record) for record in uow.hunter_families.list_enabled())
    graph = _load_graph(uow, research_run_id)
    owning_families = owning_identity_families(graph, registry)
    differential = uow.differential_observations.get(source_id)
    if differential is not None:
        observation_ids = tuple(
            dict.fromkeys(
                differential.baseline_observation_ids
                + differential.variant_observation_ids
                + differential.source_refs
            )
        )
        unresolved, cross_run, payloads = _resolve_observations(
            uow, research_run_id, observation_ids
        )
        fields = _fields_from_observation_payloads(payloads)
        return classify_identity_differential(
            research_run_id=research_run_id,
            differential_id=differential.differential_id,
            differential_run_id=differential.research_run_id,
            interpretation=differential.interpretation,
            changed_dimensions=differential.changed_dimensions,
            observation_ids=observation_ids,
            unresolved_observation_ids=unresolved,
            cross_run_observation_ids=cross_run,
            owning_family_ids=owning_families,
            **fields,
        )
    observation = uow.observations.get(source_id)
    if observation is None:
        return classify_http_authorization_observation(
            research_run_id=research_run_id,
            observation_id=source_id,
            observation_kind="MISSING",
            observation_run_id=None,
            payload={},
            owning_family_ids=(),
        )
    return classify_http_authorization_observation(
        research_run_id=research_run_id,
        observation_id=observation.observation_id,
        observation_kind=observation.observation_kind,
        observation_run_id=_observation_run_id(uow, observation.worker_result_id),
        payload=dict(observation.payload),
        owning_family_ids=owning_families,
    )


def _record_context(
    uow: UnitOfWork,
    *,
    context: IdentityAnomalyContext,
    now: datetime,
    actor_id: str,
    seen_identities: set[str],
    seen_context_signatures: set[str],
    created: int,
    skipped_duplicate: int,
    skipped_known_family: int,
    skipped_rejected: int,
) -> tuple[int, int, int, int]:
    if context.classification is IdentityAnomalyClass.KNOWN_FAMILY:
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=now,
                actor_id=actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=KNOWN_FAMILY_EVENT,
                subject_type="research_run",
                subject_id=context.research_run_id,
                payload={
                    "source_id": context.source_id,
                    "source_kind": context.source_kind,
                    "owning_family_ids": list(context.owning_family_ids),
                    "not_exploratory": True,
                    "not_authorization": True,
                    "not_a_vulnerability": True,
                },
            )
        )
        return created, skipped_duplicate, skipped_known_family + 1, skipped_rejected
    if context.classification is not IdentityAnomalyClass.REGISTRY_EXTERNAL:
        if context.classification is not IdentityAnomalyClass.REJECT_NOT_IDENTITY:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id=actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=SOURCE_REJECTED_EVENT,
                    subject_type="research_run",
                    subject_id=context.research_run_id,
                    payload={
                        "source_id": context.source_id,
                        "source_kind": context.source_kind,
                        "reason_code": context.reason_code,
                        "not_authorization": True,
                        "not_a_vulnerability": True,
                    },
                )
            )
            skipped_rejected += 1
        return created, skipped_duplicate, skipped_known_family, skipped_rejected
    if compiler_arguments_from_context(context) is None:
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=new_opaque_id(),
                occurred_at=now,
                actor_id=actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=SOURCE_REJECTED_EVENT,
                subject_type="research_run",
                subject_id=context.research_run_id,
                payload={
                    "source_id": context.source_id,
                    "source_kind": context.source_kind,
                    "reason_code": "MISSING_COMPILER_SEMANTICS",
                    "not_authorization": True,
                    "not_a_vulnerability": True,
                },
            )
        )
        return created, skipped_duplicate, skipped_known_family, skipped_rejected + 1
    record = _candidate_from_context(context, now=now)
    if (
        record.structural_identity in seen_identities
        or record.context_signature in seen_context_signatures
    ):
        return created, skipped_duplicate + 1, skipped_known_family, skipped_rejected
    try:
        uow.opportunity_selection_candidates.insert(record)
    except PersistenceConflictError:
        return created, skipped_duplicate + 1, skipped_known_family, skipped_rejected
    seen_identities.add(record.structural_identity)
    seen_context_signatures.add(record.context_signature)
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id=actor_id,
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=CANDIDATES_PROPOSED_EVENT,
            subject_type="research_run",
            subject_id=context.research_run_id,
            payload={
                "candidate_id": record.candidate_id,
                "source_id": context.source_id,
                "source_kind": context.source_kind,
                "structural_identity": record.structural_identity,
                "registry_external": True,
                "not_authorization": True,
                "not_a_vulnerability": True,
                "not_hunter_family_write": True,
            },
        )
    )
    return created + 1, skipped_duplicate, skipped_known_family, skipped_rejected


def _candidate_from_context(
    context: IdentityAnomalyContext, *, now: datetime
) -> OpportunitySelectionCandidateRecord:
    kind = OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY
    context_signature = context.context_signature()
    identity = opportunity_structural_identity(
        kind=kind,
        source_refs=(context.source_id,),
        context_signature=context_signature,
        proposed_direction=IDENTITY_ANOMALY_DIRECTION,
    )
    dimensions = OpportunityDimensions(
        expected_information_value=OrdinalLevel.HIGH,
        security_relevance_potential=OrdinalLevel.MEDIUM,
        novelty_composition=OrdinalLevel.HIGH,
        unresolved_uncertainty=OrdinalLevel.HIGH,
        chain_potential=OrdinalLevel.LOW,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=0,
        duplicate_risk=OrdinalLevel.MEDIUM,
        previous_failed_attempts=0,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=identity,
        research_run_id=context.research_run_id,
        source_system=SOURCE_SYSTEM,
        opportunity_kind=kind.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(context.source_id,),
        proposed_direction=IDENTITY_ANOMALY_DIRECTION,
        unresolved_question=IDENTITY_ANOMALY_QUESTION,
        expected_information_value_description=(
            "A registry-external identity differential can be discriminated by "
            "one bounded existing compiler using durable source identities."
        ),
        assumptions=(
            "Opportunity is not authorization",
            "Opportunity is not Evidence",
            "HunterFamily registry is unchanged by this proposal",
        ),
        dimensions=dimensions.to_mapping(),
        context_signature=context_signature,
        structural_identity=identity,
        strategy_version=IDENTITY_ANOMALY_STRATEGY_VERSION,
        created_at=now,
        outcome="PENDING",
    )


def _resolve_observations(
    uow: UnitOfWork, research_run_id: str, observation_ids: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    unresolved: list[str] = []
    cross_run: list[str] = []
    payloads: list[Mapping[str, Any]] = []
    for observation_id in observation_ids:
        observation = uow.observations.get(observation_id)
        if observation is None:
            unresolved.append(observation_id)
            continue
        run_id = _observation_run_id(uow, observation.worker_result_id)
        if run_id is None:
            unresolved.append(observation_id)
            continue
        if run_id != research_run_id:
            cross_run.append(observation_id)
            continue
        payloads.append(dict(observation.payload))
    return tuple(unresolved), tuple(cross_run), tuple(payloads)


def _observation_run_id(uow: UnitOfWork, worker_result_id: str) -> str | None:
    result = uow.worker_results.get(worker_result_id)
    if result is None:
        return None
    return result.research_run_id


def _fields_from_observation_payloads(
    payloads: tuple[Mapping[str, Any], ...]
) -> dict[str, str | None]:
    merged: dict[str, str | None] = {
        "authorized_origin": None,
        "actor": None,
        "own_object": None,
        "cross_object": None,
        "mode": None,
    }
    for payload in payloads:
        fields = compiler_fields_from_authz_payload(payload)
        for key, value in fields.items():
            if merged[key] is None and value is not None:
                merged[key] = value
    return merged


def _load_graph(uow: UnitOfWork, research_run_id: str):
    facts = uow.discovery_facts.list_for_research_run(research_run_id)
    inferences = uow.discovery_inferences.list_for_research_run(research_run_id)
    return rebuild_attack_surface_graph(
        research_run_id=research_run_id,
        strategy_version=GRAPH_STRATEGY_VERSION,
        facts=tuple(_fact_from_record(uow, row) for row in facts),
        inferences=tuple(_inference_from_record(row) for row in inferences),
    )


def _family_view(record) -> HunterFamilyView:
    return HunterFamilyView(
        family_id=record.family_id,
        name=record.name,
        target_node_kinds=record.target_node_kinds,
        preconditions=record.preconditions,
        claim_template=record.claim_template,
        evidence_requirements=record.evidence_requirements,
        validation_tier=record.validation_tier,
        enabled=record.enabled,
        version=record.version,
    )
