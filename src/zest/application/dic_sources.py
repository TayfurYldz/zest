"""Direct Differential / Invariant / Chain research-work sources. Not a second scheduler."""

from __future__ import annotations

from datetime import datetime

from zest.application.identity import new_opaque_id
from zest.application.research_work_sources import HarvestResult
from zest.data.records import OpportunitySelectionCandidateRecord
from zest.research.evaluators.causal_chain import (
    CHAIN_EVALUATION_STRATEGY,
    step_status_from_assessment,
)
from zest.research.evaluators.controlled_differential import DIFFERENTIAL_EVALUATION_STRATEGY
from zest.research.evaluators.security_invariant import (
    INVARIANT_EVALUATION_STRATEGY,
    PROPERTY_CROSS_OWNER_DENY,
    PROPERTY_PROTOCOL_DENY_NOT_COVERAGE,
    PROPERTY_UNAUTHENTICATED_DENY,
)
from zest.research.exploration import (
    OpportunityDimensions,
    OpportunityKind,
    OpportunityMode,
    OrdinalLevel,
    opportunity_structural_identity,
)

DIFFERENTIAL_SOURCE_SYSTEM = "DIFFERENTIAL"
INVARIANT_SOURCE_SYSTEM = "INVARIANT"
CHAIN_SOURCE_SYSTEM = "CHAIN"
DIFFERENTIAL_STRATEGY_VERSION = "differential.controlled.opportunity.v1"
INVARIANT_STRATEGY_VERSION = "invariant.security_property.opportunity.v1"
CHAIN_STRATEGY_VERSION = "chain.causal.opportunity.v1"
DIC_HARVEST_BOUND = 20
DIAGNOSTIC_STRATEGY_PREFIXES = (
    "differential.diagnostic",
    "invariant.diagnostic",
    "chain.diagnostic",
)


def is_diagnostic_reasoning_strategy(strategy_version: str) -> bool:
    return any(strategy_version.startswith(prefix) for prefix in DIAGNOSTIC_STRATEGY_PREFIXES)


class DifferentialWorkSource:
    source_id = DIFFERENTIAL_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        existing = sum(
            1
            for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
            if item.opportunity_kind == OpportunityKind.DIFFERENTIAL.value
        )
        for pair in _observation_pairs(uow, research_run_id):
            if created + existing >= DIC_HARVEST_BOUND:
                break
            record = _differential_candidate(research_run_id, pair, now)
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


class InvariantWorkSource:
    source_id = INVARIANT_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        existing = sum(
            1
            for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
            if item.opportunity_kind == OpportunityKind.INVARIANT.value
        )
        for item in _invariant_opportunities(uow, research_run_id):
            if created + existing >= DIC_HARVEST_BOUND:
                break
            record = _invariant_candidate(research_run_id, item, now)
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


class ChainWorkSource:
    source_id = CHAIN_SOURCE_SYSTEM

    def harvest(self, uow, *, research_run_id: str, now: datetime) -> HarvestResult:
        seen = _seen_identities(uow, research_run_id)
        created = 0
        skipped = 0
        existing = sum(
            1
            for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
            if item.opportunity_kind == OpportunityKind.CHAIN.value
        )
        for item in _chain_opportunities(uow, research_run_id):
            if created + existing >= DIC_HARVEST_BOUND:
                break
            record = _chain_candidate(research_run_id, item, now)
            if record.structural_identity in seen:
                skipped += 1
                continue
            uow.opportunity_selection_candidates.insert(record)
            seen.add(record.structural_identity)
            created += 1
        return HarvestResult(self.source_id, created, skipped)


def _seen_identities(uow, research_run_id: str) -> set[str]:
    return {
        item.structural_identity
        for item in uow.opportunity_selection_candidates.list_for_research_run(research_run_id)
    } | {
        item.structural_identity
        for item in uow.research_opportunities.list_for_research_run(research_run_id)
    }


def _observation_pairs(uow, research_run_id: str) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for result in uow.worker_results.list_for_research_run(research_run_id):
        plan = uow.experiment_plans.get(result.experiment_id)
        if plan is None:
            continue
        args = plan.arguments or {}
        path = str(args.get("path") or "")
        method = str(args.get("method") or "GET")
        if not path:
            continue
        observations = uow.observations.list_for_worker_result(result.worker_result_id)
        if not observations:
            continue
        identity = str(args.get("identity_id") or "").strip()
        if not identity:
            continue
        groups.setdefault((method, path), []).append(
            {
                "observation_id": observations[0].observation_id,
                "identity_id": identity,
                "path": path,
                "method": method,
                "origin": str(args.get("authorized_origin") or plan.target_reference),
                "payload": dict(observations[0].payload or {}),
            }
        )
    pairs: list[dict] = []
    for (_method, _path), items in groups.items():
        unique: dict[str, dict] = {}
        for item in items:
            unique.setdefault(item["identity_id"], item)
        if len(unique) < 2:
            continue
        ordered = list(unique.values())[:2]
        pairs.append({"left": ordered[0], "right": ordered[1]})
    return pairs


