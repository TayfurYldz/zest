"""Ingest a completed Worker invocation through Transition A.

Does not execute Workers, authorize, or create Evidence/Candidate/Finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.transition_a.drafts import ObservationDraft
from zest.application.transition_a.errors import (
    MalformedNormalizedPayloadError,
    UnsupportedNormalizerError,
)
from zest.application.transition_a.registry import NormalizerRegistry
from zest.application.transition_a.timestamps import parse_aware_timestamp
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    AuditEventRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from zest.data.unit_of_work import UnitOfWork
from zest.platform.contract_validation import (
    ContractValidationError,
    ContractValidator,
)
from zest.application.session_lifecycle import (
    capture_login_result,
    expire_session_on_unauthenticated_response,
    strip_ephemeral_secrets,
)
from zest.platform.secrets import CompositeSecretPort
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome

AUDIT_WORKER_RESULT_INGESTED = "WORKER_RESULT_INGESTED"
AUDIT_OBSERVATION_ADMITTED = "OBSERVATION_ADMITTED"
CONTROL_PLANE_ACTOR_ID = "control-plane"


class IngestionStatus(Enum):
    """Use-case outcome. Not WorkerResult.status. Not a vulnerability verdict."""

    INGESTED = "INGESTED"
    ALREADY_INGESTED = "ALREADY_INGESTED"
    NO_OBSERVATION = "NO_OBSERVATION"
    REJECTED_INVALID_INVOCATION = "REJECTED_INVALID_INVOCATION"


@dataclass(frozen=True)
class IngestionOutcome:
    status: IngestionStatus
    worker_result_id: str | None = None
    observation_ids: tuple[str, ...] = ()
    reason: str | None = None


def worker_result_id_for(request_id: str) -> str:
    return f"wr:{request_id}"


def observation_id_for(request_id: str, kind: str, version: str) -> str:
    return f"obs:{request_id}:{kind}:{version}"


class IngestCompletedWorkerInvocation:
    """Persist a valid completed WorkerResult and any Transition A ObservationDrafts."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        validator: ContractValidator | None = None,
        registry: NormalizerRegistry | None = None,
        clock: Clock | None = None,
        actor_id: str = CONTROL_PLANE_ACTOR_ID,
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._validator = validator or ContractValidator()
        self._registry = registry or NormalizerRegistry()
        self._clock = clock or SystemClock()
        self._actor_id = actor_id
        self._secret_port = secret_port

    def execute(
        self,
        request: Mapping[str, Any],
        outcome: WorkerInvocationOutcome,
    ) -> IngestionOutcome:
        rejected = self._reject_invalid_invocation(request, outcome)
        if rejected is not None:
            return rejected
        assert outcome.worker_result is not None
        validated = dict(outcome.worker_result)
        result, ephemeral = strip_ephemeral_secrets(validated)
        capability = request.get("worker_capability")
        action = request.get("action")
        if not isinstance(capability, str) or not isinstance(action, str):
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="trusted request is missing worker_capability/action",
            )
        try:
            normalizer = self._registry.get(capability, action)
        except UnsupportedNormalizerError as exc:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason=str(exc),
            )
        try:
            drafts = normalizer.normalize(request, result)
        except MalformedNormalizedPayloadError as exc:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason=str(exc),
            )

        correlation = request["correlation"]
        assert isinstance(correlation, Mapping)
        request_id = str(correlation["request_id"])

        with self._uow_factory.open() as uow:
            existing = uow.worker_results.get_by_request_id(request_id)
            if existing is not None:
                observations = uow.observations.list_for_worker_result(
                    existing.worker_result_id
                )
                return IngestionOutcome(
                    status=IngestionStatus.ALREADY_INGESTED,
                    worker_result_id=existing.worker_result_id,
                    observation_ids=tuple(item.observation_id for item in observations),
                    reason="request_id already ingested; not a duplicate vulnerability",
                )
            integrity = self._check_experiment_integrity(uow, request)
            if integrity is not None:
                return integrity
            record = self._worker_result_record(request, result)
            created_at = self._clock.now()
            try:
                uow.worker_results.insert(record)
            except PersistenceConflictError:
                raced = uow.worker_results.get_by_request_id(request_id)
                if raced is None:
                    raise
                observations = uow.observations.list_for_worker_result(
                    raced.worker_result_id
                )
                return IngestionOutcome(
                    status=IngestionStatus.ALREADY_INGESTED,
                    worker_result_id=raced.worker_result_id,
                    observation_ids=tuple(item.observation_id for item in observations),
                    reason="request_id already ingested; not a duplicate vulnerability",
                )
            observation_ids = self._persist_observations(
                uow, record, drafts, created_at
            )
            capture_login_result(
                uow, self._secret_port, request, result, ephemeral, created_at
            )
            expire_session_on_unauthenticated_response(
                uow, self._secret_port, request, result, created_at
            )
            self._persist_audit_events(
                uow, record, drafts, observation_ids, created_at
            )
            uow.commit()
        status = (
            IngestionStatus.INGESTED if observation_ids else IngestionStatus.NO_OBSERVATION
        )
        return IngestionOutcome(
            status=status,
            worker_result_id=record.worker_result_id,
            observation_ids=observation_ids,
        )

    def _reject_invalid_invocation(
        self,
        request: Mapping[str, Any],
        outcome: WorkerInvocationOutcome,
    ) -> IngestionOutcome | None:
        if outcome.invocation_status is not InvocationStatus.COMPLETED:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason=(
                    "invocation_status is not COMPLETED; "
                    "transport/runtime failure is not a WorkerResult"
                ),
            )
        if outcome.worker_result is None:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="COMPLETED invocation is missing a WorkerResult document",
            )
        try:
            self._validator.validate_worker_request(request)
            self._validator.validate_worker_result(outcome.worker_result)
        except ContractValidationError as exc:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason=str(exc),
            )
        if not self._validator.correlation_matches(request, outcome.worker_result):
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="correlation mismatch; WorkerResult not rewritten and not ingested",
            )
        return None

    def _check_experiment_integrity(
        self, uow: UnitOfWork, request: Mapping[str, Any]
    ) -> IngestionOutcome | None:
        correlation = request["correlation"]
        assert isinstance(correlation, Mapping)
        experiment_id = str(correlation["experiment_id"])
        research_run_id = str(correlation["research_run_id"])
        budget = request["execution_budget"]
        assert isinstance(budget, Mapping)
        budget_id = str(budget["budget_id"])
        experiment = uow.experiments.get(experiment_id)
        if experiment is None:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="experiment does not exist for this WorkerRequest",
            )
        if experiment.research_run_id != research_run_id:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="experiment research_run_id does not match WorkerRequest",
            )
        if experiment.budget_id != budget_id:
            return IngestionOutcome(
                status=IngestionStatus.REJECTED_INVALID_INVOCATION,
                reason="experiment budget_id does not match WorkerRequest",
            )
        return None

    def _worker_result_record(
        self, request: Mapping[str, Any], result: Mapping[str, Any]
    ) -> WorkerResultRecord:
        correlation = request["correlation"]
        assert isinstance(correlation, Mapping)
        budget = request["execution_budget"]
        assert isinstance(budget, Mapping)
        request_id = str(correlation["request_id"])
        started_at = None
        completed_at = None
        if result.get("started_at") is not None:
            started_at = parse_aware_timestamp(result.get("started_at"), "started_at")
        if result.get("completed_at") is not None:
            completed_at = parse_aware_timestamp(
                result.get("completed_at"), "completed_at"
            )
        parent = correlation.get("parent_request_id")
        parent_request_id = str(parent) if isinstance(parent, str) else None
        raw_artifacts = result.get("raw_artifact_descriptors")
        descriptors = None
        if isinstance(raw_artifacts, list):
            descriptors = [item for item in raw_artifacts if isinstance(item, Mapping)]
        return WorkerResultRecord(
            worker_result_id=worker_result_id_for(request_id),
            experiment_id=str(correlation["experiment_id"]),
            research_run_id=str(correlation["research_run_id"]),
            request_id=request_id,
            correlation_id=str(correlation["correlation_id"]),
            worker_capability=str(request["worker_capability"]),
            action=str(request["action"]),
            authorization_decision_reference=str(
                request["authorization_decision_reference"]
            ),
            budget_id=str(budget["budget_id"]),
            side_effect_level=int(request["side_effect_level"]),
            contract_version=str(result["contract_version"]),
            worker_id=str(result["worker_id"]),
            status=str(result["status"]),
            received_at=self._clock.now(),
            started_at=started_at,
            completed_at=completed_at,
            parent_request_id=parent_request_id,
            raw_result=result.get("raw_result") if isinstance(result.get("raw_result"), Mapping) else None,
            raw_artifact_descriptors=descriptors,
            diagnostics=result.get("diagnostics") if isinstance(result.get("diagnostics"), Mapping) else None,
            control_signal=result.get("control_signal") if isinstance(result.get("control_signal"), Mapping) else None,
        )

    def _persist_observations(
        self,
        uow: UnitOfWork,
        record: WorkerResultRecord,
        drafts: tuple[ObservationDraft, ...],
        created_at,
    ) -> tuple[str, ...]:
        ids: list[str] = []
        for draft in drafts:
            observation_id = observation_id_for(
                record.request_id, draft.observation_kind, draft.normalization_version
            )
            uow.observations.insert(
                ObservationRecord(
                    observation_id=observation_id,
                    worker_result_id=record.worker_result_id,
                    observation_kind=draft.observation_kind,
                    payload=draft.payload,
                    normalization_version=draft.normalization_version,
                    observed_at=draft.observed_at,
                    created_at=created_at,
                )
            )
            ids.append(observation_id)
        return tuple(ids)

    def _persist_audit_events(
        self,
        uow: UnitOfWork,
        record: WorkerResultRecord,
        drafts: tuple[ObservationDraft, ...],
        observation_ids: tuple[str, ...],
        created_at,
    ) -> None:
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=f"ae:wr:{record.request_id}",
                occurred_at=created_at,
                actor_id=self._actor_id,
                actor_type=ActorType.CONTROL_PLANE.value,
                event_type=AUDIT_WORKER_RESULT_INGESTED,
                subject_type="worker_result",
                subject_id=record.worker_result_id,
                payload={
                    "request_id": record.request_id,
                    "worker_result_status": record.status,
                    "observation_count": len(observation_ids),
                    "normalization_version": drafts[0].normalization_version if drafts else None,
                },
                correlation_id=record.correlation_id,
            )
        )
        for draft, observation_id in zip(drafts, observation_ids, strict=True):
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=f"ae:obs:{record.request_id}:{draft.observation_kind}:{draft.normalization_version}",
                    occurred_at=created_at,
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=AUDIT_OBSERVATION_ADMITTED,
                    subject_type="observation",
                    subject_id=observation_id,
                    payload={
                        "observation_kind": draft.observation_kind,
                        "normalization_version": draft.normalization_version,
                    },
                    correlation_id=record.correlation_id,
                )
            )
