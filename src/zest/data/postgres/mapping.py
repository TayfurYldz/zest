"""Map spine tables to Data records. Adapter-only."""

from __future__ import annotations

from typing import Any, Mapping

from zest.data.records import (
    ApprovalRecord,
    AttackSurfaceSnapshotRecord,
    AuditEventRecord,
    RunFaultRecord,
    AuthorizationSourceRecord,
    BountyTableRecord,
    CandidateAdmissionRecord,
    CandidateRecord,
    ChainHypothesisRecord,
    CoverageDebtSnapshotRecord,
    DifferentialObservationRecord,
    EvidenceAdmissionRecord,
    EvidenceRecord,
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    ExperimentRecord,
    FindingProposalRecord,
    FindingRecord,
    HypothesisAssessmentRecord,
    HypothesisRecord,
    HumanReviewRecord,
    ImpactChainEdgeRecord,
    RuntimeInstanceRecord,
    ImpactChainNodeRecord,
    ImpactChainRecord,
    InvariantHypothesisRecord,
    InvariantCounterexampleRefRecord,
    IssuedBudgetRecord,
    ObservationRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    RateLimitProfileRecord,
    ResearchAdmissionRecord,
    ResearchCycleRecord,
    ResearchOrchestrationRecord,
    ResearchReasoningRecord,
    ResearchRunRecord,
    ScopeRuleV2Record,
    SensorObservationRecord,
    TargetInferenceRecord,
    VerificationRecord,
    WorkerResultRecord,
    OpportunitySelectionCandidateRecord,
    ResearchOpportunityRecord,
    ResearchSelectionRecord,
    SnapshotRecord,
    SnapshotMemberRecord,
    ChangeEventRecord,
    BudgetConsumptionRecord,
    SessionContextRecord,
    ControlEventRecord,
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    DiscoveryInferenceRecord,
    DiscoveryInferenceSourceRecord,
    DiscoveryProjectionReceiptRecord,
    DiscoveryRunConfigRecord,
    FrontierEventRecord,
    FrontierItemRecord,
    FrontierSourceRecord,
    HunterFamilyRecord,
    HuntV3QueueRecord,
    OastAdmissionRecord,
    OastCallbackDeliveryRecord,
    OastCorrelationRecord,
    OastTokenRecord,
    OastTokenRecord,
    PreflightReportRecord,
    PromotionRunRecord,
)