def _invariant_opportunities(uow, research_run_id: str) -> list[dict]:
    items: list[dict] = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for result in uow.worker_results.list_for_research_run(research_run_id):
        plan = uow.experiment_plans.get(result.experiment_id)
        if plan is None:
            continue
        args = plan.arguments or {}
        path = str(args.get("path") or "")
        if not path:
            continue
        observations = uow.observations.list_for_worker_result(result.worker_result_id)
        if not observations:
            continue
        identity = str(args.get("identity_id") or "")
        session = str(args.get("session_context_reference") or "")
        groups.setdefault((str(args.get("method") or "GET"), path), []).append(
            {
                "observation_id": observations[0].observation_id,
                "identity_id": identity,
                "authenticated": bool(identity or session),
                "status": (observations[0].payload or {}).get("status_code"),
                "path": path,
                "method": str(args.get("method") or "GET"),
                "origin": str(args.get("authorized_origin") or plan.target_reference),
            }
        )
    for (method, path), rows in groups.items():
        authed = [row for row in rows if row["authenticated"]]
        unauthed = [row for row in rows if not row["authenticated"]]
        if not authed:
            continue
        execute = not unauthed
        items.append(
            {
                "property_id": PROPERTY_UNAUTHENTICATED_DENY,
                "kind": "ACCESS_RELATION",
                "subject": authed[0]["observation_id"],
                "peer": unauthed[0]["observation_id"] if unauthed else authed[0]["observation_id"],
                "path": path,
                "method": method,
                "origin": authed[0]["origin"],
                "execute": execute,
                "authed_status": authed[0]["status"],
                "unauthed_status": unauthed[0]["status"] if unauthed else None,
            }
        )
        owners = [row for row in authed if row["identity_id"]]
        if len({row["identity_id"] for row in owners}) >= 2:
            items.append(
                {
                    "property_id": PROPERTY_CROSS_OWNER_DENY,
                    "kind": "OWNERSHIP_RELATION",
                    "subject": owners[0]["observation_id"],
                    "peer": owners[1]["observation_id"],
                    "path": path,
                    "method": method,
                    "origin": owners[0]["origin"],
                    "execute": False,
                    "authed_status": owners[0]["status"],
                    "unauthed_status": owners[1]["status"],
                }
            )
    for event in uow.audit_events.list_for_subject("research_run", research_run_id):
        if event.event_type != "RESEARCH_WORK_CORE_DENIED":
            continue
        payload = event.payload or {}
        if payload.get("native_capability") != "http.raw_exchange" and payload.get(
            "compiled_capability"
        ) != "http.raw_exchange":
            continue
        work_id = str(payload.get("selected_work_id") or event.audit_event_id)
        items.append(
            {
                "property_id": PROPERTY_PROTOCOL_DENY_NOT_COVERAGE,
                "kind": "OTHER",
                "subject": work_id,
                "peer": work_id,
                "path": "/",
                "method": "RAW",
                "origin": str(payload.get("target_reference") or "authority"),
                "execute": False,
                "authority_blocked": True,
            }
        )
    return items


def _chain_opportunities(uow, research_run_id: str) -> list[dict]:
    assessments = [
        item
        for item in uow.hypothesis_assessments.list_for_research_run(research_run_id)
        if item.assessment_outcome
        in {"CONSISTENT_WITH_PREDICTION", "CONTRADICTS_PREDICTION", "INCONCLUSIVE"}
    ]
    if len(assessments) < 2:
        return []
    views = []
    for item in assessments[:8]:
        plan = uow.experiment_plans.get(item.experiment_id)
        args = plan.arguments if plan is not None else {}
        resource = str((args or {}).get("path") or item.experiment_id)
        views.append(
            {
                "assessment_id": item.assessment_id,
                "experiment_id": item.experiment_id,
                "engine": item.evaluation_strategy,
                "resource_key": resource,
                "outcome": item.assessment_outcome,
                "step_status": step_status_from_assessment(
                    item.assessment_outcome, authority_blocked=False
                ).value,
            }
        )
    linked = None
    unlinked = None
    for index, left in enumerate(views):
        for right in views[index + 1 :]:
            if left["resource_key"] == right["resource_key"] and linked is None:
                linked = {"left": left, "right": right, "edge": "ENABLES"}
            if left["resource_key"] != right["resource_key"] and unlinked is None:
                unlinked = {"left": left, "right": right, "edge": "ENABLES"}
    out = []
    if linked is not None:
        out.append(linked)
    if unlinked is not None:
        out.append(unlinked)
    return out[:2]


