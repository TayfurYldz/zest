"""Persist Differential / Invariant / Chain evaluations through the research fabric."""

from __future__ import annotations

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import (
    AuditEventRecord,
    ChainHypothesisRecord,
    DifferentialObservationRecord,
    InvariantHypothesisRecord,
)
from zest.research.chain import chain_structural_identity
from zest.research.differential import DifferentialDimension, DifferentialInterpretation
from zest.research.evaluators.causal_chain import (
    CHAIN_EVALUATION_STRATEGY,
    ChainNodeView,
    ChainStepStatus,
    evaluate_chain_linkage,
    step_status_from_assessment,
)
from zest.research.evaluators.controlled_differential import (
    DIFFERENTIAL_EVALUATION_STRATEGY,
    DifferentialSecurityJudgement,
    compare_controlled_payloads,
)
from zest.research.evaluators.security_invariant import (
    INVARIANT_EVALUATION_STRATEGY,
    InvariantCheckOutcome,
    PROPERTY_PROTOCOL_DENY_NOT_COVERAGE,
    evaluate_access_deny,
    evaluate_authority_block,
)
from zest.research.exploration import OpportunityKind
from zest.research.evidence import SUPPORTED_EVIDENCE_STRATEGIES

DIFFERENTIAL_EVALUATED = "DIFFERENTIAL_EVALUATED"
INVARIANT_EVALUATED = "INVARIANT_EVALUATED"
CHAIN_EVALUATED = "CHAIN_EVALUATED"
EVIDENCE_PIPELINE_PENDING = "EVIDENCE_PIPELINE_PENDING"


def assumption_map(opportunity) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in opportunity.assumptions:
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        parsed[key] = value
    return parsed


def evaluate_selected_dic(
    uow_factory: UnitOfWorkFactory,
    *,
    research_run_id: str,
    opportunity_id: str,
    clock: Clock | None = None,
) -> dict:
    clock = clock or SystemClock()
    with uow_factory.open() as uow:
        opportunity = uow.research_opportunities.get(opportunity_id)
        if opportunity is None:
            uow.rollback()
            return {}
        kind = opportunity.opportunity_kind
        existing = [
            item
            for item in uow.audit_events.list_for_subject("research_run", research_run_id)
            if item.event_type
            in {DIFFERENTIAL_EVALUATED, INVARIANT_EVALUATED, CHAIN_EVALUATED}
            and (item.payload or {}).get("opportunity_id") == opportunity_id
        ]
        if existing:
            uow.rollback()
            return dict(existing[-1].payload or {})
        if kind == OpportunityKind.DIFFERENTIAL.value:
            payload = _evaluate_differential(uow, opportunity, now=clock.now())
        elif kind == OpportunityKind.INVARIANT.value:
            payload = _evaluate_invariant(uow, opportunity, now=clock.now())
        elif kind == OpportunityKind.CHAIN.value:
            payload = _evaluate_chain(uow, opportunity, now=clock.now())
        else:
            uow.rollback()
            return {}
        uow.commit()
        return payload


def _evaluate_differential(uow, opportunity, *, now) -> dict:
    left_id, right_id = opportunity.source_refs[:2]
    left = uow.observations.get(left_id)
    right = uow.observations.get(right_id)
    if left is None or right is None:
        payload = {
            "opportunity_id": opportunity.opportunity_id,
            "judgement": DifferentialSecurityJudgement.INCOMPARABLE.value,
            "reason_codes": ["MISSING_OBSERVATION"],
            "evidence_pipeline": EVIDENCE_PIPELINE_PENDING,
            "not_a_finding": True,
        }
        _audit(uow, DIFFERENTIAL_EVALUATED, opportunity.research_run_id, now, payload)
        return payload
    assumptions = assumption_map(opportunity)
    changed_identity = assumptions.get("left_identity") != assumptions.get("right_identity")
    same_resource = assumptions.get("path") is not None
    result = compare_controlled_payloads(
        dict(left.payload or {}),
        dict(right.payload or {}),
        changed_identity=changed_identity,
        same_resource=same_resource,
    )
    differential_id = new_opaque_id()
    case_id = new_opaque_id()
    uow.differential_observations.insert(
        DifferentialObservationRecord(
            differential_id=differential_id,
            research_run_id=opportunity.research_run_id,
            case_id=case_id,
            baseline_observation_ids=(left_id,),
            variant_observation_ids=(right_id,),
            changed_dimensions=(DifferentialDimension.ACTOR.value, DifferentialDimension.STATE.value),
            common_dimensions=(DifferentialDimension.RESOURCE.value, DifferentialDimension.ACTION.value),
            observed_differences=dict(result.observed_differences),
            observed_similarities=dict(result.observed_similarities),
            interpretation=result.interpretation.value,
            source_refs=(left_id, right_id),
            strategy_version=DIFFERENTIAL_EVALUATION_STRATEGY,
            alternative_explanation_slots=("intended_behavior", "noise_fields", "insufficient_control"),
            created_at=now,
        )
    )
    assessment = (
        "SIGNAL_RECORDED"
        if result.judgement is DifferentialSecurityJudgement.CONTROLLED_SIGNAL
        else result.judgement.value
    )
    evidence = (
        "CONNECTED"
        if DIFFERENTIAL_EVALUATION_STRATEGY in SUPPORTED_EVIDENCE_STRATEGIES
        else EVIDENCE_PIPELINE_PENDING
    )
    payload = {
        "opportunity_id": opportunity.opportunity_id,
        "differential_id": differential_id,
        "judgement": result.judgement.value,
        "interpretation": result.interpretation.value,
        "reason_codes": list(result.reason_codes),
        "assessment": assessment,
        "evaluation_strategy": DIFFERENTIAL_EVALUATION_STRATEGY,
        "evidence_pipeline": evidence,
        "not_a_finding": True,
        "not_a_vulnerability": True,
    }
    _audit(uow, DIFFERENTIAL_EVALUATED, opportunity.research_run_id, now, payload)
    return payload


