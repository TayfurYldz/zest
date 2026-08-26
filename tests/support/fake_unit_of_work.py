"""In-memory Unit of Work for Application tests. Not a PostgreSQL substitute."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from zest.data.errors import (
    BudgetOverspendError,
    LeaseFencingError,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
    TerminalOrchestrationStateError,
)
from zest.data.uniqueness import (
    UQ_CANDIDATE_EVIDENCE_EVIDENCE_ID,
    UQ_EVIDENCE_EXPERIMENT_SUPPORTING,
    UQ_FINDING_PROPOSAL_CANDIDATE,
    UQ_PROMOTION_RUN_ASSESSMENT,
    UQ_PROMOTION_RUN_REPRODUCTION_EXPERIMENT,
    UQ_VERIFICATION_CANDIDATE,
)
from zest.data.records import (
    ALLOWED_CANDIDATE_STATES,
    ALLOWED_EXECUTION_ATTEMPT_STATES,
    ALLOWED_EXPERIMENT_STATES,
    ALLOWED_FINDING_PROPOSAL_STATES,
    ALLOWED_INVARIANT_STATUSES,
    ALLOWED_PROMOTION_STAGES,
    ALLOWED_SESSION_STATES,
    ApprovalRecord,
    AuditEventRecord,
    AuthorizationSourceRecord,
    BountyTableRecord,
    BudgetConsumptionRecord,
    CandidateAdmissionRecord,
    CandidateRecord,
    ChainHypothesisRecord,
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
    InvariantCounterexampleRefRecord,
    InvariantHypothesisRecord,
    IssuedBudgetRecord,
    LeaseAcquireOutcome,
    LeaseAcquireResult,
    ObservationRecord,
    OastAdmissionRecord,
    OastCallbackDeliveryRecord,
    OastCorrelationRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    PromotionRunRecord,
    RateLimitProfileRecord,
    ResearchAdmissionRecord,
    ResearchCycleRecord,
    OpportunitySelectionCandidateRecord,
    ResearchOpportunityRecord,
    ResearchOrchestrationRecord,
    ResearchReasoningRecord,
    ResearchRunRecord,
    ResearchSelectionRecord,
    ScopeRuleV2Record,
    SensorObservationRecord,
    SnapshotMemberRecord,
    SnapshotRecord,
    TargetInferenceRecord,
    TERMINAL_ORCHESTRATION_STATES,
    ChangeEventRecord,
    CoverageDebtSnapshotRecord,
    VerificationRecord,
    WorkerResultRecord,
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
    PreflightReportRecord,
    RuntimeInstanceRecord,
)
from zest.data.budget_ledger import assert_within_allowance


class _Store:
    def __init__(self) -> None:
        self.programs: dict[str, ProgramRecord] = {}
        self.scope_rules_v2: dict[str, ScopeRuleV2Record] = {}
        self.program_policies: dict[str, ProgramPolicyRecord] = {}
        self.rate_limit_profiles: dict[str, RateLimitProfileRecord] = {}
        self.bounty_tables: dict[str, BountyTableRecord] = {}
        self.authorization_sources: dict[str, AuthorizationSourceRecord] = {}
        self.research_runs: dict[str, ResearchRunRecord] = {}
        self.issued_budgets: dict[str, IssuedBudgetRecord] = {}
        self.hypotheses: dict[str, HypothesisRecord] = {}
        self.experiments: dict[str, ExperimentRecord] = {}
        self.execution_attempts: dict[str, ExecutionAttemptRecord] = {}
        self.execution_attempts_by_request: dict[str, str] = {}
        self.worker_results: dict[str, WorkerResultRecord] = {}
        self.worker_results_by_request: dict[str, str] = {}
        self.observations: dict[str, ObservationRecord] = {}
        self.oast_correlations: dict[str, OastCorrelationRecord] = {}
        self.oast_callback_deliveries: dict[str, OastCallbackDeliveryRecord] = {}
        self.oast_admissions: dict[str, OastAdmissionRecord] = {}
        self.sensor_observations: dict[str, SensorObservationRecord] = {}
        self.research_reasoning: dict[str, ResearchReasoningRecord] = {}
        self.research_admissions: dict[str, ResearchAdmissionRecord] = {}
        self.experiment_plans: dict[str, ExperimentPlanRecord] = {}
        self.hypothesis_assessments: dict[str, HypothesisAssessmentRecord] = {}
        self.evidence: dict[str, EvidenceRecord] = {}
        self.evidence_admissions: dict[str, EvidenceAdmissionRecord] = {}
        self.candidates: dict[str, CandidateRecord] = {}
        self.candidate_admissions: dict[str, CandidateAdmissionRecord] = {}
        self.promotion_runs: dict[str, PromotionRunRecord] = {}
        self.verifications: dict[str, VerificationRecord] = {}
        self.finding_proposals: dict[str, FindingProposalRecord] = {}
        self.human_reviews: dict[str, HumanReviewRecord] = {}
        self.approvals: dict[str, ApprovalRecord] = {}
        self.findings: dict[str, FindingRecord] = {}
        self.target_inferences: dict[str, TargetInferenceRecord] = {}
        self.differential_observations: dict[str, DifferentialObservationRecord] = {}
        self.invariant_hypotheses: dict[str, InvariantHypothesisRecord] = {}
        self.invariant_counterexamples: dict[str, InvariantCounterexampleRefRecord] = {}
        self.chain_hypotheses: dict[str, ChainHypothesisRecord] = {}
        self.research_opportunities: dict[str, ResearchOpportunityRecord] = {}
        self.research_selections: dict[str, ResearchSelectionRecord] = {}
        self.opportunity_selection_candidates: dict[str, OpportunitySelectionCandidateRecord] = {}
        self.snapshots: dict[str, SnapshotRecord] = {}
        self.snapshot_members: dict[str, SnapshotMemberRecord] = {}
        self.change_events: dict[str, ChangeEventRecord] = {}
        self.coverage_debt_snapshots: dict[str, CoverageDebtSnapshotRecord] = {}
        self.research_orchestrations: dict[str, ResearchOrchestrationRecord] = {}
        self.research_cycles: dict[str, ResearchCycleRecord] = {}
        self.budget_consumptions: dict[str, BudgetConsumptionRecord] = {}
        self.audit_events: dict[str, AuditEventRecord] = {}
        self.session_contexts: dict[str, SessionContextRecord] = {}
        self.discovery_run_configs: dict[str, DiscoveryRunConfigRecord] = {}
        self.control_events: dict[str, ControlEventRecord] = {}
        self.discovery_facts: dict[str, DiscoveryFactRecord] = {}
        self.discovery_fact_sources: dict[str, DiscoveryFactSourceRecord] = {}
        self.discovery_inferences: dict[str, DiscoveryInferenceRecord] = {}
        self.discovery_inference_sources: dict[str, DiscoveryInferenceSourceRecord] = {}
        self.frontier_items: dict[str, FrontierItemRecord] = {}
        self.frontier_sources: dict[str, FrontierSourceRecord] = {}
        self.frontier_events: dict[str, FrontierEventRecord] = {}
        self.discovery_projection_receipts: dict[str, DiscoveryProjectionReceiptRecord] = {}
        self.hunter_families: dict[str, HunterFamilyRecord] = {}
        self.hunt_v3_queue: dict[str, HuntV3QueueRecord] = {}
        self.runtime_instances: dict[str, RuntimeInstanceRecord] = {}
        self.preflight_reports: dict[str, PreflightReportRecord] = {}
        self.open_transactions = 0
        self.set_state_calls = 0


class _Repo:
    def __init__(self, store: dict[str, Any], fail_on_insert: bool = False) -> None:
        self._store = store
        self._fail_on_insert = fail_on_insert

    def insert(self, record: Any) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        key = _id_of(record)
        if key in self._store:
            raise PersistenceConflictError("duplicate id")
        self._store[key] = record

    def get(self, record_id: str) -> Any | None:
        return self._store.get(record_id)


class _ProgramRepo(_Repo):
    def list_recent(self, *, limit: int = 50) -> list[ProgramRecord]:
        records = sorted(
            self._store.values(),
            key=lambda record: record.created_at,
            reverse=True,
        )
        return records[:limit]


class _ResearchRunRepo(_Repo):
    def list_recent(self, *, limit: int = 50) -> list[ResearchRunRecord]:
        records = sorted(
            self._store.values(),
            key=lambda record: record.started_at,
            reverse=True,
        )
        return records[:limit]


class _ScopeRuleV2Repo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.scope_rules_v2, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_program(self, program_id: str) -> list[ScopeRuleV2Record]:
        return sorted(
            [
                record
                for record in self._root.scope_rules_v2.values()
                if record.program_id == program_id
            ],
            key=lambda record: record.rule_id,
        )


class _RateLimitProfileRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.rate_limit_profiles, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_program(self, program_id: str) -> list[RateLimitProfileRecord]:
        return sorted(
            [
                record
                for record in self._root.rate_limit_profiles.values()
                if record.program_id == program_id
            ],
            key=lambda record: record.profile_id,
        )


class _BountyTableRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.bounty_tables, fail_on_insert=fail_on_insert)
        self._root = store

    def get(self, program_id: str, severity: str) -> BountyTableRecord | None:
        return self._root.bounty_tables.get(f"{program_id}:{severity}")

    def list_for_program(self, program_id: str) -> list[BountyTableRecord]:
        return sorted(
            [
                record
                for record in self._root.bounty_tables.values()
                if record.program_id == program_id
            ],
            key=lambda record: record.severity,
        )


class _IssuedBudgetRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.issued_budgets)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[IssuedBudgetRecord]:
        return [
            record
            for record in self._root.issued_budgets.values()
            if record.research_run_id == research_run_id
        ]


class _OastCorrelationRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.oast_correlations)
        self._root = store

    def insert(self, record: OastCorrelationRecord) -> None:
        if self.get_by_attempt_id(record.attempt_id) is not None:
            raise PersistenceConflictError(
                "duplicate OAST attempt correlation",
                constraint_name="uq_oast_correlation_attempt",
            )
        super().insert(record)

    def get_by_attempt_id(self, attempt_id: str) -> OastCorrelationRecord | None:
        return next(
            (
                record
                for record in self._root.oast_correlations.values()
                if record.attempt_id == attempt_id
            ),
            None,
        )

    def list_for_research_run(self, research_run_id: str) -> list[OastCorrelationRecord]:
        return [
            record
            for record in self._root.oast_correlations.values()
            if record.research_run_id == research_run_id
        ]


class _OastCallbackDeliveryRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.oast_callback_deliveries)
        self._root = store

    def insert(self, record: OastCallbackDeliveryRecord) -> None:
        if self.get_by_provider_event(record.provider_adapter_id, record.provider_event_id):
            raise PersistenceConflictError(
                "duplicate OAST provider event",
                constraint_name="uq_oast_callback_provider_event",
            )
        if self.get_by_correlation_digest(record.correlation_id, record.normalized_digest):
            raise PersistenceConflictError(
                "duplicate OAST normalized callback",
                constraint_name="uq_oast_callback_correlation_digest",
            )
        super().insert(record)

    def get_by_provider_event(
        self, provider_adapter_id: str, provider_event_id: str
    ) -> OastCallbackDeliveryRecord | None:
        return next(
            (
                record
                for record in self._root.oast_callback_deliveries.values()
                if record.provider_adapter_id == provider_adapter_id
                and record.provider_event_id == provider_event_id
            ),
            None,
        )

    def get_by_correlation_digest(
        self, correlation_id: str, normalized_digest: str
    ) -> OastCallbackDeliveryRecord | None:
        return next(
            (
                record
                for record in self._root.oast_callback_deliveries.values()
                if record.correlation_id == correlation_id
                and record.normalized_digest == normalized_digest
            ),
            None,
        )

    def list_for_correlation(
        self, correlation_id: str
    ) -> list[OastCallbackDeliveryRecord]:
        return [
            record
            for record in self._root.oast_callback_deliveries.values()
            if record.correlation_id == correlation_id
        ]


class _OastAdmissionRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.oast_admissions)
        self._root = store

    def insert(self, record: OastAdmissionRecord) -> None:
        if self.get_by_correlation(record.correlation_id) is not None:
            raise PersistenceConflictError(
                "duplicate OAST admission",
                constraint_name="uq_oast_admission_correlation",
            )
        super().insert(record)

    def get_by_correlation(self, correlation_id: str) -> OastAdmissionRecord | None:
        return next(
            (
                record
                for record in self._root.oast_admissions.values()
                if record.correlation_id == correlation_id
            ),
            None,
        )

    def list_for_research_run(self, research_run_id: str) -> list[OastAdmissionRecord]:
        return [
            record
            for record in self._root.oast_admissions.values()
            if record.research_run_id == research_run_id
        ]


def _id_of(record: Any) -> str:
    if isinstance(record, ProgramRecord):
        return record.program_id
    if isinstance(record, ScopeRuleV2Record):
        return record.rule_id
    if isinstance(record, ProgramPolicyRecord):
        return record.program_id
    if isinstance(record, RateLimitProfileRecord):
        return record.profile_id
    if isinstance(record, BountyTableRecord):
        return f"{record.program_id}:{record.severity}"
    if isinstance(record, AuthorizationSourceRecord):
        return record.authorization_source_id
    if isinstance(record, ResearchRunRecord):
        return record.research_run_id
    if isinstance(record, IssuedBudgetRecord):
        return record.budget_id
    if isinstance(record, HypothesisRecord):
        return record.hypothesis_id
    if isinstance(record, ExperimentRecord):
        return record.experiment_id
    if isinstance(record, ExecutionAttemptRecord):
        return record.attempt_id
    if isinstance(record, WorkerResultRecord):
        return record.worker_result_id
    if isinstance(record, ObservationRecord):
        return record.observation_id
    if isinstance(record, SensorObservationRecord):
        return record.observation_id
    if isinstance(record, OastCorrelationRecord):
        return record.correlation_id
    if isinstance(record, OastCallbackDeliveryRecord):
        return record.delivery_id
    if isinstance(record, OastAdmissionRecord):
        return record.admission_id
    if isinstance(record, ResearchReasoningRecord):
        return record.reasoning_record_id
    if isinstance(record, ResearchAdmissionRecord):
        return record.admission_record_id
    if isinstance(record, ExperimentPlanRecord):
        return record.experiment_id
    if isinstance(record, HypothesisAssessmentRecord):
        return record.assessment_id
    if isinstance(record, EvidenceRecord):
        return record.evidence_id
    if isinstance(record, EvidenceAdmissionRecord):
        return record.admission_record_id
    if isinstance(record, CandidateRecord):
        return record.candidate_id
    if isinstance(record, CandidateAdmissionRecord):
        return record.admission_record_id
    if isinstance(record, PromotionRunRecord):
        return record.promotion_run_id
    if isinstance(record, VerificationRecord):
        return record.verification_id
    if isinstance(record, FindingProposalRecord):
        return record.proposal_id
    if isinstance(record, HumanReviewRecord):
        return record.review_id
    if isinstance(record, ApprovalRecord):
        return record.approval_id
    if isinstance(record, FindingRecord):
        return record.finding_id
    if isinstance(record, TargetInferenceRecord):
        return record.inference_id
    if isinstance(record, DifferentialObservationRecord):
        return record.differential_id
    if isinstance(record, InvariantHypothesisRecord):
        return record.invariant_id
    if isinstance(record, InvariantCounterexampleRefRecord):
        return record.counterexample_id
    if isinstance(record, ChainHypothesisRecord):
        return record.chain_id
    if isinstance(record, ResearchOpportunityRecord):
        return record.opportunity_id
    if isinstance(record, ResearchSelectionRecord):
        return record.selection_id
    if isinstance(record, OpportunitySelectionCandidateRecord):
        return record.candidate_id
    if isinstance(record, SnapshotRecord):
        return record.snapshot_id
    if isinstance(record, SnapshotMemberRecord):
        return f"{record.snapshot_id}:{record.observation_id}"
    if isinstance(record, ChangeEventRecord):
        return record.change_event_id
    if isinstance(record, CoverageDebtSnapshotRecord):
        return record.snapshot_id
    if isinstance(record, ResearchOrchestrationRecord):
        return record.research_run_id
    if isinstance(record, ResearchCycleRecord):
        return record.cycle_id
    if isinstance(record, BudgetConsumptionRecord):
        return record.consumption_id
    if isinstance(record, AuditEventRecord):
        return record.audit_event_id
    if isinstance(record, SessionContextRecord):
        return record.session_context_id
    if isinstance(record, DiscoveryRunConfigRecord):
        return record.research_run_id
    if isinstance(record, ControlEventRecord):
        return record.control_event_id
    if isinstance(record, DiscoveryFactRecord):
        return record.fact_id
    if isinstance(record, DiscoveryFactSourceRecord):
        return record.source_row_id
    if isinstance(record, DiscoveryInferenceRecord):
        return record.inference_id
    if isinstance(record, DiscoveryInferenceSourceRecord):
        return record.source_row_id
    if isinstance(record, FrontierItemRecord):
        return record.frontier_id
    if isinstance(record, FrontierSourceRecord):
        return record.source_row_id
    if isinstance(record, FrontierEventRecord):
        return record.event_id
    if isinstance(record, DiscoveryProjectionReceiptRecord):
        return record.receipt_id
    if isinstance(record, HunterFamilyRecord):
        return f"{record.family_id}:{record.version}"
    if isinstance(record, HuntV3QueueRecord):
        return record.queue_id
    if isinstance(record, RuntimeInstanceRecord):
        return record.runtime_instance_id
    if isinstance(record, PreflightReportRecord):
        return record.preflight_report_id
    raise PersistenceError("unknown record identity")


class _HypothesisRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.hypotheses, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: HypothesisRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        origin = record.origin_reference
        if isinstance(origin, str) and origin.startswith("exh:"):
            for existing in self._root.hypotheses.values():
                if (
                    existing.research_run_id == record.research_run_id
                    and existing.origin_reference == origin
                ):
                    raise PersistenceConflictError("duplicate exploratory hypothesis origin")
        super().insert(record)

    def list_for_research_run(self, research_run_id: str) -> list[HypothesisRecord]:
        return sorted(
            [
                record
                for record in self._root.hypotheses.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.hypothesis_id,
        )


class _ExperimentRepo(_Repo):
    def set_execution_state(self, experiment_id: str, execution_state: str) -> None:
        if execution_state not in ALLOWED_EXPERIMENT_STATES:
            raise PersistenceInputError("execution_state is not a domain execution state")
        current = self.get(experiment_id)
        if current is None:
            raise PersistenceError("experiment not found for execution_state update")
        self._store[experiment_id] = ExperimentRecord(
            experiment_id=current.experiment_id,
            research_run_id=current.research_run_id,
            hypothesis_id=current.hypothesis_id,
            budget_id=current.budget_id,
            execution_state=execution_state,
            created_at=current.created_at,
        )

    def list_for_research_run(self, research_run_id: str) -> list[ExperimentRecord]:
        return sorted(
            [
                record
                for record in self._store.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.experiment_id,
        )


class _ExecutionAttemptRepo(_Repo):
    def __init__(
        self,
        store: _Store,
        fail_on_insert: bool = False,
        fail_on_set_state: bool = False,
    ) -> None:
        super().__init__(store.execution_attempts, fail_on_insert=fail_on_insert)
        self._root = store
        self._fail_on_set_state = fail_on_set_state

    def insert(self, record: ExecutionAttemptRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        if record.request_id in self._root.execution_attempts_by_request:
            raise PersistenceConflictError("duplicate request_id")
        super().insert(record)
        self._root.execution_attempts_by_request[record.request_id] = record.attempt_id

    def get_by_request_id(self, request_id: str) -> ExecutionAttemptRecord | None:
        attempt_id = self._root.execution_attempts_by_request.get(request_id)
        if attempt_id is None:
            return None
        return self._root.execution_attempts.get(attempt_id)

    def list_for_experiment(self, experiment_id: str) -> list[ExecutionAttemptRecord]:
        return [
            record
            for record in self._root.execution_attempts.values()
            if record.experiment_id == experiment_id
        ]

    def list_for_research_run(self, research_run_id: str) -> list[ExecutionAttemptRecord]:
        return [
            record
            for record in self._root.execution_attempts.values()
            if record.research_run_id == research_run_id
        ]

    def set_state(
        self,
        attempt_id: str,
        state: str,
        *,
        dispatch_started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        if self._fail_on_set_state:
            self._root.set_state_calls += 1
            if self._root.set_state_calls >= 2:
                raise PersistenceError("injected persistence failure")
        if state not in ALLOWED_EXECUTION_ATTEMPT_STATES:
            raise PersistenceInputError("state is not an ExecutionAttempt state")
        current = self.get(attempt_id)
        if current is None:
            raise PersistenceError("execution_attempt not found for state update")
        self._store[attempt_id] = ExecutionAttemptRecord(
            attempt_id=current.attempt_id,
            request_id=current.request_id,
            experiment_id=current.experiment_id,
            research_run_id=current.research_run_id,
            correlation_id=current.correlation_id,
            worker_capability=current.worker_capability,
            action=current.action,
            target_reference=current.target_reference,
            budget_id=current.budget_id,
            side_effect_level=current.side_effect_level,
            authorization_decision_reference=current.authorization_decision_reference,
            state=state,
            created_at=current.created_at,
            authorized_at=current.authorized_at,
            dispatch_started_at=(
                dispatch_started_at
                if dispatch_started_at is not None
                else current.dispatch_started_at
            ),
            completed_at=completed_at if completed_at is not None else current.completed_at,
        )


class _WorkerResultRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.worker_results, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: WorkerResultRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        if record.request_id in self._root.worker_results_by_request:
            raise PersistenceConflictError("duplicate request_id")
        super().insert(record)
        self._root.worker_results_by_request[record.request_id] = record.worker_result_id

    def get_by_request_id(self, request_id: str) -> WorkerResultRecord | None:
        worker_result_id = self._root.worker_results_by_request.get(request_id)
        if worker_result_id is None:
            return None
        return self._root.worker_results.get(worker_result_id)

    def list_for_research_run(self, research_run_id: str) -> list[WorkerResultRecord]:
        return sorted(
            [
                record
                for record in self._root.worker_results.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.worker_result_id,
        )

    def list_for_experiment(self, experiment_id: str) -> list[WorkerResultRecord]:
        return sorted(
            [
                record
                for record in self._root.worker_results.values()
                if record.experiment_id == experiment_id
            ],
            key=lambda record: record.worker_result_id,
        )


class _ObservationRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.observations, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_worker_result(self, worker_result_id: str) -> list[ObservationRecord]:
        return [
            record
            for record in self._root.observations.values()
            if record.worker_result_id == worker_result_id
        ]

    def list_for_research_run(self, research_run_id: str) -> list[ObservationRecord]:
        result_ids = {
            record.worker_result_id
            for record in self._root.worker_results.values()
            if record.research_run_id == research_run_id
        }
        return sorted(
            [
                record
                for record in self._root.observations.values()
                if record.worker_result_id in result_ids
            ],
            key=lambda record: record.observation_id,
        )

    def list_for_experiment(self, experiment_id: str) -> list[ObservationRecord]:
        result_ids = {
            record.worker_result_id
            for record in self._root.worker_results.values()
            if record.experiment_id == experiment_id
        }
        return sorted(
            [
                record
                for record in self._root.observations.values()
                if record.worker_result_id in result_ids
            ],
            key=lambda record: record.observation_id,
        )


class _SensorObservationRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.sensor_observations, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[SensorObservationRecord]:
        return sorted(
            [
                record
                for record in self._root.sensor_observations.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.observation_id,
        )


class _AuditEventRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.audit_events, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_subject_type(self, subject_type: str) -> list[AuditEventRecord]:
        return sorted(
            [
                record
                for record in self._root.audit_events.values()
                if record.subject_type == subject_type
            ],
            key=lambda record: record.audit_event_id,
        )

    def list_for_subject(
        self, subject_type: str, subject_id: str
    ) -> list[AuditEventRecord]:
        return sorted(
            [
                record
                for record in self._root.audit_events.values()
                if record.subject_type == subject_type and record.subject_id == subject_id
            ],
            key=lambda record: record.occurred_at,
        )


class _ResearchReasoningRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.research_reasoning, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[ResearchReasoningRecord]:
        return sorted(
            [
                record
                for record in self._root.research_reasoning.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.reasoning_record_id,
        )

    def list_for_hypothesis(self, hypothesis_id: str) -> list[ResearchReasoningRecord]:
        return sorted(
            [
                record
                for record in self._root.research_reasoning.values()
                if record.hypothesis_id == hypothesis_id
            ],
            key=lambda record: record.reasoning_record_id,
        )


class _ResearchAdmissionRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.research_admissions, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[ResearchAdmissionRecord]:
        return sorted(
            [
                record
                for record in self._root.research_admissions.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.admission_record_id,
        )


class _HypothesisAssessmentRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.hypothesis_assessments, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_experiment(
        self, experiment_id: str
    ) -> list[HypothesisAssessmentRecord]:
        return sorted(
            [
                record
                for record in self._root.hypothesis_assessments.values()
                if record.experiment_id == experiment_id
            ],
            key=lambda record: record.assessment_id,
        )

    def list_for_hypothesis(
        self, hypothesis_id: str
    ) -> list[HypothesisAssessmentRecord]:
        return sorted(
            [
                record
                for record in self._root.hypothesis_assessments.values()
                if record.hypothesis_id == hypothesis_id
            ],
            key=lambda record: record.assessment_id,
        )

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[HypothesisAssessmentRecord]:
        return sorted(
            [
                record
                for record in self._root.hypothesis_assessments.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.assessment_id,
        )


class _EvidenceRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.evidence, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: EvidenceRecord) -> None:
        if record.polarity == "SUPPORTING":
            for existing in self._root.evidence.values():
                if (
                    existing.experiment_id == record.experiment_id
                    and existing.polarity == "SUPPORTING"
                ):
                    raise PersistenceConflictError(
                        "persistence unique constraint failed",
                        constraint_name=UQ_EVIDENCE_EXPERIMENT_SUPPORTING,
                    )
        super().insert(record)

    def list_for_research_run(self, research_run_id: str) -> list[EvidenceRecord]:
        return sorted(
            [
                record
                for record in self._root.evidence.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.evidence_id,
        )

    def list_for_hypothesis(self, hypothesis_id: str) -> list[EvidenceRecord]:
        return sorted(
            [
                record
                for record in self._root.evidence.values()
                if record.hypothesis_id == hypothesis_id
            ],
            key=lambda record: record.evidence_id,
        )

    def list_for_experiment(self, experiment_id: str) -> list[EvidenceRecord]:
        return sorted(
            [
                record
                for record in self._root.evidence.values()
                if record.experiment_id == experiment_id
            ],
            key=lambda record: record.evidence_id,
        )


class _EvidenceAdmissionRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.evidence_admissions, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[EvidenceAdmissionRecord]:
        return sorted(
            [
                record
                for record in self._root.evidence_admissions.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.admission_record_id,
        )


class _CandidateRepo(_Repo):
    def __init__(
        self,
        store: _Store,
        fail_on_insert: bool = False,
        fail_on_set_state: bool = False,
    ) -> None:
        super().__init__(store.candidates, fail_on_insert=fail_on_insert)
        self._root = store
        self._fail_on_set_state = fail_on_set_state

    def insert(self, record: CandidateRecord) -> None:
        incoming = set(record.evidence_ids)
        for existing in self._root.candidates.values():
            if incoming.intersection(existing.evidence_ids):
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_CANDIDATE_EVIDENCE_EVIDENCE_ID,
                )
        super().insert(record)

    def list_for_research_run(self, research_run_id: str) -> list[CandidateRecord]:
        return sorted(
            [
                record
                for record in self._root.candidates.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.candidate_id,
        )

    def set_state(self, candidate_id: str, state: str) -> None:
        if self._fail_on_set_state:
            raise PersistenceError("injected persistence failure")
        if state not in ALLOWED_CANDIDATE_STATES:
            raise PersistenceInputError("state is not a Candidate lifecycle state")
        current = self.get(candidate_id)
        if current is None:
            raise PersistenceError("candidate not found for state update")
        self._store[candidate_id] = CandidateRecord(
            candidate_id=current.candidate_id,
            research_run_id=current.research_run_id,
            hypothesis_id=current.hypothesis_id,
            claim=current.claim,
            classification=current.classification,
            state=state,
            evidence_ids=current.evidence_ids,
            created_at=current.created_at,
            admission_record_id=current.admission_record_id,
        )


class _CandidateAdmissionRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.candidate_admissions, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[CandidateAdmissionRecord]:
        return sorted(
            [
                record
                for record in self._root.candidate_admissions.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.admission_record_id,
        )


class _PromotionRunRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.promotion_runs, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: PromotionRunRecord) -> None:
        for existing in self._store.values():
            if existing.assessment_id == record.assessment_id:
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_PROMOTION_RUN_ASSESSMENT,
                )
            if (
                record.reproduction_experiment_id is not None
                and existing.reproduction_experiment_id == record.reproduction_experiment_id
            ):
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_PROMOTION_RUN_REPRODUCTION_EXPERIMENT,
                )
        super().insert(record)

    def get_by_assessment_id(self, assessment_id: str) -> PromotionRunRecord | None:
        for record in self._root.promotion_runs.values():
            if record.assessment_id == assessment_id:
                return record
        return None

    def list_for_research_run(self, research_run_id: str) -> list[PromotionRunRecord]:
        return sorted(
            [
                record
                for record in self._root.promotion_runs.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: (record.created_at, record.promotion_run_id),
        )

    def save(self, record: PromotionRunRecord) -> None:
        if record.stage not in ALLOWED_PROMOTION_STAGES:
            raise PersistenceInputError("stage is not a PromotionPipeline stage")
        if record.promotion_run_id not in self._store:
            raise PersistenceError("promotion_run not found for save")
        self._store[record.promotion_run_id] = record

    def claim_reproduction(
        self, promotion_run_id: str, reproduction_experiment_id: str
    ) -> bool:
        current = self.get(promotion_run_id)
        if (
            current is None
            or current.stage != "VERIFYING"
            or current.reproduction_experiment_id is not None
        ):
            return False
        for existing in self._store.values():
            if existing.reproduction_experiment_id == reproduction_experiment_id:
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_PROMOTION_RUN_REPRODUCTION_EXPERIMENT,
                )
        self._store[promotion_run_id] = replace(
            current,
            reproduction_experiment_id=reproduction_experiment_id,
            updated_at=datetime.now(timezone.utc),
        )
        return True


class _VerificationRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.verifications, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: VerificationRecord) -> None:
        for existing in self._root.verifications.values():
            if existing.candidate_id == record.candidate_id:
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_VERIFICATION_CANDIDATE,
                )
        super().insert(record)

    def list_for_candidate(self, candidate_id: str) -> list[VerificationRecord]:
        return sorted(
            [
                record
                for record in self._root.verifications.values()
                if record.candidate_id == candidate_id
            ],
            key=lambda record: record.verification_id,
        )

    def list_for_research_run(self, research_run_id: str) -> list[VerificationRecord]:
        return sorted(
            [
                record
                for record in self._root.verifications.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.verification_id,
        )


class _FindingProposalRepo(_Repo):
    def __init__(
        self,
        store: _Store,
        fail_on_insert: bool = False,
        fail_on_set_state: bool = False,
    ) -> None:
        super().__init__(store.finding_proposals, fail_on_insert=fail_on_insert)
        self._root = store
        self._fail_on_set_state = fail_on_set_state

    def insert(self, record: FindingProposalRecord) -> None:
        for existing in self._root.finding_proposals.values():
            if existing.candidate_id == record.candidate_id:
                raise PersistenceConflictError(
                    "persistence unique constraint failed",
                    constraint_name=UQ_FINDING_PROPOSAL_CANDIDATE,
                )
        super().insert(record)

    def list_for_candidate(self, candidate_id: str) -> list[FindingProposalRecord]:
        return sorted(
            [
                record
                for record in self._root.finding_proposals.values()
                if record.candidate_id == candidate_id
            ],
            key=lambda record: record.proposal_id,
        )

    def list_for_research_run(self, research_run_id: str) -> list[FindingProposalRecord]:
        return sorted(
            [
                record
                for record in self._root.finding_proposals.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.proposal_id,
        )

    def set_state(self, proposal_id: str, state: str) -> None:
        if self._fail_on_set_state:
            raise PersistenceError("injected persistence failure")
        if state not in ALLOWED_FINDING_PROPOSAL_STATES:
            raise PersistenceInputError("state is not a FindingProposal lifecycle state")
        current = self.get(proposal_id)
        if current is None:
            raise PersistenceError("finding_proposal not found for state update")
        self._store[proposal_id] = FindingProposalRecord(
            proposal_id=current.proposal_id,
            candidate_id=current.candidate_id,
            research_run_id=current.research_run_id,
            title=current.title,
            claim=current.claim,
            classification=current.classification,
            state=state,
            evidence_ids=current.evidence_ids,
            verification_ids=current.verification_ids,
            content_fingerprint=current.content_fingerprint,
            created_at=current.created_at,
        )


class _HumanReviewRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.human_reviews, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: HumanReviewRecord) -> None:
        for existing in self._root.human_reviews.values():
            if (
                existing.proposal_id == record.proposal_id
                and existing.content_fingerprint == record.content_fingerprint
            ):
                raise PersistenceConflictError("duplicate human review")
        super().insert(record)

    def get_for_proposal(self, proposal_id: str) -> HumanReviewRecord | None:
        matches = [
            record
            for record in self._root.human_reviews.values()
            if record.proposal_id == proposal_id
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda record: record.review_id)[0]


class _ApprovalRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.approvals, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: ApprovalRecord) -> None:
        for existing in self._root.approvals.values():
            if existing.subject_reference == record.subject_reference:
                raise PersistenceConflictError("duplicate approval subject")
        super().insert(record)

    def get_by_subject(self, subject_reference: str) -> ApprovalRecord | None:
        for record in self._root.approvals.values():
            if record.subject_reference == subject_reference:
                return record
        return None


class _FindingRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.findings, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: FindingRecord) -> None:
        for existing in self._root.findings.values():
            if existing.finding_proposal_id == record.finding_proposal_id:
                raise PersistenceConflictError("duplicate finding for proposal")
        super().insert(record)

    def get_by_proposal(self, finding_proposal_id: str) -> FindingRecord | None:
        for record in self._root.findings.values():
            if record.finding_proposal_id == finding_proposal_id:
                return record
        return None

    def list_for_research_run(self, research_run_id: str) -> list[FindingRecord]:
        return sorted(
            [
                record
                for record in self._root.findings.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.finding_id,
        )


class _TargetInferenceRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.target_inferences, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[TargetInferenceRecord]:
        return sorted(
            [
                record
                for record in self._root.target_inferences.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.inference_id,
        )


class _DifferentialObservationRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.differential_observations, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[DifferentialObservationRecord]:
        return sorted(
            [
                record
                for record in self._root.differential_observations.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.differential_id,
        )


class _InvariantHypothesisRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.invariant_hypotheses, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[InvariantHypothesisRecord]:
        return sorted(
            [
                record
                for record in self._root.invariant_hypotheses.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.invariant_id,
        )

    def set_state(self, invariant_id: str, state: str) -> None:
        if state not in ALLOWED_INVARIANT_STATUSES:
            raise PersistenceInputError("status is not an invariant hypothesis status")
        current = self.get(invariant_id)
        if current is None:
            raise PersistenceError("invariant hypothesis not found for state update")
        self._store[invariant_id] = InvariantHypothesisRecord(
            invariant_id=current.invariant_id,
            research_run_id=current.research_run_id,
            invariant_kind=current.invariant_kind,
            status=state,
            subject_refs=current.subject_refs,
            expected_behavior=current.expected_behavior,
            source_refs=current.source_refs,
            applicability_context=current.applicability_context,
            assumptions=current.assumptions,
            counterexample_refs=current.counterexample_refs,
            falsification_direction=current.falsification_direction,
            proposer_provenance=current.proposer_provenance,
            strategy_version=current.strategy_version,
            created_at=current.created_at,
        )

    def add_counterexample(self, record: InvariantCounterexampleRefRecord) -> None:
        current = self.get(record.invariant_id)
        if current is None:
            raise PersistenceError("invariant hypothesis not found for counterexample")
        if record.counterexample_id in self._root.invariant_counterexamples:
            raise PersistenceConflictError("duplicate id")
        self._root.invariant_counterexamples[record.counterexample_id] = record
        refs = current.counterexample_refs
        if record.source_ref not in refs:
            refs = refs + (record.source_ref,)
        self._store[record.invariant_id] = InvariantHypothesisRecord(
            invariant_id=current.invariant_id,
            research_run_id=current.research_run_id,
            invariant_kind=current.invariant_kind,
            status="CHALLENGED",
            subject_refs=current.subject_refs,
            expected_behavior=current.expected_behavior,
            source_refs=current.source_refs,
            applicability_context=current.applicability_context,
            assumptions=current.assumptions,
            counterexample_refs=refs,
            falsification_direction=current.falsification_direction,
            proposer_provenance=current.proposer_provenance,
            strategy_version=current.strategy_version,
            created_at=current.created_at,
        )


class _ChainHypothesisRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.chain_hypotheses, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: ChainHypothesisRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        for existing in self._root.chain_hypotheses.values():
            if (
                existing.research_run_id == record.research_run_id
                and existing.structural_identity == record.structural_identity
            ):
                raise PersistenceConflictError("duplicate chain structural identity")
        super().insert(record)

    def list_for_research_run(self, research_run_id: str) -> list[ChainHypothesisRecord]:
        return sorted(
            [
                record
                for record in self._root.chain_hypotheses.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.chain_id,
        )


class _ResearchOpportunityRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.research_opportunities, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: ResearchOpportunityRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        for existing in self._root.research_opportunities.values():
            if (
                existing.research_run_id == record.research_run_id
                and existing.structural_identity == record.structural_identity
            ):
                raise PersistenceConflictError("duplicate opportunity structural identity")
        super().insert(record)

    def list_for_research_run(self, research_run_id: str) -> list[ResearchOpportunityRecord]:
        return sorted(
            [
                record
                for record in self._root.research_opportunities.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.opportunity_id,
        )


class _ResearchSelectionRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.research_selections, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[ResearchSelectionRecord]:
        return sorted(
            [
                record
                for record in self._root.research_selections.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.selection_id,
        )


class _OpportunitySelectionCandidateRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.opportunity_selection_candidates, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: OpportunitySelectionCandidateRecord) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        for existing in self._root.opportunity_selection_candidates.values():
            if (
                existing.research_run_id == record.research_run_id
                and existing.structural_identity == record.structural_identity
            ):
                raise PersistenceConflictError("duplicate candidate structural identity")
        super().insert(record)

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[OpportunitySelectionCandidateRecord]:
        return sorted(
            [
                record
                for record in self._root.opportunity_selection_candidates.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.candidate_id,
        )

    def mark_decided(
        self,
        candidate_id: str,
        *,
        outcome: str,
        resulting_opportunity_id: str | None,
        decided_at,
    ) -> bool:
        current = self._root.opportunity_selection_candidates.get(candidate_id)
        if current is None or current.outcome != "PENDING":
            return False
        self._root.opportunity_selection_candidates[candidate_id] = replace(
            current,
            outcome=outcome,
            resulting_opportunity_id=resulting_opportunity_id,
            decided_at=decided_at,
        )
        return True


class _SnapshotRepo:
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        self._root = store
        self._fail_on_insert = fail_on_insert

    def insert(self, record: SnapshotRecord, members: tuple[SnapshotMemberRecord, ...]) -> None:
        if self._fail_on_insert:
            raise PersistenceError("injected persistence failure")
        if record.snapshot_id in self._root.snapshots:
            raise PersistenceConflictError("duplicate id")
        self._root.snapshots[record.snapshot_id] = record
        for member in members:
            self._root.snapshot_members[f"{member.snapshot_id}:{member.observation_id}"] = member

    def get(self, snapshot_id: str) -> SnapshotRecord | None:
        return self._root.snapshots.get(snapshot_id)

    def list_members(self, snapshot_id: str) -> list[SnapshotMemberRecord]:
        return sorted(
            [
                record
                for record in self._root.snapshot_members.values()
                if record.snapshot_id == snapshot_id
            ],
            key=lambda record: record.observation_id,
        )

    def list_for_research_run(self, research_run_id: str) -> list[SnapshotRecord]:
        return sorted(
            [
                record
                for record in self._root.snapshots.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.snapshot_id,
        )


class _ChangeEventRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.change_events, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[ChangeEventRecord]:
        return sorted(
            [
                record
                for record in self._root.change_events.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.change_event_id,
        )


class _CoverageDebtSnapshotRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.coverage_debt_snapshots, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[CoverageDebtSnapshotRecord]:
        return sorted(
            [
                record
                for record in self._root.coverage_debt_snapshots.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.created_at,
        )


class _ResearchOrchestrationRepo:
    def __init__(self, store: _Store) -> None:
        self._root = store

    def insert(self, record: ResearchOrchestrationRecord) -> None:
        if record.research_run_id in self._root.research_orchestrations:
            raise PersistenceConflictError("duplicate id")
        self._root.research_orchestrations[record.research_run_id] = record

    def get(self, research_run_id: str) -> ResearchOrchestrationRecord | None:
        return self._root.research_orchestrations.get(research_run_id)

    def save(
        self,
        record: ResearchOrchestrationRecord,
        *,
        expect_owner_runtime_instance_id: str | None = None,
        expect_lease_epoch: int | None = None,
        require_unowned_or_expired: bool = False,
    ) -> None:
        if (expect_owner_runtime_instance_id is None) != (expect_lease_epoch is None):
            raise PersistenceInputError(
                "expect_owner_runtime_instance_id and expect_lease_epoch must be "
                "provided together or not at all"
            )
        fenced_by_epoch = expect_owner_runtime_instance_id is not None
        if fenced_by_epoch and require_unowned_or_expired:
            raise PersistenceInputError(
                "cannot combine epoch fencing with require_unowned_or_expired"
            )
        current = self._root.research_orchestrations.get(record.research_run_id)
        if current is None:
            raise PersistenceError("research_orchestration not found for checkpoint")
        if current.state in TERMINAL_ORCHESTRATION_STATES:
            raise TerminalOrchestrationStateError(
                f"research_orchestration {record.research_run_id} is terminal "
                f"({current.state}); state and stop_reason are immutable"
            )
        if fenced_by_epoch and (
            current.owner_runtime_instance_id != expect_owner_runtime_instance_id
            or current.lease_epoch != expect_lease_epoch
        ):
            raise LeaseFencingError(
                f"research_orchestration {record.research_run_id} lease has moved on "
                f"(expected owner={expect_owner_runtime_instance_id!r} "
                f"epoch={expect_lease_epoch}, current owner="
                f"{current.owner_runtime_instance_id!r} epoch={current.lease_epoch}); "
                "ownership lost, refusing to persist"
            )
        if require_unowned_or_expired:
            now = datetime.now(timezone.utc)
            is_unowned = current.owner_runtime_instance_id is None
            is_expired = (
                current.lease_expires_at is not None and current.lease_expires_at < now
            )
            if not (is_unowned or is_expired):
                raise LeaseFencingError(
                    f"research_orchestration {record.research_run_id} is still "
                    f"actively leased by {current.owner_runtime_instance_id!r} "
                    f"(epoch={current.lease_epoch}); refusing reconciliation write"
                )
        self._root.research_orchestrations[record.research_run_id] = record

    def acquire_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        ttl_seconds: float,
    ) -> LeaseAcquireResult:
        current = self._root.research_orchestrations.get(research_run_id)
        if current is None:
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.NOT_FOUND)
        if current.state in TERMINAL_ORCHESTRATION_STATES:
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.DENIED_TERMINAL)
        now = datetime.now(timezone.utc)
        is_free = current.owner_runtime_instance_id is None
        is_same_owner = current.owner_runtime_instance_id == owner_runtime_instance_id
        is_expired = (
            current.lease_expires_at is not None and current.lease_expires_at < now
        )
        if not (is_free or is_same_owner or is_expired):
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.DENIED_HELD_BY_OTHER)
        updated = replace(
            current,
            owner_runtime_instance_id=owner_runtime_instance_id,
            lease_epoch=current.lease_epoch + 1,
            lease_expires_at=now + timedelta(seconds=ttl_seconds),
            updated_at=now,
        )
        self._root.research_orchestrations[research_run_id] = updated
        return LeaseAcquireResult(outcome=LeaseAcquireOutcome.ACQUIRED, record=updated)

    def renew_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        expected_lease_epoch: int,
        ttl_seconds: float,
    ) -> bool:
        current = self._root.research_orchestrations.get(research_run_id)
        if current is None or current.state in TERMINAL_ORCHESTRATION_STATES:
            return False
        if (
            current.owner_runtime_instance_id != owner_runtime_instance_id
            or current.lease_epoch != expected_lease_epoch
        ):
            return False
        now = datetime.now(timezone.utc)
        self._root.research_orchestrations[research_run_id] = replace(
            current, lease_expires_at=now + timedelta(seconds=ttl_seconds), updated_at=now
        )
        return True

    def release_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        expected_lease_epoch: int,
    ) -> bool:
        current = self._root.research_orchestrations.get(research_run_id)
        if current is None:
            return False
        if (
            current.owner_runtime_instance_id != owner_runtime_instance_id
            or current.lease_epoch != expected_lease_epoch
        ):
            return False
        self._root.research_orchestrations[research_run_id] = replace(
            current,
            owner_runtime_instance_id=None,
            lease_expires_at=None,
            updated_at=datetime.now(timezone.utc),
        )
        return True

    def list_recoverable(self) -> list[ResearchOrchestrationRecord]:
        return [
            record
            for record in self._root.research_orchestrations.values()
            if record.state in {"READY", "RUNNING"}
        ]


class _ResearchCycleRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.research_cycles, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[ResearchCycleRecord]:
        return sorted(
            [
                record
                for record in self._root.research_cycles.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.cycle_number,
        )


class _BudgetConsumptionRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.budget_consumptions)
        self._root = store

    def insert_within_allowance(
        self,
        record: BudgetConsumptionRecord,
        issued: IssuedBudgetRecord,
    ) -> None:
        del issued
        locked = self._root.issued_budgets.get(record.budget_id)
        if locked is None:
            raise PersistenceError("issued budget not found for consumption")
        if locked.research_run_id != record.research_run_id:
            raise PersistenceError("locked budget research_run_id mismatch")
        existing = self.list_for_budget(record.budget_id)
        if any(
            item.request_id == record.request_id
            and item.resource_type == record.resource_type
            and record.request_id is not None
            for item in existing
        ):
            return
        orchestration = None
        if record.resource_type == "MODEL_CALL":
            orchestration = self._root.research_orchestrations.get(record.research_run_id)
            if orchestration is None:
                raise BudgetOverspendError("MODEL_CALL requires locked orchestration allowance")
        assert_within_allowance(
            locked, existing, record, orchestration=orchestration
        )
        try:
            self.insert(record)
        except PersistenceConflictError:
            return

    def list_for_budget(self, budget_id: str) -> list[BudgetConsumptionRecord]:
        return [
            record
            for record in self._root.budget_consumptions.values()
            if record.budget_id == budget_id
        ]

    def list_for_research_run(self, research_run_id: str) -> list[BudgetConsumptionRecord]:
        return [
            record
            for record in self._root.budget_consumptions.values()
            if record.research_run_id == research_run_id
        ]


class _SessionContextRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.session_contexts, fail_on_insert=fail_on_insert)

    def set_state(
        self,
        session_context_id: str,
        state: str,
        *,
        established_at: datetime | None = None,
        expires_at: datetime | None = None,
        updated_at: datetime,
    ) -> None:
        if state not in ALLOWED_SESSION_STATES:
            raise PersistenceInputError("state is not a SessionContext state")
        current = self._store.get(session_context_id)
        if current is None:
            raise PersistenceError("session_context not found for state update")
        self._store[session_context_id] = replace(
            current,
            state=state,
            updated_at=updated_at,
            established_at=current.established_at if established_at is None else established_at,
            expires_at=current.expires_at if expires_at is None else expires_at,
        )


class _ControlEventRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.control_events, fail_on_insert=fail_on_insert)
        self._root = store

    def get_by_worker_result(self, worker_result_id: str) -> ControlEventRecord | None:
        for record in self._root.control_events.values():
            if record.worker_result_id == worker_result_id:
                return record
        return None

    def list_for_research_run(self, research_run_id: str) -> list[ControlEventRecord]:
        return [item for item in self._root.control_events.values() if item.research_run_id == research_run_id]


class _DiscoveryFactRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.discovery_facts, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: DiscoveryFactRecord) -> None:
        existing = self.get_by_canonical(record.research_run_id, record.canonical_key)
        if existing is not None and existing.fact_id != record.fact_id:
            raise PersistenceConflictError("duplicate semantic discovery fact")
        super().insert(record)

    def get_by_canonical(self, research_run_id: str, canonical_key: str) -> DiscoveryFactRecord | None:
        for record in self._root.discovery_facts.values():
            if record.research_run_id == research_run_id and record.canonical_key == canonical_key:
                return record
        return None

    def list_for_research_run(self, research_run_id: str) -> list[DiscoveryFactRecord]:
        return [item for item in self._root.discovery_facts.values() if item.research_run_id == research_run_id]


class _DiscoveryFactSourceRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.discovery_fact_sources, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_fact(self, fact_id: str) -> list[DiscoveryFactSourceRecord]:
        return [item for item in self._root.discovery_fact_sources.values() if item.fact_id == fact_id]


class _DiscoveryInferenceRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.discovery_inferences, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[DiscoveryInferenceRecord]:
        return [item for item in self._root.discovery_inferences.values() if item.research_run_id == research_run_id]


class _FrontierItemRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.frontier_items, fail_on_insert=fail_on_insert)
        self._root = store

    def lock(self, frontier_id: str) -> FrontierItemRecord | None:
        return self.get(frontier_id)

    def list_for_research_run(self, research_run_id: str) -> list[FrontierItemRecord]:
        return [item for item in self._root.frontier_items.values() if item.research_run_id == research_run_id]

    def set_cache_state(self, frontier_id: str, current_state: str, state_version: int) -> None:
        current = self.get(frontier_id)
        if current is None:
            raise PersistenceError("frontier_item not found for cache update")
        self._store[frontier_id] = replace(
            current, current_state=current_state, state_version=state_version
        )


class _FrontierEventRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.frontier_events, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: FrontierEventRecord) -> None:
        if record.event_kind == "SELECTED":
            for existing in self._root.frontier_events.values():
                if (
                    existing.frontier_id == record.frontier_id
                    and existing.event_kind == "SELECTED"
                    and existing.selection_generation == record.selection_generation
                ):
                    raise PersistenceConflictError("persistence unique constraint failed")
        super().insert(record)

    def list_for_frontier(self, frontier_id: str) -> list[FrontierEventRecord]:
        return sorted(
            [item for item in self._root.frontier_events.values() if item.frontier_id == frontier_id],
            key=lambda item: item.sequence,
        )

    def list_for_research_run(self, research_run_id: str) -> list[FrontierEventRecord]:
        return [item for item in self._root.frontier_events.values() if item.research_run_id == research_run_id]


class _ReceiptRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.discovery_projection_receipts, fail_on_insert=fail_on_insert)
        self._root = store

    def insert(self, record: DiscoveryProjectionReceiptRecord) -> None:
        if record.observation_id and self.has_observation(record.research_run_id, record.observation_id):
            raise PersistenceConflictError("persistence unique constraint failed")
        if record.control_event_id and self.has_control_event(
            record.research_run_id, record.control_event_id
        ):
            raise PersistenceConflictError("persistence unique constraint failed")
        super().insert(record)

    def has_observation(self, research_run_id: str, observation_id: str) -> bool:
        return any(
            item.research_run_id == research_run_id and item.observation_id == observation_id
            for item in self._root.discovery_projection_receipts.values()
        )

    def has_control_event(self, research_run_id: str, control_event_id: str) -> bool:
        return any(
            item.research_run_id == research_run_id and item.control_event_id == control_event_id
            for item in self._root.discovery_projection_receipts.values()
        )


class _HunterFamilyRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.hunter_families, fail_on_insert=fail_on_insert)
        self._root = store

    def get(self, family_id: str, version: int) -> HunterFamilyRecord | None:
        return self._root.hunter_families.get(f"{family_id}:{version}")

    def get_latest(self, family_id: str) -> HunterFamilyRecord | None:
        matches = [
            record
            for record in self._root.hunter_families.values()
            if record.family_id == family_id
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda record: record.version, reverse=True)[0]

    def list_enabled(self) -> list[HunterFamilyRecord]:
        return sorted(
            [record for record in self._root.hunter_families.values() if record.enabled],
            key=lambda record: (record.family_id, record.version),
        )


class _HuntV3QueueRepo(_Repo):
    def __init__(self, store: _Store, fail_on_insert: bool = False) -> None:
        super().__init__(store.hunt_v3_queue, fail_on_insert=fail_on_insert)
        self._root = store

    def list_for_research_run(self, research_run_id: str) -> list[HuntV3QueueRecord]:
        return sorted(
            [
                record
                for record in self._root.hunt_v3_queue.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.created_at,
        )

    def list_pending_for_research_run(self, research_run_id: str) -> list[HuntV3QueueRecord]:
        return sorted(
            [
                record
                for record in self._root.hunt_v3_queue.values()
                if record.research_run_id == research_run_id and record.state == "PENDING"
            ],
            key=lambda record: record.created_at,
        )

    def set_state(
        self, queue_id: str, state: str, *, from_state: str | None = None
    ) -> None:
        if state not in {"PENDING", "APPROVED", "RUN", "BLOCKED"}:
            raise PersistenceInputError("state is not a HuntV3Queue state")
        if from_state is not None and from_state not in {
            "PENDING",
            "APPROVED",
            "RUN",
            "BLOCKED",
        }:
            raise PersistenceInputError("from_state is not a HuntV3Queue state")
        current = self.get(queue_id)
        if current is None:
            raise PersistenceError("hunt_v3_queue item not found for state update")
        if from_state is not None and current.state != from_state:
            raise PersistenceConflictError("hunt_v3_queue state transition conflict")
        self._store[queue_id] = HuntV3QueueRecord(
            queue_id=current.queue_id,
            research_run_id=current.research_run_id,
            hypothesis_id=current.hypothesis_id,
            family_id=current.family_id,
            node_canonical_key=current.node_canonical_key,
            identity_id=current.identity_id,
            capability=current.capability,
            action=current.action,
            arguments=current.arguments,
            side_effect_level=current.side_effect_level,
            state=state,
            created_at=current.created_at,
        )


class _RuntimeInstanceRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.runtime_instances)
        self._root = store

    def save(self, record: RuntimeInstanceRecord) -> None:
        if record.runtime_instance_id not in self._store:
            raise PersistenceError("runtime_instance not found")
        self._store[record.runtime_instance_id] = record

    def list_active(self) -> list[RuntimeInstanceRecord]:
        return [
            record
            for record in self._root.runtime_instances.values()
            if record.status in {"STARTING", "RUNNING", "DRAINING"}
        ]


class _PreflightReportRepo(_Repo):
    def __init__(self, store: _Store) -> None:
        super().__init__(store.preflight_reports)
        self._root = store

    def latest_for_research_run(
        self, research_run_id: str
    ) -> PreflightReportRecord | None:
        matches = self.list_for_research_run(research_run_id)
        return matches[-1] if matches else None

    def list_for_research_run(
        self, research_run_id: str
    ) -> list[PreflightReportRecord]:
        return sorted(
            [
                record
                for record in self._root.preflight_reports.values()
                if record.research_run_id == research_run_id
            ],
            key=lambda record: record.created_at,
        )


class FakeUnitOfWork:
    def __init__(self, store: _Store | None = None, fail_on: str | None = None) -> None:
        self._store = store or _Store()
        self._fail_on = fail_on
        self._committed = False
        self._snapshot: _Store | None = None
        self.programs = _ProgramRepo(self._store.programs)
        self.scope_rules_v2 = _ScopeRuleV2Repo(self._store)
        self.program_policies = _Repo(self._store.program_policies)
        self.rate_limit_profiles = _RateLimitProfileRepo(self._store)
        self.bounty_tables = _BountyTableRepo(self._store)
        self.authorization_sources = _Repo(self._store.authorization_sources)
        self.research_runs = _ResearchRunRepo(self._store.research_runs)
        self.issued_budgets = _IssuedBudgetRepo(self._store)
        self.hypotheses = _HypothesisRepo(
            self._store, fail_on_insert=fail_on == "hypotheses"
        )
        self.experiments = _ExperimentRepo(self._store.experiments)
        self.execution_attempts = _ExecutionAttemptRepo(
            self._store,
            fail_on_insert=fail_on == "execution_attempts",
            fail_on_set_state=fail_on == "attempt_outcome",
        )
        self.worker_results = _WorkerResultRepo(
            self._store, fail_on_insert=fail_on == "worker_results"
        )
        self.observations = _ObservationRepo(
            self._store, fail_on_insert=fail_on == "observations"
        )
        self.sensor_observations = _SensorObservationRepo(
            self._store, fail_on_insert=fail_on == "sensor_observations"
        )
        self.oast_correlations = _OastCorrelationRepo(self._store)
        self.oast_callback_deliveries = _OastCallbackDeliveryRepo(self._store)
        self.oast_admissions = _OastAdmissionRepo(self._store)
        self.research_reasoning = _ResearchReasoningRepo(
            self._store, fail_on_insert=fail_on == "research_reasoning"
        )
        self.research_admissions = _ResearchAdmissionRepo(
            self._store, fail_on_insert=fail_on == "research_admissions"
        )
        self.experiment_plans = _Repo(
            self._store.experiment_plans, fail_on_insert=fail_on == "experiment_plans"
        )
        self.hypothesis_assessments = _HypothesisAssessmentRepo(
            self._store, fail_on_insert=fail_on == "hypothesis_assessments"
        )
        self.evidence = _EvidenceRepo(self._store, fail_on_insert=fail_on == "evidence")
        self.evidence_admissions = _EvidenceAdmissionRepo(
            self._store, fail_on_insert=fail_on == "evidence_admissions"
        )
        self.candidates = _CandidateRepo(
            self._store,
            fail_on_insert=fail_on == "candidates",
            fail_on_set_state=fail_on == "candidate_state",
        )
        self.candidate_admissions = _CandidateAdmissionRepo(
            self._store, fail_on_insert=fail_on == "candidate_admissions"
        )
        self.promotion_runs = _PromotionRunRepo(
            self._store, fail_on_insert=fail_on == "promotion_runs"
        )
        self.verifications = _VerificationRepo(
            self._store, fail_on_insert=fail_on == "verifications"
        )
        self.finding_proposals = _FindingProposalRepo(
            self._store,
            fail_on_insert=fail_on == "finding_proposals",
            fail_on_set_state=fail_on == "finding_proposal_state",
        )
        self.human_reviews = _HumanReviewRepo(
            self._store, fail_on_insert=fail_on == "human_reviews"
        )
        self.approvals = _ApprovalRepo(
            self._store, fail_on_insert=fail_on == "approvals"
        )
        self.findings = _FindingRepo(self._store, fail_on_insert=fail_on == "findings")
        self.target_inferences = _TargetInferenceRepo(
            self._store, fail_on_insert=fail_on == "target_inferences"
        )
        self.differential_observations = _DifferentialObservationRepo(
            self._store, fail_on_insert=fail_on == "differential_observations"
        )
        self.invariant_hypotheses = _InvariantHypothesisRepo(
            self._store, fail_on_insert=fail_on == "invariant_hypotheses"
        )
        self.chain_hypotheses = _ChainHypothesisRepo(
            self._store, fail_on_insert=fail_on == "chain_hypotheses"
        )
        self.research_opportunities = _ResearchOpportunityRepo(
            self._store, fail_on_insert=fail_on == "research_opportunities"
        )
        self.research_selections = _ResearchSelectionRepo(
            self._store, fail_on_insert=fail_on == "research_selections"
        )
        self.opportunity_selection_candidates = _OpportunitySelectionCandidateRepo(
            self._store, fail_on_insert=fail_on == "opportunity_selection_candidates"
        )
        self.snapshots = _SnapshotRepo(
            self._store, fail_on_insert=fail_on == "snapshots"
        )
        self.change_events = _ChangeEventRepo(
            self._store, fail_on_insert=fail_on == "change_events"
        )
        self.coverage_debt_snapshots = _CoverageDebtSnapshotRepo(
            self._store, fail_on_insert=fail_on == "coverage_debt_snapshots"
        )
        self.research_orchestrations = _ResearchOrchestrationRepo(self._store)
        self.research_cycles = _ResearchCycleRepo(
            self._store, fail_on_insert=fail_on == "research_cycles"
        )
        self.budget_consumptions = _BudgetConsumptionRepo(self._store)
        self.audit_events = _AuditEventRepo(
            self._store, fail_on_insert=fail_on == "audit_events"
        )
        self.session_contexts = _SessionContextRepo(
            self._store, fail_on_insert=fail_on == "session_contexts"
        )
        self.discovery_run_configs = _Repo(self._store.discovery_run_configs)
        self.control_events = _ControlEventRepo(self._store)
        self.discovery_facts = _DiscoveryFactRepo(self._store)
        self.discovery_fact_sources = _DiscoveryFactSourceRepo(self._store)
        self.discovery_inferences = _DiscoveryInferenceRepo(self._store)
        self.discovery_inference_sources = _Repo(self._store.discovery_inference_sources)
        self.frontier_items = _FrontierItemRepo(self._store)
        self.frontier_sources = _Repo(self._store.frontier_sources)
        self.frontier_events = _FrontierEventRepo(self._store)
        self.discovery_projection_receipts = _ReceiptRepo(self._store)
        self.hunter_families = _HunterFamilyRepo(
            self._store, fail_on_insert=fail_on == "hunter_families"
        )
        self.hunt_v3_queue = _HuntV3QueueRepo(
            self._store, fail_on_insert=fail_on == "hunt_v3_queue"
        )
        self.runtime_instances = _RuntimeInstanceRepo(self._store)
        self.preflight_reports = _PreflightReportRepo(self._store)

    def __enter__(self) -> FakeUnitOfWork:
        self._store.open_transactions += 1
        self._snapshot = deepcopy(self._store)
        self._committed = False
        return self

    def commit(self) -> None:
        self._committed = True
        self._snapshot = None

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._restore(self._snapshot)
        self._committed = False

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            self._store.open_transactions = max(0, self._store.open_transactions - 1)
        return False

    def _restore(self, snapshot: _Store) -> None:
        self._store.programs.clear()
        self._store.programs.update(snapshot.programs)
        self._store.scope_rules_v2.clear()
        self._store.scope_rules_v2.update(snapshot.scope_rules_v2)
        self._store.program_policies.clear()
        self._store.program_policies.update(snapshot.program_policies)
        self._store.rate_limit_profiles.clear()
        self._store.rate_limit_profiles.update(snapshot.rate_limit_profiles)
        self._store.bounty_tables.clear()
        self._store.bounty_tables.update(snapshot.bounty_tables)
        self._store.authorization_sources.clear()
        self._store.authorization_sources.update(snapshot.authorization_sources)
        self._store.research_runs.clear()
        self._store.research_runs.update(snapshot.research_runs)
        self._store.issued_budgets.clear()
        self._store.issued_budgets.update(snapshot.issued_budgets)
        self._store.hypotheses.clear()
        self._store.hypotheses.update(snapshot.hypotheses)
        self._store.experiments.clear()
        self._store.experiments.update(snapshot.experiments)
        self._store.execution_attempts.clear()
        self._store.execution_attempts.update(snapshot.execution_attempts)
        self._store.execution_attempts_by_request.clear()
        self._store.execution_attempts_by_request.update(
            snapshot.execution_attempts_by_request
        )
        self._store.worker_results.clear()
        self._store.worker_results.update(snapshot.worker_results)
        self._store.worker_results_by_request.clear()
        self._store.worker_results_by_request.update(snapshot.worker_results_by_request)
        self._store.observations.clear()
        self._store.observations.update(snapshot.observations)
        self._store.oast_correlations.clear()
        self._store.oast_correlations.update(snapshot.oast_correlations)
        self._store.oast_callback_deliveries.clear()
        self._store.oast_callback_deliveries.update(snapshot.oast_callback_deliveries)
        self._store.oast_admissions.clear()
        self._store.oast_admissions.update(snapshot.oast_admissions)
        self._store.research_reasoning.clear()
        self._store.research_reasoning.update(snapshot.research_reasoning)
        self._store.research_admissions.clear()
        self._store.research_admissions.update(snapshot.research_admissions)
        self._store.experiment_plans.clear()
        self._store.experiment_plans.update(snapshot.experiment_plans)
        self._store.hypothesis_assessments.clear()
        self._store.hypothesis_assessments.update(snapshot.hypothesis_assessments)
        self._store.evidence.clear()
        self._store.evidence.update(snapshot.evidence)
        self._store.evidence_admissions.clear()
        self._store.evidence_admissions.update(snapshot.evidence_admissions)
        self._store.candidates.clear()
        self._store.candidates.update(snapshot.candidates)
        self._store.candidate_admissions.clear()
        self._store.candidate_admissions.update(snapshot.candidate_admissions)
        self._store.promotion_runs.clear()
        self._store.promotion_runs.update(snapshot.promotion_runs)
        self._store.verifications.clear()
        self._store.verifications.update(snapshot.verifications)
        self._store.finding_proposals.clear()
        self._store.finding_proposals.update(snapshot.finding_proposals)
        self._store.human_reviews.clear()
        self._store.human_reviews.update(snapshot.human_reviews)
        self._store.approvals.clear()
        self._store.approvals.update(snapshot.approvals)
        self._store.findings.clear()
        self._store.findings.update(snapshot.findings)
        self._store.target_inferences.clear()
        self._store.target_inferences.update(snapshot.target_inferences)
        self._store.differential_observations.clear()
        self._store.differential_observations.update(snapshot.differential_observations)
        self._store.invariant_hypotheses.clear()
        self._store.invariant_hypotheses.update(snapshot.invariant_hypotheses)
        self._store.invariant_counterexamples.clear()
        self._store.invariant_counterexamples.update(snapshot.invariant_counterexamples)
        self._store.chain_hypotheses.clear()
        self._store.chain_hypotheses.update(snapshot.chain_hypotheses)
        self._store.research_opportunities.clear()
        self._store.research_opportunities.update(snapshot.research_opportunities)
        self._store.research_selections.clear()
        self._store.research_selections.update(snapshot.research_selections)
        self._store.opportunity_selection_candidates.clear()
        self._store.opportunity_selection_candidates.update(
            snapshot.opportunity_selection_candidates
        )
        self._store.snapshots.clear()
        self._store.snapshots.update(snapshot.snapshots)
        self._store.snapshot_members.clear()
        self._store.snapshot_members.update(snapshot.snapshot_members)
        self._store.change_events.clear()
        self._store.change_events.update(snapshot.change_events)
        self._store.coverage_debt_snapshots.clear()
        self._store.coverage_debt_snapshots.update(snapshot.coverage_debt_snapshots)
        self._store.research_orchestrations.clear()
        self._store.research_orchestrations.update(snapshot.research_orchestrations)
        self._store.research_cycles.clear()
        self._store.research_cycles.update(snapshot.research_cycles)
        self._store.budget_consumptions.clear()
        self._store.budget_consumptions.update(snapshot.budget_consumptions)
        self._store.audit_events.clear()
        self._store.audit_events.update(snapshot.audit_events)
        self._store.session_contexts.clear()
        self._store.session_contexts.update(snapshot.session_contexts)
        self._store.discovery_run_configs.clear()
        self._store.discovery_run_configs.update(snapshot.discovery_run_configs)
        self._store.control_events.clear()
        self._store.control_events.update(snapshot.control_events)
        self._store.discovery_facts.clear()
        self._store.discovery_facts.update(snapshot.discovery_facts)
        self._store.discovery_fact_sources.clear()
        self._store.discovery_fact_sources.update(snapshot.discovery_fact_sources)
        self._store.discovery_inferences.clear()
        self._store.discovery_inferences.update(snapshot.discovery_inferences)
        self._store.discovery_inference_sources.clear()
        self._store.discovery_inference_sources.update(snapshot.discovery_inference_sources)
        self._store.frontier_items.clear()
        self._store.frontier_items.update(snapshot.frontier_items)
        self._store.frontier_sources.clear()
        self._store.frontier_sources.update(snapshot.frontier_sources)
        self._store.frontier_events.clear()
        self._store.frontier_events.update(snapshot.frontier_events)
        self._store.discovery_projection_receipts.clear()
        self._store.discovery_projection_receipts.update(snapshot.discovery_projection_receipts)
        self._store.hunter_families.clear()
        self._store.hunter_families.update(snapshot.hunter_families)
        self._store.hunt_v3_queue.clear()
        self._store.hunt_v3_queue.update(snapshot.hunt_v3_queue)
        self._store.runtime_instances.clear()
        self._store.runtime_instances.update(snapshot.runtime_instances)
        self._store.preflight_reports.clear()
        self._store.preflight_reports.update(snapshot.preflight_reports)


class FakeUnitOfWorkFactory:
    def __init__(self, store: _Store | None = None, fail_on: str | None = None) -> None:
        self.store = store or _Store()
        self.fail_on = fail_on

    def open(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self.store, fail_on=self.fail_on)
