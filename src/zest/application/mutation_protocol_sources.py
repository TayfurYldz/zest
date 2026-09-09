"""Direct MutationEngine and Protocol parser-plan research work sources."""

from __future__ import annotations

from datetime import datetime

from zest.application.coverage.debt_view import rebuild_coverage_graph
from zest.application.hunt_validation import side_effect_for_family
from zest.application.identity import new_opaque_id
from zest.core.enums import ActorType, ScopeClassification
from zest.data.records import AuditEventRecord, OpportunitySelectionCandidateRecord
from zest.research.compiler_registry import PROTOCOL_FAMILIES
from zest.research.discovery.graph import AttackSurfaceNodeKind
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)
from zest.research.mutation.engine import MutationEngine
from zest.research.selection import HunterFamilyView
from zest.safe_data import reject_secret_keys as reject_secret_structure

MUTATION_SOURCE_SYSTEM = "MUTATION"
PROTOCOL_SOURCE_SYSTEM = "PROTOCOL"
MUTATION_STRATEGY_VERSION = "mutation.variant.opportunity.v1"
PROTOCOL_STRATEGY_VERSION = "protocol.step.opportunity.v1"
MUTATION_VARIANT_BOUND = "MUTATION_VARIANT_BOUND"
MUTATION_HARVEST_BOUND = 20


def mutation_context_signature(node_id: str, family_id: str, rule_id: str, variant_id: str) -> str:
    return f"mutation:{node_id}:{family_id}:{rule_id}:{variant_id}"


def protocol_context_signature(family_id: str, node_key: str, identity_id: str) -> str:
    return f"hunter_coverage:{family_id}:{node_key}:{identity_id}"


def protocol_structural_identity(family_id: str, node_key: str, identity_id: str) -> str:
    direction = (
        f"Investigate HunterFamily {family_id} against node {node_key} "
        f"for identity {identity_id} (coverage state V3_QUEUED)."
    )
    return opportunity_structural_identity(
        kind=OpportunityKind.PROTOCOL_STEP,
        source_refs=(family_id, node_key, identity_id),
        context_signature=protocol_context_signature(family_id, node_key, identity_id),
        proposed_direction=direction,
    )


class MutationWorkSource:
    source_id = MUTATION_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime):
        from zest.application.research_work_sources import HarvestResult

        seen = _seen_identities(uow, research_run_id)
        existing_mutation = sum(
            1
            for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
            if item.opportunity_kind == OpportunityKind.MUTATION_VARIANT.value
        )
        graph = rebuild_coverage_graph(uow, research_run_id, SURFACE_DISCOVERY_STRATEGY_VERSION)
        engine = MutationEngine()
        created = 0
        skipped = 0
        if existing_mutation >= MUTATION_HARVEST_BOUND:
            return HarvestResult(self.source_id, created, skipped)
        for node in graph.nodes:
            if created + existing_mutation >= MUTATION_HARVEST_BOUND:
                break
            if node.kind not in {
                AttackSurfaceNodeKind.HTTP_OPERATION,
                AttackSurfaceNodeKind.EXACT_PATH,
            }:
                continue
            if node.scope_classification is not ScopeClassification.IN_SCOPE:
                continue
            variants = engine.mutate(
                node, graph, variant_id_prefix=f"{research_run_id}:{node.node_id}"
            )
            for variant in variants:
                if created + existing_mutation >= MUTATION_HARVEST_BOUND:
                    break
                record = _mutation_candidate(research_run_id, variant, now)
                if record.structural_identity in seen:
                    skipped += 1
                    continue
                arguments = _sanitized_mutation_arguments(variant.arguments)
                reject_secret_structure(arguments, "mutation_variant")
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=now,
                        actor_id="control-plane:mutation-source",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type=MUTATION_VARIANT_BOUND,
                        subject_type="research_run",
                        subject_id=research_run_id,
                        correlation_id=variant.variant_id,
                        payload={
                            "variant_id": variant.variant_id,
                            "node_id": variant.node_id,
                            "family_id": variant.family_id,
                            "mutation_rule_id": variant.mutation_rule_id,
                            "capability_id": variant.capability_id,
                            "action": variant.action,
                            "arguments": arguments,
                            "target_reference": variant.target_reference,
                            "baseline_ref": node.canonical_key,
                            "not_evidence": True,
                        },
                    )
                )
                uow.opportunity_selection_candidates.insert(record)
                seen.add(record.structural_identity)
                created += 1
        return HarvestResult(self.source_id, created, skipped)


class ProtocolWorkSource:
    source_id = PROTOCOL_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime):
        from zest.application.research_work_sources import HarvestResult

        seen = _seen_identities(uow, research_run_id)
        graph = rebuild_coverage_graph(uow, research_run_id, SURFACE_DISCOVERY_STRATEGY_VERSION)
        families = [item for item in uow.hunter_families.list_enabled() if item.name in PROTOCOL_FAMILIES]
        created = 0
        skipped = 0
        for family in families:
            view = HunterFamilyView(
                family_id=family.family_id,
                name=family.name,
                target_node_kinds=family.target_node_kinds,
                preconditions=family.preconditions,
                claim_template=family.claim_template,
                evidence_requirements=family.evidence_requirements,
                validation_tier=family.validation_tier,
                enabled=family.enabled,
                version=family.version,
            )
            signals = tuple(
                (family.evidence_requirements or {}).get("required_surface_signals") or ()
            )
            for node in graph.nodes:
                if not _protocol_node_matches(node, view, signals):
                    continue
                identity_id = node.identity_ids[0] if node.identity_ids else "ANONYMOUS"
                record = protocol_candidate(
                    research_run_id,
                    family_id=family.family_id,
                    family_name=family.name,
                    node_key=node.canonical_key,
                    identity_id=identity_id,
                    now=now,
                    source_system=PROTOCOL_SOURCE_SYSTEM,
                )
                if record.structural_identity in seen:
                    skipped += 1
                    continue
                uow.opportunity_selection_candidates.insert(record)
                seen.add(record.structural_identity)
                created += 1
        return HarvestResult(self.source_id, created, skipped)