def _evaluate_invariant(uow, opportunity, *, now) -> dict:
    assumptions = assumption_map(opportunity)
    property_id = assumptions.get("property", "UNKNOWN")
    if property_id == PROPERTY_PROTOCOL_DENY_NOT_COVERAGE or assumptions.get("authority") == "BLOCKED":
        result = evaluate_authority_block(property_id)
    else:
        left = uow.observations.get(opportunity.source_refs[0])
        right = uow.observations.get(opportunity.source_refs[1]) if len(opportunity.source_refs) > 1 else None
        left_status = (left.payload or {}).get("status_code") if left is not None else None
        right_status = (right.payload or {}).get("status_code") if right is not None else None
        same_obs = (
            len(opportunity.source_refs) > 1
            and opportunity.source_refs[0] == opportunity.source_refs[1]
        )
        if same_obs:
            probe = _latest_unauthenticated_observation(
                uow,
                opportunity.research_run_id,
                path=assumptions.get("path"),
            )
            if probe is not None and probe.observation_id != opportunity.source_refs[0]:
                right = probe
                right_status = (right.payload or {}).get("status_code")
                same_obs = False
        result = evaluate_access_deny(
            property_id=property_id,
            allowed_status=left_status,
            denied_status=right_status,
            has_allowed_observation=left is not None,
            has_denied_observation=right is not None and not same_obs,
        )
    invariant_id = new_opaque_id()
    uow.invariant_hypotheses.insert(
        InvariantHypothesisRecord(
            invariant_id=invariant_id,
            research_run_id=opportunity.research_run_id,
            invariant_kind=assumptions.get("kind", "ACCESS_RELATION"),
            status="CHALLENGED" if result.outcome is InvariantCheckOutcome.VIOLATED else "TESTABLE",
            subject_refs=(opportunity.source_refs[0],),
            expected_behavior=f"property {property_id} must hold on observed target behavior",
            source_refs=opportunity.source_refs,
            applicability_context={
                "property_id": property_id,
                "outcome": result.outcome.value,
                "path": assumptions.get("path"),
            },
            assumptions=(),
            counterexample_refs=(),
            falsification_direction="observe the complementary identity or unauthenticated request succeeding",
            proposer_provenance="invariant.work_source",
            strategy_version=INVARIANT_EVALUATION_STRATEGY,
            created_at=now,
        )
    )
    evidence = (
        "CONNECTED"
        if result.may_support_evidence and INVARIANT_EVALUATION_STRATEGY in SUPPORTED_EVIDENCE_STRATEGIES
        else EVIDENCE_PIPELINE_PENDING
    )
    payload = {
        "opportunity_id": opportunity.opportunity_id,
        "invariant_id": invariant_id,
        "property_id": property_id,
        "outcome": result.outcome.value,
        "reason_codes": list(result.reason_codes),
        "evaluation_strategy": INVARIANT_EVALUATION_STRATEGY,
        "evidence_pipeline": evidence,
        "not_a_finding": True,
    }
    _audit(uow, INVARIANT_EVALUATED, opportunity.research_run_id, now, payload)
    return payload


