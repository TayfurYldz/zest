"""Persistence records for the A3 authoritative spine.

These are Data-layer Python types, not language-neutral contracts and not Core
authorization objects. Hypothesis is not fact, Evidence, or Finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any, Mapping

from research_os.core.enums import ActorType, AuthorizationSourceState
from research_os.data.errors import PersistenceInputError
from research_os.safe_data import SecretMaterialError, reject_secret_keys as reject_secret_structure

SECRET_VALUE_KEYS = {
    "token",
    "password",
    "api_key",
    "apiKey",
    "raw_secret",
    "credential",
    "secret_value",
    "secretValue",
}

ALLOWED_ACTOR_TYPES = frozenset(item.value for item in ActorType)
ALLOWED_AUTHORIZATION_STATES = frozenset(
    item.value for item in AuthorizationSourceState
)


class ExperimentExecutionState(Enum):
    """Experiment execution outcome. Not Hypothesis belief. Not a Finding."""

    PLANNED = "PLANNED"
    AUTHORIZATION_CHECK = "AUTHORIZATION_CHECK"
    READY = "READY"
    RUNNING = "RUNNING"
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class WorkerResultStatus(Enum):
    """Untrusted execution status from the Worker contract. Not Evidence."""

    SUCCEEDED = "SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    REAUTHORIZATION_REQUIRED = "REAUTHORIZATION_REQUIRED"


ALLOWED_EXPERIMENT_STATES = frozenset(
    item.value for item in ExperimentExecutionState
)
ALLOWED_WORKER_RESULT_STATUSES = frozenset(item.value for item in WorkerResultStatus)


class ExecutionAttemptState(Enum):
    """One intended Worker invocation. Not Evidence. Not Hypothesis outcome."""

    AUTHORIZED = "AUTHORIZED"
    DISPATCHING = "DISPATCHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


ALLOWED_EXECUTION_ATTEMPT_STATES = frozenset(
    item.value for item in ExecutionAttemptState
)

ALLOWED_REASONING_ROLES = frozenset({"GENERATOR", "FALSIFIER"})
ALLOWED_ADMISSION_OUTCOMES = frozenset(
    {
        "ADMITTED",
        "REJECTED_UNTESTABLE",
        "REJECTED_UNSUPPORTED",
        "REJECTED_POLICY_CONFLICT",
        "NEEDS_MORE_CONTEXT",
        "MODEL_INVOCATION_FAILED",
    }
)
ALLOWED_ASSESSMENT_OUTCOMES = frozenset(
    {
        "CONSISTENT_WITH_PREDICTION",
        "CONTRADICTS_PREDICTION",
        "INCONCLUSIVE",
        "EXECUTION_UNUSABLE",
        "NEEDS_MORE_CONTEXT",
    }
)
ALLOWED_EVALUATOR_KINDS = frozenset({"DETERMINISTIC"})
ASSESSMENT_RATIONALE_FORBIDDEN_KEYS = frozenset(
    {"severity", "evidence", "finding", "confidence", "candidate"}
)
ALLOWED_EVIDENCE_POLARITIES = frozenset({"SUPPORTING", "CONTRADICTING", "NEUTRAL"})
ALLOWED_EVIDENCE_ADMISSION_OUTCOMES = frozenset(
    {
        "ADMITTED",
        "REJECTED_INSUFFICIENT_SUPPORT",
        "REJECTED_BROKEN_PROVENANCE",
        "REJECTED_EXECUTION_UNUSABLE",
        "REJECTED_POLICY_CONFLICT",
        "NEEDS_VERIFICATION",
    }
)
EVIDENCE_FORBIDDEN_KEYS = frozenset(
    {
        "severity",
        "finding",
        "candidate",
        "exploitability",
        "authorization",
        "confidence",
        "verification",
    }
)
ALLOWED_CANDIDATE_STATES = frozenset(
    {
        "OPEN",
        "VERIFYING",
        "VALIDATED",
        "REJECTED",
        "INCONCLUSIVE",
        "DUPLICATE",
        "OUT_OF_SCOPE",
    }
)
ALLOWED_CANDIDATE_CLASSIFICATIONS = frozenset(
    {
        "DIAGNOSTIC_PLUMBING",
        "HTTP_AUTHORIZATION_DIFFERENTIAL",
        "HTTP_STATE_TRANSITION_AUTHORIZATION",
    }
)
ALLOWED_PROMOTION_STAGES = frozenset(
    {
        "EVIDENCE_REJECTED",
        "EVIDENCE_ADMITTED",
        "CANDIDATE_REJECTED",
        "CANDIDATE_OPEN",
        "VERIFYING",
        "REPRODUCTION_EXECUTED",
        "VERIFIED",
        "PROPOSAL_RECORDED",
        "STOPPED",
    }
)
ALLOWED_CANDIDATE_ADMISSION_OUTCOMES = frozenset(
    {
        "ADMITTED",
        "REJECTED_INSUFFICIENT_SUPPORT",
        "REJECTED_BROKEN_PROVENANCE",
        "REJECTED_CLAIM_EXCEEDS_EVIDENCE",
        "REJECTED_NOT_TESTABLE",
        "REJECTED_POLICY_CONFLICT",
    }
)
ALLOWED_VERIFICATION_OUTCOMES = frozenset(
    {
        "VALIDATED",
        "REJECTED",
        "INCONCLUSIVE",
        "DUPLICATE",
        "OUT_OF_SCOPE",
    }
)
ALLOWED_VERIFIER_KINDS = frozenset({"DETERMINISTIC"})
ALLOWED_FINDING_PROPOSAL_STATES = frozenset(
    {"PROPOSED", "HUMAN_REVIEW", "APPROVED", "REJECTED"}
)
ALLOWED_HUMAN_REVIEW_DECISIONS = frozenset({"APPROVE", "REJECT"})
ALLOWED_RECORDED_APPROVAL_DECISIONS = frozenset({"APPROVE", "REJECT"})
ALLOWED_FINDING_CLASSIFICATIONS = frozenset(
    {
        "DIAGNOSTIC_PLUMBING",
        "HTTP_AUTHORIZATION_DIFFERENTIAL",
        "HTTP_STATE_TRANSITION_AUTHORIZATION",
    }
)
ALLOWED_TARGET_INFERENCE_STATUSES = frozenset({"INFERRED", "HYPOTHESIZED"})
ALLOWED_TARGET_ELEMENT_KINDS = frozenset(
    {
        "ACTOR",
        "ROLE",
        "SESSION",
        "RESOURCE",
        "ACTION",
        "STATE",
        "RELATIONSHIP",
        "STATE_TRANSITION",
    }
)
ALLOWED_DIFFERENTIAL_INTERPRETATIONS = frozenset(
    {"CONTROLLED_DIFFERENCE", "EQUIVALENT", "INCOMPARABLE"}
)
ALLOWED_INVARIANT_KINDS = frozenset(
    {
        "ACCESS_RELATION",
        "STATE_TRANSITION",
        "OWNERSHIP_RELATION",
        "ROLE_BOUNDARY",
        "SESSION_BINDING",
        "RESOURCE_ISOLATION",
        "IMMUTABILITY_AFTER_STATE",
        "SEQUENCE_PRECONDITION",
        "INPUT_OUTPUT_RELATION",
        "OTHER",
    }
)
ALLOWED_INVARIANT_STATUSES = frozenset({"TESTABLE", "CHALLENGED", "RETIRED"})
ALLOWED_OPPORTUNITY_KINDS = frozenset(
    {
        "HYPOTHESIS_FOLLOWUP",
        "DIFFERENTIAL_FOLLOWUP",
        "INVARIANT_CHALLENGE",
        "CHAIN_EXTENSION",
        "NEGATIVE_KNOWLEDGE_REVISIT",
        "UNRESOLVED_TARGET_RELATION",
        "CONTROL_EXPERIMENT",
        "SURFACE_DISCOVERY",
        "HUNTER_COVERAGE_GAP",
        "REGISTRY_EXTERNAL_EXPLORATORY",
        "OTHER",
    }
)
ALLOWED_OPPORTUNITY_MODES = frozenset({"EXPLORATION", "EXPLOITATION"})
ALLOWED_CANDIDATE_SOURCE_SYSTEMS = frozenset(
    {"HUNTER_COVERAGE", "REGISTRY_EXTERNAL_ANOMALY"}
)
ALLOWED_CANDIDATE_OUTCOMES = frozenset({"PENDING", "ADMITTED", "NOT_ADMITTED"})
ALLOWED_SELECTION_OUTCOMES = frozenset(
    {
        "SELECT",
        "DEFER",
        "SKIP_DUPLICATE",
        "SKIP_LOW_INFORMATION",
        "BLOCKED_BUDGET",
        "BLOCKED_POLICY",
        "NEEDS_MORE_CONTEXT",
    }
)
ALLOWED_CHANGE_CATEGORIES = frozenset(
    {
        "ADDED",
        "REMOVED",
        "MODIFIED",
        "RELATION_CHANGED",
        "STATE_CHANGED",
        "BEHAVIOR_CHANGED",
        "UNKNOWN_CHANGE",
    }
)
ALLOWED_CHAIN_NODE_KINDS = frozenset(
    {
        "OBSERVATION",
        "CAPABILITY",
        "STATE",
        "STATE_TRANSITION",
        "INVARIANT",
        "EXPERIMENT",
        "HYPOTHESIS",
    }
)
TARGET_MODEL_SECRET_KEYS = SECRET_VALUE_KEYS | {
    "session_token",
    "cookie",
    "authorization",
}
FINDING_FORBIDDEN_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "cve",
        "bounty",
        "exploitability",
        "confidence",
        "vulnerability",
    }
)
CANDIDATE_FORBIDDEN_KEYS = frozenset(
    {
        "severity",
        "cvss",
        "finding",
        "finding_proposal",
        "exploitability",
        "authorization",
        "confidence",
    }
)


def require_opaque_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersistenceInputError(f"{field_name} must be a non-empty opaque id")
    return value


def require_optional_opaque_id(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return require_opaque_id(value, field_name)


def require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PersistenceInputError(f"{field_name} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PersistenceInputError(f"{field_name} must be timezone-aware")
    return value


def require_non_negative_int(value: int, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PersistenceInputError(
            f"{field_name} must be a non-negative int; 0 is not unlimited"
        )
    return value


def _reject_secret_keys(payload: Mapping[str, Any] | None, field_name: str) -> None:
    if payload is None:
        return
    if not isinstance(payload, Mapping):
        raise PersistenceInputError(f"{field_name} must be a mapping")
    try:
        reject_secret_structure(payload, field_name)
    except SecretMaterialError as exc:
        raise PersistenceInputError(str(exc)) from exc


def _optional_mapping(
    value: Mapping[str, Any] | None, field_name: str
) -> dict[str, Any] | None:
    if value is None:
        return None
    return _require_mapping(value, field_name)


def _require_mapping(
    value: Mapping[str, Any], field_name: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PersistenceInputError(f"{field_name} must be a mapping")
    _reject_secret_keys(value, field_name)
    return dict(value)


@dataclass(frozen=True)
class ProgramRecord:
    program_id: str
    created_at: datetime
    name: str | None = None
    handle: str | None = None
    platform: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.program_id, "program_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.name is not None and not isinstance(self.name, str):
            raise PersistenceInputError("name must be a string or None")
        if self.handle is not None and not isinstance(self.handle, str):
            raise PersistenceInputError("handle must be a string or None")
        if self.platform is not None and not isinstance(self.platform, str):
            raise PersistenceInputError("platform must be a string or None")


@dataclass(frozen=True)
class ScopeRuleV2Record:
    rule_id: str
    program_id: str
    effect: str
    scheme: str
    source_reference: str
    created_at: datetime
    host: str | None = None
    host_pattern: str | None = None
    port: int | None = None
    path_prefix: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.rule_id, "rule_id")
        require_opaque_id(self.program_id, "program_id")
        require_opaque_id(self.source_reference, "source_reference")
        require_aware_datetime(self.created_at, "created_at")
        if self.host is not None and not isinstance(self.host, str):
            raise PersistenceInputError("host must be a string or None")
        if self.host_pattern is not None and not isinstance(self.host_pattern, str):
            raise PersistenceInputError("host_pattern must be a string or None")
        if self.port is not None and (
            not isinstance(self.port, int) or isinstance(self.port, bool) or self.port < 1
        ):
            raise PersistenceInputError("port must be a positive integer or None")
        if self.path_prefix is not None and not isinstance(self.path_prefix, str):
            raise PersistenceInputError("path_prefix must be a string or None")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, "expires_at")
        if self.host is None and self.host_pattern is None:
            raise PersistenceInputError("host or host_pattern is required")
        if self.host is not None and self.host_pattern is not None:
            raise PersistenceInputError("host and host_pattern are mutually exclusive")


@dataclass(frozen=True)
class ProgramPolicyRecord:
    program_id: str
    loopback_fixture: bool
    max_response_bytes: int
    timeout_ms: int
    created_at: datetime
    updated_at: datetime
    action_policy: Mapping[str, Any] | None = None
    daily_llm_budget_microdollars: int | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.program_id, "program_id")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
        if not isinstance(self.loopback_fixture, bool):
            raise PersistenceInputError("loopback_fixture must be a bool")
        require_non_negative_int(self.max_response_bytes, "max_response_bytes")
        require_non_negative_int(self.timeout_ms, "timeout_ms")
        _optional_mapping(self.action_policy, "action_policy")
        if self.daily_llm_budget_microdollars is not None:
            require_non_negative_int(
                self.daily_llm_budget_microdollars, "daily_llm_budget_microdollars"
            )


@dataclass(frozen=True)
class SensorObservationRecord:
    observation_id: str
    research_run_id: str
    sensor_id: str
    target_reference: str
    collected_at: datetime
    payload_digest: str
    epistemic_status: str
    source_metadata: Mapping[str, Any]
    payload: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.observation_id, "observation_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.sensor_id, "sensor_id")
        require_opaque_id(self.target_reference, "target_reference")
        require_opaque_id(self.payload_digest, "payload_digest")
        if not isinstance(self.epistemic_status, str) or not self.epistemic_status.strip():
            raise PersistenceInputError("epistemic_status must be a non-empty string")
        require_aware_datetime(self.collected_at, "collected_at")
        require_aware_datetime(self.created_at, "created_at")
        _require_mapping(self.source_metadata, "source_metadata")
        _require_mapping(self.payload, "payload")


@dataclass(frozen=True)
class RateLimitProfileRecord:
    profile_id: str
    program_id: str
    max_requests_per_window: int
    window_seconds: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.profile_id, "profile_id")
        require_opaque_id(self.program_id, "program_id")
        require_aware_datetime(self.created_at, "created_at")
        require_non_negative_int(self.max_requests_per_window, "max_requests_per_window")
        require_non_negative_int(self.window_seconds, "window_seconds")


@dataclass(frozen=True)
class OastTokenRecord:
    token_id: str
    research_run_id: str
    hypothesis_id: str
    target_reference: str
    expires_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.token_id, "token_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.target_reference, "target_reference")
        require_aware_datetime(self.expires_at, "expires_at")
        require_aware_datetime(self.created_at, "created_at")


OAST_MAX_NORMALIZED_PAYLOAD_BYTES = 16_384
OAST_MAX_PROVIDER_ADAPTER_ID_LENGTH = 128
OAST_MAX_PROVIDER_EVENT_ID_LENGTH = 256


def _require_oast_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PersistenceInputError("normalized_payload must be a mapping")
    _reject_secret_keys(value, "normalized_payload")
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersistenceInputError(
            "normalized_payload must be JSON-serializable"
        ) from exc
    if len(encoded) > OAST_MAX_NORMALIZED_PAYLOAD_BYTES:
        raise PersistenceInputError(
            "normalized_payload exceeds the bounded payload limit"
        )
    return dict(value)


@dataclass(frozen=True)
class OastCorrelationRecord:
    """Immutable persistence binding for one ExecutionAttempt."""

    correlation_id: str
    attempt_id: str
    experiment_id: str
    research_run_id: str
    target_reference: str
    identity_id: str
    armed_at: datetime
    expires_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "correlation_id",
            "attempt_id",
            "experiment_id",
            "research_run_id",
            "target_reference",
            "identity_id",
        ):
            require_opaque_id(getattr(self, field_name), field_name)
        armed_at = require_aware_datetime(self.armed_at, "armed_at")
        expires_at = require_aware_datetime(self.expires_at, "expires_at")
        require_aware_datetime(self.created_at, "created_at")
        if expires_at <= armed_at:
            raise PersistenceInputError("expires_at must be later than armed_at")


@dataclass(frozen=True)
class OastCallbackDeliveryRecord:
    """Append-only bounded callback ledger entry."""

    delivery_id: str
    correlation_id: str
    provider_adapter_id: str
    provider_event_id: str
    received_at: datetime
    normalized_payload: Mapping[str, Any]
    normalized_digest: str

    def __post_init__(self) -> None:
        require_opaque_id(self.delivery_id, "delivery_id")
        require_opaque_id(self.correlation_id, "correlation_id")
        require_opaque_id(self.provider_adapter_id, "provider_adapter_id")
        require_opaque_id(self.provider_event_id, "provider_event_id")
        if len(self.provider_adapter_id) > OAST_MAX_PROVIDER_ADAPTER_ID_LENGTH:
            raise PersistenceInputError("provider_adapter_id exceeds its length limit")
        if len(self.provider_event_id) > OAST_MAX_PROVIDER_EVENT_ID_LENGTH:
            raise PersistenceInputError("provider_event_id exceeds its length limit")
        require_aware_datetime(self.received_at, "received_at")
        object.__setattr__(
            self,
            "normalized_payload",
            _require_oast_payload(self.normalized_payload),
        )
        require_opaque_id(self.normalized_digest, "normalized_digest")
        digest = self.normalized_digest.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PersistenceInputError("normalized_digest must be a SHA-256 hexadecimal digest")
        object.__setattr__(self, "normalized_digest", digest)


@dataclass(frozen=True)
class OastAdmissionRecord:
    """Durable one-per-correlation admission link reserved for Slice B."""

    admission_id: str
    correlation_id: str
    research_run_id: str
    sensor_observation_id: str
    discovery_fact_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "admission_id",
            "correlation_id",
            "research_run_id",
            "sensor_observation_id",
            "discovery_fact_id",
        ):
            require_opaque_id(getattr(self, field_name), field_name)
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class BountyTableRecord:
    program_id: str
    severity: str
    created_at: datetime
    reward_range: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.program_id, "program_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.severity, str) or not self.severity.strip():
            raise PersistenceInputError("severity must be a non-empty string")
        _optional_mapping(self.reward_range, "reward_range")


@dataclass(frozen=True)
class AuthorizationSourceRecord:
    authorization_source_id: str
    program_id: str
    state: str
    provenance_reference: str
    created_at: datetime
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.authorization_source_id, "authorization_source_id")
        require_opaque_id(self.program_id, "program_id")
        require_opaque_id(self.provenance_reference, "provenance_reference")
        require_aware_datetime(self.created_at, "created_at")
        if self.state not in ALLOWED_AUTHORIZATION_STATES:
            raise PersistenceInputError("state must be ACTIVE, EXPIRED, or REVOKED")
        if self.effective_from is not None:
            require_aware_datetime(self.effective_from, "effective_from")
        if self.effective_until is not None:
            require_aware_datetime(self.effective_until, "effective_until")
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until < self.effective_from
        ):
            raise PersistenceInputError(
                "effective_until must be >= effective_from when both are set"
            )


@dataclass(frozen=True)
class ResearchRunRecord:
    research_run_id: str
    program_id: str
    authorization_source_id: str
    initiated_by_actor_id: str
    initiated_by_actor_type: str
    started_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.program_id, "program_id")
        require_opaque_id(self.authorization_source_id, "authorization_source_id")
        require_opaque_id(self.initiated_by_actor_id, "initiated_by_actor_id")
        require_aware_datetime(self.started_at, "started_at")
        if self.initiated_by_actor_type not in ALLOWED_ACTOR_TYPES:
            raise PersistenceInputError(
                "initiated_by_actor_type must be a locked identity class; MODEL is not an actor"
            )


@dataclass(frozen=True)
class IssuedBudgetRecord:
    """Immutable Core-issued envelope. 0 is no allowance, never unlimited."""

    budget_id: str
    research_run_id: str | None
    max_requests: int
    max_tool_calls: int
    max_runtime_ms: int
    max_concurrency: int
    issued_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.budget_id, "budget_id")
        require_optional_opaque_id(self.research_run_id, "research_run_id")
        require_non_negative_int(self.max_requests, "max_requests")
        require_non_negative_int(self.max_tool_calls, "max_tool_calls")
        require_non_negative_int(self.max_runtime_ms, "max_runtime_ms")
        require_non_negative_int(self.max_concurrency, "max_concurrency")
        require_aware_datetime(self.issued_at, "issued_at")


@dataclass(frozen=True)
class HypothesisRecord:
    """A claim to test. Not fact, Evidence, Candidate, or Finding."""

    hypothesis_id: str
    research_run_id: str
    claim: str
    created_at: datetime
    origin_reference: str | None = None
    identity_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise PersistenceInputError("claim must be a non-empty string")
        if self.origin_reference is not None:
            require_opaque_id(self.origin_reference, "origin_reference")
        if self.identity_id is not None:
            require_opaque_id(self.identity_id, "identity_id")


@dataclass(frozen=True)
class ResearchReasoningRecord:
    """Append-only untrusted reasoning provenance. Not Observation, Evidence, or Hypothesis truth."""

    reasoning_record_id: str
    research_run_id: str
    hypothesis_id: str | None
    role: str
    adapter_identity: str
    provider_adapter_identity: str
    correlation_id: str
    context_fingerprint: str
    structured_output: Mapping[str, Any]
    created_at: datetime
    model_id: str | None = None
    model_version: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.reasoning_record_id, "reasoning_record_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_optional_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.adapter_identity, "adapter_identity")
        require_opaque_id(self.provider_adapter_identity, "provider_adapter_identity")
        require_opaque_id(self.correlation_id, "correlation_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.role not in ALLOWED_REASONING_ROLES:
            raise PersistenceInputError("role is not a Research reasoning role")
        if not isinstance(self.context_fingerprint, str) or not self.context_fingerprint.strip():
            raise PersistenceInputError("context_fingerprint must be a non-empty string")
        if not isinstance(self.structured_output, Mapping):
            raise PersistenceInputError("structured_output must be a mapping")
        _reject_secret_keys(self.structured_output, "structured_output")
        object.__setattr__(self, "structured_output", dict(self.structured_output))
        if self.model_id is not None:
            require_opaque_id(self.model_id, "model_id")
        if self.model_version is not None:
            if not isinstance(self.model_version, str) or not self.model_version.strip():
                raise PersistenceInputError("model_version must be a non-empty string when set")


@dataclass(frozen=True)
class ResearchAdmissionRecord:
    """Append-only research-process admission history. Not Hypothesis truth or Evidence."""

    admission_record_id: str
    research_run_id: str
    outcome: str
    reason: str
    reason_code: str
    context_fingerprint: str
    created_at: datetime
    generator_reasoning_record_id: str | None = None
    falsifier_reasoning_record_id: str | None = None
    admitted_hypothesis_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.admission_record_id, "admission_record_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.outcome not in ALLOWED_ADMISSION_OUTCOMES:
            raise PersistenceInputError("outcome is not a Research admission outcome")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise PersistenceInputError("reason must be a non-empty string")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise PersistenceInputError("reason_code must be a non-empty string")
        if not isinstance(self.context_fingerprint, str) or not self.context_fingerprint.strip():
            raise PersistenceInputError("context_fingerprint must be a non-empty string")
        require_optional_opaque_id(
            self.generator_reasoning_record_id, "generator_reasoning_record_id"
        )
        require_optional_opaque_id(
            self.falsifier_reasoning_record_id, "falsifier_reasoning_record_id"
        )
        require_optional_opaque_id(self.admitted_hypothesis_id, "admitted_hypothesis_id")
        if self.outcome == "ADMITTED" and self.admitted_hypothesis_id is None:
            raise PersistenceInputError("ADMITTED admission requires admitted_hypothesis_id")
        if self.outcome != "ADMITTED" and self.admitted_hypothesis_id is not None:
            raise PersistenceInputError("rejected admission must not carry admitted_hypothesis_id")


@dataclass(frozen=True)
class ExperimentPlanRecord:
    """Immutable executed-plan specification. Not Experiment lifecycle and not authorization."""

    experiment_id: str
    research_run_id: str
    hypothesis_id: str
    required_capability: str
    action: str
    target_reference: str
    side_effect_level: int
    arguments: Mapping[str, Any]
    requested_budget_id: str
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    created_at: datetime
    capability_version: str | None = None
    capability_definition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.required_capability, "required_capability")
        require_opaque_id(self.action, "action")
        require_opaque_id(self.target_reference, "target_reference")
        require_opaque_id(self.requested_budget_id, "requested_budget_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.side_effect_level not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect_level must be 0, 1, 2, or 3")
        if not isinstance(self.expected_observation, str) or not self.expected_observation.strip():
            raise PersistenceInputError("expected_observation must be a non-empty string")
        if (
            not isinstance(self.disconfirming_observation, str)
            or not self.disconfirming_observation.strip()
        ):
            raise PersistenceInputError("disconfirming_observation must be a non-empty string")
        if not isinstance(self.evaluation_strategy, str) or not self.evaluation_strategy.strip():
            raise PersistenceInputError("evaluation_strategy must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise PersistenceInputError("arguments must be a mapping")
        _reject_secret_keys(self.arguments, "arguments")
        object.__setattr__(self, "arguments", dict(self.arguments))
        if self.capability_version is not None:
            require_opaque_id(self.capability_version, "capability_version")
        if self.capability_definition_fingerprint is not None:
            require_opaque_id(
                self.capability_definition_fingerprint,
                "capability_definition_fingerprint",
            )


@dataclass(frozen=True)
class HypothesisAssessmentRecord:
    """Append-only context-bound assessment. Not Evidence, Candidate, or Finding."""

    assessment_id: str
    hypothesis_id: str
    experiment_id: str
    research_run_id: str
    assessment_outcome: str
    observation_ids: tuple[str, ...]
    evaluator_kind: str
    evaluator_version: str
    rationale: Mapping[str, Any]
    evaluation_strategy: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.assessment_id, "assessment_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.assessment_outcome not in ALLOWED_ASSESSMENT_OUTCOMES:
            raise PersistenceInputError("assessment_outcome is not a HypothesisAssessment outcome")
        if self.evaluator_kind not in ALLOWED_EVALUATOR_KINDS:
            raise PersistenceInputError("evaluator_kind is not a supported evaluator kind")
        if not isinstance(self.evaluator_version, str) or not self.evaluator_version.strip():
            raise PersistenceInputError("evaluator_version must be a non-empty string")
        if not isinstance(self.evaluation_strategy, str) or not self.evaluation_strategy.strip():
            raise PersistenceInputError("evaluation_strategy must be a non-empty string")
        if not isinstance(self.observation_ids, tuple):
            raise PersistenceInputError("observation_ids must be a tuple")
        cleaned_ids: list[str] = []
        for index, item in enumerate(self.observation_ids):
            cleaned_ids.append(require_opaque_id(item, f"observation_ids[{index}]"))
        object.__setattr__(self, "observation_ids", tuple(cleaned_ids))
        if not isinstance(self.rationale, Mapping):
            raise PersistenceInputError("rationale must be a mapping")
        found = ASSESSMENT_RATIONALE_FORBIDDEN_KEYS.intersection(self.rationale.keys())
        if found:
            raise PersistenceInputError(
                f"assessment rationale must not contain {sorted(found)}"
            )
        _reject_secret_keys(self.rationale, "rationale")
        object.__setattr__(self, "rationale", dict(self.rationale))


@dataclass(frozen=True)
class EvidenceRecord:
    """Append-only admitted Evidence. Not Candidate, Finding, or Verification."""

    evidence_id: str
    research_run_id: str
    hypothesis_id: str
    experiment_id: str
    admission_record_id: str
    polarity: str
    claim_scope: str
    observation_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.evidence_id, "evidence_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.admission_record_id, "admission_record_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.polarity not in ALLOWED_EVIDENCE_POLARITIES:
            raise PersistenceInputError("polarity is not an Evidence polarity")
        if not isinstance(self.claim_scope, str) or not self.claim_scope.strip():
            raise PersistenceInputError("claim_scope must be a non-empty string")
        if not isinstance(self.observation_ids, tuple) or not self.observation_ids:
            raise PersistenceInputError("observation_ids must be a non-empty tuple")
        cleaned: list[str] = []
        for index, item in enumerate(self.observation_ids):
            cleaned.append(require_opaque_id(item, f"observation_ids[{index}]"))
        object.__setattr__(self, "observation_ids", tuple(cleaned))
        if not isinstance(self.assessment_ids, tuple) or not self.assessment_ids:
            raise PersistenceInputError("assessment_ids must be a non-empty tuple")
        assessments: list[str] = []
        for index, item in enumerate(self.assessment_ids):
            assessments.append(require_opaque_id(item, f"assessment_ids[{index}]"))
        object.__setattr__(self, "assessment_ids", tuple(assessments))


@dataclass(frozen=True)
class EvidenceAdmissionRecord:
    """Append-only Evidence admission history. Rejected proposals create no Evidence."""

    admission_record_id: str
    proposal_id: str
    research_run_id: str
    outcome: str
    reason_codes: tuple[str, ...]
    observation_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    admission_policy_version: str
    evaluator_version: str
    created_at: datetime
    admitted_evidence_id: str | None = None
    claim_scope: str | None = None
    polarity: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.admission_record_id, "admission_record_id")
        require_opaque_id(self.proposal_id, "proposal_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.outcome not in ALLOWED_EVIDENCE_ADMISSION_OUTCOMES:
            raise PersistenceInputError("outcome is not an Evidence admission outcome")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise PersistenceInputError("reason_codes must be a non-empty tuple")
        if not isinstance(self.observation_ids, tuple):
            raise PersistenceInputError("observation_ids must be a tuple")
        if not isinstance(self.assessment_ids, tuple):
            raise PersistenceInputError("assessment_ids must be a tuple")
        if not isinstance(self.admission_policy_version, str) or not self.admission_policy_version.strip():
            raise PersistenceInputError("admission_policy_version must be a non-empty string")
        if not isinstance(self.evaluator_version, str) or not self.evaluator_version.strip():
            raise PersistenceInputError("evaluator_version must be a non-empty string")
        if self.outcome == "ADMITTED":
            require_opaque_id(self.admitted_evidence_id, "admitted_evidence_id")
        elif self.admitted_evidence_id is not None:
            raise PersistenceInputError(
                "admitted_evidence_id must be null when Evidence is not admitted"
            )
        if self.polarity is not None and self.polarity not in ALLOWED_EVIDENCE_POLARITIES:
            raise PersistenceInputError("polarity is not an Evidence polarity")
        if self.claim_scope is not None and (
            not isinstance(self.claim_scope, str) or not self.claim_scope.strip()
        ):
            raise PersistenceInputError("claim_scope must be a non-empty string when set")


@dataclass(frozen=True)
class CandidateRecord:
    """Potential security-testable claim. OPEN is not a vulnerability. VALIDATED is not a Finding."""

    candidate_id: str
    research_run_id: str
    hypothesis_id: str
    claim: str
    classification: str
    state: str
    evidence_ids: tuple[str, ...]
    created_at: datetime
    admission_record_id: str

    def __post_init__(self) -> None:
        require_opaque_id(self.candidate_id, "candidate_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.admission_record_id, "admission_record_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise PersistenceInputError("claim must be a non-empty string")
        if self.classification not in ALLOWED_CANDIDATE_CLASSIFICATIONS:
            raise PersistenceInputError("classification is not an allowed Candidate class")
        if self.state not in ALLOWED_CANDIDATE_STATES:
            raise PersistenceInputError("state is not a Candidate lifecycle state")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise PersistenceInputError("evidence_ids must be a non-empty tuple")
        cleaned: list[str] = []
        for index, item in enumerate(self.evidence_ids):
            cleaned.append(require_opaque_id(item, f"evidence_ids[{index}]"))
        object.__setattr__(self, "evidence_ids", tuple(cleaned))


@dataclass(frozen=True)
class CandidateAdmissionRecord:
    """Append-only Candidate admission history. Rejected proposals create no Candidate."""

    admission_record_id: str
    proposal_id: str
    research_run_id: str
    outcome: str
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    admission_policy_version: str
    created_at: datetime
    admitted_candidate_id: str | None = None
    claim: str | None = None
    classification: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.admission_record_id, "admission_record_id")
        require_opaque_id(self.proposal_id, "proposal_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.outcome not in ALLOWED_CANDIDATE_ADMISSION_OUTCOMES:
            raise PersistenceInputError("outcome is not a Candidate admission outcome")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise PersistenceInputError("reason_codes must be a non-empty tuple")
        if not isinstance(self.evidence_ids, tuple):
            raise PersistenceInputError("evidence_ids must be a tuple")
        cleaned: list[str] = []
        for index, item in enumerate(self.evidence_ids):
            cleaned.append(require_opaque_id(item, f"evidence_ids[{index}]"))
        object.__setattr__(self, "evidence_ids", tuple(cleaned))
        if not isinstance(self.admission_policy_version, str) or not self.admission_policy_version.strip():
            raise PersistenceInputError("admission_policy_version must be a non-empty string")
        if self.outcome == "ADMITTED":
            require_opaque_id(self.admitted_candidate_id, "admitted_candidate_id")
        elif self.admitted_candidate_id is not None:
            raise PersistenceInputError(
                "admitted_candidate_id must be null when Candidate is not admitted"
            )
        if self.classification is not None and self.classification not in ALLOWED_CANDIDATE_CLASSIFICATIONS:
            raise PersistenceInputError("classification is not an allowed Candidate class")
        if self.claim is not None and (not isinstance(self.claim, str) or not self.claim.strip()):
            raise PersistenceInputError("claim must be a non-empty string when set")


@dataclass(frozen=True)
class VerificationRecord:
    """Append-only Verification process record. Does not commit Candidate state by itself."""

    verification_id: str
    candidate_id: str
    research_run_id: str
    strategy: str
    outcome: str
    proposed_candidate_state: str
    original_evidence_ids: tuple[str, ...]
    reproduction_evidence_ids: tuple[str, ...]
    negative_control_evidence_ids: tuple[str, ...]
    alternative_explanation_checks: Mapping[str, Any]
    verifier_kind: str
    verifier_identity: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.verification_id, "verification_id")
        require_opaque_id(self.candidate_id, "candidate_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.strategy, str) or not self.strategy.strip():
            raise PersistenceInputError("strategy must be a non-empty string")
        if self.outcome not in ALLOWED_VERIFICATION_OUTCOMES:
            raise PersistenceInputError("outcome is not a Verification outcome")
        if self.proposed_candidate_state not in ALLOWED_CANDIDATE_STATES:
            raise PersistenceInputError("proposed_candidate_state is not a Candidate state")
        if self.proposed_candidate_state != self.outcome:
            raise PersistenceInputError("proposed_candidate_state must match outcome")
        if self.verifier_kind not in ALLOWED_VERIFIER_KINDS:
            raise PersistenceInputError("verifier_kind is not an allowed verifier kind")
        if not isinstance(self.verifier_identity, str) or not self.verifier_identity.strip():
            raise PersistenceInputError("verifier_identity must be a non-empty string")
        if not isinstance(self.original_evidence_ids, tuple) or not self.original_evidence_ids:
            raise PersistenceInputError("original_evidence_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "original_evidence_ids",
            tuple(
                require_opaque_id(item, f"original_evidence_ids[{index}]")
                for index, item in enumerate(self.original_evidence_ids)
            ),
        )
        if not isinstance(self.reproduction_evidence_ids, tuple):
            raise PersistenceInputError("reproduction_evidence_ids must be a tuple")
        object.__setattr__(
            self,
            "reproduction_evidence_ids",
            tuple(
                require_opaque_id(item, f"reproduction_evidence_ids[{index}]")
                for index, item in enumerate(self.reproduction_evidence_ids)
            ),
        )
        if not isinstance(self.negative_control_evidence_ids, tuple):
            raise PersistenceInputError("negative_control_evidence_ids must be a tuple")
        object.__setattr__(
            self,
            "negative_control_evidence_ids",
            tuple(
                require_opaque_id(item, f"negative_control_evidence_ids[{index}]")
                for index, item in enumerate(self.negative_control_evidence_ids)
            ),
        )
        if not isinstance(self.alternative_explanation_checks, Mapping):
            raise PersistenceInputError("alternative_explanation_checks must be a mapping")
        found = CANDIDATE_FORBIDDEN_KEYS.intersection(self.alternative_explanation_checks.keys())
        if found:
            raise PersistenceInputError(
                f"alternative_explanation_checks must not contain {sorted(found)}"
            )
        _reject_secret_keys(self.alternative_explanation_checks, "alternative_explanation_checks")
        object.__setattr__(
            self,
            "alternative_explanation_checks",
            dict(self.alternative_explanation_checks),
        )


@dataclass(frozen=True)
class PromotionRunRecord:
    """Durable PromotionPipeline provenance. Not Evidence, Candidate, or Finding.

    Mutable stage machine. Authoritative Evidence/Candidate/Verification/
    FindingProposal rows remain in their own tables; this record only tracks
    which stage of the composed pipeline owns a given assessment.
    """

    promotion_run_id: str
    research_run_id: str
    assessment_id: str
    original_experiment_id: str
    stage: str
    created_at: datetime
    updated_at: datetime
    evidence_id: str | None = None
    candidate_id: str | None = None
    verification_id: str | None = None
    finding_proposal_id: str | None = None
    reproduction_experiment_id: str | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.promotion_run_id, "promotion_run_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.assessment_id, "assessment_id")
        require_opaque_id(self.original_experiment_id, "original_experiment_id")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
        if self.stage not in ALLOWED_PROMOTION_STAGES:
            raise PersistenceInputError("stage is not a PromotionPipeline stage")
        require_optional_opaque_id(self.evidence_id, "evidence_id")
        require_optional_opaque_id(self.candidate_id, "candidate_id")
        require_optional_opaque_id(self.verification_id, "verification_id")
        require_optional_opaque_id(self.finding_proposal_id, "finding_proposal_id")
        require_optional_opaque_id(
            self.reproduction_experiment_id, "reproduction_experiment_id"
        )
        if self.stop_reason is not None and (
            not isinstance(self.stop_reason, str) or not self.stop_reason.strip()
        ):
            raise PersistenceInputError("stop_reason must be a non-empty string when set")


@dataclass(frozen=True)
class FindingProposalRecord:
    """Reviewable proposal. APPROVED is not a Finding. Content is immutable after insert."""

    proposal_id: str
    candidate_id: str
    research_run_id: str
    title: str
    claim: str
    classification: str
    state: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    content_fingerprint: str
    created_at: datetime
    impact_chain_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_opaque_id(self.proposal_id, "proposal_id")
        require_opaque_id(self.candidate_id, "candidate_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.title, str) or not self.title.strip():
            raise PersistenceInputError("title must be a non-empty string")
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise PersistenceInputError("claim must be a non-empty string")
        if self.classification not in ALLOWED_FINDING_CLASSIFICATIONS:
            raise PersistenceInputError("classification is not an allowed Finding class")
        if self.state not in ALLOWED_FINDING_PROPOSAL_STATES:
            raise PersistenceInputError("state is not a FindingProposal lifecycle state")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise PersistenceInputError("evidence_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(
                require_opaque_id(item, f"evidence_ids[{index}]")
                for index, item in enumerate(self.evidence_ids)
            ),
        )
        if not isinstance(self.verification_ids, tuple) or not self.verification_ids:
            raise PersistenceInputError("verification_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "verification_ids",
            tuple(
                require_opaque_id(item, f"verification_ids[{index}]")
                for index, item in enumerate(self.verification_ids)
            ),
        )
        if not isinstance(self.content_fingerprint, str) or not self.content_fingerprint.strip():
            raise PersistenceInputError("content_fingerprint must be a non-empty string")
        if not isinstance(self.impact_chain_ids, tuple):
            raise PersistenceInputError("impact_chain_ids must be a tuple")
        object.__setattr__(
            self,
            "impact_chain_ids",
            tuple(
                require_opaque_id(item, f"impact_chain_ids[{index}]")
                for index, item in enumerate(self.impact_chain_ids)
            ),
        )


@dataclass(frozen=True)
class HumanReviewRecord:
    """Append-only human review. Not Core Approval and not a Finding."""

    review_id: str
    proposal_id: str
    content_fingerprint: str
    decision: str
    reviewer_id: str
    actor_type: str
    reason_codes: tuple[str, ...]
    created_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.review_id, "review_id")
        require_opaque_id(self.proposal_id, "proposal_id")
        require_opaque_id(self.reviewer_id, "reviewer_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.content_fingerprint, str) or not self.content_fingerprint.strip():
            raise PersistenceInputError("content_fingerprint must be a non-empty string")
        if self.decision not in ALLOWED_HUMAN_REVIEW_DECISIONS:
            raise PersistenceInputError("decision is not a HumanReview decision")
        if self.actor_type not in ALLOWED_ACTOR_TYPES:
            raise PersistenceInputError("actor_type must be a locked identity class")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise PersistenceInputError("reason_codes must be a non-empty tuple")
        if self.note is not None and (not isinstance(self.note, str) or not self.note.strip()):
            raise PersistenceInputError("note must be a non-empty string when set")


@dataclass(frozen=True)
class ApprovalRecord:
    """Append-only Core Approval record. Not AuditEvent and not Finding truth."""

    approval_id: str
    subject_reference: str
    decision: str
    decided_by: str
    actor_type: str
    recorded: bool
    created_at: datetime
    research_run_id: str
    proposal_id: str
    human_review_id: str

    def __post_init__(self) -> None:
        require_opaque_id(self.approval_id, "approval_id")
        require_opaque_id(self.subject_reference, "subject_reference")
        require_opaque_id(self.decided_by, "decided_by")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.proposal_id, "proposal_id")
        require_opaque_id(self.human_review_id, "human_review_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.decision not in ALLOWED_RECORDED_APPROVAL_DECISIONS:
            raise PersistenceInputError("decision is not an Approval decision")
        if self.actor_type not in ALLOWED_ACTOR_TYPES:
            raise PersistenceInputError("actor_type must be a locked identity class")
        if not isinstance(self.recorded, bool):
            raise PersistenceInputError("recorded must be bool")
        if not self.recorded:
            raise PersistenceInputError("persisted Approval must be recorded")


@dataclass(frozen=True)
class FindingRecord:
    """Append-only accepted research result. Diagnostic plumbing is not a vulnerability."""

    finding_id: str
    finding_proposal_id: str
    candidate_id: str
    research_run_id: str
    approval_id: str
    human_review_id: str
    title: str
    claim: str
    classification: str
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.finding_id, "finding_id")
        require_opaque_id(self.finding_proposal_id, "finding_proposal_id")
        require_opaque_id(self.candidate_id, "candidate_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.approval_id, "approval_id")
        require_opaque_id(self.human_review_id, "human_review_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.title, str) or not self.title.strip():
            raise PersistenceInputError("title must be a non-empty string")
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise PersistenceInputError("claim must be a non-empty string")
        if self.classification not in ALLOWED_FINDING_CLASSIFICATIONS:
            raise PersistenceInputError("classification is not an allowed Finding class")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise PersistenceInputError("evidence_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(
                require_opaque_id(item, f"evidence_ids[{index}]")
                for index, item in enumerate(self.evidence_ids)
            ),
        )
        if not isinstance(self.verification_ids, tuple) or not self.verification_ids:
            raise PersistenceInputError("verification_ids must be a non-empty tuple")
        object.__setattr__(
            self,
            "verification_ids",
            tuple(
                require_opaque_id(item, f"verification_ids[{index}]")
                for index, item in enumerate(self.verification_ids)
            ),
        )


@dataclass(frozen=True)
class TargetInferenceRecord:
    """Append-only inferred/hypothesized target-model element. Not Observation and not SoR fact."""

    inference_id: str
    research_run_id: str
    kind: str
    epistemic_status: str
    opaque_ref: str
    statement: str
    source_refs: tuple[str, ...]
    attributes: Mapping[str, Any]
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.inference_id, "inference_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.opaque_ref, "opaque_ref")
        require_aware_datetime(self.created_at, "created_at")
        if self.kind not in ALLOWED_TARGET_ELEMENT_KINDS:
            raise PersistenceInputError("kind is not a target-model element kind")
        if self.epistemic_status not in ALLOWED_TARGET_INFERENCE_STATUSES:
            raise PersistenceInputError("epistemic_status must remain INFERRED or HYPOTHESIZED")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise PersistenceInputError("statement must be a non-empty string")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise PersistenceInputError("source_refs must be a non-empty tuple")
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise PersistenceInputError("strategy_version must be a non-empty string")
        if not isinstance(self.attributes, Mapping):
            raise PersistenceInputError("attributes must be a mapping")
        found = TARGET_MODEL_SECRET_KEYS.intersection(self.attributes.keys())
        if found:
            raise PersistenceInputError(
                f"attributes must not contain secret-value keys: {sorted(found)}"
            )
        object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class DifferentialObservationRecord:
    """Append-only differential comparison. Not Evidence, Candidate, or Finding."""

    differential_id: str
    research_run_id: str
    case_id: str
    baseline_observation_ids: tuple[str, ...]
    variant_observation_ids: tuple[str, ...]
    changed_dimensions: tuple[str, ...]
    common_dimensions: tuple[str, ...]
    observed_differences: Mapping[str, Any]
    observed_similarities: Mapping[str, Any]
    interpretation: str
    source_refs: tuple[str, ...]
    strategy_version: str
    alternative_explanation_slots: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.differential_id, "differential_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.case_id, "case_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.interpretation not in ALLOWED_DIFFERENTIAL_INTERPRETATIONS:
            raise PersistenceInputError("interpretation is not a differential interpretation")
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise PersistenceInputError("strategy_version must be a non-empty string")
        for field_name, value in (
            ("baseline_observation_ids", self.baseline_observation_ids),
            ("variant_observation_ids", self.variant_observation_ids),
            ("changed_dimensions", self.changed_dimensions),
            ("common_dimensions", self.common_dimensions),
            ("source_refs", self.source_refs),
        ):
            if not isinstance(value, tuple) or not value:
                raise PersistenceInputError(f"{field_name} must be a non-empty tuple")
            object.__setattr__(
                self,
                field_name,
                tuple(
                    require_opaque_id(item, f"{field_name}[{index}]")
                    for index, item in enumerate(value)
                ),
            )
        if not isinstance(self.alternative_explanation_slots, tuple):
            raise PersistenceInputError("alternative_explanation_slots must be a tuple")
        object.__setattr__(
            self,
            "alternative_explanation_slots",
            tuple(
                require_opaque_id(item, f"alternative_explanation_slots[{index}]")
                for index, item in enumerate(self.alternative_explanation_slots)
            ),
        )
        if not isinstance(self.observed_differences, Mapping):
            raise PersistenceInputError("observed_differences must be a mapping")
        if not isinstance(self.observed_similarities, Mapping):
            raise PersistenceInputError("observed_similarities must be a mapping")
        for field_name, payload in (
            ("observed_differences", self.observed_differences),
            ("observed_similarities", self.observed_similarities),
        ):
            found = TARGET_MODEL_SECRET_KEYS.intersection(payload.keys())
            if found:
                raise PersistenceInputError(
                    f"{field_name} must not contain secret-value keys: {sorted(found)}"
                )
        object.__setattr__(self, "observed_differences", dict(self.observed_differences))
        object.__setattr__(self, "observed_similarities", dict(self.observed_similarities))


@dataclass(frozen=True)
class InvariantHypothesisRecord:
    """Expected-behavior hypothesis. Not a fact, ScopeRule, Evidence, or Finding."""

    invariant_id: str
    research_run_id: str
    invariant_kind: str
    status: str
    subject_refs: tuple[str, ...]
    expected_behavior: str
    source_refs: tuple[str, ...]
    applicability_context: Mapping[str, Any]
    assumptions: tuple[str, ...]
    counterexample_refs: tuple[str, ...]
    falsification_direction: str
    proposer_provenance: str
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.invariant_id, "invariant_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.invariant_kind not in ALLOWED_INVARIANT_KINDS:
            raise PersistenceInputError("invariant_kind is not an expectation class")
        if self.status not in ALLOWED_INVARIANT_STATUSES:
            raise PersistenceInputError("status is not an invariant hypothesis status")
        if not isinstance(self.expected_behavior, str) or not self.expected_behavior.strip():
            raise PersistenceInputError("expected_behavior must be a non-empty string")
        if not isinstance(self.falsification_direction, str) or not self.falsification_direction.strip():
            raise PersistenceInputError("falsification_direction must be a non-empty string")
        if not isinstance(self.proposer_provenance, str) or not self.proposer_provenance.strip():
            raise PersistenceInputError("proposer_provenance must be a non-empty string")
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise PersistenceInputError("strategy_version must be a non-empty string")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise PersistenceInputError("source_refs must be a non-empty tuple")
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        object.__setattr__(
            self,
            "subject_refs",
            tuple(
                require_opaque_id(item, f"subject_refs[{index}]")
                for index, item in enumerate(self.subject_refs)
            ),
        )
        object.__setattr__(
            self,
            "counterexample_refs",
            tuple(
                require_opaque_id(item, f"counterexample_refs[{index}]")
                for index, item in enumerate(self.counterexample_refs)
            ),
        )
        object.__setattr__(
            self,
            "assumptions",
            tuple(
                require_opaque_id(item, f"assumptions[{index}]")
                for index, item in enumerate(self.assumptions)
            ),
        )
        if not isinstance(self.applicability_context, Mapping):
            raise PersistenceInputError("applicability_context must be a mapping")
        found = TARGET_MODEL_SECRET_KEYS.intersection(self.applicability_context.keys())
        if found:
            raise PersistenceInputError(
                f"applicability_context must not contain secret-value keys: {sorted(found)}"
            )
        object.__setattr__(self, "applicability_context", dict(self.applicability_context))


@dataclass(frozen=True)
class InvariantSourceRefRecord:
    """Append-only invariant source. Not Observation truth."""

    invariant_id: str
    source_ref: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.invariant_id, "invariant_id")
        require_opaque_id(self.source_ref, "source_ref")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class InvariantCounterexampleRefRecord:
    """Append-only context-bound contradiction. Not a global disproof."""

    counterexample_id: str
    invariant_id: str
    source_ref: str
    applicability_context: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.counterexample_id, "counterexample_id")
        require_opaque_id(self.invariant_id, "invariant_id")
        require_opaque_id(self.source_ref, "source_ref")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.applicability_context, Mapping):
            raise PersistenceInputError("applicability_context must be a mapping")
        object.__setattr__(self, "applicability_context", dict(self.applicability_context))


@dataclass(frozen=True)
class ChainHypothesisRecord:
    """Append-only chain hypothesis. Not Evidence, Candidate, Finding, or an exploit."""

    chain_id: str
    research_run_id: str
    structural_identity: str
    steps: tuple[Mapping[str, Any], ...]
    source_refs: tuple[str, ...]
    preconditions: tuple[str, ...]
    expected_resulting_capability: str
    unresolved_assumptions: tuple[str, ...]
    falsification_points: tuple[str, ...]
    descriptive_features: Mapping[str, Any]
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.chain_id, "chain_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.structural_identity, "structural_identity")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.steps, tuple) or len(self.steps) < 2:
            raise PersistenceInputError("steps must contain at least two mappings")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        if not self.source_refs:
            raise PersistenceInputError("source_refs must be a non-empty tuple")
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        if not isinstance(self.expected_resulting_capability, str) or not self.expected_resulting_capability.strip():
            raise PersistenceInputError("expected_resulting_capability must be a non-empty string")
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise PersistenceInputError("strategy_version must be a non-empty string")
        if not isinstance(self.descriptive_features, Mapping):
            raise PersistenceInputError("descriptive_features must be a mapping")
        found = TARGET_MODEL_SECRET_KEYS.intersection(self.descriptive_features.keys())
        if found:
            raise PersistenceInputError(
                f"descriptive_features must not contain secret-value keys: {sorted(found)}"
            )
        object.__setattr__(self, "descriptive_features", dict(self.descriptive_features))
        object.__setattr__(self, "steps", tuple(dict(step) for step in self.steps))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        object.__setattr__(self, "unresolved_assumptions", tuple(self.unresolved_assumptions))
        object.__setattr__(self, "falsification_points", tuple(self.falsification_points))


@dataclass(frozen=True)
class ResearchOpportunityRecord:
    """Selected or considered research direction. Not Hypothesis truth and not authorization."""

    opportunity_id: str
    research_run_id: str
    opportunity_kind: str
    mode: str
    source_refs: tuple[str, ...]
    proposed_direction: str
    unresolved_question: str
    expected_information_value_description: str
    assumptions: tuple[str, ...]
    dimensions: Mapping[str, Any]
    context_signature: str
    novelty_composition_marker: bool
    prior_attempt_refs: tuple[str, ...]
    structural_identity: str
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.opportunity_id, "opportunity_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.structural_identity, "structural_identity")
        require_aware_datetime(self.created_at, "created_at")
        if self.opportunity_kind not in ALLOWED_OPPORTUNITY_KINDS:
            raise PersistenceInputError("opportunity_kind is not a research workflow category")
        if self.mode not in ALLOWED_OPPORTUNITY_MODES:
            raise PersistenceInputError("mode is not exploration or exploitation")
        if not isinstance(self.proposed_direction, str) or not self.proposed_direction.strip():
            raise PersistenceInputError("proposed_direction must be a non-empty string")
        if not isinstance(self.unresolved_question, str) or not self.unresolved_question.strip():
            raise PersistenceInputError("unresolved_question must be a non-empty string")
        if not isinstance(self.dimensions, Mapping):
            raise PersistenceInputError("dimensions must be a mapping")
        found = FINDING_FORBIDDEN_KEYS.intersection(self.dimensions.keys())
        if found:
            raise PersistenceInputError(f"dimensions must not contain {sorted(found)}")
        if "priority_score" in self.dimensions or "weighted_score" in self.dimensions:
            raise PersistenceInputError("dimensions must not contain a priority score")
        object.__setattr__(self, "dimensions", dict(self.dimensions))
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        if not self.source_refs:
            raise PersistenceInputError("source_refs must be a non-empty tuple")
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        object.__setattr__(self, "prior_attempt_refs", tuple(self.prior_attempt_refs))
        object.__setattr__(
            self, "context_signature", require_opaque_id(self.context_signature, "context_signature")
        )
        object.__setattr__(
            self, "strategy_version", require_opaque_id(self.strategy_version, "strategy_version")
        )


@dataclass(frozen=True)
class ResearchSelectionRecord:
    """Append-only selection decision. Not Core authorization."""

    selection_id: str
    research_run_id: str
    opportunity_id: str
    outcome: str
    reason_codes: tuple[str, ...]
    structural_identity: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.selection_id, "selection_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.opportunity_id, "opportunity_id")
        require_opaque_id(self.structural_identity, "structural_identity")
        require_aware_datetime(self.created_at, "created_at")
        if self.outcome not in ALLOWED_SELECTION_OUTCOMES:
            raise PersistenceInputError("outcome is not a selection outcome")
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))


@dataclass(frozen=True)
class OpportunitySelectionCandidateRecord:
    """A durable, pre-admission proposal for `ResearchOpportunityRecord`.

    Bridges a non-diagnostic opportunity producer (e.g. Hunter/Coverage) to the
    single `SelectResearchOpportunities` admission path without becoming a
    second `ResearchOpportunity` authority: it holds exactly what is needed to
    reconstruct one candidate `ResearchOpportunity` for the pure selector, plus
    a one-way `PENDING -> ADMITTED|NOT_ADMITTED` decision outcome recorded by
    that same selector. It never itself becomes the canonical opportunity
    record -- an ADMITTED candidate's canonical form is the
    `ResearchOpportunityRecord` referenced by `resulting_opportunity_id`.
    """

    candidate_id: str
    research_run_id: str
    source_system: str
    opportunity_kind: str
    mode: str
    source_refs: tuple[str, ...]
    proposed_direction: str
    unresolved_question: str
    expected_information_value_description: str
    assumptions: tuple[str, ...]
    dimensions: Mapping[str, Any]
    context_signature: str
    structural_identity: str
    strategy_version: str
    created_at: datetime
    outcome: str = "PENDING"
    resulting_opportunity_id: str | None = None
    decided_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.candidate_id, "candidate_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.structural_identity, "structural_identity")
        require_aware_datetime(self.created_at, "created_at")
        if self.source_system not in ALLOWED_CANDIDATE_SOURCE_SYSTEMS:
            raise PersistenceInputError("source_system is not a recognized candidate producer")
        if self.opportunity_kind not in ALLOWED_OPPORTUNITY_KINDS:
            raise PersistenceInputError("opportunity_kind is not a research workflow category")
        if self.mode not in ALLOWED_OPPORTUNITY_MODES:
            raise PersistenceInputError("mode is not exploration or exploitation")
        if not isinstance(self.proposed_direction, str) or not self.proposed_direction.strip():
            raise PersistenceInputError("proposed_direction must be a non-empty string")
        if not isinstance(self.unresolved_question, str) or not self.unresolved_question.strip():
            raise PersistenceInputError("unresolved_question must be a non-empty string")
        if not isinstance(self.dimensions, Mapping):
            raise PersistenceInputError("dimensions must be a mapping")
        found = FINDING_FORBIDDEN_KEYS.intersection(self.dimensions.keys())
        if found:
            raise PersistenceInputError(f"dimensions must not contain {sorted(found)}")
        if "priority_score" in self.dimensions or "weighted_score" in self.dimensions:
            raise PersistenceInputError("dimensions must not contain a priority score")
        object.__setattr__(self, "dimensions", dict(self.dimensions))
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        if not self.source_refs:
            raise PersistenceInputError("source_refs must be a non-empty tuple")
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        object.__setattr__(
            self, "context_signature", require_opaque_id(self.context_signature, "context_signature")
        )
        object.__setattr__(
            self, "strategy_version", require_opaque_id(self.strategy_version, "strategy_version")
        )
        if self.outcome not in ALLOWED_CANDIDATE_OUTCOMES:
            raise PersistenceInputError("outcome is not a recognized candidate outcome")
        object.__setattr__(
            self,
            "resulting_opportunity_id",
            require_optional_opaque_id(self.resulting_opportunity_id, "resulting_opportunity_id"),
        )
        if self.outcome == "PENDING" and self.resulting_opportunity_id is not None:
            raise PersistenceInputError("a PENDING candidate must not have a resulting_opportunity_id")
        if self.outcome == "PENDING" and self.decided_at is not None:
            raise PersistenceInputError("a PENDING candidate must not have decided_at")
        if self.outcome != "PENDING":
            require_aware_datetime(self.decided_at, "decided_at")
        if self.outcome == "ADMITTED" and self.resulting_opportunity_id is None:
            raise PersistenceInputError("an ADMITTED candidate must carry resulting_opportunity_id")
        if self.outcome == "NOT_ADMITTED" and self.resulting_opportunity_id is not None:
            raise PersistenceInputError("a NOT_ADMITTED candidate must not carry resulting_opportunity_id")


@dataclass(frozen=True)
class SnapshotRecord:
    """Immutable point-in-time research view by reference. Not a full SoR copy."""

    snapshot_id: str
    research_run_id: str
    program_id: str
    target_identity: str
    captured_at: datetime
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.snapshot_id, "snapshot_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.program_id, "program_id")
        require_opaque_id(self.target_identity, "target_identity")
        require_aware_datetime(self.captured_at, "captured_at")
        require_aware_datetime(self.created_at, "created_at")
        object.__setattr__(
            self, "strategy_version", require_opaque_id(self.strategy_version, "strategy_version")
        )


@dataclass(frozen=True)
class SnapshotMemberRecord:
    """Append-only snapshot observation reference. Not Observation truth."""

    snapshot_id: str
    observation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.snapshot_id, "snapshot_id")
        require_opaque_id(self.observation_id, "observation_id")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class ChangeEventRecord:
    """Append-only deterministic snapshot delta. Not Evidence or a vulnerability."""

    change_event_id: str
    research_run_id: str
    baseline_snapshot_id: str
    variant_snapshot_id: str
    category: str
    statement: str
    source_refs: tuple[str, ...]
    strategy_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.change_event_id, "change_event_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.baseline_snapshot_id, "baseline_snapshot_id")
        require_opaque_id(self.variant_snapshot_id, "variant_snapshot_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.category not in ALLOWED_CHANGE_CATEGORIES:
            raise PersistenceInputError("category is not a ChangeEvent category")
        if self.category == "VULNERABILITY_INTRODUCED":
            raise PersistenceInputError("vulnerability introduction is not a ChangeEvent category")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise PersistenceInputError("statement must be a non-empty string")
        object.__setattr__(
            self,
            "source_refs",
            tuple(
                require_opaque_id(item, f"source_refs[{index}]")
                for index, item in enumerate(self.source_refs)
            ),
        )
        object.__setattr__(
            self, "strategy_version", require_opaque_id(self.strategy_version, "strategy_version")
        )


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    research_run_id: str
    hypothesis_id: str
    budget_id: str
    execution_state: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.budget_id, "budget_id")
        require_aware_datetime(self.created_at, "created_at")
        if self.execution_state not in ALLOWED_EXPERIMENT_STATES:
            raise PersistenceInputError("execution_state is not a domain execution state")


@dataclass(frozen=True)
class ExecutionAttemptRecord:
    """Durable intended Worker invocation. Not a WorkerResult and not Evidence."""

    attempt_id: str
    request_id: str
    experiment_id: str
    research_run_id: str
    correlation_id: str
    worker_capability: str
    action: str
    target_reference: str
    budget_id: str
    side_effect_level: int
    authorization_decision_reference: str
    state: str
    created_at: datetime
    authorized_at: datetime | None = None
    dispatch_started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.attempt_id, "attempt_id")
        require_opaque_id(self.request_id, "request_id")
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.correlation_id, "correlation_id")
        require_opaque_id(self.worker_capability, "worker_capability")
        require_opaque_id(self.action, "action")
        require_opaque_id(self.target_reference, "target_reference")
        require_opaque_id(self.budget_id, "budget_id")
        require_opaque_id(
            self.authorization_decision_reference, "authorization_decision_reference"
        )
        require_aware_datetime(self.created_at, "created_at")
        if self.side_effect_level not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect_level must be 0, 1, 2, or 3")
        if self.state not in ALLOWED_EXECUTION_ATTEMPT_STATES:
            raise PersistenceInputError("state is not an ExecutionAttempt state")
        if self.authorized_at is not None:
            require_aware_datetime(self.authorized_at, "authorized_at")
        if self.dispatch_started_at is not None:
            require_aware_datetime(self.dispatch_started_at, "dispatch_started_at")
        if self.completed_at is not None:
            require_aware_datetime(self.completed_at, "completed_at")


@dataclass(frozen=True)
class WorkerResultRecord:
    """UNTRUSTED EXECUTION OUTPUT. Insert does not create Observation or Evidence."""

    worker_result_id: str
    experiment_id: str
    research_run_id: str
    request_id: str
    correlation_id: str
    worker_capability: str
    action: str
    authorization_decision_reference: str
    budget_id: str
    side_effect_level: int
    contract_version: str
    worker_id: str
    status: str
    received_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    parent_request_id: str | None = None
    raw_result: Mapping[str, Any] | None = None
    raw_artifact_descriptors: list[Mapping[str, Any]] | None = None
    diagnostics: Mapping[str, Any] | None = None
    control_signal: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.worker_result_id, "worker_result_id")
        require_opaque_id(self.experiment_id, "experiment_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.request_id, "request_id")
        require_opaque_id(self.correlation_id, "correlation_id")
        require_opaque_id(self.worker_capability, "worker_capability")
        require_opaque_id(self.action, "action")
        require_opaque_id(
            self.authorization_decision_reference, "authorization_decision_reference"
        )
        require_opaque_id(self.budget_id, "budget_id")
        require_opaque_id(self.worker_id, "worker_id")
        require_aware_datetime(self.received_at, "received_at")
        if not isinstance(self.contract_version, str) or not self.contract_version.strip():
            raise PersistenceInputError("contract_version must be a non-empty string")
        if self.status not in ALLOWED_WORKER_RESULT_STATUSES:
            raise PersistenceInputError("status is not a WorkerResult execution status")
        if self.side_effect_level not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect_level must be 0, 1, 2, or 3")
        if self.started_at is not None:
            require_aware_datetime(self.started_at, "started_at")
        if self.completed_at is not None:
            require_aware_datetime(self.completed_at, "completed_at")
        if self.parent_request_id is not None:
            require_opaque_id(self.parent_request_id, "parent_request_id")
        object.__setattr__(
            self, "raw_result", _optional_mapping(self.raw_result, "raw_result")
        )
        object.__setattr__(
            self, "diagnostics", _optional_mapping(self.diagnostics, "diagnostics")
        )
        object.__setattr__(
            self,
            "control_signal",
            _optional_mapping(self.control_signal, "control_signal"),
        )
        if self.raw_artifact_descriptors is not None:
            if not isinstance(self.raw_artifact_descriptors, list):
                raise PersistenceInputError("raw_artifact_descriptors must be a list")
            cleaned: list[Mapping[str, Any]] = []
            for index, item in enumerate(self.raw_artifact_descriptors):
                mapped = _optional_mapping(item, f"raw_artifact_descriptors[{index}]")
                if mapped is None:
                    raise PersistenceInputError("raw_artifact_descriptors items must be mappings")
                cleaned.append(mapped)
            object.__setattr__(self, "raw_artifact_descriptors", cleaned)


@dataclass(frozen=True)
class ObservationRecord:
    """Deterministic observed fact. Not a vulnerability and not Evidence."""

    observation_id: str
    worker_result_id: str
    observation_kind: str
    payload: Mapping[str, Any]
    normalization_version: str
    observed_at: datetime
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.observation_id, "observation_id")
        require_opaque_id(self.worker_result_id, "worker_result_id")
        require_aware_datetime(self.observed_at, "observed_at")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.observation_kind, str) or not self.observation_kind.strip():
            raise PersistenceInputError("observation_kind must be a non-empty string")
        if not isinstance(self.normalization_version, str) or not self.normalization_version.strip():
            raise PersistenceInputError("normalization_version must be a non-empty string")
        if not isinstance(self.payload, Mapping):
            raise PersistenceInputError("payload must be a mapping")
        _reject_secret_keys(self.payload, "payload")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class AuditEventRecord:
    """Append-only reconstructive history. Not Evidence. Not a log substitute."""

    audit_event_id: str
    occurred_at: datetime
    actor_id: str
    actor_type: str
    event_type: str
    subject_type: str
    subject_id: str
    payload: Mapping[str, Any]
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.audit_event_id, "audit_event_id")
        require_opaque_id(self.actor_id, "actor_id")
        require_opaque_id(self.subject_id, "subject_id")
        require_aware_datetime(self.occurred_at, "occurred_at")
        if self.actor_type not in ALLOWED_ACTOR_TYPES:
            raise PersistenceInputError("actor_type must be a locked identity class")
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise PersistenceInputError("event_type must be a non-empty string")
        if not isinstance(self.subject_type, str) or not self.subject_type.strip():
            raise PersistenceInputError("subject_type must be a non-empty string")
        if not isinstance(self.payload, Mapping):
            raise PersistenceInputError("payload must be a mapping")
        _reject_secret_keys(self.payload, "payload")
        object.__setattr__(self, "payload", dict(self.payload))
        if self.correlation_id is not None:
            require_opaque_id(self.correlation_id, "correlation_id")


ALLOWED_ORCHESTRATION_STATES = frozenset(
    {
        "READY",
        "RUNNING",
        "PAUSED",
        "WAITING_HUMAN",
        "BLOCKED",
        "BUDGET_EXHAUSTED",
        "COMPLETED",
        "FAILED_OPERATIONAL",
    }
)
# Mirrors research_os.research.orchestration.TERMINAL_ORCHESTRATION_STATES.
# Duplicated (not imported) because Data must not import Research decisions;
# keep both lists in sync if the set of terminal states ever changes.
TERMINAL_ORCHESTRATION_STATES = frozenset(
    {
        "COMPLETED",
        "BUDGET_EXHAUSTED",
        "FAILED_OPERATIONAL",
    }
)
ALLOWED_ORCHESTRATION_PHASES = frozenset(
    {
        "CYCLE_READY",
        "OPPORTUNITY_SELECTED",
        "HYPOTHESIS_ADMITTED",
        "EXPERIMENT_PLANNED",
        "AUTHORIZATION_REQUESTED",
        "ATTEMPT_AUTHORIZED",
        "DISPATCHING",
        "WORKER_RESULT_RECORDED",
        "TRANSITION_A_COMPLETE",
        "ASSESSMENT_COMPLETE",
        "TRANSITION_B_COMPLETE",
        "CYCLE_COMPLETE",
    }
)
ALLOWED_CYCLE_OUTCOMES = frozenset(
    {
        "CONTINUE",
        "PAUSE",
        "COMPLETE",
        "BLOCKED",
        "REQUIRE_HUMAN_REVIEW",
    }
)
ALLOWED_BUDGET_RESOURCE_TYPES = frozenset(
    {
        "MODEL_CALL",
        "MODEL_TOKENS_IN",
        "MODEL_TOKENS_OUT",
        "MODEL_ESCALATION_DECISION",
        "WORKER_INVOCATION",
        "REQUEST",
        "EXECUTION_TIME",
        "ARTIFACT_BYTES",
        "COST",
    }
)
ALLOWED_BUDGET_UNITS = frozenset(
    {
        "count",
        "milliseconds",
        "bytes",
    }
)
ALLOWED_SESSION_STATES = frozenset(
    {"NEW", "AUTHENTICATING", "ACTIVE", "EXPIRED", "REVOKED", "FAILED"}
)
ALLOWED_AUTHENTICATION_METHODS = frozenset({"HTTP_FORM_LOGIN"})


@dataclass(frozen=True)
class ResearchOrchestrationRecord:
    """Durable checkpoint for one ResearchRun controller. Not AuditEvent workflow state."""

    research_run_id: str
    state: str
    cycle_number: int
    last_phase: str
    policy_version: str
    max_cycles: int
    max_experiments: int
    max_model_calls: int
    max_worker_invocations: int
    max_elapsed_ms: int
    max_selected_opportunities: int
    max_runtime_fallback: int
    side_effect_ceiling: int
    allow_repeated_control_experiments: bool
    created_at: datetime
    updated_at: datetime
    checkpoint_at: datetime
    budget_id: str
    target_reference: str
    research_question: str
    configuration_fingerprint: str
    current_phase: str
    last_opportunity_id: str | None = None
    last_hypothesis_id: str | None = None
    last_experiment_id: str | None = None
    pause_reason: str | None = None
    stop_reason: str | None = None
    active_cycle_id: str | None = None
    last_attempt_id: str | None = None
    last_observation_id: str | None = None
    last_assessment_id: str | None = None
    last_worker_result_id: str | None = None
    routing_policy_version: str | None = None
    scope_fingerprint: str | None = None
    owner_runtime_instance_id: str | None = None
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.budget_id, "budget_id")
        if self.state not in ALLOWED_ORCHESTRATION_STATES:
            raise PersistenceInputError("state is not an orchestration state")
        require_non_negative_int(self.lease_epoch, "lease_epoch")
        require_non_negative_int(self.cycle_number, "cycle_number")
        require_non_negative_int(self.max_cycles, "max_cycles")
        require_non_negative_int(self.max_experiments, "max_experiments")
        require_non_negative_int(self.max_model_calls, "max_model_calls")
        require_non_negative_int(self.max_worker_invocations, "max_worker_invocations")
        require_non_negative_int(self.max_elapsed_ms, "max_elapsed_ms")
        require_non_negative_int(self.max_selected_opportunities, "max_selected_opportunities")
        require_non_negative_int(self.max_runtime_fallback, "max_runtime_fallback")
        if self.side_effect_ceiling not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect_ceiling must be 0, 1, 2, or 3")
        if not isinstance(self.last_phase, str) or not self.last_phase.strip():
            raise PersistenceInputError("last_phase must be a non-empty string")
        if self.current_phase not in ALLOWED_ORCHESTRATION_PHASES:
            raise PersistenceInputError("current_phase is not an orchestration phase")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise PersistenceInputError("policy_version must be a non-empty string")
        if not isinstance(self.allow_repeated_control_experiments, bool):
            raise PersistenceInputError("allow_repeated_control_experiments must be bool")
        if not isinstance(self.target_reference, str) or not self.target_reference.strip():
            raise PersistenceInputError("target_reference must be a non-empty string")
        if not isinstance(self.research_question, str) or not self.research_question.strip():
            raise PersistenceInputError("research_question must be a non-empty string")
        if (
            not isinstance(self.configuration_fingerprint, str)
            or len(self.configuration_fingerprint) != 64
        ):
            raise PersistenceInputError("configuration_fingerprint must be a SHA-256 hex digest")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
        require_aware_datetime(self.checkpoint_at, "checkpoint_at")
        require_optional_opaque_id(self.last_opportunity_id, "last_opportunity_id")
        require_optional_opaque_id(self.last_hypothesis_id, "last_hypothesis_id")
        require_optional_opaque_id(self.last_experiment_id, "last_experiment_id")
        require_optional_opaque_id(self.active_cycle_id, "active_cycle_id")
        require_optional_opaque_id(self.last_attempt_id, "last_attempt_id")
        require_optional_opaque_id(self.last_observation_id, "last_observation_id")
        require_optional_opaque_id(self.last_assessment_id, "last_assessment_id")
        require_optional_opaque_id(self.last_worker_result_id, "last_worker_result_id")
        if self.pause_reason is not None and (
            not isinstance(self.pause_reason, str) or not self.pause_reason.strip()
        ):
            raise PersistenceInputError("pause_reason must be a non-empty string when set")
        if self.stop_reason is not None and (
            not isinstance(self.stop_reason, str) or not self.stop_reason.strip()
        ):
            raise PersistenceInputError("stop_reason must be a non-empty string when set")
        if self.routing_policy_version is not None and (
            not isinstance(self.routing_policy_version, str)
            or not self.routing_policy_version.strip()
        ):
            raise PersistenceInputError("routing_policy_version must be a non-empty string when set")
        if self.scope_fingerprint is not None and (
            not isinstance(self.scope_fingerprint, str) or len(self.scope_fingerprint) != 64
        ):
            raise PersistenceInputError("scope_fingerprint must be a SHA-256 hex digest when set")


class LeaseAcquireOutcome(Enum):
    """Classification of an acquire_lease() attempt. Not a research decision."""

    ACQUIRED = "ACQUIRED"
    DENIED_TERMINAL = "DENIED_TERMINAL"
    DENIED_HELD_BY_OTHER = "DENIED_HELD_BY_OTHER"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class RuntimeInstanceRecord:
    """Durable identity of one research-osd process. Not research authority."""

    runtime_instance_id: str
    host_identity: str
    process_id: str
    engine_version: str
    status: str
    capabilities_summary: Mapping[str, Any]
    started_at: datetime
    last_seen_at: datetime
    stopped_at: datetime | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.runtime_instance_id, "runtime_instance_id")
        if not isinstance(self.host_identity, str) or not self.host_identity.strip():
            raise PersistenceInputError("host_identity must be a non-empty string")
        if not isinstance(self.process_id, str) or not self.process_id.strip():
            raise PersistenceInputError("process_id must be a non-empty string")
        if not isinstance(self.engine_version, str) or not self.engine_version.strip():
            raise PersistenceInputError("engine_version must be a non-empty string")
        if self.status not in {"STARTING", "RUNNING", "DRAINING", "STOPPED"}:
            raise PersistenceInputError("status is not a valid runtime_instance status")
        object.__setattr__(self, "capabilities_summary", dict(self.capabilities_summary))
        reject_secret_structure(self.capabilities_summary, "capabilities_summary")
        require_aware_datetime(self.started_at, "started_at")
        require_aware_datetime(self.last_seen_at, "last_seen_at")
        if self.stopped_at is not None:
            require_aware_datetime(self.stopped_at, "stopped_at")


ALLOWED_PREFLIGHT_REPORT_STATUSES = frozenset({"READY_TO_START", "NOT_READY"})


@dataclass(frozen=True)
class PreflightReportRecord:
    """Append-only operator Preflight evidence. Not START authority."""

    preflight_report_id: str
    research_run_id: str
    runtime_instance_id: str
    created_at: datetime
    release_version: str
    configuration_fingerprint: str
    status: str
    checks: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        require_opaque_id(self.preflight_report_id, "preflight_report_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.runtime_instance_id, "runtime_instance_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.release_version, str) or not self.release_version.strip():
            raise PersistenceInputError("release_version must be a non-empty string")
        if (
            not isinstance(self.configuration_fingerprint, str)
            or not self.configuration_fingerprint.strip()
        ):
            raise PersistenceInputError(
                "configuration_fingerprint must be a non-empty string"
            )
        if self.status not in ALLOWED_PREFLIGHT_REPORT_STATUSES:
            raise PersistenceInputError("status is not a valid preflight_report status")
        sanitized = tuple(dict(item) for item in self.checks)
        for item in sanitized:
            reject_secret_structure(item, "preflight_report.checks")
        object.__setattr__(self, "checks", sanitized)


@dataclass(frozen=True)
class LeaseAcquireResult:
    """Outcome of one acquire_lease() call. record is populated only on ACQUIRED."""

    outcome: LeaseAcquireOutcome
    record: "ResearchOrchestrationRecord | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, LeaseAcquireOutcome):
            raise PersistenceInputError("outcome must be a LeaseAcquireOutcome")
        if self.outcome is LeaseAcquireOutcome.ACQUIRED and self.record is None:
            raise PersistenceInputError("record is required when outcome is ACQUIRED")


@dataclass(frozen=True)
class ResearchCycleRecord:
    """Append-only cycle history. Not a message queue and not a Finding."""

    cycle_id: str
    research_run_id: str
    cycle_number: int
    phase_completed: str
    outcome: str
    created_at: datetime
    stop_reason: str | None = None
    opportunity_id: str | None = None
    hypothesis_id: str | None = None
    experiment_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.cycle_id, "cycle_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_non_negative_int(self.cycle_number, "cycle_number")
        if not isinstance(self.phase_completed, str) or not self.phase_completed.strip():
            raise PersistenceInputError("phase_completed must be a non-empty string")
        if self.outcome not in ALLOWED_CYCLE_OUTCOMES:
            raise PersistenceInputError("outcome is not a cycle outcome")
        require_aware_datetime(self.created_at, "created_at")
        if self.stop_reason is not None and (
            not isinstance(self.stop_reason, str) or not self.stop_reason.strip()
        ):
            raise PersistenceInputError("stop_reason must be a non-empty string when set")
        require_optional_opaque_id(self.opportunity_id, "opportunity_id")
        require_optional_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_optional_opaque_id(self.experiment_id, "experiment_id")


@dataclass(frozen=True)
class BudgetConsumptionRecord:
    """Append-only consumption ledger row. Not IssuedBudget and not a mutable counter."""

    consumption_id: str
    budget_id: str
    research_run_id: str | None
    resource_type: str
    amount: int
    unit: str
    occurred_at: datetime
    provenance: str
    experiment_id: str | None = None
    request_id: str | None = None
    resource_metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.consumption_id, "consumption_id")
        require_opaque_id(self.budget_id, "budget_id")
        require_optional_opaque_id(self.research_run_id, "research_run_id")
        if self.resource_type not in ALLOWED_BUDGET_RESOURCE_TYPES:
            raise PersistenceInputError("resource_type is not a budget resource type")
        if self.unit not in ALLOWED_BUDGET_UNITS:
            raise PersistenceInputError("unit is not a known budget unit")
        if not isinstance(self.amount, int) or isinstance(self.amount, bool) or self.amount <= 0:
            raise PersistenceInputError("amount must be a positive int")
        require_aware_datetime(self.occurred_at, "occurred_at")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise PersistenceInputError("provenance must be a non-empty string")
        require_optional_opaque_id(self.experiment_id, "experiment_id")
        require_optional_opaque_id(self.request_id, "request_id")
        _optional_mapping(self.resource_metadata, "resource_metadata")
        if self.resource_type in {"REQUEST", "MODEL_CALL", "WORKER_INVOCATION", "MODEL_TOKENS_IN", "MODEL_TOKENS_OUT", "MODEL_ESCALATION_DECISION"} and self.unit != "count":
            raise PersistenceInputError("count resources must use unit count")
        if self.resource_type == "EXECUTION_TIME" and self.unit != "milliseconds":
            raise PersistenceInputError("EXECUTION_TIME must use milliseconds")
        if self.resource_type == "ARTIFACT_BYTES" and self.unit != "bytes":
            raise PersistenceInputError("ARTIFACT_BYTES must use bytes")
        if self.resource_type == "COST":
            raise PersistenceInputError("COST is not recorded without an issued cost unit")


@dataclass(frozen=True)
class SessionContextRecord:
    """Durable session metadata. Never stores cookie, token, or password values."""

    session_context_id: str
    research_run_id: str
    identity_id: str
    actor_reference: str
    origin: str
    authentication_profile_reference: str
    authentication_method: str
    secret_scheme: str
    secret_name: str
    state: str
    created_at: datetime
    updated_at: datetime
    established_at: datetime | None = None
    expires_at: datetime | None = None
    session_cookie_name: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.session_context_id, "session_context_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.identity_id, "identity_id")
        require_opaque_id(self.actor_reference, "actor_reference")
        require_opaque_id(self.authentication_profile_reference, "authentication_profile_reference")
        if not isinstance(self.origin, str) or not self.origin.strip():
            raise PersistenceInputError("origin must be a non-empty string")
        if self.authentication_method not in ALLOWED_AUTHENTICATION_METHODS:
            raise PersistenceInputError("authentication_method is not supported")
        if not isinstance(self.secret_scheme, str) or not self.secret_scheme.strip():
            raise PersistenceInputError("secret_scheme must be a non-empty string")
        if not isinstance(self.secret_name, str) or not self.secret_name.strip():
            raise PersistenceInputError("secret_name must be a non-empty string")
        if self.state not in ALLOWED_SESSION_STATES:
            raise PersistenceInputError("state is not a SessionContext state")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
        if self.established_at is not None:
            require_aware_datetime(self.established_at, "established_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, "expires_at")
        if self.session_cookie_name is not None and (
            not isinstance(self.session_cookie_name, str) or not self.session_cookie_name.strip()
        ):
            raise PersistenceInputError("session_cookie_name must be a non-empty string when set")
        _reject_secret_keys({"origin": self.origin, "secret_name": self.secret_name}, "session_context")


ALLOWED_DISCOVERY_FACT_KINDS = frozenset(
    {
        "ORIGIN",
        "EXACT_PATH",
        "HTTP_OPERATION",
        "PAGE_STATE",
        "CONTROL",
        "FORM",
        "RESPONSE_SHAPE",
        "RESOURCE_INSTANCE_CANDIDATE",
        "WORKFLOW_STATE",
        "WORKFLOW_TRANSITION",
        "SCOPE_BOUNDARY_CANDIDATE",
        # SD-G2 sensor-derived external census kinds.
        "DOMAIN",
        "HOSTNAME",
        "CERT",
        "SERVICE",
        "TECH",
        "JS_BUNDLE",
        "API_SPEC",
    }
)
ALLOWED_CONTROL_EVENT_KINDS = frozenset(
    {
        "REAUTHORIZATION_REQUIRED",
        "REDIRECT_BOUNDARY",
        "POPUP_BOUNDARY",
        "NEW_ORIGIN_BOUNDARY",
        "IFRAME_BOUNDARY",
    }
)
ALLOWED_DISCOVERY_INFERENCE_KINDS = frozenset(
    {"ROUTE_TEMPLATE", "OBJECT_TYPE", "OBJECT_INSTANCE", "SAME_AS"}
)
ALLOWED_DISCOVERY_GOAL_KINDS = frozenset(
    {
        "INSPECT_PATH",
        "INSPECT_CONTROL",
        "CHARACTERIZE_HTTP_OPERATION",
        "OBSERVE_UNDER_IDENTITY",
        "RESOLVE_TRANSITION_RESULT",
        "RESOLVE_OBJECT_TYPE",
        "INSPECT_SPA_PATH",
    }
)
ALLOWED_FRONTIER_EVENT_KINDS = frozenset(
    {
        "CREATED",
        "ELIGIBLE",
        "SELECTED",
        "BLOCKED_SCOPE",
        "BLOCKED_AUTH",
        "BLOCKED_BUDGET",
        "AWAITING_REAUTHORIZATION",
        "NO_NEW_INFORMATION",
        "OBSERVED",
        "FAILED_TRANSIENT",
        "FAILED_TERMINAL",
        "SUPERSEDED",
    }
)
DISCOVERY_STRATEGY_VERSION = "surface.discovery.v1"


def _one_primary(*values: object) -> None:
    if sum(item is not None for item in values) != 1:
        raise PersistenceInputError("exactly one primary source is required")


@dataclass(frozen=True)
class DiscoveryRunConfigRecord:
    research_run_id: str
    strategy_version: str
    seed_target_reference: str
    normalized_origin: str
    normalized_path: str
    max_discovery_cycles: int
    max_frontier_items: int
    max_new_facts_per_cycle: int
    max_browser_actions: int
    max_http_transactions: int
    max_per_route_revisit: int
    max_identity_variants: int
    max_transition_depth: int
    max_graph_depth_from_seed: int
    max_template_inference_fanout: int
    max_duplicate_observations: int
    configuration_fingerprint: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.research_run_id, "research_run_id")
        if self.strategy_version != DISCOVERY_STRATEGY_VERSION:
            raise PersistenceInputError("strategy_version must be surface.discovery.v1")
        require_opaque_id(self.configuration_fingerprint, "configuration_fingerprint")
        require_aware_datetime(self.created_at, "created_at")
        for name in (
            "max_discovery_cycles",
            "max_frontier_items",
            "max_new_facts_per_cycle",
            "max_browser_actions",
            "max_http_transactions",
            "max_per_route_revisit",
            "max_identity_variants",
            "max_transition_depth",
            "max_graph_depth_from_seed",
            "max_template_inference_fanout",
            "max_duplicate_observations",
        ):
            require_non_negative_int(getattr(self, name), name)


@dataclass(frozen=True)
class ControlEventRecord:
    control_event_id: str
    research_run_id: str
    event_kind: str
    worker_result_id: str
    identity_id: str
    target_reference: str
    created_at: datetime
    session_context_id: str | None = None
    channel: str | None = None
    location_origin: str | None = None
    location_path: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.control_event_id, "control_event_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.worker_result_id, "worker_result_id")
        require_opaque_id(self.identity_id, "identity_id")
        require_opaque_id(self.target_reference, "target_reference")
        require_aware_datetime(self.created_at, "created_at")
        if self.event_kind not in ALLOWED_CONTROL_EVENT_KINDS:
            raise PersistenceInputError("event_kind is not a ControlEvent kind")
        require_optional_opaque_id(self.session_context_id, "session_context_id")
        require_optional_opaque_id(self.request_id, "request_id")


@dataclass(frozen=True)
class DiscoveryFactRecord:
    fact_id: str
    research_run_id: str
    fact_kind: str
    canonical_key: str
    epistemic_status: str
    identity_id: str
    target_reference: str
    created_at: datetime
    session_context_id: str | None = None
    normalized_origin: str | None = None
    normalized_path: str | None = None
    http_method: str | None = None
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.fact_id, "fact_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.canonical_key, "canonical_key")
        require_opaque_id(self.identity_id, "identity_id")
        require_opaque_id(self.target_reference, "target_reference")
        require_aware_datetime(self.created_at, "created_at")
        if self.fact_kind not in ALLOWED_DISCOVERY_FACT_KINDS:
            raise PersistenceInputError("fact_kind is not a DiscoveryFact kind")
        if self.epistemic_status not in {"OBSERVED", "DERIVED"}:
            raise PersistenceInputError("epistemic_status must be OBSERVED or DERIVED")
        if self.fact_kind == "SCOPE_BOUNDARY_CANDIDATE" and self.epistemic_status != "DERIVED":
            raise PersistenceInputError("SCOPE_BOUNDARY_CANDIDATE must be DERIVED")
        _reject_secret_keys(self.attributes, "attributes")
        if self.attributes is not None:
            object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class DiscoveryFactSourceRecord:
    source_row_id: str
    research_run_id: str
    fact_id: str
    created_at: datetime
    observation_id: str | None = None
    sensor_observation_id: str | None = None
    control_event_id: str | None = None
    source_fact_id: str | None = None
    source_inference_id: str | None = None
    worker_result_id: str | None = None
    execution_attempt_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.source_row_id, "source_row_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.fact_id, "fact_id")
        require_aware_datetime(self.created_at, "created_at")
        _one_primary(
            self.observation_id,
            self.sensor_observation_id,
            self.control_event_id,
            self.source_fact_id,
            self.source_inference_id,
        )


@dataclass(frozen=True)
class DiscoveryInferenceRecord:
    inference_id: str
    research_run_id: str
    inference_kind: str
    canonical_key: str
    epistemic_status: str
    identity_id: str
    created_at: datetime
    attributes: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.inference_id, "inference_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        if self.inference_kind not in ALLOWED_DISCOVERY_INFERENCE_KINDS:
            raise PersistenceInputError("inference_kind is not a DiscoveryInference kind")
        if self.epistemic_status not in {"INFERRED", "HYPOTHESIZED"}:
            raise PersistenceInputError("inference cannot be OBSERVED")
        require_aware_datetime(self.created_at, "created_at")
        _reject_secret_keys(self.attributes, "attributes")
        if self.attributes is not None:
            object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class DiscoveryInferenceSourceRecord:
    source_row_id: str
    research_run_id: str
    inference_id: str
    created_at: datetime
    observation_id: str | None = None
    control_event_id: str | None = None
    source_fact_id: str | None = None
    source_inference_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.source_row_id, "source_row_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.inference_id, "inference_id")
        require_aware_datetime(self.created_at, "created_at")
        _one_primary(
            self.observation_id,
            self.control_event_id,
            self.source_fact_id,
            self.source_inference_id,
        )


@dataclass(frozen=True)
class FrontierItemRecord:
    frontier_id: str
    research_run_id: str
    strategy_version: str
    goal_kind: str
    candidate_origin: str
    candidate_path: str
    identity_id: str
    proposed_capability: str
    proposed_action: str
    expected_side_effect: int
    budget_class: int
    structural_signature: str
    dedupe_identity: str
    created_at: datetime
    session_context_id: str | None = None
    scope_hint: str | None = None
    attributes: Mapping[str, Any] | None = None
    current_state: str | None = None
    state_version: int | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.frontier_id, "frontier_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        if self.strategy_version != DISCOVERY_STRATEGY_VERSION:
            raise PersistenceInputError("strategy_version must be surface.discovery.v1")
        if self.goal_kind not in ALLOWED_DISCOVERY_GOAL_KINDS:
            raise PersistenceInputError("goal_kind is not a DiscoveryGoalKind")
        if self.structural_signature.startswith("el-"):
            raise PersistenceInputError("FrontierItem must not persist ephemeral element refs")
        if self.expected_side_effect not in (0, 1, 2, 3) or self.budget_class not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect/budget_class must be 0..3")
        require_aware_datetime(self.created_at, "created_at")
        _reject_secret_keys(self.attributes, "attributes")
        if self.attributes is not None:
            object.__setattr__(self, "attributes", dict(self.attributes))


@dataclass(frozen=True)
class FrontierSourceRecord:
    source_row_id: str
    research_run_id: str
    frontier_id: str
    created_at: datetime
    seed_config_run_id: str | None = None
    source_fact_id: str | None = None
    source_inference_id: str | None = None
    control_event_id: str | None = None
    observation_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.source_row_id, "source_row_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.frontier_id, "frontier_id")
        require_aware_datetime(self.created_at, "created_at")
        _one_primary(
            self.seed_config_run_id,
            self.source_fact_id,
            self.source_inference_id,
            self.control_event_id,
            self.observation_id,
        )
        if self.seed_config_run_id is not None and self.seed_config_run_id != self.research_run_id:
            raise PersistenceInputError("seed config source must be same-run")


@dataclass(frozen=True)
class FrontierEventRecord:
    event_id: str
    frontier_id: str
    research_run_id: str
    event_kind: str
    sequence: int
    created_at: datetime
    selection_generation: int | None = None
    execution_attempt_id: str | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.event_id, "event_id")
        require_opaque_id(self.frontier_id, "frontier_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        if self.event_kind not in ALLOWED_FRONTIER_EVENT_KINDS:
            raise PersistenceInputError("event_kind is not a FrontierEvent kind")
        require_non_negative_int(self.sequence, "sequence")
        if self.sequence < 1:
            raise PersistenceInputError("sequence must be >= 1")
        require_aware_datetime(self.created_at, "created_at")
        if self.event_kind == "SELECTED":
            if not isinstance(self.selection_generation, int) or self.selection_generation < 1:
                raise PersistenceInputError("SELECTED requires selection_generation >= 1")


@dataclass(frozen=True)
class DiscoveryProjectionReceiptRecord:
    receipt_id: str
    research_run_id: str
    strategy_version: str
    source_plane: str
    created_at: datetime
    observation_id: str | None = None
    control_event_id: str | None = None

    def __post_init__(self) -> None:
        require_opaque_id(self.receipt_id, "receipt_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        if self.strategy_version != DISCOVERY_STRATEGY_VERSION:
            raise PersistenceInputError("strategy_version must be surface.discovery.v1")
        if self.source_plane == "OBSERVATION":
            require_opaque_id(self.observation_id or "", "observation_id")
            if self.control_event_id is not None:
                raise PersistenceInputError("OBSERVATION receipt must not carry control_event_id")
        elif self.source_plane == "CONTROL_EVENT":
            require_opaque_id(self.control_event_id or "", "control_event_id")
            if self.observation_id is not None:
                raise PersistenceInputError("CONTROL_EVENT receipt must not carry observation_id")
        else:
            raise PersistenceInputError("source_plane must be OBSERVATION or CONTROL_EVENT")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class AttackSurfaceSnapshotRecord:
    """Immutable attack-surface graph summary. Nodes/edges rebuild from ledger."""

    snapshot_id: str
    research_run_id: str
    strategy_version: str
    node_count: int
    edge_count: int
    graph_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.snapshot_id, "snapshot_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise PersistenceInputError("strategy_version must be a non-empty string")
        if not isinstance(self.node_count, int) or isinstance(self.node_count, bool) or self.node_count < 0:
            raise PersistenceInputError("node_count must be a non-negative int")
        if not isinstance(self.edge_count, int) or isinstance(self.edge_count, bool) or self.edge_count < 0:
            raise PersistenceInputError("edge_count must be a non-negative int")
        if not isinstance(self.graph_hash, str) or len(self.graph_hash) != 64:
            raise PersistenceInputError("graph_hash must be a SHA-256 hex digest")




@dataclass(frozen=True)
class CoverageDebtSnapshotRecord:
    """Immutable coverage-debt matrix summary. Full matrix rebuilds from ledger."""

    snapshot_id: str
    research_run_id: str
    matrix_hash: str
    cell_counts: Mapping[str, Any]
    total_debt: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.snapshot_id, "snapshot_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_aware_datetime(self.created_at, "created_at")
        if not isinstance(self.matrix_hash, str) or len(self.matrix_hash) != 64:
            raise PersistenceInputError("matrix_hash must be a SHA-256 hex digest")
        object.__setattr__(
            self, "cell_counts", _require_mapping(self.cell_counts, "cell_counts")
        )
        if (
            not isinstance(self.total_debt, int)
            or isinstance(self.total_debt, bool)
            or self.total_debt < 0
        ):
            raise PersistenceInputError("total_debt must be a non-negative int")


@dataclass(frozen=True)
class HunterFamilyRecord:
    """Data-driven hypothesis family registry entry. Append-only versioning."""

    family_id: str
    name: str
    target_node_kinds: tuple[str, ...]
    preconditions: Mapping[str, Any]
    claim_template: str
    evidence_requirements: Mapping[str, Any]
    validation_tier: str
    enabled: bool
    version: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.family_id, "family_id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise PersistenceInputError("name must be a non-empty string")
        if not isinstance(self.target_node_kinds, tuple) or not all(
            isinstance(item, str) and item.strip() for item in self.target_node_kinds
        ):
            raise PersistenceInputError("target_node_kinds must be a tuple of non-empty strings")
        object.__setattr__(
            self, "preconditions", _require_mapping(self.preconditions, "preconditions")
        )
        if not isinstance(self.claim_template, str) or not self.claim_template.strip():
            raise PersistenceInputError("claim_template must be a non-empty string")
        object.__setattr__(
            self,
            "evidence_requirements",
            _require_mapping(self.evidence_requirements, "evidence_requirements"),
        )
        if self.validation_tier not in {"V1", "V2", "V3"}:
            raise PersistenceInputError("validation_tier must be V1, V2, or V3")
        if not isinstance(self.enabled, bool):
            raise PersistenceInputError("enabled must be a boolean")
        require_non_negative_int(self.version, "version")
        if self.version < 1:
            raise PersistenceInputError("version must be >= 1")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class HuntV3QueueRecord:
    """Pending active-experiment queue item produced by the hunt cycle."""

    queue_id: str
    research_run_id: str
    hypothesis_id: str
    family_id: str
    node_canonical_key: str
    identity_id: str | None
    capability: str
    action: str
    arguments: Mapping[str, Any]
    side_effect_level: int
    state: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.queue_id, "queue_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.hypothesis_id, "hypothesis_id")
        require_opaque_id(self.family_id, "family_id")
        if not isinstance(self.node_canonical_key, str) or not self.node_canonical_key.strip():
            raise PersistenceInputError("node_canonical_key must be a non-empty string")
        if self.identity_id is not None:
            require_opaque_id(self.identity_id, "identity_id")
        if not isinstance(self.capability, str) or not self.capability.strip():
            raise PersistenceInputError("capability must be a non-empty string")
        if not isinstance(self.action, str) or not self.action.strip():
            raise PersistenceInputError("action must be a non-empty string")
        object.__setattr__(self, "arguments", _require_mapping(self.arguments, "arguments"))
        if self.side_effect_level not in (0, 1, 2, 3):
            raise PersistenceInputError("side_effect_level must be 0, 1, 2, or 3")
        if self.state not in {"PENDING", "APPROVED", "RUN", "BLOCKED"}:
            raise PersistenceInputError("state must be PENDING, APPROVED, RUN, or BLOCKED")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class ImpactChainRecord:
    """Immutable impact-chain registration. Nodes/edges stored separately."""

    chain_id: str
    research_run_id: str
    program_id: str
    graph_hash: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.chain_id, "chain_id")
        require_opaque_id(self.research_run_id, "research_run_id")
        require_opaque_id(self.program_id, "program_id")
        if self.graph_hash is not None and (
            not isinstance(self.graph_hash, str) or len(self.graph_hash) != 64
        ):
            raise PersistenceInputError("graph_hash must be a SHA-256 hex digest or None")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class ImpactChainNodeRecord:
    """One node of a persisted impact chain."""

    node_id: str
    chain_id: str
    impact_kind: str
    claim_text: str
    scope_ref: Mapping[str, Any]
    proof_refs: tuple[str, ...]
    ordering: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.node_id, "node_id")
        require_opaque_id(self.chain_id, "chain_id")
        if not isinstance(self.impact_kind, str) or not self.impact_kind.strip():
            raise PersistenceInputError("impact_kind must be a non-empty string")
        if not isinstance(self.claim_text, str) or not self.claim_text.strip():
            raise PersistenceInputError("claim_text must be a non-empty string")
        object.__setattr__(self, "scope_ref", _require_mapping(self.scope_ref, "scope_ref"))
        if not isinstance(self.proof_refs, tuple) or not self.proof_refs:
            raise PersistenceInputError("proof_refs must be a non-empty tuple")
        object.__setattr__(
            self,
            "proof_refs",
            tuple(
                require_opaque_id(item, f"proof_refs[{index}]")
                for index, item in enumerate(self.proof_refs)
            ),
        )
        if not isinstance(self.ordering, int) or isinstance(self.ordering, bool) or self.ordering < 0:
            raise PersistenceInputError("ordering must be a non-negative int")
        require_aware_datetime(self.created_at, "created_at")


@dataclass(frozen=True)
class ImpactChainEdgeRecord:
    """One edge of a persisted impact chain. The relation itself must be proven."""

    edge_id: str
    chain_id: str
    from_node_id: str
    to_node_id: str
    relation: str
    proof_refs: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        require_opaque_id(self.edge_id, "edge_id")
        require_opaque_id(self.chain_id, "chain_id")
        require_opaque_id(self.from_node_id, "from_node_id")
        require_opaque_id(self.to_node_id, "to_node_id")
        if not isinstance(self.relation, str) or not self.relation.strip():
            raise PersistenceInputError("relation must be a non-empty string")
        if not isinstance(self.proof_refs, tuple) or not self.proof_refs:
            raise PersistenceInputError("proof_refs must be a non-empty tuple")
        object.__setattr__(
            self,
            "proof_refs",
            tuple(
                require_opaque_id(item, f"proof_refs[{index}]")
                for index, item in enumerate(self.proof_refs)
            ),
        )
        require_aware_datetime(self.created_at, "created_at")
