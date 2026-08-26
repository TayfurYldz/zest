"""Unit-of-Work protocol. Core does not manage database transactions."""

from __future__ import annotations

from typing import Protocol

from zest.data.ports import (
    ApprovalRepository,
    AttackSurfaceSnapshotRepository,
    AuditEventRepository,
    AuthorizationSourceRepository,
    BudgetConsumptionRepository,
    CandidateAdmissionRepository,
    CandidateRepository,
    ChainHypothesisRepository,
    DifferentialObservationRepository,
    EvidenceAdmissionRepository,
    EvidenceRepository,
    ExecutionAttemptRepository,
    ExperimentPlanRepository,
    ExperimentRepository,
    FindingProposalRepository,
    FindingRepository,
    HypothesisAssessmentRepository,
    HypothesisRepository,
    HumanReviewRepository,
    ImpactChainRepository,
    InvariantHypothesisRepository,
    IssuedBudgetRepository,
    ObservationRepository,
    OastAdmissionRepository,
    OastCallbackDeliveryRepository,
    OastCorrelationRepository,
    ProgramRepository,
    PromotionRunRepository,
    ResearchAdmissionRepository,
    ResearchCycleRepository,
    SensorObservationRepository,
    ResearchOrchestrationRepository,
    ResearchReasoningRepository,
    ResearchRunRepository,
    TargetInferenceRepository,
    VerificationRepository,
    WorkerResultRepository,
    OpportunitySelectionCandidateRepository,
    PreflightReportRepository,
    ResearchOpportunityRepository,
    ResearchSelectionRepository,
    SnapshotRepository,
    ChangeEventRepository,
    SessionContextRepository,
    DiscoveryRunConfigRepository,
    ControlEventRepository,
    CoverageDebtSnapshotRepository,
    DiscoveryFactRepository,
    DiscoveryFactSourceRepository,
    DiscoveryInferenceRepository,
    DiscoveryInferenceSourceRepository,
    FrontierItemRepository,
    FrontierSourceRepository,
    FrontierEventRepository,
    DiscoveryProjectionReceiptRepository,
    AttackSurfaceSnapshotRepository,
    HunterFamilyRepository,
    HuntV3QueueRepository,
    ImpactChainRepository,
    RuntimeInstanceRepository,
)


class UnitOfWork(Protocol):
    """One explicit transaction. Commit is required; otherwise rollback."""

    programs: ProgramRepository
    scope_rules_v2: ScopeRuleV2Repository
    program_policies: ProgramPolicyRepository
    sensor_observations: SensorObservationRepository
    rate_limit_profiles: RateLimitProfileRepository
    bounty_tables: BountyTableRepository
    authorization_sources: AuthorizationSourceRepository
    research_runs: ResearchRunRepository
    issued_budgets: IssuedBudgetRepository
    hypotheses: HypothesisRepository
    experiments: ExperimentRepository
    execution_attempts: ExecutionAttemptRepository
    worker_results: WorkerResultRepository
    observations: ObservationRepository
    oast_correlations: OastCorrelationRepository
    oast_callback_deliveries: OastCallbackDeliveryRepository
    oast_admissions: OastAdmissionRepository
    research_reasoning: ResearchReasoningRepository
    research_admissions: ResearchAdmissionRepository
    experiment_plans: ExperimentPlanRepository
    hypothesis_assessments: HypothesisAssessmentRepository
    evidence: EvidenceRepository
    evidence_admissions: EvidenceAdmissionRepository
    candidates: CandidateRepository
    candidate_admissions: CandidateAdmissionRepository
    promotion_runs: PromotionRunRepository
    verifications: VerificationRepository
    finding_proposals: FindingProposalRepository
    human_reviews: HumanReviewRepository
    approvals: ApprovalRepository
    findings: FindingRepository
    target_inferences: TargetInferenceRepository
    differential_observations: DifferentialObservationRepository
    invariant_hypotheses: InvariantHypothesisRepository
    chain_hypotheses: ChainHypothesisRepository
    research_opportunities: ResearchOpportunityRepository
    research_selections: ResearchSelectionRepository
    opportunity_selection_candidates: OpportunitySelectionCandidateRepository
    snapshots: SnapshotRepository
    change_events: ChangeEventRepository
    coverage_debt_snapshots: CoverageDebtSnapshotRepository
    research_orchestrations: ResearchOrchestrationRepository
    research_cycles: ResearchCycleRepository
    budget_consumptions: BudgetConsumptionRepository
    audit_events: AuditEventRepository
    session_contexts: SessionContextRepository
    discovery_run_configs: DiscoveryRunConfigRepository
    control_events: ControlEventRepository
    discovery_facts: DiscoveryFactRepository
    discovery_fact_sources: DiscoveryFactSourceRepository
    discovery_inferences: DiscoveryInferenceRepository
    discovery_inference_sources: DiscoveryInferenceSourceRepository
    frontier_items: FrontierItemRepository
    frontier_sources: FrontierSourceRepository
    frontier_events: FrontierEventRepository
    discovery_projection_receipts: DiscoveryProjectionReceiptRepository
    attack_surface_snapshots: AttackSurfaceSnapshotRepository
    hunter_families: HunterFamilyRepository
    hunt_v3_queue: HuntV3QueueRepository
    impact_chains: ImpactChainRepository
    runtime_instances: RuntimeInstanceRepository
    preflight_reports: PreflightReportRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type, exc, tb) -> bool: ...