def _evaluate_chain(uow, opportunity, *, now) -> dict:
    assumptions = assumption_map(opportunity)
    left_id, right_id = opportunity.source_refs[:2]
    left_rec = uow.hypothesis_assessments.get(left_id)
    right_rec = uow.hypothesis_assessments.get(right_id)
    left = ChainNodeView(
        node_id=left_id,
        engine=assumptions.get("left_engine", "UNKNOWN"),
        resource_key=assumptions.get("left_resource", ""),
        assessment_outcome=None if left_rec is None else left_rec.assessment_outcome,
        authority_status="AUTHORIZED",
        step_status=step_status_from_assessment(
            None if left_rec is None else left_rec.assessment_outcome,
            authority_blocked=False,
        ),
    )
    right = ChainNodeView(
        node_id=right_id,
        engine=assumptions.get("right_engine", "UNKNOWN"),
        resource_key=assumptions.get("right_resource", ""),
        assessment_outcome=None if right_rec is None else right_rec.assessment_outcome,
        authority_status="AUTHORIZED",
        step_status=step_status_from_assessment(
            None if right_rec is None else right_rec.assessment_outcome,
            authority_blocked=False,
        ),
    )
    result = evaluate_chain_linkage(left, right, claimed_edge=assumptions.get("edge", "ENABLES"))
    steps = (
        {
            "step_index": 0,
            "node_kind": "OBSERVATION",
            "source_ref": left.node_id,
            "epistemic_status": "OBSERVED" if left.step_status is ChainStepStatus.PROVEN else "HYPOTHESIZED",
            "state_signature": left.resource_key or left.node_id,
            "side_effect_level": 0,
            "statement": f"chain node {left.engine}",
            "incoming_edge": None,
            "step_status": left.step_status.value,
        },
        {
            "step_index": 1,
            "node_kind": "OBSERVATION",
            "source_ref": right.node_id,
            "epistemic_status": "OBSERVED" if right.step_status is ChainStepStatus.PROVEN else "HYPOTHESIZED",
            "state_signature": right.resource_key or right.node_id,
            "side_effect_level": 0,
            "statement": f"chain node {right.engine}",
            "incoming_edge": "ENABLES",
            "step_status": right.step_status.value,
        },
    )
    from zest.research.chain import ChainEdgeKind, ChainNodeKind, ChainStep
    from zest.research.target_model import TargetEpistemicStatus

    domain_steps = (
        ChainStep(
            step_index=0,
            node_kind=ChainNodeKind.OBSERVATION,
            source_ref=left.node_id,
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            state_signature=left.resource_key or left.node_id,
            side_effect_level=0,
            statement="left chain node",
        ),
        ChainStep(
            step_index=1,
            node_kind=ChainNodeKind.STATE,
            source_ref=right.node_id,
            epistemic_status=TargetEpistemicStatus.OBSERVED,
            state_signature=right.resource_key or right.node_id,
            side_effect_level=0,
            statement="right chain node",
            incoming_edge=ChainEdgeKind.ENABLES,
        ),
    )
    structural = chain_structural_identity(domain_steps)
    chain_id = new_opaque_id()
    uow.chain_hypotheses.insert(
        ChainHypothesisRecord(
            chain_id=chain_id,
            research_run_id=opportunity.research_run_id,
            structural_identity=structural,
            steps=steps,
            source_refs=(left_id, right_id),
            preconditions=(f"resource:{left.resource_key}",),
            expected_resulting_capability="COMPOSED_RESEARCH_STATE",
            unresolved_assumptions=() if result.linkage.value == "SUPPORTED" else ("INSUFFICIENT_LINKAGE",),
            falsification_points=("missing shared resource", "untested step"),
            descriptive_features={
                "linkage": result.linkage.value,
                "impact_status": result.impact_status.value,
                "not_an_exploit": True,
            },
            strategy_version=CHAIN_EVALUATION_STRATEGY,
            created_at=now,
        )
    )
    evidence = (
        "CONNECTED"
        if result.may_support_evidence and CHAIN_EVALUATION_STRATEGY in SUPPORTED_EVIDENCE_STRATEGIES
        else EVIDENCE_PIPELINE_PENDING
    )
    payload = {
        "opportunity_id": opportunity.opportunity_id,
        "chain_id": chain_id,
        "linkage": result.linkage.value,
        "impact_status": result.impact_status.value,
        "reason_codes": list(result.reason_codes),
        "nodes": list(result.nodes),
        "edges": list(result.edges),
        "left_step_status": left.step_status.value,
        "right_step_status": right.step_status.value,
        "evaluation_strategy": CHAIN_EVALUATION_STRATEGY,
        "evidence_pipeline": evidence,
        "not_a_finding": True,
        "impact_not_verified": True,
    }
    _audit(uow, CHAIN_EVALUATED, opportunity.research_run_id, now, payload)
    return payload


def _latest_unauthenticated_observation(uow, research_run_id: str, path: str | None):
    if not path:
        return None
    latest = None
    for result in uow.worker_results.list_for_research_run(research_run_id):
        plan = uow.experiment_plans.get(result.experiment_id)
        if plan is None:
            continue
        args = plan.arguments or {}
        if str(args.get("path") or "") != path:
            continue
        if args.get("identity_id") or args.get("session_context_reference"):
            continue
        observations = uow.observations.list_for_worker_result(result.worker_result_id)
        if observations:
            latest = observations[0]
    return latest


def _audit(uow, event_type: str, research_run_id: str, now, payload: dict) -> None:
    uow.audit_events.insert(
        AuditEventRecord(
            audit_event_id=new_opaque_id(),
            occurred_at=now,
            actor_id="control-plane:dic-evaluator",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type=event_type,
            subject_type="research_run",
            subject_id=research_run_id,
            payload=payload,
        )
    )