def _dimensions(se: int) -> dict:
    return OpportunityDimensions(
        expected_information_value=OrdinalLevel.HIGH,
        security_relevance_potential=OrdinalLevel.HIGH,
        novelty_composition=OrdinalLevel.MEDIUM,
        unresolved_uncertainty=OrdinalLevel.HIGH,
        chain_potential=OrdinalLevel.MEDIUM,
        evidence_coverage=OrdinalLevel.LOW,
        execution_cost=OrdinalLevel.LOW,
        side_effect_requirement=se,
        duplicate_risk=OrdinalLevel.LOW,
        previous_failed_attempts=0,
    ).to_mapping()


def _differential_candidate(research_run_id: str, pair: dict, now: datetime):
    left = pair["left"]
    right = pair["right"]
    source_refs = (left["observation_id"], right["observation_id"])
    context = f"differential:{left['method']}:{left['path']}:{left['identity_id']}:{right['identity_id']}"
    direction = (
        f"Compare controlled observations {left['observation_id']} vs "
        f"{right['observation_id']} at {left['method']} {left['path']}."
    )
    identity = opportunity_structural_identity(
        kind=OpportunityKind.DIFFERENTIAL,
        source_refs=source_refs,
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=DIFFERENTIAL_SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.DIFFERENTIAL.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=source_refs,
        proposed_direction=direction,
        unresolved_question="What controlled relation holds between these observations?",
        expected_information_value_description=(
            f"identity {left['identity_id']} vs {right['identity_id']} on {left['path']}"
        ),
        assumptions=(
            f"path:{left['path']}",
            f"method:{left['method']}",
            f"origin:{left['origin']}",
            f"left_identity:{left['identity_id']}",
            f"right_identity:{right['identity_id']}",
            f"evaluation_strategy:{DIFFERENTIAL_EVALUATION_STRATEGY}",
            "mode:evaluate",
            "not_a_vulnerability",
        ),
        dimensions=_dimensions(0),
        context_signature=context,
        structural_identity=identity,
        strategy_version=DIFFERENTIAL_STRATEGY_VERSION,
        created_at=now,
    )


def _invariant_candidate(research_run_id: str, item: dict, now: datetime):
    source_refs = (item["subject"], item["peer"])
    context = f"invariant:{item['property_id']}:{item['method']}:{item['path']}"
    mode = "execute" if item.get("execute") else "evaluate"
    se = 0
    direction = (
        f"Check invariant {item['property_id']} on {item['method']} {item['path']}."
    )
    identity = opportunity_structural_identity(
        kind=OpportunityKind.INVARIANT,
        source_refs=source_refs,
        context_signature=context,
        proposed_direction=direction,
    )
    assumptions = [
        f"property:{item['property_id']}",
        f"kind:{item['kind']}",
        f"path:{item['path']}",
        f"method:{item['method']}",
        f"origin:{item['origin']}",
        f"mode:{mode}",
        f"evaluation_strategy:{INVARIANT_EVALUATION_STRATEGY}",
    ]
    if item.get("authority_blocked"):
        assumptions.append("authority:BLOCKED")
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=INVARIANT_SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.INVARIANT.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=source_refs,
        proposed_direction=direction,
        unresolved_question="Does the expected security property hold on observed behavior?",
        expected_information_value_description=item["property_id"],
        assumptions=tuple(assumptions),
        dimensions=_dimensions(se),
        context_signature=context,
        structural_identity=identity,
        strategy_version=INVARIANT_STRATEGY_VERSION,
        created_at=now,
    )


def _chain_candidate(research_run_id: str, item: dict, now: datetime):
    left = item["left"]
    right = item["right"]
    source_refs = (left["assessment_id"], right["assessment_id"])
    context = (
        f"chain:{left['assessment_id']}:{right['assessment_id']}:{item['edge']}"
    )
    direction = (
        f"Compose chain from assessments {left['assessment_id']} and {right['assessment_id']}."
    )
    identity = opportunity_structural_identity(
        kind=OpportunityKind.CHAIN,
        source_refs=source_refs,
        context_signature=context,
        proposed_direction=direction,
    )
    return OpportunitySelectionCandidateRecord(
        candidate_id=new_opaque_id(),
        research_run_id=research_run_id,
        source_system=CHAIN_SOURCE_SYSTEM,
        opportunity_kind=OpportunityKind.CHAIN.value,
        mode=OpportunityMode.EXPLORATION.value,
        source_refs=source_refs,
        proposed_direction=direction,
        unresolved_question="Is there a proven causal link between these research states?",
        expected_information_value_description=f"edge={item['edge']}",
        assumptions=(
            f"edge:{item['edge']}",
            f"left_resource:{left['resource_key']}",
            f"right_resource:{right['resource_key']}",
            f"left_step:{left['step_status']}",
            f"right_step:{right['step_status']}",
            f"left_engine:{left['engine']}",
            f"right_engine:{right['engine']}",
            f"evaluation_strategy:{CHAIN_EVALUATION_STRATEGY}",
            "mode:evaluate",
            "impact:HYPOTHESIS",
        ),
        dimensions=_dimensions(0),
        context_signature=context,
        structural_identity=identity,
        strategy_version=CHAIN_STRATEGY_VERSION,
        created_at=now,
    )
