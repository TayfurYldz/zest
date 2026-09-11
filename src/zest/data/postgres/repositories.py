"""SQLAlchemy Core repositories. Not imported by Core, Research, or Workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from zest.data.errors import (
    BudgetOverspendError,
    LeaseFencingError,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
    TerminalOrchestrationStateError,
)
from zest.data.postgres.engine import raise_if_unavailable
from zest.data.postgres import mapping as map_row
from zest.data.postgres import tables
from zest.data.records import (
    ALLOWED_CANDIDATE_STATES,
    ALLOWED_EXECUTION_ATTEMPT_STATES,
    ALLOWED_EXPERIMENT_STATES,
    ALLOWED_FINDING_PROPOSAL_STATES,
    ALLOWED_INVARIANT_STATUSES,
    ALLOWED_PROMOTION_STAGES,
    ALLOWED_SESSION_STATES,
    ALLOWED_TARGET_CONTACT_STATUSES,
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
    HunterFamilyRecord,
    HuntV3QueueRecord,
    HypothesisRecord,
    HumanReviewRecord,
    ImpactChainEdgeRecord,
    ImpactChainNodeRecord,
    ImpactChainRecord,
    InvariantCounterexampleRefRecord,
    InvariantHypothesisRecord,
    IssuedBudgetRecord,
    LeaseAcquireOutcome,
    LeaseAcquireResult,
    ObservationRecord,
    OastAdmissionRecord,
    OastCallbackDeliveryRecord,
    OastCorrelationRecord,
    OastTokenRecord,
    PreflightReportRecord,
    ProgramPolicyRecord,
    ProgramRecord,
    PromotionRunRecord,
    RateLimitProfileRecord,
    ResearchAdmissionRecord,
    ResearchCycleRecord,
    ResearchOrchestrationRecord,
    ResearchReasoningRecord,
    ResearchRunRecord,
    RuntimeInstanceRecord,
    RunFaultRecord,
    ScopeRuleV2Record,
    SensorObservationRecord,
    TargetInferenceRecord,
    VerificationRecord,
    WorkerResultRecord,
    require_opaque_id,
    OpportunitySelectionCandidateRecord,
    ResearchOpportunityRecord,
    ResearchSelectionRecord,
    SnapshotRecord,
    SnapshotMemberRecord,
    ChangeEventRecord,
    SessionContextRecord,
    TERMINAL_ORCHESTRATION_STATES,
)
from zest.data.budget_ledger import assert_within_allowance


T = TypeVar("T")


def _constraint_name(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if isinstance(name, str) and name:
        return name
    return None


def _raise_integrity(exc: IntegrityError) -> None:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    if sqlstate == "23505":
        raise PersistenceConflictError(
            "persistence unique constraint failed",
            constraint_name=_constraint_name(exc),
        ) from exc
    raise PersistenceError("persistence integrity constraint failed") from exc


def _execute_write(connection: Connection, statement) -> None:
    """Execute one write. Unique conflicts do not abort the outer transaction.

    Callers catch PersistenceConflictError and continue in the same Unit of Work.
    A PostgreSQL unique violation otherwise poisons the transaction, so each
    write runs inside a SAVEPOINT.
    """

    try:
        with connection.begin_nested():
            connection.execute(statement)
    except IntegrityError as exc:
        _raise_integrity(exc)
    except SQLAlchemyError as exc:
        raise_if_unavailable(exc)
        raise PersistenceError("persistence write failed") from exc


def _server_now(connection: Connection) -> datetime:
    """Current time per PostgreSQL, not the application host's clock.

    Lease-expiration decisions must not depend on app-server clock skew
    across runtime instances; every comparison and every computed expiry in
    this module is anchored to this single round trip.
    """

    try:
        return connection.execute(select(func.now())).scalar_one()
    except SQLAlchemyError as exc:
        raise_if_unavailable(exc)
        raise PersistenceError("persistence read failed") from exc


def _validate_read_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise PersistenceInputError("limit must be a positive integer")
    return limit


def _apply_read_limit(statement, limit: int | None):
    validated = _validate_read_limit(limit)
    return statement if validated is None else statement.limit(validated)


def _fetch_one(
    connection: Connection,
    table,
    id_column,
    record_id: str,
    builder: Callable[[Any], T],
) -> T | None:
    try:
        row = connection.execute(
            select(table).where(id_column == record_id)
        ).mappings().one_or_none()
    except SQLAlchemyError as exc:
        raise_if_unavailable(exc)
        raise PersistenceError("persistence read failed") from exc
    if row is None:
        return None
    return builder(row)


class PostgresProgramRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ProgramRecord) -> None:
        _execute_write(
            self._connection,
            tables.program.insert().values(
                program_id=record.program_id,
                name=record.name,
                handle=record.handle,
                platform=record.platform,
                created_at=record.created_at,
            ),
        )

    def get(self, program_id: str) -> ProgramRecord | None:
        require_opaque_id(program_id, "program_id")
        return _fetch_one(
            self._connection,
            tables.program,
            tables.program.c.program_id,
            program_id,
            map_row.program_from_row,
        )

    def list_recent(self, *, limit: int = 50) -> list[ProgramRecord]:
        if not isinstance(limit, int) or limit <= 0:
            raise PersistenceInputError("limit must be a positive integer")
        try:
            rows = self._connection.execute(
                select(tables.program)
                .order_by(tables.program.c.created_at.desc())
                .limit(limit)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.program_from_row(row) for row in rows]


class PostgresScopeRuleV2Repository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ScopeRuleV2Record) -> None:
        _execute_write(
            self._connection,
            tables.scope_rule_v2.insert().values(
                rule_id=record.rule_id,
                program_id=record.program_id,
                effect=record.effect,
                scheme=record.scheme,
                host=record.host,
                host_pattern=record.host_pattern,
                port=record.port,
                path_prefix=record.path_prefix,
                source_reference=record.source_reference,
                expires_at=record.expires_at,
                created_at=record.created_at,
            ),
        )

    def get(self, rule_id: str) -> ScopeRuleV2Record | None:
        require_opaque_id(rule_id, "rule_id")
        return _fetch_one(
            self._connection,
            tables.scope_rule_v2,
            tables.scope_rule_v2.c.rule_id,
            rule_id,
            map_row.scope_rule_v2_from_row,
        )

    def list_for_program(self, program_id: str) -> list[ScopeRuleV2Record]:
        require_opaque_id(program_id, "program_id")
        try:
            rows = self._connection.execute(
                select(tables.scope_rule_v2)
                .where(tables.scope_rule_v2.c.program_id == program_id)
                .order_by(tables.scope_rule_v2.c.rule_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.scope_rule_v2_from_row(row) for row in rows]


class PostgresProgramPolicyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ProgramPolicyRecord) -> None:
        _execute_write(
            self._connection,
            tables.program_policy.insert().values(
                program_id=record.program_id,
                loopback_fixture=record.loopback_fixture,
                max_response_bytes=record.max_response_bytes,
                timeout_ms=record.timeout_ms,
                action_policy=dict(record.action_policy) if record.action_policy is not None else {},
                daily_llm_budget_microdollars=record.daily_llm_budget_microdollars,
                created_at=record.created_at,
                updated_at=record.updated_at,
            ),
        )

    def get(self, program_id: str) -> ProgramPolicyRecord | None:
        require_opaque_id(program_id, "program_id")
        return _fetch_one(
            self._connection,
            tables.program_policy,
            tables.program_policy.c.program_id,
            program_id,
            map_row.program_policy_from_row,
        )


class PostgresSensorObservationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: SensorObservationRecord) -> None:
        _execute_write(
            self._connection,
            tables.sensor_observation.insert().values(
                observation_id=record.observation_id,
                research_run_id=record.research_run_id,
                sensor_id=record.sensor_id,
                target_reference=record.target_reference,
                collected_at=record.collected_at,
                payload_digest=record.payload_digest,
                epistemic_status=record.epistemic_status,
                source_metadata=dict(record.source_metadata),
                payload=dict(record.payload),
                created_at=record.created_at,
            ),
        )

    def get(self, observation_id: str) -> SensorObservationRecord | None:
        require_opaque_id(observation_id, "observation_id")
        return _fetch_one(
            self._connection,
            tables.sensor_observation,
            tables.sensor_observation.c.observation_id,
            observation_id,
            map_row.sensor_observation_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[SensorObservationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.sensor_observation)
                    .where(tables.sensor_observation.c.research_run_id == research_run_id)
                    .order_by(tables.sensor_observation.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.sensor_observation_from_row(row) for row in rows]

    def list_for_sensor(
        self, research_run_id: str, sensor_id: str
    ) -> list[SensorObservationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        require_opaque_id(sensor_id, "sensor_id")
        try:
            rows = self._connection.execute(
                select(tables.sensor_observation)
                .where(tables.sensor_observation.c.research_run_id == research_run_id)
                .where(tables.sensor_observation.c.sensor_id == sensor_id)
                .order_by(tables.sensor_observation.c.created_at)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.sensor_observation_from_row(row) for row in rows]


class PostgresRateLimitProfileRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: RateLimitProfileRecord) -> None:
        _execute_write(
            self._connection,
            tables.rate_limit_profile.insert().values(
                profile_id=record.profile_id,
                program_id=record.program_id,
                max_requests_per_window=record.max_requests_per_window,
                window_seconds=record.window_seconds,
                created_at=record.created_at,
            ),
        )

    def get(self, profile_id: str) -> RateLimitProfileRecord | None:
        require_opaque_id(profile_id, "profile_id")
        return _fetch_one(
            self._connection,
            tables.rate_limit_profile,
            tables.rate_limit_profile.c.profile_id,
            profile_id,
            map_row.rate_limit_profile_from_row,
        )

    def list_for_program(self, program_id: str) -> list[RateLimitProfileRecord]:
        require_opaque_id(program_id, "program_id")
        try:
            rows = self._connection.execute(
                select(tables.rate_limit_profile)
                .where(tables.rate_limit_profile.c.program_id == program_id)
                .order_by(tables.rate_limit_profile.c.profile_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.rate_limit_profile_from_row(row) for row in rows]


class PostgresOastTokenRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: OastTokenRecord) -> None:
        _execute_write(
            self._connection,
            tables.oast_token.insert().values(
                token_id=record.token_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                target_reference=record.target_reference,
                expires_at=record.expires_at,
                created_at=record.created_at,
            ),
        )

    def get(self, token_id: str) -> OastTokenRecord | None:
        require_opaque_id(token_id, "token_id")
        return _fetch_one(
            self._connection,
            tables.oast_token,
            tables.oast_token.c.token_id,
            token_id,
            map_row.oast_token_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[OastTokenRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.oast_token)
                    .where(tables.oast_token.c.research_run_id == research_run_id)
                    .order_by(tables.oast_token.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.oast_token_from_row(row) for row in rows]


class PostgresOastCorrelationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: OastCorrelationRecord) -> None:
        _execute_write(
            self._connection,
            tables.oast_correlation.insert().values(
                correlation_id=record.correlation_id,
                attempt_id=record.attempt_id,
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                target_reference=record.target_reference,
                identity_id=record.identity_id,
                armed_at=record.armed_at,
                expires_at=record.expires_at,
                created_at=record.created_at,
            ),
        )

    def get(self, correlation_id: str) -> OastCorrelationRecord | None:
        require_opaque_id(correlation_id, "correlation_id")
        return _fetch_one(
            self._connection,
            tables.oast_correlation,
            tables.oast_correlation.c.correlation_id,
            correlation_id,
            map_row.oast_correlation_from_row,
        )

    def get_by_attempt_id(self, attempt_id: str) -> OastCorrelationRecord | None:
        require_opaque_id(attempt_id, "attempt_id")
        return _fetch_one(
            self._connection,
            tables.oast_correlation,
            tables.oast_correlation.c.attempt_id,
            attempt_id,
            map_row.oast_correlation_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[OastCorrelationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.oast_correlation)
                    .where(tables.oast_correlation.c.research_run_id == research_run_id)
                    .order_by(tables.oast_correlation.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.oast_correlation_from_row(row) for row in rows]


class PostgresOastCallbackDeliveryRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: OastCallbackDeliveryRecord) -> None:
        _execute_write(
            self._connection,
            tables.oast_callback_delivery.insert().values(
                delivery_id=record.delivery_id,
                correlation_id=record.correlation_id,
                provider_adapter_id=record.provider_adapter_id,
                provider_event_id=record.provider_event_id,
                received_at=record.received_at,
                normalized_payload=dict(record.normalized_payload),
                normalized_digest=record.normalized_digest,
            ),
        )

    def get(self, delivery_id: str) -> OastCallbackDeliveryRecord | None:
        require_opaque_id(delivery_id, "delivery_id")
        return _fetch_one(
            self._connection,
            tables.oast_callback_delivery,
            tables.oast_callback_delivery.c.delivery_id,
            delivery_id,
            map_row.oast_callback_delivery_from_row,
        )

    def get_by_provider_event(
        self, provider_adapter_id: str, provider_event_id: str
    ) -> OastCallbackDeliveryRecord | None:
        require_opaque_id(provider_adapter_id, "provider_adapter_id")
        require_opaque_id(provider_event_id, "provider_event_id")
        try:
            row = self._connection.execute(
                select(tables.oast_callback_delivery)
                .where(
                    tables.oast_callback_delivery.c.provider_adapter_id
                    == provider_adapter_id
                )
                .where(
                    tables.oast_callback_delivery.c.provider_event_id
                    == provider_event_id
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return (
            None
            if row is None
            else map_row.oast_callback_delivery_from_row(row)
        )

    def get_by_correlation_digest(
        self, correlation_id: str, normalized_digest: str
    ) -> OastCallbackDeliveryRecord | None:
        require_opaque_id(correlation_id, "correlation_id")
        require_opaque_id(normalized_digest, "normalized_digest")
        try:
            row = self._connection.execute(
                select(tables.oast_callback_delivery)
                .where(tables.oast_callback_delivery.c.correlation_id == correlation_id)
                .where(
                    tables.oast_callback_delivery.c.normalized_digest
                    == normalized_digest
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return (
            None
            if row is None
            else map_row.oast_callback_delivery_from_row(row)
        )

    def list_for_correlation(
        self, correlation_id: str, *, limit: int | None = None
    ) -> list[OastCallbackDeliveryRecord]:
        require_opaque_id(correlation_id, "correlation_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.oast_callback_delivery)
                    .where(tables.oast_callback_delivery.c.correlation_id == correlation_id)
                    .order_by(tables.oast_callback_delivery.c.received_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.oast_callback_delivery_from_row(row) for row in rows]


class PostgresOastAdmissionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: OastAdmissionRecord) -> None:
        _execute_write(
            self._connection,
            tables.oast_admission.insert().values(
                admission_id=record.admission_id,
                correlation_id=record.correlation_id,
                research_run_id=record.research_run_id,
                sensor_observation_id=record.sensor_observation_id,
                discovery_fact_id=record.discovery_fact_id,
                created_at=record.created_at,
            ),
        )

    def get(self, admission_id: str) -> OastAdmissionRecord | None:
        require_opaque_id(admission_id, "admission_id")
        return _fetch_one(
            self._connection,
            tables.oast_admission,
            tables.oast_admission.c.admission_id,
            admission_id,
            map_row.oast_admission_from_row,
        )

    def get_by_correlation(self, correlation_id: str) -> OastAdmissionRecord | None:
        require_opaque_id(correlation_id, "correlation_id")
        return _fetch_one(
            self._connection,
            tables.oast_admission,
            tables.oast_admission.c.correlation_id,
            correlation_id,
            map_row.oast_admission_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[OastAdmissionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.oast_admission)
                    .where(tables.oast_admission.c.research_run_id == research_run_id)
                    .order_by(tables.oast_admission.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.oast_admission_from_row(row) for row in rows]


class PostgresBountyTableRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: BountyTableRecord) -> None:
        _execute_write(
            self._connection,
            tables.bounty_table.insert().values(
                program_id=record.program_id,
                severity=record.severity,
                reward_range=dict(record.reward_range) if record.reward_range is not None else None,
                created_at=record.created_at,
            ),
        )

    def get(self, program_id: str, severity: str) -> BountyTableRecord | None:
        require_opaque_id(program_id, "program_id")
        if not isinstance(severity, str) or not severity.strip():
            raise PersistenceInputError("severity must be a non-empty string")
        try:
            row = self._connection.execute(
                select(tables.bounty_table)
                .where(tables.bounty_table.c.program_id == program_id)
                .where(tables.bounty_table.c.severity == severity)
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.bounty_table_from_row(row)

    def list_for_program(self, program_id: str) -> list[BountyTableRecord]:
        require_opaque_id(program_id, "program_id")
        try:
            rows = self._connection.execute(
                select(tables.bounty_table)
                .where(tables.bounty_table.c.program_id == program_id)
                .order_by(tables.bounty_table.c.severity)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.bounty_table_from_row(row) for row in rows]


class PostgresAuthorizationSourceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: AuthorizationSourceRecord) -> None:
        _execute_write(
            self._connection,
            tables.authorization_source.insert().values(
                authorization_source_id=record.authorization_source_id,
                program_id=record.program_id,
                state=record.state,
                provenance_reference=record.provenance_reference,
                effective_from=record.effective_from,
                effective_until=record.effective_until,
                created_at=record.created_at,
            ),
        )

    def get(self, authorization_source_id: str) -> AuthorizationSourceRecord | None:
        require_opaque_id(authorization_source_id, "authorization_source_id")
        return _fetch_one(
            self._connection,
            tables.authorization_source,
            tables.authorization_source.c.authorization_source_id,
            authorization_source_id,
            map_row.authorization_source_from_row,
        )


class PostgresResearchRunRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchRunRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_run.insert().values(
                research_run_id=record.research_run_id,
                program_id=record.program_id,
                authorization_source_id=record.authorization_source_id,
                initiated_by_actor_id=record.initiated_by_actor_id,
                initiated_by_actor_type=record.initiated_by_actor_type,
                started_at=record.started_at,
            ),
        )

    def get(self, research_run_id: str) -> ResearchRunRecord | None:
        require_opaque_id(research_run_id, "research_run_id")
        return _fetch_one(
            self._connection,
            tables.research_run,
            tables.research_run.c.research_run_id,
            research_run_id,
            map_row.research_run_from_row,
        )

    def list_recent(self, *, limit: int = 50) -> list[ResearchRunRecord]:
        if not isinstance(limit, int) or limit <= 0:
            raise PersistenceInputError("limit must be a positive integer")
        try:
            rows = self._connection.execute(
                select(tables.research_run)
                .order_by(tables.research_run.c.started_at.desc())
                .limit(limit)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_run_from_row(row) for row in rows]


class PostgresIssuedBudgetRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: IssuedBudgetRecord) -> None:
        _execute_write(
            self._connection,
            tables.issued_budget.insert().values(
                budget_id=record.budget_id,
                research_run_id=record.research_run_id,
                max_requests=record.max_requests,
                max_tool_calls=record.max_tool_calls,
                max_runtime_ms=record.max_runtime_ms,
                max_concurrency=record.max_concurrency,
                issued_at=record.issued_at,
            ),
        )

    def get(self, budget_id: str) -> IssuedBudgetRecord | None:
        require_opaque_id(budget_id, "budget_id")
        return _fetch_one(
            self._connection,
            tables.issued_budget,
            tables.issued_budget.c.budget_id,
            budget_id,
            map_row.issued_budget_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[IssuedBudgetRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.issued_budget)
                    .where(tables.issued_budget.c.research_run_id == research_run_id)
                    .order_by(tables.issued_budget.c.budget_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.issued_budget_from_row(row) for row in rows]


class PostgresHypothesisRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: HypothesisRecord) -> None:
        _execute_write(
            self._connection,
            tables.hypothesis.insert().values(
                hypothesis_id=record.hypothesis_id,
                research_run_id=record.research_run_id,
                claim=record.claim,
                origin_reference=record.origin_reference,
                identity_id=record.identity_id,
                created_at=record.created_at,
            ),
        )

    def get(self, hypothesis_id: str) -> HypothesisRecord | None:
        require_opaque_id(hypothesis_id, "hypothesis_id")
        return _fetch_one(
            self._connection,
            tables.hypothesis,
            tables.hypothesis.c.hypothesis_id,
            hypothesis_id,
            map_row.hypothesis_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[HypothesisRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.hypothesis)
                    .where(tables.hypothesis.c.research_run_id == research_run_id)
                    .order_by(tables.hypothesis.c.hypothesis_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hypothesis_from_row(row) for row in rows]


class PostgresExperimentRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ExperimentRecord) -> None:
        _execute_write(
            self._connection,
            tables.experiment.insert().values(
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                budget_id=record.budget_id,
                execution_state=record.execution_state,
                created_at=record.created_at,
            ),
        )

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        require_opaque_id(experiment_id, "experiment_id")
        return _fetch_one(
            self._connection,
            tables.experiment,
            tables.experiment.c.experiment_id,
            experiment_id,
            map_row.experiment_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ExperimentRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.experiment)
                    .where(tables.experiment.c.research_run_id == research_run_id)
                    .order_by(tables.experiment.c.experiment_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.experiment_from_row(row) for row in rows]

    def set_execution_state(self, experiment_id: str, execution_state: str) -> None:
        require_opaque_id(experiment_id, "experiment_id")
        if execution_state not in ALLOWED_EXPERIMENT_STATES:
            raise PersistenceInputError("execution_state is not a domain execution state")
        result = self._connection.execute(
            update(tables.experiment)
            .where(tables.experiment.c.experiment_id == experiment_id)
            .values(execution_state=execution_state)
        )
        if result.rowcount != 1:
            raise PersistenceError("experiment not found for execution_state update")


class PostgresExecutionAttemptRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ExecutionAttemptRecord) -> None:
        _execute_write(
            self._connection,
            tables.execution_attempt.insert().values(
                attempt_id=record.attempt_id,
                request_id=record.request_id,
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                correlation_id=record.correlation_id,
                worker_capability=record.worker_capability,
                action=record.action,
                target_reference=record.target_reference,
                budget_id=record.budget_id,
                side_effect_level=record.side_effect_level,
                authorization_decision_reference=record.authorization_decision_reference,
                state=record.state,
                created_at=record.created_at,
                authorized_at=record.authorized_at,
                dispatch_started_at=record.dispatch_started_at,
                completed_at=record.completed_at,
                target_contact_status=record.target_contact_status,
            ),
        )

    def get(self, attempt_id: str) -> ExecutionAttemptRecord | None:
        require_opaque_id(attempt_id, "attempt_id")
        return _fetch_one(
            self._connection,
            tables.execution_attempt,
            tables.execution_attempt.c.attempt_id,
            attempt_id,
            map_row.execution_attempt_from_row,
        )

    def get_by_request_id(self, request_id: str) -> ExecutionAttemptRecord | None:
        require_opaque_id(request_id, "request_id")
        return _fetch_one(
            self._connection,
            tables.execution_attempt,
            tables.execution_attempt.c.request_id,
            request_id,
            map_row.execution_attempt_from_row,
        )

    def list_for_experiment(self, experiment_id: str) -> list[ExecutionAttemptRecord]:
        require_opaque_id(experiment_id, "experiment_id")
        try:
            rows = self._connection.execute(
                select(tables.execution_attempt).where(
                    tables.execution_attempt.c.experiment_id == experiment_id
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.execution_attempt_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ExecutionAttemptRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.execution_attempt)
                    .where(tables.execution_attempt.c.research_run_id == research_run_id)
                    .order_by(tables.execution_attempt.c.attempt_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.execution_attempt_from_row(row) for row in rows]

    def set_state(
        self,
        attempt_id: str,
        state: str,
        *,
        dispatch_started_at: datetime | None = None,
        completed_at: datetime | None = None,
        target_contact_status: str | None = None,
    ) -> None:
        require_opaque_id(attempt_id, "attempt_id")
        if state not in ALLOWED_EXECUTION_ATTEMPT_STATES:
            raise PersistenceInputError("state is not an ExecutionAttempt state")
        if (
            target_contact_status is not None
            and target_contact_status not in ALLOWED_TARGET_CONTACT_STATUSES
        ):
            raise PersistenceInputError("target_contact_status is not valid")
        values: dict[str, object] = {"state": state}
        if dispatch_started_at is not None:
            values["dispatch_started_at"] = dispatch_started_at
        if completed_at is not None:
            values["completed_at"] = completed_at
        if target_contact_status is not None:
            values["target_contact_status"] = target_contact_status
        try:
            result = self._connection.execute(
                update(tables.execution_attempt)
                .where(tables.execution_attempt.c.attempt_id == attempt_id)
                .values(**values)
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        if result.rowcount != 1:
            raise PersistenceError("execution_attempt not found for state update")


class PostgresWorkerResultRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: WorkerResultRecord) -> None:
        _execute_write(
            self._connection,
            tables.worker_result.insert().values(
                worker_result_id=record.worker_result_id,
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                request_id=record.request_id,
                correlation_id=record.correlation_id,
                parent_request_id=record.parent_request_id,
                worker_capability=record.worker_capability,
                action=record.action,
                authorization_decision_reference=record.authorization_decision_reference,
                budget_id=record.budget_id,
                side_effect_level=record.side_effect_level,
                contract_version=record.contract_version,
                worker_id=record.worker_id,
                status=record.status,
                started_at=record.started_at,
                completed_at=record.completed_at,
                received_at=record.received_at,
                raw_result=record.raw_result,
                raw_artifact_descriptors=record.raw_artifact_descriptors,
                diagnostics=record.diagnostics,
                control_signal=record.control_signal,
            ),
        )

    def get(self, worker_result_id: str) -> WorkerResultRecord | None:
        require_opaque_id(worker_result_id, "worker_result_id")
        return _fetch_one(
            self._connection,
            tables.worker_result,
            tables.worker_result.c.worker_result_id,
            worker_result_id,
            map_row.worker_result_from_row,
        )

    def get_by_request_id(self, request_id: str) -> WorkerResultRecord | None:
        require_opaque_id(request_id, "request_id")
        return _fetch_one(
            self._connection,
            tables.worker_result,
            tables.worker_result.c.request_id,
            request_id,
            map_row.worker_result_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[WorkerResultRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.worker_result)
                    .where(tables.worker_result.c.research_run_id == research_run_id)
                    .order_by(tables.worker_result.c.worker_result_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.worker_result_from_row(row) for row in rows]

    def list_for_experiment(self, experiment_id: str) -> list[WorkerResultRecord]:
        require_opaque_id(experiment_id, "experiment_id")
        try:
            rows = self._connection.execute(
                select(tables.worker_result)
                .where(tables.worker_result.c.experiment_id == experiment_id)
                .order_by(tables.worker_result.c.worker_result_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.worker_result_from_row(row) for row in rows]


class PostgresObservationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ObservationRecord) -> None:
        _execute_write(
            self._connection,
            tables.observation.insert().values(
                observation_id=record.observation_id,
                worker_result_id=record.worker_result_id,
                observation_kind=record.observation_kind,
                payload=dict(record.payload),
                normalization_version=record.normalization_version,
                observed_at=record.observed_at,
                created_at=record.created_at,
            ),
        )

    def get(self, observation_id: str) -> ObservationRecord | None:
        require_opaque_id(observation_id, "observation_id")
        return _fetch_one(
            self._connection,
            tables.observation,
            tables.observation.c.observation_id,
            observation_id,
            map_row.observation_from_row,
        )

    def list_for_worker_result(self, worker_result_id: str) -> list[ObservationRecord]:
        require_opaque_id(worker_result_id, "worker_result_id")
        try:
            rows = self._connection.execute(
                select(tables.observation).where(
                    tables.observation.c.worker_result_id == worker_result_id
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.observation_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ObservationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.observation)
                    .join(
                        tables.worker_result,
                        tables.observation.c.worker_result_id
                        == tables.worker_result.c.worker_result_id,
                    )
                    .where(tables.worker_result.c.research_run_id == research_run_id)
                    .order_by(tables.observation.c.observation_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.observation_from_row(row) for row in rows]

    def list_for_experiment(self, experiment_id: str) -> list[ObservationRecord]:
        require_opaque_id(experiment_id, "experiment_id")
        try:
            rows = self._connection.execute(
                select(tables.observation)
                .join(
                    tables.worker_result,
                    tables.observation.c.worker_result_id
                    == tables.worker_result.c.worker_result_id,
                )
                .where(tables.worker_result.c.experiment_id == experiment_id)
                .order_by(tables.observation.c.observation_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.observation_from_row(row) for row in rows]


class PostgresResearchReasoningRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchReasoningRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_reasoning.insert().values(
                reasoning_record_id=record.reasoning_record_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                role=record.role,
                adapter_identity=record.adapter_identity,
                provider_adapter_identity=record.provider_adapter_identity,
                correlation_id=record.correlation_id,
                context_fingerprint=record.context_fingerprint,
                structured_output=dict(record.structured_output),
                created_at=record.created_at,
                model_id=record.model_id,
                model_version=record.model_version,
            ),
        )

    def get(self, reasoning_record_id: str) -> ResearchReasoningRecord | None:
        require_opaque_id(reasoning_record_id, "reasoning_record_id")
        return _fetch_one(
            self._connection,
            tables.research_reasoning,
            tables.research_reasoning.c.reasoning_record_id,
            reasoning_record_id,
            map_row.research_reasoning_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[ResearchReasoningRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.research_reasoning)
                    .where(tables.research_reasoning.c.research_run_id == research_run_id)
                    .order_by(tables.research_reasoning.c.reasoning_record_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_reasoning_from_row(row) for row in rows]

    def list_for_hypothesis(self, hypothesis_id: str) -> list[ResearchReasoningRecord]:
        require_opaque_id(hypothesis_id, "hypothesis_id")
        try:
            rows = self._connection.execute(
                select(tables.research_reasoning)
                .where(tables.research_reasoning.c.hypothesis_id == hypothesis_id)
                .order_by(tables.research_reasoning.c.reasoning_record_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_reasoning_from_row(row) for row in rows]


class PostgresResearchAdmissionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchAdmissionRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_admission.insert().values(
                admission_record_id=record.admission_record_id,
                research_run_id=record.research_run_id,
                generator_reasoning_record_id=record.generator_reasoning_record_id,
                falsifier_reasoning_record_id=record.falsifier_reasoning_record_id,
                outcome=record.outcome,
                admitted_hypothesis_id=record.admitted_hypothesis_id,
                reason=record.reason,
                reason_code=record.reason_code,
                context_fingerprint=record.context_fingerprint,
                created_at=record.created_at,
            ),
        )

    def get(self, admission_record_id: str) -> ResearchAdmissionRecord | None:
        require_opaque_id(admission_record_id, "admission_record_id")
        return _fetch_one(
            self._connection,
            tables.research_admission,
            tables.research_admission.c.admission_record_id,
            admission_record_id,
            map_row.research_admission_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[ResearchAdmissionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.research_admission)
                    .where(tables.research_admission.c.research_run_id == research_run_id)
                    .order_by(tables.research_admission.c.admission_record_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_admission_from_row(row) for row in rows]


class PostgresExperimentPlanRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ExperimentPlanRecord) -> None:
        _execute_write(
            self._connection,
            tables.experiment_plan.insert().values(
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                required_capability=record.required_capability,
                action=record.action,
                target_reference=record.target_reference,
                side_effect_level=record.side_effect_level,
                arguments=dict(record.arguments),
                requested_budget_id=record.requested_budget_id,
                expected_observation=record.expected_observation,
                disconfirming_observation=record.disconfirming_observation,
                evaluation_strategy=record.evaluation_strategy,
                capability_version=record.capability_version,
                capability_definition_fingerprint=record.capability_definition_fingerprint,
                created_at=record.created_at,
            ),
        )

    def get(self, experiment_id: str) -> ExperimentPlanRecord | None:
        require_opaque_id(experiment_id, "experiment_id")
        return _fetch_one(
            self._connection,
            tables.experiment_plan,
            tables.experiment_plan.c.experiment_id,
            experiment_id,
            map_row.experiment_plan_from_row,
        )


class PostgresHypothesisAssessmentRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: HypothesisAssessmentRecord) -> None:
        _execute_write(
            self._connection,
            tables.hypothesis_assessment.insert().values(
                assessment_id=record.assessment_id,
                hypothesis_id=record.hypothesis_id,
                experiment_id=record.experiment_id,
                research_run_id=record.research_run_id,
                assessment_outcome=record.assessment_outcome,
                observation_ids=list(record.observation_ids),
                evaluator_kind=record.evaluator_kind,
                evaluator_version=record.evaluator_version,
                rationale=dict(record.rationale),
                evaluation_strategy=record.evaluation_strategy,
                created_at=record.created_at,
            ),
        )

    def get(self, assessment_id: str) -> HypothesisAssessmentRecord | None:
        require_opaque_id(assessment_id, "assessment_id")
        return _fetch_one(
            self._connection,
            tables.hypothesis_assessment,
            tables.hypothesis_assessment.c.assessment_id,
            assessment_id,
            map_row.hypothesis_assessment_from_row,
        )

    def list_for_experiment(
        self, experiment_id: str
    ) -> list[HypothesisAssessmentRecord]:
        require_opaque_id(experiment_id, "experiment_id")
        try:
            rows = self._connection.execute(
                select(tables.hypothesis_assessment)
                .where(tables.hypothesis_assessment.c.experiment_id == experiment_id)
                .order_by(tables.hypothesis_assessment.c.assessment_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hypothesis_assessment_from_row(row) for row in rows]

    def list_for_hypothesis(
        self, hypothesis_id: str
    ) -> list[HypothesisAssessmentRecord]:
        require_opaque_id(hypothesis_id, "hypothesis_id")
        try:
            rows = self._connection.execute(
                select(tables.hypothesis_assessment)
                .where(tables.hypothesis_assessment.c.hypothesis_id == hypothesis_id)
                .order_by(tables.hypothesis_assessment.c.assessment_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hypothesis_assessment_from_row(row) for row in rows]

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[HypothesisAssessmentRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.hypothesis_assessment)
                    .where(tables.hypothesis_assessment.c.research_run_id == research_run_id)
                    .order_by(tables.hypothesis_assessment.c.assessment_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hypothesis_assessment_from_row(row) for row in rows]


class PostgresEvidenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: EvidenceRecord) -> None:
        _execute_write(
            self._connection,
            tables.evidence.insert().values(
                evidence_id=record.evidence_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                experiment_id=record.experiment_id,
                admission_record_id=record.admission_record_id,
                polarity=record.polarity,
                claim_scope=record.claim_scope,
                observation_ids=list(record.observation_ids),
                assessment_ids=list(record.assessment_ids),
                created_at=record.created_at,
            ),
        )
        for observation_id in record.observation_ids:
            _execute_write(
                self._connection,
                tables.evidence_observation.insert().values(
                    evidence_id=record.evidence_id,
                    observation_id=observation_id,
                ),
            )

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        require_opaque_id(evidence_id, "evidence_id")
        return _fetch_one(
            self._connection,
            tables.evidence,
            tables.evidence.c.evidence_id,
            evidence_id,
            map_row.evidence_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[EvidenceRecord]:
        return self._list(
            tables.evidence.c.research_run_id,
            research_run_id,
            "research_run_id",
            limit=limit,
        )

    def list_for_hypothesis(self, hypothesis_id: str) -> list[EvidenceRecord]:
        return self._list(tables.evidence.c.hypothesis_id, hypothesis_id, "hypothesis_id")

    def list_for_experiment(self, experiment_id: str) -> list[EvidenceRecord]:
        return self._list(tables.evidence.c.experiment_id, experiment_id, "experiment_id")

    def _list(
        self,
        column,
        value: str,
        field_name: str,
        *,
        limit: int | None = None,
    ) -> list[EvidenceRecord]:
        require_opaque_id(value, field_name)
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.evidence)
                    .where(column == value)
                    .order_by(tables.evidence.c.evidence_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.evidence_from_row(row) for row in rows]


class PostgresEvidenceAdmissionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: EvidenceAdmissionRecord) -> None:
        _execute_write(
            self._connection,
            tables.evidence_admission.insert().values(
                admission_record_id=record.admission_record_id,
                proposal_id=record.proposal_id,
                research_run_id=record.research_run_id,
                outcome=record.outcome,
                reason_codes=list(record.reason_codes),
                observation_ids=list(record.observation_ids),
                assessment_ids=list(record.assessment_ids),
                admission_policy_version=record.admission_policy_version,
                evaluator_version=record.evaluator_version,
                created_at=record.created_at,
                admitted_evidence_id=record.admitted_evidence_id,
                claim_scope=record.claim_scope,
                polarity=record.polarity,
            ),
        )

    def get(self, admission_record_id: str) -> EvidenceAdmissionRecord | None:
        require_opaque_id(admission_record_id, "admission_record_id")
        return _fetch_one(
            self._connection,
            tables.evidence_admission,
            tables.evidence_admission.c.admission_record_id,
            admission_record_id,
            map_row.evidence_admission_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[EvidenceAdmissionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.evidence_admission)
                    .where(tables.evidence_admission.c.research_run_id == research_run_id)
                    .order_by(tables.evidence_admission.c.admission_record_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.evidence_admission_from_row(row) for row in rows]


class PostgresCandidateRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: CandidateRecord) -> None:
        _execute_write(
            self._connection,
            tables.candidate.insert().values(
                candidate_id=record.candidate_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                claim=record.claim,
                classification=record.classification,
                state=record.state,
                evidence_ids=list(record.evidence_ids),
                admission_record_id=record.admission_record_id,
                created_at=record.created_at,
            ),
        )
        for evidence_id in record.evidence_ids:
            _execute_write(
                self._connection,
                tables.candidate_evidence.insert().values(
                    candidate_id=record.candidate_id,
                    evidence_id=evidence_id,
                ),
            )

    def get(self, candidate_id: str) -> CandidateRecord | None:
        require_opaque_id(candidate_id, "candidate_id")
        return _fetch_one(
            self._connection,
            tables.candidate,
            tables.candidate.c.candidate_id,
            candidate_id,
            map_row.candidate_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[CandidateRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.candidate)
                    .where(tables.candidate.c.research_run_id == research_run_id)
                    .order_by(tables.candidate.c.candidate_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.candidate_from_row(row) for row in rows]

    def set_state(self, candidate_id: str, state: str) -> None:
        require_opaque_id(candidate_id, "candidate_id")
        if state not in ALLOWED_CANDIDATE_STATES:
            raise PersistenceInputError("state is not a Candidate lifecycle state")
        result = self._connection.execute(
            update(tables.candidate)
            .where(tables.candidate.c.candidate_id == candidate_id)
            .values(state=state)
        )
        if result.rowcount != 1:
            raise PersistenceError("candidate not found for state update")


class PostgresCandidateAdmissionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: CandidateAdmissionRecord) -> None:
        _execute_write(
            self._connection,
            tables.candidate_admission.insert().values(
                admission_record_id=record.admission_record_id,
                proposal_id=record.proposal_id,
                research_run_id=record.research_run_id,
                outcome=record.outcome,
                reason_codes=list(record.reason_codes),
                evidence_ids=list(record.evidence_ids),
                admission_policy_version=record.admission_policy_version,
                created_at=record.created_at,
                admitted_candidate_id=record.admitted_candidate_id,
                claim=record.claim,
                classification=record.classification,
            ),
        )

    def get(self, admission_record_id: str) -> CandidateAdmissionRecord | None:
        require_opaque_id(admission_record_id, "admission_record_id")
        return _fetch_one(
            self._connection,
            tables.candidate_admission,
            tables.candidate_admission.c.admission_record_id,
            admission_record_id,
            map_row.candidate_admission_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[CandidateAdmissionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.candidate_admission)
                    .where(tables.candidate_admission.c.research_run_id == research_run_id)
                    .order_by(tables.candidate_admission.c.admission_record_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.candidate_admission_from_row(row) for row in rows]


class PostgresPromotionRunRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: PromotionRunRecord) -> None:
        _execute_write(
            self._connection,
            tables.promotion_run.insert().values(
                **_promotion_run_values(record)
            ),
        )

    def get(self, promotion_run_id: str) -> PromotionRunRecord | None:
        require_opaque_id(promotion_run_id, "promotion_run_id")
        return _fetch_one(
            self._connection,
            tables.promotion_run,
            tables.promotion_run.c.promotion_run_id,
            promotion_run_id,
            map_row.promotion_run_from_row,
        )

    def get_by_assessment_id(self, assessment_id: str) -> PromotionRunRecord | None:
        require_opaque_id(assessment_id, "assessment_id")
        return _fetch_one(
            self._connection,
            tables.promotion_run,
            tables.promotion_run.c.assessment_id,
            assessment_id,
            map_row.promotion_run_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[PromotionRunRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.promotion_run)
                    .where(tables.promotion_run.c.research_run_id == research_run_id)
                    .order_by(tables.promotion_run.c.created_at, tables.promotion_run.c.promotion_run_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.promotion_run_from_row(row) for row in rows]

    def save(self, record: PromotionRunRecord) -> None:
        if record.stage not in ALLOWED_PROMOTION_STAGES:
            raise PersistenceInputError("stage is not a PromotionPipeline stage")
        result = self._connection.execute(
            update(tables.promotion_run)
            .where(tables.promotion_run.c.promotion_run_id == record.promotion_run_id)
            .values(**_promotion_run_values(record))
        )
        if result.rowcount != 1:
            raise PersistenceError("promotion_run not found for save")

    def claim_reproduction(
        self, promotion_run_id: str, reproduction_experiment_id: str
    ) -> bool:
        require_opaque_id(promotion_run_id, "promotion_run_id")
        require_opaque_id(reproduction_experiment_id, "reproduction_experiment_id")
        now = _server_now(self._connection)
        try:
            result = self._connection.execute(
                update(tables.promotion_run)
                .where(tables.promotion_run.c.promotion_run_id == promotion_run_id)
                .where(tables.promotion_run.c.stage == "VERIFYING")
                .where(tables.promotion_run.c.reproduction_experiment_id.is_(None))
                .values(
                    reproduction_experiment_id=reproduction_experiment_id,
                    updated_at=now,
                )
            )
        except IntegrityError as exc:
            orig = getattr(exc, "orig", None)
            sqlstate = getattr(orig, "sqlstate", None)
            if sqlstate == "23505":
                return False
            _raise_integrity(exc)
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        return result.rowcount == 1


def _promotion_run_values(record: PromotionRunRecord) -> dict[str, object]:
    return {
        "promotion_run_id": record.promotion_run_id,
        "research_run_id": record.research_run_id,
        "assessment_id": record.assessment_id,
        "original_experiment_id": record.original_experiment_id,
        "stage": record.stage,
        "evidence_id": record.evidence_id,
        "candidate_id": record.candidate_id,
        "verification_id": record.verification_id,
        "finding_proposal_id": record.finding_proposal_id,
        "reproduction_experiment_id": record.reproduction_experiment_id,
        "stop_reason": record.stop_reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


class PostgresVerificationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: VerificationRecord) -> None:
        _execute_write(
            self._connection,
            tables.verification.insert().values(
                verification_id=record.verification_id,
                candidate_id=record.candidate_id,
                research_run_id=record.research_run_id,
                strategy=record.strategy,
                outcome=record.outcome,
                proposed_candidate_state=record.proposed_candidate_state,
                original_evidence_ids=list(record.original_evidence_ids),
                reproduction_evidence_ids=list(record.reproduction_evidence_ids),
                negative_control_evidence_ids=list(record.negative_control_evidence_ids),
                alternative_explanation_checks=dict(record.alternative_explanation_checks),
                verifier_kind=record.verifier_kind,
                verifier_identity=record.verifier_identity,
                created_at=record.created_at,
            ),
        )

    def get(self, verification_id: str) -> VerificationRecord | None:
        require_opaque_id(verification_id, "verification_id")
        return _fetch_one(
            self._connection,
            tables.verification,
            tables.verification.c.verification_id,
            verification_id,
            map_row.verification_from_row,
        )

    def list_for_candidate(self, candidate_id: str) -> list[VerificationRecord]:
        require_opaque_id(candidate_id, "candidate_id")
        try:
            rows = self._connection.execute(
                select(tables.verification)
                .where(tables.verification.c.candidate_id == candidate_id)
                .order_by(tables.verification.c.verification_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.verification_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[VerificationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.verification)
                    .where(tables.verification.c.research_run_id == research_run_id)
                    .order_by(tables.verification.c.verification_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.verification_from_row(row) for row in rows]


class PostgresFindingProposalRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: FindingProposalRecord) -> None:
        _execute_write(
            self._connection,
            tables.finding_proposal.insert().values(
                proposal_id=record.proposal_id,
                candidate_id=record.candidate_id,
                research_run_id=record.research_run_id,
                title=record.title,
                claim=record.claim,
                classification=record.classification,
                state=record.state,
                evidence_ids=list(record.evidence_ids),
                verification_ids=list(record.verification_ids),
                content_fingerprint=record.content_fingerprint,
                impact_chain_ids=list(record.impact_chain_ids),
                created_at=record.created_at,
            ),
        )

    def get(self, proposal_id: str) -> FindingProposalRecord | None:
        require_opaque_id(proposal_id, "proposal_id")
        return _fetch_one(
            self._connection,
            tables.finding_proposal,
            tables.finding_proposal.c.proposal_id,
            proposal_id,
            map_row.finding_proposal_from_row,
        )

    def list_for_candidate(self, candidate_id: str) -> list[FindingProposalRecord]:
        require_opaque_id(candidate_id, "candidate_id")
        try:
            rows = self._connection.execute(
                select(tables.finding_proposal)
                .where(tables.finding_proposal.c.candidate_id == candidate_id)
                .order_by(tables.finding_proposal.c.proposal_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.finding_proposal_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[FindingProposalRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.finding_proposal)
                    .where(tables.finding_proposal.c.research_run_id == research_run_id)
                    .order_by(tables.finding_proposal.c.proposal_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.finding_proposal_from_row(row) for row in rows]

    def set_state(self, proposal_id: str, state: str) -> None:
        require_opaque_id(proposal_id, "proposal_id")
        if state not in ALLOWED_FINDING_PROPOSAL_STATES:
            raise PersistenceInputError("state is not a FindingProposal lifecycle state")
        result = self._connection.execute(
            update(tables.finding_proposal)
            .where(tables.finding_proposal.c.proposal_id == proposal_id)
            .values(state=state)
        )
        if result.rowcount != 1:
            raise PersistenceError("finding_proposal not found for state update")


class PostgresHumanReviewRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: HumanReviewRecord) -> None:
        _execute_write(
            self._connection,
            tables.human_review.insert().values(
                review_id=record.review_id,
                proposal_id=record.proposal_id,
                content_fingerprint=record.content_fingerprint,
                decision=record.decision,
                reviewer_id=record.reviewer_id,
                actor_type=record.actor_type,
                reason_codes=list(record.reason_codes),
                created_at=record.created_at,
                note=record.note,
            ),
        )

    def get(self, review_id: str) -> HumanReviewRecord | None:
        require_opaque_id(review_id, "review_id")
        return _fetch_one(
            self._connection,
            tables.human_review,
            tables.human_review.c.review_id,
            review_id,
            map_row.human_review_from_row,
        )

    def get_for_proposal(self, proposal_id: str) -> HumanReviewRecord | None:
        require_opaque_id(proposal_id, "proposal_id")
        try:
            row = self._connection.execute(
                select(tables.human_review).where(
                    tables.human_review.c.proposal_id == proposal_id
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.human_review_from_row(row)


class PostgresApprovalRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ApprovalRecord) -> None:
        _execute_write(
            self._connection,
            tables.approval.insert().values(
                approval_id=record.approval_id,
                subject_reference=record.subject_reference,
                decision=record.decision,
                decided_by=record.decided_by,
                actor_type=record.actor_type,
                recorded=record.recorded,
                created_at=record.created_at,
                research_run_id=record.research_run_id,
                proposal_id=record.proposal_id,
                human_review_id=record.human_review_id,
            ),
        )

    def get(self, approval_id: str) -> ApprovalRecord | None:
        require_opaque_id(approval_id, "approval_id")
        return _fetch_one(
            self._connection,
            tables.approval,
            tables.approval.c.approval_id,
            approval_id,
            map_row.approval_from_row,
        )

    def get_by_subject(self, subject_reference: str) -> ApprovalRecord | None:
        require_opaque_id(subject_reference, "subject_reference")
        try:
            row = self._connection.execute(
                select(tables.approval).where(
                    tables.approval.c.subject_reference == subject_reference
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.approval_from_row(row)


class PostgresFindingRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: FindingRecord) -> None:
        _execute_write(
            self._connection,
            tables.finding.insert().values(
                finding_id=record.finding_id,
                finding_proposal_id=record.finding_proposal_id,
                candidate_id=record.candidate_id,
                research_run_id=record.research_run_id,
                approval_id=record.approval_id,
                human_review_id=record.human_review_id,
                title=record.title,
                claim=record.claim,
                classification=record.classification,
                evidence_ids=list(record.evidence_ids),
                verification_ids=list(record.verification_ids),
                created_at=record.created_at,
            ),
        )

    def get(self, finding_id: str) -> FindingRecord | None:
        require_opaque_id(finding_id, "finding_id")
        return _fetch_one(
            self._connection,
            tables.finding,
            tables.finding.c.finding_id,
            finding_id,
            map_row.finding_from_row,
        )

    def get_by_proposal(self, finding_proposal_id: str) -> FindingRecord | None:
        require_opaque_id(finding_proposal_id, "finding_proposal_id")
        try:
            row = self._connection.execute(
                select(tables.finding).where(
                    tables.finding.c.finding_proposal_id == finding_proposal_id
                )
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.finding_from_row(row)

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[FindingRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.finding)
                    .where(tables.finding.c.research_run_id == research_run_id)
                    .order_by(tables.finding.c.finding_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.finding_from_row(row) for row in rows]


class PostgresTargetInferenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: TargetInferenceRecord) -> None:
        _execute_write(
            self._connection,
            tables.target_inference.insert().values(
                inference_id=record.inference_id,
                research_run_id=record.research_run_id,
                kind=record.kind,
                epistemic_status=record.epistemic_status,
                opaque_ref=record.opaque_ref,
                statement=record.statement,
                source_refs=list(record.source_refs),
                attributes=dict(record.attributes),
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )

    def get(self, inference_id: str) -> TargetInferenceRecord | None:
        require_opaque_id(inference_id, "inference_id")
        return _fetch_one(
            self._connection,
            tables.target_inference,
            tables.target_inference.c.inference_id,
            inference_id,
            map_row.target_inference_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[TargetInferenceRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.target_inference)
                    .where(tables.target_inference.c.research_run_id == research_run_id)
                    .order_by(tables.target_inference.c.inference_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.target_inference_from_row(row) for row in rows]


class PostgresDifferentialObservationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: DifferentialObservationRecord) -> None:
        _execute_write(
            self._connection,
            tables.differential_observation.insert().values(
                differential_id=record.differential_id,
                research_run_id=record.research_run_id,
                case_id=record.case_id,
                baseline_observation_ids=list(record.baseline_observation_ids),
                variant_observation_ids=list(record.variant_observation_ids),
                changed_dimensions=list(record.changed_dimensions),
                common_dimensions=list(record.common_dimensions),
                observed_differences=dict(record.observed_differences),
                observed_similarities=dict(record.observed_similarities),
                interpretation=record.interpretation,
                source_refs=list(record.source_refs),
                strategy_version=record.strategy_version,
                alternative_explanation_slots=list(record.alternative_explanation_slots),
                created_at=record.created_at,
            ),
        )

    def get(self, differential_id: str) -> DifferentialObservationRecord | None:
        require_opaque_id(differential_id, "differential_id")
        return _fetch_one(
            self._connection,
            tables.differential_observation,
            tables.differential_observation.c.differential_id,
            differential_id,
            map_row.differential_observation_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[DifferentialObservationRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.differential_observation)
                    .where(tables.differential_observation.c.research_run_id == research_run_id)
                    .order_by(tables.differential_observation.c.differential_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.differential_observation_from_row(row) for row in rows]


class PostgresInvariantHypothesisRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: InvariantHypothesisRecord) -> None:
        _execute_write(
            self._connection,
            tables.invariant_hypothesis.insert().values(
                invariant_id=record.invariant_id,
                research_run_id=record.research_run_id,
                invariant_kind=record.invariant_kind,
                status=record.status,
                subject_refs=list(record.subject_refs),
                expected_behavior=record.expected_behavior,
                source_refs=list(record.source_refs),
                applicability_context=dict(record.applicability_context),
                assumptions=list(record.assumptions),
                counterexample_refs=list(record.counterexample_refs),
                falsification_direction=record.falsification_direction,
                proposer_provenance=record.proposer_provenance,
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )
        for source_ref in record.source_refs:
            _execute_write(
                self._connection,
                tables.invariant_source_ref.insert().values(
                    invariant_id=record.invariant_id,
                    source_ref=source_ref,
                    created_at=record.created_at,
                ),
            )

    def get(self, invariant_id: str) -> InvariantHypothesisRecord | None:
        require_opaque_id(invariant_id, "invariant_id")
        return _fetch_one(
            self._connection,
            tables.invariant_hypothesis,
            tables.invariant_hypothesis.c.invariant_id,
            invariant_id,
            map_row.invariant_hypothesis_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[InvariantHypothesisRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.invariant_hypothesis)
                    .where(tables.invariant_hypothesis.c.research_run_id == research_run_id)
                    .order_by(tables.invariant_hypothesis.c.invariant_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.invariant_hypothesis_from_row(row) for row in rows]

    def set_state(self, invariant_id: str, state: str) -> None:
        require_opaque_id(invariant_id, "invariant_id")
        if state not in ALLOWED_INVARIANT_STATUSES:
            raise PersistenceInputError("status is not an invariant hypothesis status")
        result = self._connection.execute(
            update(tables.invariant_hypothesis)
            .where(tables.invariant_hypothesis.c.invariant_id == invariant_id)
            .values(status=state)
        )
        if result.rowcount != 1:
            raise PersistenceError("invariant hypothesis not found for state update")

    def add_counterexample(self, record: InvariantCounterexampleRefRecord) -> None:
        current = self.get(record.invariant_id)
        if current is None:
            raise PersistenceError("invariant hypothesis not found for counterexample")
        refs = current.counterexample_refs
        if record.source_ref not in refs:
            refs = refs + (record.source_ref,)
        _execute_write(
            self._connection,
            tables.invariant_counterexample_ref.insert().values(
                counterexample_id=record.counterexample_id,
                invariant_id=record.invariant_id,
                source_ref=record.source_ref,
                applicability_context=dict(record.applicability_context),
                created_at=record.created_at,
            ),
        )
        result = self._connection.execute(
            update(tables.invariant_hypothesis)
            .where(tables.invariant_hypothesis.c.invariant_id == record.invariant_id)
            .values(status="CHALLENGED", counterexample_refs=list(refs))
        )
        if result.rowcount != 1:
            raise PersistenceError("invariant hypothesis not found for counterexample")


class PostgresChainHypothesisRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ChainHypothesisRecord) -> None:
        _execute_write(
            self._connection,
            tables.chain_hypothesis.insert().values(
                chain_id=record.chain_id,
                research_run_id=record.research_run_id,
                structural_identity=record.structural_identity,
                steps=[dict(step) for step in record.steps],
                source_refs=list(record.source_refs),
                preconditions=list(record.preconditions),
                expected_resulting_capability=record.expected_resulting_capability,
                unresolved_assumptions=list(record.unresolved_assumptions),
                falsification_points=list(record.falsification_points),
                descriptive_features=dict(record.descriptive_features),
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )

    def get(self, chain_id: str) -> ChainHypothesisRecord | None:
        require_opaque_id(chain_id, "chain_id")
        return _fetch_one(
            self._connection,
            tables.chain_hypothesis,
            tables.chain_hypothesis.c.chain_id,
            chain_id,
            map_row.chain_hypothesis_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ChainHypothesisRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.chain_hypothesis)
                    .where(tables.chain_hypothesis.c.research_run_id == research_run_id)
                    .order_by(tables.chain_hypothesis.c.chain_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.chain_hypothesis_from_row(row) for row in rows]


class PostgresResearchOpportunityRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchOpportunityRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_opportunity.insert().values(
                opportunity_id=record.opportunity_id,
                research_run_id=record.research_run_id,
                opportunity_kind=record.opportunity_kind,
                mode=record.mode,
                source_refs=list(record.source_refs),
                proposed_direction=record.proposed_direction,
                unresolved_question=record.unresolved_question,
                expected_information_value_description=record.expected_information_value_description,
                assumptions=list(record.assumptions),
                dimensions=dict(record.dimensions),
                context_signature=record.context_signature,
                novelty_composition_marker=record.novelty_composition_marker,
                prior_attempt_refs=list(record.prior_attempt_refs),
                structural_identity=record.structural_identity,
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )

    def get(self, opportunity_id: str) -> ResearchOpportunityRecord | None:
        require_opaque_id(opportunity_id, "opportunity_id")
        return _fetch_one(
            self._connection,
            tables.research_opportunity,
            tables.research_opportunity.c.opportunity_id,
            opportunity_id,
            map_row.research_opportunity_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ResearchOpportunityRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.research_opportunity)
                    .where(tables.research_opportunity.c.research_run_id == research_run_id)
                    .order_by(tables.research_opportunity.c.opportunity_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_opportunity_from_row(row) for row in rows]


class PostgresResearchSelectionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchSelectionRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_selection.insert().values(
                selection_id=record.selection_id,
                research_run_id=record.research_run_id,
                opportunity_id=record.opportunity_id,
                outcome=record.outcome,
                reason_codes=list(record.reason_codes),
                structural_identity=record.structural_identity,
                created_at=record.created_at,
            ),
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ResearchSelectionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.research_selection)
                    .where(tables.research_selection.c.research_run_id == research_run_id)
                    .order_by(tables.research_selection.c.selection_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_selection_from_row(row) for row in rows]


class PostgresOpportunitySelectionCandidateRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: OpportunitySelectionCandidateRecord) -> None:
        _execute_write(
            self._connection,
            tables.opportunity_selection_candidate.insert().values(
                candidate_id=record.candidate_id,
                research_run_id=record.research_run_id,
                source_system=record.source_system,
                opportunity_kind=record.opportunity_kind,
                mode=record.mode,
                source_refs=list(record.source_refs),
                proposed_direction=record.proposed_direction,
                unresolved_question=record.unresolved_question,
                expected_information_value_description=record.expected_information_value_description,
                assumptions=list(record.assumptions),
                dimensions=dict(record.dimensions),
                context_signature=record.context_signature,
                structural_identity=record.structural_identity,
                strategy_version=record.strategy_version,
                created_at=record.created_at,
                outcome=record.outcome,
                resulting_opportunity_id=record.resulting_opportunity_id,
                decided_at=record.decided_at,
            ),
        )

    def get(self, candidate_id: str) -> OpportunitySelectionCandidateRecord | None:
        require_opaque_id(candidate_id, "candidate_id")
        return _fetch_one(
            self._connection,
            tables.opportunity_selection_candidate,
            tables.opportunity_selection_candidate.c.candidate_id,
            candidate_id,
            map_row.opportunity_selection_candidate_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[OpportunitySelectionCandidateRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.opportunity_selection_candidate)
                    .where(
                        tables.opportunity_selection_candidate.c.research_run_id == research_run_id
                    )
                    .order_by(tables.opportunity_selection_candidate.c.candidate_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.opportunity_selection_candidate_from_row(row) for row in rows]

    def mark_decided(
        self,
        candidate_id: str,
        *,
        outcome: str,
        resulting_opportunity_id: str | None,
        decided_at: datetime,
    ) -> bool:
        """CAS: only a still-PENDING candidate can be decided, exactly once.

        A second call for the same candidate_id (e.g. a retried cycle) is a
        safe no-op rather than a silent overwrite of the first decision.
        """

        require_opaque_id(candidate_id, "candidate_id")
        if outcome not in ("ADMITTED", "NOT_ADMITTED"):
            raise PersistenceInputError("outcome must be ADMITTED or NOT_ADMITTED")
        try:
            result = self._connection.execute(
                update(tables.opportunity_selection_candidate)
                .where(tables.opportunity_selection_candidate.c.candidate_id == candidate_id)
                .where(tables.opportunity_selection_candidate.c.outcome == "PENDING")
                .values(
                    outcome=outcome,
                    resulting_opportunity_id=resulting_opportunity_id,
                    decided_at=decided_at,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        return result.rowcount == 1


class PostgresSnapshotRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: SnapshotRecord, members: tuple[SnapshotMemberRecord, ...]) -> None:
        _execute_write(
            self._connection,
            tables.snapshot.insert().values(
                snapshot_id=record.snapshot_id,
                research_run_id=record.research_run_id,
                program_id=record.program_id,
                target_identity=record.target_identity,
                captured_at=record.captured_at,
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )
        for member in members:
            _execute_write(
                self._connection,
                tables.snapshot_member.insert().values(
                    snapshot_id=member.snapshot_id,
                    observation_id=member.observation_id,
                    created_at=member.created_at,
                ),
            )

    def get(self, snapshot_id: str) -> SnapshotRecord | None:
        require_opaque_id(snapshot_id, "snapshot_id")
        return _fetch_one(
            self._connection,
            tables.snapshot,
            tables.snapshot.c.snapshot_id,
            snapshot_id,
            map_row.snapshot_from_row,
        )

    def list_members(self, snapshot_id: str) -> list[SnapshotMemberRecord]:
        require_opaque_id(snapshot_id, "snapshot_id")
        try:
            rows = self._connection.execute(
                select(tables.snapshot_member)
                .where(tables.snapshot_member.c.snapshot_id == snapshot_id)
                .order_by(tables.snapshot_member.c.observation_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.snapshot_member_from_row(row) for row in rows]

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[SnapshotRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.snapshot)
                    .where(tables.snapshot.c.research_run_id == research_run_id)
                    .order_by(tables.snapshot.c.captured_at, tables.snapshot.c.snapshot_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.snapshot_from_row(row) for row in rows]


class PostgresChangeEventRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ChangeEventRecord) -> None:
        _execute_write(
            self._connection,
            tables.change_event.insert().values(
                change_event_id=record.change_event_id,
                research_run_id=record.research_run_id,
                baseline_snapshot_id=record.baseline_snapshot_id,
                variant_snapshot_id=record.variant_snapshot_id,
                category=record.category,
                statement=record.statement,
                source_refs=list(record.source_refs),
                strategy_version=record.strategy_version,
                created_at=record.created_at,
            ),
        )

    def get(self, change_event_id: str) -> ChangeEventRecord | None:
        require_opaque_id(change_event_id, "change_event_id")
        return _fetch_one(
            self._connection,
            tables.change_event,
            tables.change_event.c.change_event_id,
            change_event_id,
            map_row.change_event_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ChangeEventRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.change_event)
                    .where(tables.change_event.c.research_run_id == research_run_id)
                    .order_by(tables.change_event.c.change_event_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.change_event_from_row(row) for row in rows]


class PostgresAuditEventRepository:
    """Insert-only. Updates and deletes are rejected by PostgreSQL triggers."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: AuditEventRecord) -> None:
        _execute_write(
            self._connection,
            tables.audit_event.insert().values(
                audit_event_id=record.audit_event_id,
                occurred_at=record.occurred_at,
                actor_id=record.actor_id,
                actor_type=record.actor_type,
                event_type=record.event_type,
                subject_type=record.subject_type,
                subject_id=record.subject_id,
                correlation_id=record.correlation_id,
                payload=dict(record.payload),
            ),
        )

    def get(self, audit_event_id: str) -> AuditEventRecord | None:
        require_opaque_id(audit_event_id, "audit_event_id")
        return _fetch_one(
            self._connection,
            tables.audit_event,
            tables.audit_event.c.audit_event_id,
            audit_event_id,
            map_row.audit_event_from_row,
        )

    def list_for_subject_type(self, subject_type: str) -> list[AuditEventRecord]:
        if not isinstance(subject_type, str) or not subject_type.strip():
            raise PersistenceInputError("subject_type must be a non-empty string")
        try:
            rows = self._connection.execute(
                select(tables.audit_event)
                .where(tables.audit_event.c.subject_type == subject_type)
                .order_by(tables.audit_event.c.occurred_at)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.audit_event_from_row(row) for row in rows]

    def list_for_subject(
        self, subject_type: str, subject_id: str, *, limit: int | None = None
    ) -> list[AuditEventRecord]:
        if not isinstance(subject_type, str) or not subject_type.strip():
            raise PersistenceInputError("subject_type must be a non-empty string")
        require_opaque_id(subject_id, "subject_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.audit_event)
                    .where(tables.audit_event.c.subject_type == subject_type)
                    .where(tables.audit_event.c.subject_id == subject_id)
                    .order_by(tables.audit_event.c.occurred_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.audit_event_from_row(row) for row in rows]


class PostgresRunFaultRepository:
    """Insert-only typed operational fault repository."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: RunFaultRecord) -> None:
        _execute_write(
            self._connection,
            tables.run_fault.insert().values(
                fault_id=record.fault_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                experiment_id=record.experiment_id,
                attempt_id=record.attempt_id,
                request_id=record.request_id,
                capability=record.capability,
                action=record.action,
                runtime_instance_id=record.runtime_instance_id,
                correlation_id=record.correlation_id,
                component=record.component,
                phase=record.phase,
                fault_class=record.fault_class,
                fault_code=record.fault_code,
                fatal=record.fatal,
                occurred_at=record.occurred_at,
                resolved_at=record.resolved_at,
                diagnostic_summary=record.diagnostic_summary,
            ),
        )

    def get(self, fault_id: str) -> RunFaultRecord | None:
        require_opaque_id(fault_id, "fault_id")
        return _fetch_one(
            self._connection,
            tables.run_fault,
            tables.run_fault.c.fault_id,
            fault_id,
            map_row.run_fault_from_row,
        )

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[RunFaultRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.run_fault)
                    .where(tables.run_fault.c.research_run_id == research_run_id)
                    .order_by(tables.run_fault.c.occurred_at, tables.run_fault.c.fault_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.run_fault_from_row(row) for row in rows]

    def list_unresolved_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[RunFaultRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.run_fault)
                    .where(tables.run_fault.c.research_run_id == research_run_id)
                    .where(tables.run_fault.c.resolved_at.is_(None))
                    .order_by(tables.run_fault.c.occurred_at, tables.run_fault.c.fault_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.run_fault_from_row(row) for row in rows]

class PostgresResearchOrchestrationRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchOrchestrationRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_orchestration.insert().values(
                **_orchestration_values(record)
            ),
        )

    def get(self, research_run_id: str) -> ResearchOrchestrationRecord | None:
        require_opaque_id(research_run_id, "research_run_id")
        return _fetch_one(
            self._connection,
            tables.research_orchestration,
            tables.research_orchestration.c.research_run_id,
            research_run_id,
            map_row.research_orchestration_from_row,
        )

    def save(
        self,
        record: ResearchOrchestrationRecord,
        *,
        expect_owner_runtime_instance_id: str | None = None,
        expect_lease_epoch: int | None = None,
        require_unowned_or_expired: bool = False,
    ) -> None:
        require_opaque_id(record.research_run_id, "research_run_id")
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
        values = _orchestration_values(record)
        values.pop("research_run_id")
        try:
            statement = (
                update(tables.research_orchestration)
                .where(
                    tables.research_orchestration.c.research_run_id
                    == record.research_run_id
                )
                .where(
                    tables.research_orchestration.c.state.notin_(
                        tuple(TERMINAL_ORCHESTRATION_STATES)
                    )
                )
            )
            if fenced_by_epoch:
                statement = statement.where(
                    tables.research_orchestration.c.owner_runtime_instance_id
                    == expect_owner_runtime_instance_id
                ).where(tables.research_orchestration.c.lease_epoch == expect_lease_epoch)
            if require_unowned_or_expired:
                server_now = _server_now(self._connection)
                statement = statement.where(
                    or_(
                        tables.research_orchestration.c.owner_runtime_instance_id.is_(None),
                        tables.research_orchestration.c.lease_expires_at < server_now,
                    )
                )
            result = self._connection.execute(statement.values(**values))
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        if result.rowcount == 1:
            return
        current = _fetch_one(
            self._connection,
            tables.research_orchestration,
            tables.research_orchestration.c.research_run_id,
            record.research_run_id,
            map_row.research_orchestration_from_row,
        )
        if current is None:
            raise PersistenceError("research_orchestration not found for checkpoint")
        if current.state in TERMINAL_ORCHESTRATION_STATES:
            raise TerminalOrchestrationStateError(
                f"research_orchestration {record.research_run_id} is terminal "
                f"({current.state}); state and stop_reason are immutable"
            )
        if fenced_by_epoch:
            raise LeaseFencingError(
                f"research_orchestration {record.research_run_id} lease has moved on "
                f"(expected owner={expect_owner_runtime_instance_id!r} "
                f"epoch={expect_lease_epoch}, current owner="
                f"{current.owner_runtime_instance_id!r} epoch={current.lease_epoch}); "
                "ownership lost, refusing to persist"
            )
        if require_unowned_or_expired:
            raise LeaseFencingError(
                f"research_orchestration {record.research_run_id} is still actively "
                f"leased by {current.owner_runtime_instance_id!r} "
                f"(epoch={current.lease_epoch}); refusing reconciliation write"
            )
        raise PersistenceError("persistence write failed")

    def acquire_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        ttl_seconds: float,
    ) -> LeaseAcquireResult:
        require_opaque_id(research_run_id, "research_run_id")
        if not isinstance(owner_runtime_instance_id, str) or not owner_runtime_instance_id.strip():
            raise PersistenceInputError("owner_runtime_instance_id must be a non-empty string")
        if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise PersistenceInputError("ttl_seconds must be > 0")
        try:
            server_now = _server_now(self._connection)
            expires_at = server_now + timedelta(seconds=ttl_seconds)
            result = self._connection.execute(
                update(tables.research_orchestration)
                .where(tables.research_orchestration.c.research_run_id == research_run_id)
                .where(
                    tables.research_orchestration.c.state.notin_(
                        tuple(TERMINAL_ORCHESTRATION_STATES)
                    )
                )
                .where(
                    or_(
                        tables.research_orchestration.c.owner_runtime_instance_id.is_(None),
                        tables.research_orchestration.c.owner_runtime_instance_id
                        == owner_runtime_instance_id,
                        tables.research_orchestration.c.lease_expires_at < server_now,
                    )
                )
                .values(
                    owner_runtime_instance_id=owner_runtime_instance_id,
                    lease_epoch=tables.research_orchestration.c.lease_epoch + 1,
                    lease_expires_at=expires_at,
                    updated_at=server_now,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        current = _fetch_one(
            self._connection,
            tables.research_orchestration,
            tables.research_orchestration.c.research_run_id,
            research_run_id,
            map_row.research_orchestration_from_row,
        )
        if result.rowcount == 1:
            if current is None:
                raise PersistenceError("research_orchestration vanished during lease acquire")
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.ACQUIRED, record=current)
        if current is None:
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.NOT_FOUND)
        if current.state in TERMINAL_ORCHESTRATION_STATES:
            return LeaseAcquireResult(outcome=LeaseAcquireOutcome.DENIED_TERMINAL)
        return LeaseAcquireResult(outcome=LeaseAcquireOutcome.DENIED_HELD_BY_OTHER)

    def renew_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        expected_lease_epoch: int,
        ttl_seconds: float,
    ) -> bool:
        require_opaque_id(research_run_id, "research_run_id")
        if not isinstance(owner_runtime_instance_id, str) or not owner_runtime_instance_id.strip():
            raise PersistenceInputError("owner_runtime_instance_id must be a non-empty string")
        if not isinstance(ttl_seconds, (int, float)) or ttl_seconds <= 0:
            raise PersistenceInputError("ttl_seconds must be > 0")
        try:
            server_now = _server_now(self._connection)
            result = self._connection.execute(
                update(tables.research_orchestration)
                .where(tables.research_orchestration.c.research_run_id == research_run_id)
                .where(
                    tables.research_orchestration.c.owner_runtime_instance_id
                    == owner_runtime_instance_id
                )
                .where(tables.research_orchestration.c.lease_epoch == expected_lease_epoch)
                .where(
                    tables.research_orchestration.c.state.notin_(
                        tuple(TERMINAL_ORCHESTRATION_STATES)
                    )
                )
                .values(
                    lease_expires_at=server_now + timedelta(seconds=ttl_seconds),
                    updated_at=server_now,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        return result.rowcount == 1

    def release_lease(
        self,
        research_run_id: str,
        *,
        owner_runtime_instance_id: str,
        expected_lease_epoch: int,
    ) -> bool:
        require_opaque_id(research_run_id, "research_run_id")
        if not isinstance(owner_runtime_instance_id, str) or not owner_runtime_instance_id.strip():
            raise PersistenceInputError("owner_runtime_instance_id must be a non-empty string")
        try:
            server_now = _server_now(self._connection)
            result = self._connection.execute(
                update(tables.research_orchestration)
                .where(tables.research_orchestration.c.research_run_id == research_run_id)
                .where(
                    tables.research_orchestration.c.owner_runtime_instance_id
                    == owner_runtime_instance_id
                )
                .where(tables.research_orchestration.c.lease_epoch == expected_lease_epoch)
                .values(
                    owner_runtime_instance_id=None,
                    lease_expires_at=None,
                    updated_at=server_now,
                )
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence write failed") from exc
        return result.rowcount == 1

    def list_recoverable(self) -> list[ResearchOrchestrationRecord]:
        try:
            rows = (
                self._connection.execute(
                    select(tables.research_orchestration).where(
                        tables.research_orchestration.c.state.in_(("READY", "RUNNING", "BLOCKED"))
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_orchestration_from_row(row) for row in rows]


class PostgresResearchCycleRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: ResearchCycleRecord) -> None:
        _execute_write(
            self._connection,
            tables.research_cycle.insert().values(
                cycle_id=record.cycle_id,
                research_run_id=record.research_run_id,
                cycle_number=record.cycle_number,
                phase_completed=record.phase_completed,
                outcome=record.outcome,
                stop_reason=record.stop_reason,
                opportunity_id=record.opportunity_id,
                hypothesis_id=record.hypothesis_id,
                experiment_id=record.experiment_id,
                created_at=record.created_at,
            ),
        )

    def get(self, cycle_id: str) -> ResearchCycleRecord | None:
        require_opaque_id(cycle_id, "cycle_id")
        return _fetch_one(
            self._connection,
            tables.research_cycle,
            tables.research_cycle.c.cycle_id,
            cycle_id,
            map_row.research_cycle_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ResearchCycleRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.research_cycle)
                    .where(tables.research_cycle.c.research_run_id == research_run_id)
                    .order_by(tables.research_cycle.c.cycle_number),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.research_cycle_from_row(row) for row in rows]


class PostgresBudgetConsumptionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: BudgetConsumptionRecord) -> None:
        _execute_write(
            self._connection,
            tables.budget_consumption.insert().values(**_consumption_values(record)),
        )

    def insert_within_allowance(
        self,
        record: BudgetConsumptionRecord,
        issued: IssuedBudgetRecord,
    ) -> None:
        del issued
        try:
            locked = self._connection.execute(
                select(tables.issued_budget)
                .where(tables.issued_budget.c.budget_id == record.budget_id)
                .with_for_update()
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if locked is None:
            raise PersistenceError("issued budget not found for consumption")
        mapped_issued = map_row.issued_budget_from_row(locked)
        if mapped_issued.budget_id != record.budget_id:
            raise PersistenceError("locked budget id mismatch")
        if mapped_issued.research_run_id != record.research_run_id:
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
            try:
                orch_row = self._connection.execute(
                    select(tables.research_orchestration)
                    .where(
                        tables.research_orchestration.c.research_run_id
                        == record.research_run_id
                    )
                    .with_for_update()
                ).mappings().one_or_none()
            except SQLAlchemyError as exc:
                raise PersistenceError("persistence read failed") from exc
            if orch_row is None:
                raise BudgetOverspendError("MODEL_CALL requires locked orchestration allowance")
            orchestration = map_row.research_orchestration_from_row(orch_row)
        assert_within_allowance(
            mapped_issued, existing, record, orchestration=orchestration
        )
        try:
            self.insert(record)
        except PersistenceConflictError:
            return

    def get(self, consumption_id: str) -> BudgetConsumptionRecord | None:
        require_opaque_id(consumption_id, "consumption_id")
        return _fetch_one(
            self._connection,
            tables.budget_consumption,
            tables.budget_consumption.c.consumption_id,
            consumption_id,
            map_row.budget_consumption_from_row,
        )

    def list_for_budget(self, budget_id: str) -> list[BudgetConsumptionRecord]:
        require_opaque_id(budget_id, "budget_id")
        try:
            rows = self._connection.execute(
                select(tables.budget_consumption)
                .where(tables.budget_consumption.c.budget_id == budget_id)
                .order_by(tables.budget_consumption.c.consumption_id)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.budget_consumption_from_row(row) for row in rows]

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[BudgetConsumptionRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.budget_consumption)
                    .where(tables.budget_consumption.c.research_run_id == research_run_id)
                    .order_by(tables.budget_consumption.c.consumption_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.budget_consumption_from_row(row) for row in rows]


def _orchestration_values(record: ResearchOrchestrationRecord) -> dict[str, object]:
    return {
        "research_run_id": record.research_run_id,
        "state": record.state,
        "cycle_number": record.cycle_number,
        "last_phase": record.last_phase,
        "last_opportunity_id": record.last_opportunity_id,
        "last_hypothesis_id": record.last_hypothesis_id,
        "last_experiment_id": record.last_experiment_id,
        "pause_reason": record.pause_reason,
        "stop_reason": record.stop_reason,
        "policy_version": record.policy_version,
        "max_cycles": record.max_cycles,
        "max_experiments": record.max_experiments,
        "max_model_calls": record.max_model_calls,
        "max_worker_invocations": record.max_worker_invocations,
        "max_elapsed_ms": record.max_elapsed_ms,
        "max_selected_opportunities": record.max_selected_opportunities,
        "max_runtime_fallback": record.max_runtime_fallback,
        "side_effect_ceiling": record.side_effect_ceiling,
        "allow_repeated_control_experiments": record.allow_repeated_control_experiments,
        "budget_id": record.budget_id,
        "target_reference": record.target_reference,
        "research_question": record.research_question,
        "configuration_fingerprint": record.configuration_fingerprint,
        "current_phase": record.current_phase,
        "active_cycle_id": record.active_cycle_id,
        "last_attempt_id": record.last_attempt_id,
        "last_observation_id": record.last_observation_id,
        "last_assessment_id": record.last_assessment_id,
        "last_worker_result_id": record.last_worker_result_id,
        "routing_policy_version": record.routing_policy_version,
        "scope_fingerprint": record.scope_fingerprint,
        "owner_runtime_instance_id": record.owner_runtime_instance_id,
        "lease_epoch": record.lease_epoch,
        "lease_expires_at": record.lease_expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "checkpoint_at": record.checkpoint_at,
    }


def _consumption_values(record: BudgetConsumptionRecord) -> dict[str, object]:
    return {
        "consumption_id": record.consumption_id,
        "budget_id": record.budget_id,
        "research_run_id": record.research_run_id,
        "experiment_id": record.experiment_id,
        "request_id": record.request_id,
        "resource_type": record.resource_type,
        "amount": record.amount,
        "unit": record.unit,
        "occurred_at": record.occurred_at,
        "provenance": record.provenance,
        "resource_metadata": dict(record.resource_metadata) if record.resource_metadata is not None else None,
    }


class PostgresSessionContextRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: SessionContextRecord) -> None:
        _execute_write(
            self._connection,
            tables.session_context.insert().values(
                session_context_id=record.session_context_id,
                research_run_id=record.research_run_id,
                identity_id=record.identity_id,
                actor_reference=record.actor_reference,
                origin=record.origin,
                authentication_profile_reference=record.authentication_profile_reference,
                authentication_method=record.authentication_method,
                secret_scheme=record.secret_scheme,
                secret_name=record.secret_name,
                state=record.state,
                created_at=record.created_at,
                updated_at=record.updated_at,
                established_at=record.established_at,
                expires_at=record.expires_at,
                session_cookie_name=record.session_cookie_name,
            ),
        )

    def get(self, session_context_id: str) -> SessionContextRecord | None:
        require_opaque_id(session_context_id, "session_context_id")
        return _fetch_one(
            self._connection,
            tables.session_context,
            tables.session_context.c.session_context_id,
            session_context_id,
            map_row.session_context_from_row,
        )

    def set_state(
        self,
        session_context_id: str,
        state: str,
        *,
        established_at: datetime | None = None,
        expires_at: datetime | None = None,
        updated_at: datetime,
    ) -> None:
        require_opaque_id(session_context_id, "session_context_id")
        if state not in ALLOWED_SESSION_STATES:
            raise PersistenceInputError("state is not a SessionContext state")
        values: dict[str, object] = {"state": state, "updated_at": updated_at}
        if established_at is not None:
            values["established_at"] = established_at
        if expires_at is not None:
            values["expires_at"] = expires_at
        result = self._connection.execute(
            update(tables.session_context)
            .where(tables.session_context.c.session_context_id == session_context_id)
            .values(**values)
        )
        if result.rowcount != 1:
            raise PersistenceError("session_context not found for state update")

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[SessionContextRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.session_context)
                    .where(tables.session_context.c.research_run_id == research_run_id)
                    .order_by(tables.session_context.c.session_context_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.session_context_from_row(row) for row in rows]


class PostgresHunterFamilyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: HunterFamilyRecord) -> None:
        _execute_write(
            self._connection,
            tables.hunter_family.insert().values(
                family_id=record.family_id,
                name=record.name,
                target_node_kinds=list(record.target_node_kinds),
                preconditions=dict(record.preconditions),
                claim_template=record.claim_template,
                evidence_requirements=dict(record.evidence_requirements),
                validation_tier=record.validation_tier,
                enabled=record.enabled,
                version=record.version,
                created_at=record.created_at,
            ),
        )

    def get(self, family_id: str, version: int) -> HunterFamilyRecord | None:
        require_opaque_id(family_id, "family_id")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PersistenceInputError("version must be a positive integer")
        try:
            row = self._connection.execute(
                select(tables.hunter_family)
                .where(tables.hunter_family.c.family_id == family_id)
                .where(tables.hunter_family.c.version == version)
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.hunter_family_from_row(row)

    def get_latest(self, family_id: str) -> HunterFamilyRecord | None:
        require_opaque_id(family_id, "family_id")
        try:
            row = self._connection.execute(
                select(tables.hunter_family)
                .where(tables.hunter_family.c.family_id == family_id)
                .order_by(tables.hunter_family.c.version.desc())
                .limit(1)
            ).mappings().one_or_none()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.hunter_family_from_row(row)

    def list_enabled(self) -> list[HunterFamilyRecord]:
        try:
            rows = self._connection.execute(
                select(tables.hunter_family)
                .where(tables.hunter_family.c.enabled == True)
                .order_by(tables.hunter_family.c.family_id, tables.hunter_family.c.version)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hunter_family_from_row(row) for row in rows]


class PostgresHuntV3QueueRepository:
    ALLOWED_STATES = frozenset({"PENDING", "APPROVED", "RUN", "BLOCKED"})

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: HuntV3QueueRecord) -> None:
        _execute_write(
            self._connection,
            tables.hunt_v3_queue.insert().values(
                queue_id=record.queue_id,
                research_run_id=record.research_run_id,
                hypothesis_id=record.hypothesis_id,
                family_id=record.family_id,
                node_canonical_key=record.node_canonical_key,
                identity_id=record.identity_id,
                capability=record.capability,
                action=record.action,
                arguments=dict(record.arguments),
                side_effect_level=record.side_effect_level,
                state=record.state,
                created_at=record.created_at,
            ),
        )

    def get(self, queue_id: str) -> HuntV3QueueRecord | None:
        require_opaque_id(queue_id, "queue_id")
        return _fetch_one(
            self._connection,
            tables.hunt_v3_queue,
            tables.hunt_v3_queue.c.queue_id,
            queue_id,
            map_row.hunt_v3_queue_from_row,
        )

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[HuntV3QueueRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.hunt_v3_queue)
                    .where(tables.hunt_v3_queue.c.research_run_id == research_run_id)
                    .order_by(tables.hunt_v3_queue.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hunt_v3_queue_from_row(row) for row in rows]

    def list_pending_for_research_run(self, research_run_id: str) -> list[HuntV3QueueRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                select(tables.hunt_v3_queue)
                .where(tables.hunt_v3_queue.c.research_run_id == research_run_id)
                .where(tables.hunt_v3_queue.c.state == "PENDING")
                .order_by(tables.hunt_v3_queue.c.created_at)
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.hunt_v3_queue_from_row(row) for row in rows]

    def set_state(
        self, queue_id: str, state: str, *, from_state: str | None = None
    ) -> None:
        require_opaque_id(queue_id, "queue_id")
        if state not in self.ALLOWED_STATES:
            raise PersistenceInputError("state is not a HuntV3Queue state")
        if from_state is not None and from_state not in self.ALLOWED_STATES:
            raise PersistenceInputError("from_state is not a HuntV3Queue state")
        stmt = (
            update(tables.hunt_v3_queue)
            .where(tables.hunt_v3_queue.c.queue_id == queue_id)
            .values(state=state)
        )
        if from_state is not None:
            stmt = stmt.where(tables.hunt_v3_queue.c.state == from_state)
        result = self._connection.execute(stmt)
        if result.rowcount != 1:
            if from_state is not None:
                raise PersistenceConflictError("hunt_v3_queue state transition conflict")
            raise PersistenceError("hunt_v3_queue item not found for state update")


class PostgresImpactChainRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(
        self,
        record: ImpactChainRecord,
        nodes: tuple[ImpactChainNodeRecord, ...],
        edges: tuple[ImpactChainEdgeRecord, ...],
    ) -> None:
        _execute_write(
            self._connection,
            tables.impact_chain.insert().values(
                chain_id=record.chain_id,
                research_run_id=record.research_run_id,
                program_id=record.program_id,
                graph_hash=record.graph_hash,
                created_at=record.created_at,
            ),
        )
        for node in nodes:
            _execute_write(
                self._connection,
                tables.impact_chain_node.insert().values(
                    node_id=node.node_id,
                    chain_id=node.chain_id,
                    impact_kind=node.impact_kind,
                    claim_text=node.claim_text,
                    scope_ref=dict(node.scope_ref),
                    proof_refs=list(node.proof_refs),
                    ordering=node.ordering,
                    created_at=node.created_at,
                ),
            )
        for edge in edges:
            _execute_write(
                self._connection,
                tables.impact_chain_edge.insert().values(
                    edge_id=edge.edge_id,
                    chain_id=edge.chain_id,
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    relation=edge.relation,
                    proof_refs=list(edge.proof_refs),
                    created_at=edge.created_at,
                ),
            )

    def get(self, chain_id: str) -> ImpactChainRecord | None:
        require_opaque_id(chain_id, "chain_id")
        return _fetch_one(
            self._connection,
            tables.impact_chain,
            tables.impact_chain.c.chain_id,
            chain_id,
            map_row.impact_chain_from_row,
        )

    def get_nodes(self, chain_id: str, *, limit: int | None = None) -> tuple[ImpactChainNodeRecord, ...]:
        require_opaque_id(chain_id, "chain_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.impact_chain_node)
                    .where(tables.impact_chain_node.c.chain_id == chain_id)
                    .order_by(tables.impact_chain_node.c.ordering),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return tuple(map_row.impact_chain_node_from_row(row) for row in rows)

    def get_edges(self, chain_id: str, *, limit: int | None = None) -> tuple[ImpactChainEdgeRecord, ...]:
        require_opaque_id(chain_id, "chain_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.impact_chain_edge)
                    .where(tables.impact_chain_edge.c.chain_id == chain_id),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return tuple(map_row.impact_chain_edge_from_row(row) for row in rows)

    def list_for_research_run(self, research_run_id: str, *, limit: int | None = None) -> list[ImpactChainRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.impact_chain)
                    .where(tables.impact_chain.c.research_run_id == research_run_id)
                    .order_by(tables.impact_chain.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.impact_chain_from_row(row) for row in rows]


class PostgresRuntimeInstanceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: RuntimeInstanceRecord) -> None:
        _execute_write(
            self._connection,
            tables.runtime_instance.insert().values(
                runtime_instance_id=record.runtime_instance_id,
                host_identity=record.host_identity,
                process_id=record.process_id,
                engine_version=record.engine_version,
                status=record.status,
                capabilities_summary=dict(record.capabilities_summary),
                started_at=record.started_at,
                last_seen_at=record.last_seen_at,
                stopped_at=record.stopped_at,
            ),
        )

    def get(self, runtime_instance_id: str) -> RuntimeInstanceRecord | None:
        require_opaque_id(runtime_instance_id, "runtime_instance_id")
        return _fetch_one(
            self._connection,
            tables.runtime_instance,
            tables.runtime_instance.c.runtime_instance_id,
            runtime_instance_id,
            map_row.runtime_instance_from_row,
        )

    def save(self, record: RuntimeInstanceRecord) -> None:
        require_opaque_id(record.runtime_instance_id, "runtime_instance_id")
        _execute_write(
            self._connection,
            update(tables.runtime_instance)
            .where(
                tables.runtime_instance.c.runtime_instance_id == record.runtime_instance_id
            )
            .values(
                host_identity=record.host_identity,
                process_id=record.process_id,
                engine_version=record.engine_version,
                status=record.status,
                capabilities_summary=dict(record.capabilities_summary),
                started_at=record.started_at,
                last_seen_at=record.last_seen_at,
                stopped_at=record.stopped_at,
            ),
        )

    def list_active(self) -> list[RuntimeInstanceRecord]:
        try:
            rows = (
                self._connection.execute(
                    select(tables.runtime_instance).where(
                        tables.runtime_instance.c.status.in_(("STARTING", "RUNNING", "DRAINING"))
                    )
                )
                .mappings()
                .all()
            )
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.runtime_instance_from_row(row) for row in rows]


class PostgresPreflightReportRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def insert(self, record: PreflightReportRecord) -> None:
        _execute_write(
            self._connection,
            tables.preflight_report.insert().values(
                preflight_report_id=record.preflight_report_id,
                research_run_id=record.research_run_id,
                runtime_instance_id=record.runtime_instance_id,
                created_at=record.created_at,
                release_version=record.release_version,
                configuration_fingerprint=record.configuration_fingerprint,
                status=record.status,
                checks=[dict(item) for item in record.checks],
            ),
        )

    def get(self, preflight_report_id: str) -> PreflightReportRecord | None:
        require_opaque_id(preflight_report_id, "preflight_report_id")
        return _fetch_one(
            self._connection,
            tables.preflight_report,
            tables.preflight_report.c.preflight_report_id,
            preflight_report_id,
            map_row.preflight_report_from_row,
        )

    def latest_for_research_run(
        self, research_run_id: str
    ) -> PreflightReportRecord | None:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            row = self._connection.execute(
                select(tables.preflight_report)
                .where(tables.preflight_report.c.research_run_id == research_run_id)
                .order_by(tables.preflight_report.c.created_at.desc())
                .limit(1)
            ).mappings().first()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        if row is None:
            return None
        return map_row.preflight_report_from_row(row)

    def list_for_research_run(
        self, research_run_id: str, *, limit: int | None = None
    ) -> list[PreflightReportRecord]:
        require_opaque_id(research_run_id, "research_run_id")
        try:
            rows = self._connection.execute(
                _apply_read_limit(
                    select(tables.preflight_report)
                    .where(tables.preflight_report.c.research_run_id == research_run_id)
                    .order_by(tables.preflight_report.c.created_at),
                    limit,
                )
            ).mappings().all()
        except SQLAlchemyError as exc:
            raise PersistenceError("persistence read failed") from exc
        return [map_row.preflight_report_from_row(row) for row in rows]