def hunter_native_protocol_candidate(
    *,
    research_run_id: str,
    family_id: str,
    family_name: str,
    node_key: str,
    identity_id: str,
    now: datetime,
    source_system: str,
) -> OpportunitySelectionCandidateRecord:
    return protocol_candidate(
        research_run_id,
        family_id=family_id,
        family_name=family_name,
        node_key=node_key,
        identity_id=identity_id,
        now=now,
        source_system=source_system,
    )


def protocol_candidate(
    research_run_id: str,
    *,
    family_id: str,
    family_name: str,
    node_key: str,
    identity_id: str,
    now: datetime,
    source_system: str,
) -> OpportunitySelectionCandidateRecord:
    native_se = side_effect_for_family(family_name)
    direction = (
        f"Execute native protocol parser step for {family_name} "
        f"on {node_key} identity {identity_id}."
    )
    context = protocol_context_signature(family_id, node_key, identity_id)
    identity = opportunity_structural_identity(
        kind=OpportunityKind.PROTOCOL_STEP,
        source_refs=(family_id, node_key, identity_id),
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=source_system,
        opportunity_kind=OpportunityKind.PROTOCOL_STEP.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(family_id, node_key, identity_id),
        proposed_direction=direction,
        unresolved_question="Does this protocol step produce a bounded raw-exchange observation?",
        expected_information_value_description=f"family={family_name}; se={native_se}",
        assumptions=(
            "protocol_step_is_not_a_finding",
            f"native_side_effect:{native_se}",
            f"family_name:{family_name}",
        ),
        dimensions=OpportunityDimensions(
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
        ).to_mapping(),
        context_signature=context,
        structural_identity=identity,
        strategy_version=PROTOCOL_STRATEGY_VERSION,
        created_at=now,
    )


def _mutation_candidate(research_run_id, variant, now: datetime) -> OpportunitySelectionCandidateRecord:
    se = 1 if variant.action == "mutate" else 0
    direction = (
        f"Execute mutation family {variant.family_id} rule {variant.mutation_rule_id} "
        f"on baseline {variant.node_id}."
    )
    context = mutation_context_signature(
        variant.node_id, variant.family_id, variant.mutation_rule_id, variant.variant_id
    )
    identity = opportunity_structural_identity(
        kind=OpportunityKind.MUTATION_VARIANT,
        source_refs=(variant.variant_id, variant.node_id, variant.family_id),
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=MUTATION_SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.MUTATION_VARIANT.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=(variant.variant_id, variant.node_id, variant.family_id, variant.mutation_rule_id),
        proposed_direction=direction,
        unresolved_question="Does this bounded mutation change observable HTTP behavior?",
        expected_information_value_description=(
            f"family={variant.family_id}; rule={variant.mutation_rule_id}; "
            f"capability={variant.capability_id}"
        ),
        assumptions=(
            "mutation_generation_is_not_evidence",
            f"baseline_ref:{variant.node_id}",
            f"native_capability:{variant.capability_id}",
            f"side_effect_class:{se}",
        ),
        dimensions=OpportunityDimensions(
            expected_information_value=OrdinalLevel.LOW,
            security_relevance_potential=OrdinalLevel.MEDIUM,
            novelty_composition=OrdinalLevel.MEDIUM,
            unresolved_uncertainty=OrdinalLevel.MEDIUM,
            chain_potential=OrdinalLevel.LOW,
            evidence_coverage=OrdinalLevel.LOW,
            execution_cost=OrdinalLevel.LOW,
            side_effect_requirement=se,
            duplicate_risk=OrdinalLevel.LOW,
            previous_failed_attempts=0,
        ).to_mapping(),
        context_signature=context,
        structural_identity=identity,
        strategy_version=MUTATION_STRATEGY_VERSION,
        created_at=now,
    )


def _sanitized_mutation_arguments(arguments: dict) -> dict:
    """Persist compile args without secret-named headers. Does not invent payloads."""

    payload = dict(arguments)
    headers = payload.get("headers")
    if isinstance(headers, dict):
        payload["headers"] = {
            name: value
            for name, value in headers.items()
            if str(name).lower()
            not in {
                "authorization",
                "cookie",
                "cookie2",
                "set-cookie",
                "proxy-authorization",
            }
        }
        if not payload["headers"]:
            payload.pop("headers")
    return payload


def _protocol_node_matches(node, family: HunterFamilyView, signals: tuple) -> bool:
    if node.kind.value not in family.target_node_kinds:
        return False
    if node.scope_classification is not ScopeClassification.IN_SCOPE:
        return False
    attrs = node.attributes or {}
    observed = attrs.get("protocol_surface_signals") or ()
    if isinstance(observed, str):
        observed = (observed,)
    if not signals:
        return True
    return any(item in observed for item in signals)


def _seen_identities(uow, research_run_id: str) -> set[str]:
    return {
        item.structural_identity
        for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
    } | {
        item.structural_identity
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
    }
