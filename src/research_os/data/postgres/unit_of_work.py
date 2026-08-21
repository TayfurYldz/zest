"""Synchronous Unit of Work. Explicit commit; otherwise rollback. Core does not own this."""

from __future__ import annotations

from sqlalchemy.engine import Connection, Engine

from research_os.data.errors import PersistenceError
from research_os.data.postgres.repositories import (
    PostgresApprovalRepository,
    PostgresAuditEventRepository,
    PostgresAuthorizationSourceRepository,
    PostgresBountyTableRepository,
    PostgresCandidateAdmissionRepository,
    PostgresCandidateRepository,
    PostgresChainHypothesisRepository,
    PostgresDifferentialObservationRepository,
    PostgresEvidenceAdmissionRepository,
    PostgresEvidenceRepository,
    PostgresExecutionAttemptRepository,
    PostgresExperimentPlanRepository,
    PostgresExperimentRepository,
    PostgresFindingProposalRepository,
    PostgresFindingRepository,
    PostgresHunterFamilyRepository,
    PostgresHuntV3QueueRepository,
    PostgresImpactChainRepository,
    PostgresHypothesisAssessmentRepository,
    PostgresHypothesisRepository,
    PostgresHumanReviewRepository,
    PostgresInvariantHypothesisRepository,
    PostgresIssuedBudgetRepository,
    PostgresObservationRepository,
    PostgresOastTokenRepository,
    PostgresProgramPolicyRepository,
    PostgresProgramRepository,
    PostgresPromotionRunRepository,
    PostgresRateLimitProfileRepository,
    PostgresResearchAdmissionRepository,
    PostgresResearchReasoningRepository,
    PostgresResearchRunRepository,
    PostgresScopeRuleV2Repository,
    PostgresSensorObservationRepository,
    PostgresTargetInferenceRepository,
    PostgresVerificationRepository,
    PostgresWorkerResultRepository,
    PostgresOpportunitySelectionCandidateRepository,
    PostgresResearchOpportunityRepository,
    PostgresResearchSelectionRepository,
    PostgresSnapshotRepository,
    PostgresChangeEventRepository,
    PostgresResearchOrchestrationRepository,
    PostgresResearchCycleRepository,
    PostgresBudgetConsumptionRepository,
    PostgresSessionContextRepository,
)
from research_os.data.postgres.discovery_repositories import (
    PostgresAttackSurfaceSnapshotRepository,
    PostgresControlEventRepository,
    PostgresCoverageDebtSnapshotRepository,
    PostgresDiscoveryFactRepository,
    PostgresDiscoveryFactSourceRepository,
    PostgresDiscoveryInferenceRepository,
    PostgresDiscoveryInferenceSourceRepository,
    PostgresDiscoveryProjectionReceiptRepository,
    PostgresDiscoveryRunConfigRepository,
    PostgresFrontierEventRepository,
    PostgresFrontierItemRepository,
    PostgresFrontierSourceRepository,
)


class PostgresUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self._transaction = None
        self._committed = False
        self.programs: PostgresProgramRepository
        self.scope_rules_v2: PostgresScopeRuleV2Repository
        self.program_policies: PostgresProgramPolicyRepository
        self.sensor_observations: PostgresSensorObservationRepository
        self.rate_limit_profiles: PostgresRateLimitProfileRepository
        self.bounty_tables: PostgresBountyTableRepository
        self.authorization_sources: PostgresAuthorizationSourceRepository
        self.research_runs: PostgresResearchRunRepository
        self.issued_budgets: PostgresIssuedBudgetRepository
        self.hypotheses: PostgresHypothesisRepository
        self.experiments: PostgresExperimentRepository
        self.execution_attempts: PostgresExecutionAttemptRepository
        self.worker_results: PostgresWorkerResultRepository
        self.observations: PostgresObservationRepository
        self.oast_tokens: PostgresOastTokenRepository
        self.research_reasoning: PostgresResearchReasoningRepository
        self.research_admissions: PostgresResearchAdmissionRepository
        self.experiment_plans: PostgresExperimentPlanRepository
        self.hypothesis_assessments: PostgresHypothesisAssessmentRepository
        self.evidence: PostgresEvidenceRepository
        self.evidence_admissions: PostgresEvidenceAdmissionRepository
        self.candidates: PostgresCandidateRepository
        self.candidate_admissions: PostgresCandidateAdmissionRepository
        self.promotion_runs: PostgresPromotionRunRepository
        self.verifications: PostgresVerificationRepository
        self.finding_proposals: PostgresFindingProposalRepository
        self.human_reviews: PostgresHumanReviewRepository
        self.approvals: PostgresApprovalRepository
        self.findings: PostgresFindingRepository
        self.target_inferences: PostgresTargetInferenceRepository
        self.differential_observations: PostgresDifferentialObservationRepository
        self.invariant_hypotheses: PostgresInvariantHypothesisRepository
        self.chain_hypotheses: PostgresChainHypothesisRepository
        self.research_opportunities: PostgresResearchOpportunityRepository
        self.research_selections: PostgresResearchSelectionRepository
        self.opportunity_selection_candidates: PostgresOpportunitySelectionCandidateRepository
        self.snapshots: PostgresSnapshotRepository
        self.change_events: PostgresChangeEventRepository
        self.research_orchestrations: PostgresResearchOrchestrationRepository
        self.research_cycles: PostgresResearchCycleRepository
        self.budget_consumptions: PostgresBudgetConsumptionRepository
        self.audit_events: PostgresAuditEventRepository
        self.session_contexts: PostgresSessionContextRepository
        self.discovery_run_configs: PostgresDiscoveryRunConfigRepository
        self.control_events: PostgresControlEventRepository
        self.discovery_facts: PostgresDiscoveryFactRepository
        self.discovery_fact_sources: PostgresDiscoveryFactSourceRepository
        self.discovery_inferences: PostgresDiscoveryInferenceRepository
        self.discovery_inference_sources: PostgresDiscoveryInferenceSourceRepository
        self.frontier_items: PostgresFrontierItemRepository
        self.frontier_sources: PostgresFrontierSourceRepository
        self.frontier_events: PostgresFrontierEventRepository
        self.discovery_projection_receipts: PostgresDiscoveryProjectionReceiptRepository
        self.attack_surface_snapshots: PostgresAttackSurfaceSnapshotRepository
        self.coverage_debt_snapshots: PostgresCoverageDebtSnapshotRepository
        self.hunter_families: PostgresHunterFamilyRepository
        self.hunt_v3_queue: PostgresHuntV3QueueRepository
        self.impact_chains: PostgresImpactChainRepository

    def open(self) -> PostgresUnitOfWork:
        return type(self)(self._engine)

    def __enter__(self) -> PostgresUnitOfWork:
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        self._committed = False
        self.programs = PostgresProgramRepository(self._connection)
        self.scope_rules_v2 = PostgresScopeRuleV2Repository(self._connection)
        self.program_policies = PostgresProgramPolicyRepository(self._connection)
        self.sensor_observations = PostgresSensorObservationRepository(self._connection)
        self.rate_limit_profiles = PostgresRateLimitProfileRepository(self._connection)
        self.bounty_tables = PostgresBountyTableRepository(self._connection)
        self.authorization_sources = PostgresAuthorizationSourceRepository(
            self._connection
        )
        self.research_runs = PostgresResearchRunRepository(self._connection)
        self.issued_budgets = PostgresIssuedBudgetRepository(self._connection)
        self.hypotheses = PostgresHypothesisRepository(self._connection)
        self.experiments = PostgresExperimentRepository(self._connection)
        self.execution_attempts = PostgresExecutionAttemptRepository(self._connection)
        self.worker_results = PostgresWorkerResultRepository(self._connection)
        self.observations = PostgresObservationRepository(self._connection)
        self.oast_tokens = PostgresOastTokenRepository(self._connection)
        self.research_reasoning = PostgresResearchReasoningRepository(self._connection)
        self.research_admissions = PostgresResearchAdmissionRepository(self._connection)
        self.experiment_plans = PostgresExperimentPlanRepository(self._connection)
        self.hypothesis_assessments = PostgresHypothesisAssessmentRepository(
            self._connection
        )
        self.evidence = PostgresEvidenceRepository(self._connection)
        self.evidence_admissions = PostgresEvidenceAdmissionRepository(self._connection)
        self.candidates = PostgresCandidateRepository(self._connection)
        self.candidate_admissions = PostgresCandidateAdmissionRepository(self._connection)
        self.promotion_runs = PostgresPromotionRunRepository(self._connection)
        self.verifications = PostgresVerificationRepository(self._connection)
        self.finding_proposals = PostgresFindingProposalRepository(self._connection)
        self.human_reviews = PostgresHumanReviewRepository(self._connection)
        self.approvals = PostgresApprovalRepository(self._connection)
        self.findings = PostgresFindingRepository(self._connection)
        self.target_inferences = PostgresTargetInferenceRepository(self._connection)
        self.differential_observations = PostgresDifferentialObservationRepository(
            self._connection
        )
        self.invariant_hypotheses = PostgresInvariantHypothesisRepository(self._connection)
        self.chain_hypotheses = PostgresChainHypothesisRepository(self._connection)
        self.research_opportunities = PostgresResearchOpportunityRepository(self._connection)
        self.research_selections = PostgresResearchSelectionRepository(self._connection)
        self.opportunity_selection_candidates = PostgresOpportunitySelectionCandidateRepository(
            self._connection
        )
        self.snapshots = PostgresSnapshotRepository(self._connection)
        self.change_events = PostgresChangeEventRepository(self._connection)
        self.research_orchestrations = PostgresResearchOrchestrationRepository(
            self._connection
        )
        self.research_cycles = PostgresResearchCycleRepository(self._connection)
        self.budget_consumptions = PostgresBudgetConsumptionRepository(self._connection)
        self.audit_events = PostgresAuditEventRepository(self._connection)
        self.session_contexts = PostgresSessionContextRepository(self._connection)
        self.discovery_run_configs = PostgresDiscoveryRunConfigRepository(self._connection)
        self.control_events = PostgresControlEventRepository(self._connection)
        self.discovery_facts = PostgresDiscoveryFactRepository(self._connection)
        self.discovery_fact_sources = PostgresDiscoveryFactSourceRepository(self._connection)
        self.discovery_inferences = PostgresDiscoveryInferenceRepository(self._connection)
        self.discovery_inference_sources = PostgresDiscoveryInferenceSourceRepository(
            self._connection
        )
        self.frontier_items = PostgresFrontierItemRepository(self._connection)
        self.frontier_sources = PostgresFrontierSourceRepository(self._connection)
        self.frontier_events = PostgresFrontierEventRepository(self._connection)
        self.discovery_projection_receipts = PostgresDiscoveryProjectionReceiptRepository(
            self._connection
        )
        self.attack_surface_snapshots = PostgresAttackSurfaceSnapshotRepository(
            self._connection
        )
        self.coverage_debt_snapshots = PostgresCoverageDebtSnapshotRepository(
            self._connection
        )
        self.hunter_families = PostgresHunterFamilyRepository(self._connection)
        self.hunt_v3_queue = PostgresHuntV3QueueRepository(self._connection)
        self.impact_chains = PostgresImpactChainRepository(self._connection)
        return self

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceError("unit of work has no active transaction")
        self._transaction.commit()
        self._committed = True

    def rollback(self) -> None:
        if self._transaction is not None and self._transaction.is_active:
            self._transaction.rollback()
        self._committed = False

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._transaction is not None and self._transaction.is_active:
                if exc_type is not None or not self._committed:
                    self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
        return False