def _mapping(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def program_from_row(row: Mapping[str, Any]) -> ProgramRecord:
    data = _mapping(row)
    return ProgramRecord(
        program_id=data["program_id"],
        created_at=data["created_at"],
        name=data.get("name"),
        handle=data.get("handle"),
        platform=data.get("platform"),
    )


def scope_rule_v2_from_row(row: Mapping[str, Any]) -> ScopeRuleV2Record:
    data = _mapping(row)
    return ScopeRuleV2Record(
        rule_id=data["rule_id"],
        program_id=data["program_id"],
        effect=data["effect"],
        scheme=data["scheme"],
        source_reference=data["source_reference"],
        created_at=data["created_at"],
        host=data.get("host"),
        host_pattern=data.get("host_pattern"),
        port=data.get("port"),
        path_prefix=data.get("path_prefix"),
        expires_at=data.get("expires_at"),
    )


def program_policy_from_row(row: Mapping[str, Any]) -> ProgramPolicyRecord:
    data = _mapping(row)
    return ProgramPolicyRecord(
        program_id=data["program_id"],
        loopback_fixture=bool(data["loopback_fixture"]),
        max_response_bytes=int(data["max_response_bytes"]),
        timeout_ms=int(data["timeout_ms"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        action_policy=dict(data["action_policy"] or {}),
        daily_llm_budget_microdollars=data.get("daily_llm_budget_microdollars"),
    )


def sensor_observation_from_row(row: Mapping[str, Any]) -> SensorObservationRecord:
    data = _mapping(row)
    return SensorObservationRecord(
        observation_id=data["observation_id"],
        research_run_id=data["research_run_id"],
        sensor_id=data["sensor_id"],
        target_reference=data["target_reference"],
        collected_at=data["collected_at"],
        payload_digest=data["payload_digest"],
        epistemic_status=data["epistemic_status"],
        source_metadata=dict(data["source_metadata"] or {}),
        payload=dict(data["payload"] or {}),
        created_at=data["created_at"],
    )


def rate_limit_profile_from_row(row: Mapping[str, Any]) -> RateLimitProfileRecord:
    data = _mapping(row)
    return RateLimitProfileRecord(
        profile_id=data["profile_id"],
        program_id=data["program_id"],
        max_requests_per_window=int(data["max_requests_per_window"]),
        window_seconds=int(data["window_seconds"]),
        created_at=data["created_at"],
    )


def oast_token_from_row(row: Mapping[str, Any]) -> OastTokenRecord:
    data = _mapping(row)
    return OastTokenRecord(
        token_id=data["token_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        target_reference=data["target_reference"],
        expires_at=data["expires_at"],
        created_at=data["created_at"],
    )


def oast_correlation_from_row(row: Mapping[str, Any]) -> OastCorrelationRecord:
    data = _mapping(row)
    return OastCorrelationRecord(
        correlation_id=data["correlation_id"],
        attempt_id=data["attempt_id"],
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        target_reference=data["target_reference"],
        identity_id=data["identity_id"],
        armed_at=data["armed_at"],
        expires_at=data["expires_at"],
        created_at=data["created_at"],
    )


def oast_callback_delivery_from_row(
    row: Mapping[str, Any],
) -> OastCallbackDeliveryRecord:
    data = _mapping(row)
    return OastCallbackDeliveryRecord(
        delivery_id=data["delivery_id"],
        correlation_id=data["correlation_id"],
        provider_adapter_id=data["provider_adapter_id"],
        provider_event_id=data["provider_event_id"],
        received_at=data["received_at"],
        normalized_payload=data["normalized_payload"],
        normalized_digest=data["normalized_digest"],
    )


def oast_admission_from_row(row: Mapping[str, Any]) -> OastAdmissionRecord:
    data = _mapping(row)
    return OastAdmissionRecord(
        admission_id=data["admission_id"],
        correlation_id=data["correlation_id"],
        research_run_id=data["research_run_id"],
        sensor_observation_id=data["sensor_observation_id"],
        discovery_fact_id=data["discovery_fact_id"],
        created_at=data["created_at"],
    )


def bounty_table_from_row(row: Mapping[str, Any]) -> BountyTableRecord:
    data = _mapping(row)
    return BountyTableRecord(
        program_id=data["program_id"],
        severity=data["severity"],
        created_at=data["created_at"],
        reward_range=dict(data["reward_range"]) if data.get("reward_range") is not None else None,
    )


def authorization_source_from_row(row: Mapping[str, Any]) -> AuthorizationSourceRecord:
    data = _mapping(row)
    return AuthorizationSourceRecord(
        authorization_source_id=data["authorization_source_id"],
        program_id=data["program_id"],
        state=data["state"],
        provenance_reference=data["provenance_reference"],
        created_at=data["created_at"],
        effective_from=data.get("effective_from"),
        effective_until=data.get("effective_until"),
    )


def research_run_from_row(row: Mapping[str, Any]) -> ResearchRunRecord:
    data = _mapping(row)
    return ResearchRunRecord(
        research_run_id=data["research_run_id"],
        program_id=data["program_id"],
        authorization_source_id=data["authorization_source_id"],
        initiated_by_actor_id=data["initiated_by_actor_id"],
        initiated_by_actor_type=data["initiated_by_actor_type"],
        started_at=data["started_at"],
    )


def issued_budget_from_row(row: Mapping[str, Any]) -> IssuedBudgetRecord:
    data = _mapping(row)
    return IssuedBudgetRecord(
        budget_id=data["budget_id"],
        research_run_id=data["research_run_id"],
        max_requests=data["max_requests"],
        max_tool_calls=data["max_tool_calls"],
        max_runtime_ms=data["max_runtime_ms"],
        max_concurrency=data["max_concurrency"],
        issued_at=data["issued_at"],
    )


def hypothesis_from_row(row: Mapping[str, Any]) -> HypothesisRecord:
    data = _mapping(row)
    return HypothesisRecord(
        hypothesis_id=data["hypothesis_id"],
        research_run_id=data["research_run_id"],
        claim=data["claim"],
        created_at=data["created_at"],
        origin_reference=data.get("origin_reference"),
        identity_id=data.get("identity_id"),
    )


def experiment_from_row(row: Mapping[str, Any]) -> ExperimentRecord:
    data = _mapping(row)
    return ExperimentRecord(
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        budget_id=data["budget_id"],
        execution_state=data["execution_state"],
        created_at=data["created_at"],
    )


def execution_attempt_from_row(row: Mapping[str, Any]) -> ExecutionAttemptRecord:
    data = _mapping(row)
    return ExecutionAttemptRecord(
        attempt_id=data["attempt_id"],
        request_id=data["request_id"],
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        correlation_id=data["correlation_id"],
        worker_capability=data["worker_capability"],
        action=data["action"],
        target_reference=data["target_reference"],
        budget_id=data["budget_id"],
        side_effect_level=data["side_effect_level"],
        authorization_decision_reference=data["authorization_decision_reference"],
        state=data["state"],
        created_at=data["created_at"],
        authorized_at=data.get("authorized_at"),
        dispatch_started_at=data.get("dispatch_started_at"),
        completed_at=data.get("completed_at"),
        target_contact_status=data.get("target_contact_status", "UNKNOWN"),
    )


def worker_result_from_row(row: Mapping[str, Any]) -> WorkerResultRecord:
    data = _mapping(row)
    return WorkerResultRecord(
        worker_result_id=data["worker_result_id"],
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        request_id=data["request_id"],
        correlation_id=data["correlation_id"],
        worker_capability=data["worker_capability"],
        action=data["action"],
        authorization_decision_reference=data["authorization_decision_reference"],
        budget_id=data["budget_id"],
        side_effect_level=data["side_effect_level"],
        contract_version=data["contract_version"],
        worker_id=data["worker_id"],
        status=data["status"],
        received_at=data["received_at"],
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        parent_request_id=data.get("parent_request_id"),
        raw_result=data.get("raw_result"),
        raw_artifact_descriptors=data.get("raw_artifact_descriptors"),
        diagnostics=data.get("diagnostics"),
        control_signal=data.get("control_signal"),
    )


def observation_from_row(row: Mapping[str, Any]) -> ObservationRecord:
    data = _mapping(row)
    return ObservationRecord(
        observation_id=data["observation_id"],
        worker_result_id=data["worker_result_id"],
        observation_kind=data["observation_kind"],
        payload=data["payload"],
        normalization_version=data["normalization_version"],
        observed_at=data["observed_at"],
        created_at=data["created_at"],
    )


def audit_event_from_row(row: Mapping[str, Any]) -> AuditEventRecord:
    data = _mapping(row)
    return AuditEventRecord(
        audit_event_id=data["audit_event_id"],
        occurred_at=data["occurred_at"],
        actor_id=data["actor_id"],
        actor_type=data["actor_type"],
        event_type=data["event_type"],
        subject_type=data["subject_type"],
        subject_id=data["subject_id"],
        payload=data["payload"],
        correlation_id=data.get("correlation_id"),
    )


def run_fault_from_row(row: Mapping[str, Any]) -> RunFaultRecord:
    data = _mapping(row)
    return RunFaultRecord(
        fault_id=data["fault_id"],
        research_run_id=data["research_run_id"],
        component=data["component"],
        phase=data["phase"],
        fault_class=data["fault_class"],
        fault_code=data["fault_code"],
        fatal=bool(data["fatal"]),
        occurred_at=data["occurred_at"],
        diagnostic_summary=data["diagnostic_summary"],
        hypothesis_id=data.get("hypothesis_id"),
        experiment_id=data.get("experiment_id"),
        attempt_id=data.get("attempt_id"),
        request_id=data.get("request_id"),
        capability=data.get("capability"),
        action=data.get("action"),
        runtime_instance_id=data.get("runtime_instance_id"),
        correlation_id=data.get("correlation_id"),
        resolved_at=data.get("resolved_at"),
    )


def research_reasoning_from_row(row: Mapping[str, Any]) -> ResearchReasoningRecord:
    data = _mapping(row)
    return ResearchReasoningRecord(
        reasoning_record_id=data["reasoning_record_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        role=data["role"],
        adapter_identity=data["adapter_identity"],
        provider_adapter_identity=data["provider_adapter_identity"],
        correlation_id=data["correlation_id"],
        context_fingerprint=data["context_fingerprint"],
        structured_output=data["structured_output"],
        created_at=data["created_at"],
        model_id=data.get("model_id"),
        model_version=data.get("model_version"),
    )


def research_admission_from_row(row: Mapping[str, Any]) -> ResearchAdmissionRecord:
    data = _mapping(row)
    return ResearchAdmissionRecord(
        admission_record_id=data["admission_record_id"],
        research_run_id=data["research_run_id"],
        outcome=data["outcome"],
        reason=data["reason"],
        reason_code=data["reason_code"],
        context_fingerprint=data["context_fingerprint"],
        created_at=data["created_at"],
        generator_reasoning_record_id=data.get("generator_reasoning_record_id"),
        falsifier_reasoning_record_id=data.get("falsifier_reasoning_record_id"),
        admitted_hypothesis_id=data.get("admitted_hypothesis_id"),
    )


def experiment_plan_from_row(row: Mapping[str, Any]) -> ExperimentPlanRecord:
    data = _mapping(row)
    return ExperimentPlanRecord(
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        required_capability=data["required_capability"],
        action=data["action"],
        target_reference=data["target_reference"],
        side_effect_level=data["side_effect_level"],
        arguments=data["arguments"],
        requested_budget_id=data["requested_budget_id"],
        expected_observation=data["expected_observation"],
        disconfirming_observation=data["disconfirming_observation"],
        evaluation_strategy=data["evaluation_strategy"],
        created_at=data["created_at"],
        capability_version=data.get("capability_version"),
        capability_definition_fingerprint=data.get("capability_definition_fingerprint"),
    )


def hypothesis_assessment_from_row(row: Mapping[str, Any]) -> HypothesisAssessmentRecord:
    data = _mapping(row)
    observation_ids = data["observation_ids"]
    if isinstance(observation_ids, tuple):
        ids = observation_ids
    elif isinstance(observation_ids, list):
        ids = tuple(observation_ids)
    else:
        ids = ()
    return HypothesisAssessmentRecord(
        assessment_id=data["assessment_id"],
        hypothesis_id=data["hypothesis_id"],
        experiment_id=data["experiment_id"],
        research_run_id=data["research_run_id"],
        assessment_outcome=data["assessment_outcome"],
        observation_ids=ids,
        evaluator_kind=data["evaluator_kind"],
        evaluator_version=data["evaluator_version"],
        rationale=data["rationale"],
        evaluation_strategy=data["evaluation_strategy"],
        created_at=data["created_at"],
    )


def _id_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def evidence_from_row(row: Mapping[str, Any]) -> EvidenceRecord:
    data = _mapping(row)
    return EvidenceRecord(
        evidence_id=data["evidence_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        experiment_id=data["experiment_id"],
        admission_record_id=data["admission_record_id"],
        polarity=data["polarity"],
        claim_scope=data["claim_scope"],
        observation_ids=_id_tuple(data["observation_ids"]),
        assessment_ids=_id_tuple(data["assessment_ids"]),
        created_at=data["created_at"],
    )


def evidence_admission_from_row(row: Mapping[str, Any]) -> EvidenceAdmissionRecord:
    data = _mapping(row)
    return EvidenceAdmissionRecord(
        admission_record_id=data["admission_record_id"],
        proposal_id=data["proposal_id"],
        research_run_id=data["research_run_id"],
        outcome=data["outcome"],
        reason_codes=_id_tuple(data["reason_codes"]),
        observation_ids=_id_tuple(data["observation_ids"]),
        assessment_ids=_id_tuple(data["assessment_ids"]),
        admission_policy_version=data["admission_policy_version"],
        evaluator_version=data["evaluator_version"],
        created_at=data["created_at"],
        admitted_evidence_id=data.get("admitted_evidence_id"),
        claim_scope=data.get("claim_scope"),
        polarity=data.get("polarity"),
    )


def candidate_from_row(row: Mapping[str, Any]) -> CandidateRecord:
    data = _mapping(row)
    return CandidateRecord(
        candidate_id=data["candidate_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        claim=data["claim"],
        classification=data["classification"],
        state=data["state"],
        evidence_ids=_id_tuple(data["evidence_ids"]),
        created_at=data["created_at"],
        admission_record_id=data["admission_record_id"],
    )


def candidate_admission_from_row(row: Mapping[str, Any]) -> CandidateAdmissionRecord:
    data = _mapping(row)
    return CandidateAdmissionRecord(
        admission_record_id=data["admission_record_id"],
        proposal_id=data["proposal_id"],
        research_run_id=data["research_run_id"],
        outcome=data["outcome"],
        reason_codes=_id_tuple(data["reason_codes"]),
        evidence_ids=_id_tuple(data["evidence_ids"]),
        admission_policy_version=data["admission_policy_version"],
        created_at=data["created_at"],
        admitted_candidate_id=data.get("admitted_candidate_id"),
        claim=data.get("claim"),
        classification=data.get("classification"),
    )


def verification_from_row(row: Mapping[str, Any]) -> VerificationRecord:
    data = _mapping(row)
    return VerificationRecord(
        verification_id=data["verification_id"],
        candidate_id=data["candidate_id"],
        research_run_id=data["research_run_id"],
        strategy=data["strategy"],
        outcome=data["outcome"],
        proposed_candidate_state=data["proposed_candidate_state"],
        original_evidence_ids=_id_tuple(data["original_evidence_ids"]),
        reproduction_evidence_ids=_id_tuple(data["reproduction_evidence_ids"]),
        negative_control_evidence_ids=_id_tuple(data["negative_control_evidence_ids"]),
        alternative_explanation_checks=dict(data["alternative_explanation_checks"] or {}),
        verifier_kind=data["verifier_kind"],
        verifier_identity=data["verifier_identity"],
        created_at=data["created_at"],
    )


def promotion_run_from_row(row: Mapping[str, Any]) -> PromotionRunRecord:
    data = _mapping(row)
    return PromotionRunRecord(
        promotion_run_id=data["promotion_run_id"],
        research_run_id=data["research_run_id"],
        assessment_id=data["assessment_id"],
        original_experiment_id=data["original_experiment_id"],
        stage=data["stage"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        evidence_id=data.get("evidence_id"),
        candidate_id=data.get("candidate_id"),
        verification_id=data.get("verification_id"),
        finding_proposal_id=data.get("finding_proposal_id"),
        reproduction_experiment_id=data.get("reproduction_experiment_id"),
        stop_reason=data.get("stop_reason"),
    )


def finding_proposal_from_row(row: Mapping[str, Any]) -> FindingProposalRecord:
    data = _mapping(row)
    return FindingProposalRecord(
        proposal_id=data["proposal_id"],
        candidate_id=data["candidate_id"],
        research_run_id=data["research_run_id"],
        title=data["title"],
        claim=data["claim"],
        classification=data["classification"],
        state=data["state"],
        evidence_ids=_id_tuple(data["evidence_ids"]),
        verification_ids=_id_tuple(data["verification_ids"]),
        content_fingerprint=data["content_fingerprint"],
        created_at=data["created_at"],
        impact_chain_ids=_id_tuple(data.get("impact_chain_ids", [])),
    )


def human_review_from_row(row: Mapping[str, Any]) -> HumanReviewRecord:
    data = _mapping(row)
    return HumanReviewRecord(
        review_id=data["review_id"],
        proposal_id=data["proposal_id"],
        content_fingerprint=data["content_fingerprint"],
        decision=data["decision"],
        reviewer_id=data["reviewer_id"],
        actor_type=data["actor_type"],
        reason_codes=_id_tuple(data["reason_codes"]),
        created_at=data["created_at"],
        note=data.get("note"),
    )


def approval_from_row(row: Mapping[str, Any]) -> ApprovalRecord:
    data = _mapping(row)
    return ApprovalRecord(
        approval_id=data["approval_id"],
        subject_reference=data["subject_reference"],
        decision=data["decision"],
        decided_by=data["decided_by"],
        actor_type=data["actor_type"],
        recorded=bool(data["recorded"]),
        created_at=data["created_at"],
        research_run_id=data["research_run_id"],
        proposal_id=data["proposal_id"],
        human_review_id=data["human_review_id"],
    )


def finding_from_row(row: Mapping[str, Any]) -> FindingRecord:
    data = _mapping(row)
    return FindingRecord(
        finding_id=data["finding_id"],
        finding_proposal_id=data["finding_proposal_id"],
        candidate_id=data["candidate_id"],
        research_run_id=data["research_run_id"],
        approval_id=data["approval_id"],
        human_review_id=data["human_review_id"],
        title=data["title"],
        claim=data["claim"],
        classification=data["classification"],
        evidence_ids=_id_tuple(data["evidence_ids"]),
        verification_ids=_id_tuple(data["verification_ids"]),
        created_at=data["created_at"],
    )


def target_inference_from_row(row: Mapping[str, Any]) -> TargetInferenceRecord:
    data = _mapping(row)
    return TargetInferenceRecord(
        inference_id=data["inference_id"],
        research_run_id=data["research_run_id"],
        kind=data["kind"],
        epistemic_status=data["epistemic_status"],
        opaque_ref=data["opaque_ref"],
        statement=data["statement"],
        source_refs=_id_tuple(data["source_refs"]),
        attributes=dict(data["attributes"] or {}),
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def differential_observation_from_row(row: Mapping[str, Any]) -> DifferentialObservationRecord:
    data = _mapping(row)
    return DifferentialObservationRecord(
        differential_id=data["differential_id"],
        research_run_id=data["research_run_id"],
        case_id=data["case_id"],
        baseline_observation_ids=_id_tuple(data["baseline_observation_ids"]),
        variant_observation_ids=_id_tuple(data["variant_observation_ids"]),
        changed_dimensions=_id_tuple(data["changed_dimensions"]),
        common_dimensions=_id_tuple(data["common_dimensions"]),
        observed_differences=dict(data["observed_differences"] or {}),
        observed_similarities=dict(data["observed_similarities"] or {}),
        interpretation=data["interpretation"],
        source_refs=_id_tuple(data["source_refs"]),
        strategy_version=data["strategy_version"],
        alternative_explanation_slots=_id_tuple(data["alternative_explanation_slots"]),
        created_at=data["created_at"],
    )


def invariant_hypothesis_from_row(row: Mapping[str, Any]) -> InvariantHypothesisRecord:
    data = _mapping(row)
    return InvariantHypothesisRecord(
        invariant_id=data["invariant_id"],
        research_run_id=data["research_run_id"],
        invariant_kind=data["invariant_kind"],
        status=data["status"],
        subject_refs=_id_tuple(data["subject_refs"]),
        expected_behavior=data["expected_behavior"],
        source_refs=_id_tuple(data["source_refs"]),
        applicability_context=dict(data["applicability_context"] or {}),
        assumptions=_id_tuple(data["assumptions"]),
        counterexample_refs=_id_tuple(data["counterexample_refs"]),
        falsification_direction=data["falsification_direction"],
        proposer_provenance=data["proposer_provenance"],
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def invariant_counterexample_from_row(
    row: Mapping[str, Any],
) -> InvariantCounterexampleRefRecord:
    data = _mapping(row)
    return InvariantCounterexampleRefRecord(
        counterexample_id=data["counterexample_id"],
        invariant_id=data["invariant_id"],
        source_ref=data["source_ref"],
        applicability_context=dict(data["applicability_context"] or {}),
        created_at=data["created_at"],
    )


def chain_hypothesis_from_row(row: Mapping[str, Any]) -> ChainHypothesisRecord:
    data = _mapping(row)
    steps = data["steps"] or []
    return ChainHypothesisRecord(
        chain_id=data["chain_id"],
        research_run_id=data["research_run_id"],
        structural_identity=data["structural_identity"],
        steps=tuple(dict(item) for item in steps),
        source_refs=_id_tuple(data["source_refs"]),
        preconditions=_id_tuple(data["preconditions"]),
        expected_resulting_capability=data["expected_resulting_capability"],
        unresolved_assumptions=_id_tuple(data["unresolved_assumptions"]),
        falsification_points=_id_tuple(data["falsification_points"]),
        descriptive_features=dict(data["descriptive_features"] or {}),
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def research_opportunity_from_row(row: Mapping[str, Any]) -> ResearchOpportunityRecord:
    data = _mapping(row)
    return ResearchOpportunityRecord(
        opportunity_id=data["opportunity_id"],
        research_run_id=data["research_run_id"],
        opportunity_kind=data["opportunity_kind"],
        mode=data["mode"],
        source_refs=_id_tuple(data["source_refs"]),
        proposed_direction=data["proposed_direction"],
        unresolved_question=data["unresolved_question"],
        expected_information_value_description=data[
            "expected_information_value_description"
        ],
        assumptions=_id_tuple(data["assumptions"]),
        dimensions=dict(data["dimensions"] or {}),
        context_signature=data["context_signature"],
        novelty_composition_marker=bool(data["novelty_composition_marker"]),
        prior_attempt_refs=_id_tuple(data["prior_attempt_refs"] or []),
        structural_identity=data["structural_identity"],
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def research_selection_from_row(row: Mapping[str, Any]) -> ResearchSelectionRecord:
    data = _mapping(row)
    return ResearchSelectionRecord(
        selection_id=data["selection_id"],
        research_run_id=data["research_run_id"],
        opportunity_id=data["opportunity_id"],
        outcome=data["outcome"],
        reason_codes=_id_tuple(data["reason_codes"]),
        structural_identity=data["structural_identity"],
        created_at=data["created_at"],
    )


def opportunity_selection_candidate_from_row(
    row: Mapping[str, Any]
) -> OpportunitySelectionCandidateRecord:
    data = _mapping(row)
    return OpportunitySelectionCandidateRecord(
        candidate_id=data["candidate_id"],
        research_run_id=data["research_run_id"],
        source_system=data["source_system"],
        opportunity_kind=data["opportunity_kind"],
        mode=data["mode"],
        source_refs=_id_tuple(data["source_refs"]),
        proposed_direction=data["proposed_direction"],
        unresolved_question=data["unresolved_question"],
        expected_information_value_description=data[
            "expected_information_value_description"
        ],
        assumptions=_id_tuple(data["assumptions"]),
        dimensions=dict(data["dimensions"] or {}),
        context_signature=data["context_signature"],
        structural_identity=data["structural_identity"],
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
        outcome=data["outcome"],
        resulting_opportunity_id=data["resulting_opportunity_id"],
        decided_at=data["decided_at"],
    )


def snapshot_from_row(row: Mapping[str, Any]) -> SnapshotRecord:
    data = _mapping(row)
    return SnapshotRecord(
        snapshot_id=data["snapshot_id"],
        research_run_id=data["research_run_id"],
        program_id=data["program_id"],
        target_identity=data["target_identity"],
        captured_at=data["captured_at"],
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def snapshot_member_from_row(row: Mapping[str, Any]) -> SnapshotMemberRecord:
    data = _mapping(row)
    return SnapshotMemberRecord(
        snapshot_id=data["snapshot_id"],
        observation_id=data["observation_id"],
        created_at=data["created_at"],
    )


def change_event_from_row(row: Mapping[str, Any]) -> ChangeEventRecord:
    data = _mapping(row)
    return ChangeEventRecord(
        change_event_id=data["change_event_id"],
        research_run_id=data["research_run_id"],
        baseline_snapshot_id=data["baseline_snapshot_id"],
        variant_snapshot_id=data["variant_snapshot_id"],
        category=data["category"],
        statement=data["statement"],
        source_refs=_id_tuple(data["source_refs"]),
        strategy_version=data["strategy_version"],
        created_at=data["created_at"],
    )


def research_orchestration_from_row(row: Mapping[str, Any]) -> ResearchOrchestrationRecord:
    data = _mapping(row)
    return ResearchOrchestrationRecord(
        research_run_id=data["research_run_id"],
        state=data["state"],
        cycle_number=data["cycle_number"],
        last_phase=data["last_phase"],
        last_opportunity_id=data["last_opportunity_id"],
        last_hypothesis_id=data["last_hypothesis_id"],
        last_experiment_id=data["last_experiment_id"],
        pause_reason=data["pause_reason"],
        stop_reason=data["stop_reason"],
        policy_version=data["policy_version"],
        max_cycles=data["max_cycles"],
        max_experiments=data["max_experiments"],
        max_model_calls=data["max_model_calls"],
        max_worker_invocations=data["max_worker_invocations"],
        max_elapsed_ms=data["max_elapsed_ms"],
        max_selected_opportunities=data["max_selected_opportunities"],
        max_runtime_fallback=data["max_runtime_fallback"],
        side_effect_ceiling=data["side_effect_ceiling"],
        allow_repeated_control_experiments=bool(data["allow_repeated_control_experiments"]),
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        checkpoint_at=data["checkpoint_at"],
        budget_id=data["budget_id"],
        target_reference=data["target_reference"],
        research_question=data["research_question"],
        configuration_fingerprint=data["configuration_fingerprint"],
        current_phase=data["current_phase"],
        active_cycle_id=data["active_cycle_id"],
        last_attempt_id=data["last_attempt_id"],
        last_observation_id=data["last_observation_id"],
        last_assessment_id=data["last_assessment_id"],
        last_worker_result_id=data["last_worker_result_id"],
        routing_policy_version=data["routing_policy_version"],
        scope_fingerprint=data["scope_fingerprint"],
        compiled_scope_fingerprint=data.get(
            "compiled_scope_fingerprint"
        ),
        program_policy_fingerprint=data.get(
            "program_policy_fingerprint"
        ),
        owner_runtime_instance_id=data["owner_runtime_instance_id"],
        lease_epoch=data["lease_epoch"],
        lease_expires_at=data["lease_expires_at"],
    )


def research_cycle_from_row(row: Mapping[str, Any]) -> ResearchCycleRecord:
    data = _mapping(row)
    return ResearchCycleRecord(
        cycle_id=data["cycle_id"],
        research_run_id=data["research_run_id"],
        cycle_number=data["cycle_number"],
        phase_completed=data["phase_completed"],
        outcome=data["outcome"],
        stop_reason=data["stop_reason"],
        opportunity_id=data["opportunity_id"],
        hypothesis_id=data["hypothesis_id"],
        experiment_id=data["experiment_id"],
        created_at=data["created_at"],
    )


def budget_consumption_from_row(row: Mapping[str, Any]) -> BudgetConsumptionRecord:
    data = _mapping(row)
    return BudgetConsumptionRecord(
        consumption_id=data["consumption_id"],
        budget_id=data["budget_id"],
        research_run_id=data["research_run_id"],
        experiment_id=data["experiment_id"],
        request_id=data["request_id"],
        resource_type=data["resource_type"],
        amount=data["amount"],
        unit=data["unit"],
        occurred_at=data["occurred_at"],
        provenance=data["provenance"],
        resource_metadata=dict(data["resource_metadata"]) if data.get("resource_metadata") is not None else None,
    )


def session_context_from_row(row: Mapping[str, Any]) -> SessionContextRecord:
    data = _mapping(row)
    return SessionContextRecord(
        session_context_id=data["session_context_id"],
        research_run_id=data["research_run_id"],
        identity_id=data["identity_id"],
        actor_reference=data["actor_reference"],
        origin=data["origin"],
        authentication_profile_reference=data["authentication_profile_reference"],
        authentication_method=data["authentication_method"],
        secret_scheme=data["secret_scheme"],
        secret_name=data["secret_name"],
        state=data["state"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        established_at=data.get("established_at"),
        expires_at=data.get("expires_at"),
        session_cookie_name=data.get("session_cookie_name"),
    )


def discovery_run_config_from_row(row: Mapping[str, Any]) -> DiscoveryRunConfigRecord:
    data = _mapping(row)
    return DiscoveryRunConfigRecord(
        research_run_id=data["research_run_id"],
        strategy_version=data["strategy_version"],
        seed_target_reference=data["seed_target_reference"],
        normalized_origin=data["normalized_origin"],
        normalized_path=data["normalized_path"],
        max_discovery_cycles=data["max_discovery_cycles"],
        max_frontier_items=data["max_frontier_items"],
        max_new_facts_per_cycle=data["max_new_facts_per_cycle"],
        max_browser_actions=data["max_browser_actions"],
        max_http_transactions=data["max_http_transactions"],
        max_per_route_revisit=data["max_per_route_revisit"],
        max_identity_variants=data["max_identity_variants"],
        max_transition_depth=data["max_transition_depth"],
        max_graph_depth_from_seed=data["max_graph_depth_from_seed"],
        max_template_inference_fanout=data["max_template_inference_fanout"],
        max_duplicate_observations=data["max_duplicate_observations"],
        configuration_fingerprint=data["configuration_fingerprint"],
        created_at=data["created_at"],
    )


def control_event_from_row(row: Mapping[str, Any]) -> ControlEventRecord:
    data = _mapping(row)
    return ControlEventRecord(
        control_event_id=data["control_event_id"],
        research_run_id=data["research_run_id"],
        event_kind=data["event_kind"],
        worker_result_id=data["worker_result_id"],
        identity_id=data["identity_id"],
        target_reference=data["target_reference"],
        created_at=data["created_at"],
        session_context_id=data.get("session_context_id"),
        channel=data.get("channel"),
        location_origin=data.get("location_origin"),
        location_path=data.get("location_path"),
        request_id=data.get("request_id"),
    )


def discovery_fact_from_row(row: Mapping[str, Any]) -> DiscoveryFactRecord:
    data = _mapping(row)
    return DiscoveryFactRecord(
        fact_id=data["fact_id"],
        research_run_id=data["research_run_id"],
        fact_kind=data["fact_kind"],
        canonical_key=data["canonical_key"],
        epistemic_status=data["epistemic_status"],
        identity_id=data["identity_id"],
        target_reference=data["target_reference"],
        created_at=data["created_at"],
        session_context_id=data.get("session_context_id"),
        normalized_origin=data.get("normalized_origin"),
        normalized_path=data.get("normalized_path"),
        http_method=data.get("http_method"),
        attributes=data.get("attributes"),
    )


def discovery_fact_source_from_row(row: Mapping[str, Any]) -> DiscoveryFactSourceRecord:
    data = _mapping(row)
    return DiscoveryFactSourceRecord(
        source_row_id=data["source_row_id"],
        research_run_id=data["research_run_id"],
        fact_id=data["fact_id"],
        created_at=data["created_at"],
        observation_id=data.get("observation_id"),
        sensor_observation_id=data.get("sensor_observation_id"),
        control_event_id=data.get("control_event_id"),
        source_fact_id=data.get("source_fact_id"),
        source_inference_id=data.get("source_inference_id"),
        worker_result_id=data.get("worker_result_id"),
        execution_attempt_id=data.get("execution_attempt_id"),
    )


def discovery_inference_from_row(row: Mapping[str, Any]) -> DiscoveryInferenceRecord:
    data = _mapping(row)
    return DiscoveryInferenceRecord(
        inference_id=data["inference_id"],
        research_run_id=data["research_run_id"],
        inference_kind=data["inference_kind"],
        canonical_key=data["canonical_key"],
        epistemic_status=data["epistemic_status"],
        identity_id=data["identity_id"],
        created_at=data["created_at"],
        attributes=data.get("attributes"),
    )


def discovery_inference_source_from_row(row: Mapping[str, Any]) -> DiscoveryInferenceSourceRecord:
    data = _mapping(row)
    return DiscoveryInferenceSourceRecord(
        source_row_id=data["source_row_id"],
        research_run_id=data["research_run_id"],
        inference_id=data["inference_id"],
        created_at=data["created_at"],
        observation_id=data.get("observation_id"),
        control_event_id=data.get("control_event_id"),
        source_fact_id=data.get("source_fact_id"),
        source_inference_id=data.get("source_inference_id"),
    )


def frontier_item_from_row(row: Mapping[str, Any]) -> FrontierItemRecord:
    data = _mapping(row)
    return FrontierItemRecord(
        frontier_id=data["frontier_id"],
        research_run_id=data["research_run_id"],
        strategy_version=data["strategy_version"],
        goal_kind=data["goal_kind"],
        candidate_origin=data["candidate_origin"],
        candidate_path=data["candidate_path"],
        identity_id=data["identity_id"],
        proposed_capability=data["proposed_capability"],
        proposed_action=data["proposed_action"],
        expected_side_effect=data["expected_side_effect"],
        budget_class=data["budget_class"],
        structural_signature=data["structural_signature"],
        dedupe_identity=data["dedupe_identity"],
        created_at=data["created_at"],
        session_context_id=data.get("session_context_id"),
        scope_hint=data.get("scope_hint"),
        attributes=data.get("attributes"),
        current_state=data.get("current_state"),
        state_version=data.get("state_version"),
    )


def frontier_source_from_row(row: Mapping[str, Any]) -> FrontierSourceRecord:
    data = _mapping(row)
    return FrontierSourceRecord(
        source_row_id=data["source_row_id"],
        research_run_id=data["research_run_id"],
        frontier_id=data["frontier_id"],
        created_at=data["created_at"],
        seed_config_run_id=data.get("seed_config_run_id"),
        source_fact_id=data.get("source_fact_id"),
        source_inference_id=data.get("source_inference_id"),
        control_event_id=data.get("control_event_id"),
        observation_id=data.get("observation_id"),
    )


def frontier_event_from_row(row: Mapping[str, Any]) -> FrontierEventRecord:
    data = _mapping(row)
    return FrontierEventRecord(
        event_id=data["event_id"],
        frontier_id=data["frontier_id"],
        research_run_id=data["research_run_id"],
        event_kind=data["event_kind"],
        sequence=data["sequence"],
        created_at=data["created_at"],
        selection_generation=data.get("selection_generation"),
        execution_attempt_id=data.get("execution_attempt_id"),
        reason_code=data.get("reason_code"),
    )


def discovery_projection_receipt_from_row(
    row: Mapping[str, Any],
) -> DiscoveryProjectionReceiptRecord:
    data = _mapping(row)
    return DiscoveryProjectionReceiptRecord(
        receipt_id=data["receipt_id"],
        research_run_id=data["research_run_id"],
        strategy_version=data["strategy_version"],
        source_plane=data["source_plane"],
        created_at=data["created_at"],
        observation_id=data.get("observation_id"),
        control_event_id=data.get("control_event_id"),
    )


def attack_surface_snapshot_from_row(row: Mapping[str, Any]) -> AttackSurfaceSnapshotRecord:
    data = _mapping(row)
    return AttackSurfaceSnapshotRecord(
        snapshot_id=data["snapshot_id"],
        research_run_id=data["research_run_id"],
        strategy_version=data["strategy_version"],
        node_count=int(data["node_count"]),
        edge_count=int(data["edge_count"]),
        graph_hash=data["graph_hash"],
        created_at=data["created_at"],
    )


def coverage_debt_snapshot_from_row(row: Mapping[str, Any]) -> CoverageDebtSnapshotRecord:
    data = _mapping(row)
    return CoverageDebtSnapshotRecord(
        snapshot_id=data["snapshot_id"],
        research_run_id=data["research_run_id"],
        matrix_hash=data["matrix_hash"],
        cell_counts=dict(data["cell_counts"] or {}),
        total_debt=int(data["total_debt"]),
        created_at=data["created_at"],
    )


def hunter_family_from_row(row: Mapping[str, Any]) -> HunterFamilyRecord:
    data = _mapping(row)
    return HunterFamilyRecord(
        family_id=data["family_id"],
        name=data["name"],
        target_node_kinds=_id_tuple(data["target_node_kinds"]),
        preconditions=dict(data["preconditions"] or {}),
        claim_template=data["claim_template"],
        evidence_requirements=dict(data["evidence_requirements"] or {}),
        validation_tier=data["validation_tier"],
        enabled=bool(data["enabled"]),
        version=int(data["version"]),
        created_at=data["created_at"],
    )


def hunt_v3_queue_from_row(row: Mapping[str, Any]) -> HuntV3QueueRecord:
    data = _mapping(row)
    return HuntV3QueueRecord(
        queue_id=data["queue_id"],
        research_run_id=data["research_run_id"],
        hypothesis_id=data["hypothesis_id"],
        family_id=data["family_id"],
        node_canonical_key=data["node_canonical_key"],
        identity_id=data.get("identity_id"),
        capability=data["capability"],
        action=data["action"],
        arguments=dict(data["arguments"] or {}),
        side_effect_level=int(data["side_effect_level"]),
        state=data["state"],
        created_at=data["created_at"],
    )


def impact_chain_from_row(row: Mapping[str, Any]) -> ImpactChainRecord:
    data = _mapping(row)
    return ImpactChainRecord(
        chain_id=data["chain_id"],
        research_run_id=data["research_run_id"],
        program_id=data["program_id"],
        graph_hash=data.get("graph_hash"),
        created_at=data["created_at"],
    )


def impact_chain_node_from_row(row: Mapping[str, Any]) -> ImpactChainNodeRecord:
    data = _mapping(row)
    return ImpactChainNodeRecord(
        node_id=data["node_id"],
        chain_id=data["chain_id"],
        impact_kind=data["impact_kind"],
        claim_text=data["claim_text"],
        scope_ref=dict(data["scope_ref"] or {}),
        proof_refs=_id_tuple(data["proof_refs"]),
        ordering=int(data["ordering"]),
        created_at=data["created_at"],
    )


def impact_chain_edge_from_row(row: Mapping[str, Any]) -> ImpactChainEdgeRecord:
    data = _mapping(row)
    return ImpactChainEdgeRecord(
        edge_id=data["edge_id"],
        chain_id=data["chain_id"],
        from_node_id=data["from_node_id"],
        to_node_id=data["to_node_id"],
        relation=data["relation"],
        proof_refs=_id_tuple(data["proof_refs"]),
        created_at=data["created_at"],
    )


def runtime_instance_from_row(row: Mapping[str, Any]) -> RuntimeInstanceRecord:
    data = _mapping(row)
    return RuntimeInstanceRecord(
        runtime_instance_id=data["runtime_instance_id"],
        host_identity=data["host_identity"],
        process_id=data["process_id"],
        engine_version=data["engine_version"],
        status=data["status"],
        capabilities_summary=dict(data["capabilities_summary"] or {}),
        started_at=data["started_at"],
        last_seen_at=data["last_seen_at"],
        stopped_at=data["stopped_at"],
    )


def preflight_report_from_row(row: Mapping[str, Any]) -> PreflightReportRecord:
    data = _mapping(row)
    raw_checks = data["checks"] or []
    return PreflightReportRecord(
        preflight_report_id=data["preflight_report_id"],
        research_run_id=data["research_run_id"],
        runtime_instance_id=data["runtime_instance_id"],
        created_at=data["created_at"],
        release_version=data["release_version"],
        configuration_fingerprint=data["configuration_fingerprint"],
        status=data["status"],
        checks=tuple(dict(item) for item in raw_checks),
    )
