"""Admit provider-normalized OAST callbacks through the Slice B boundary.

Callbacks are untrusted external input. Only a persisted correlation binding can
select the execution run, target, identity, and bounded admission window.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from zest.application.identity import new_opaque_id
from zest.application.ports import UnitOfWorkFactory
from zest.application.sensor.admit import AdmitSensorObservations, SensorAdmissionError
from zest.core.enums import ActorType, ScopeClassification
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    AuditEventRecord,
    OastAdmissionRecord,
    OastCallbackDeliveryRecord,
    SensorObservationRecord,
)
from zest.research.oast.types import OastCallback, OastCallbackDelivery
from zest.research.sensor.types import build_observation


class OastCallbackAdmissionError(Exception):
    """Callback rejected from authoritative OAST admission."""


@dataclass(frozen=True)
class OastCallbackAdmissionResult:
    admitted: bool
    observation_id: str
    fact_id: str | None
    reason_code: str


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, correlation_id: str) -> str:
    digest = hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()
    return f"oast:{prefix}:{digest}"


class AdmitOastCallback:
    """Atomically ledger and admit one correlation-bound callback."""

    SENSOR_ID = "oast.correlation"
    OAST_SEMANTICS = "correlated external callback observed"

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or _default_clock

    def execute(
        self,
        callback: OastCallback | OastCallbackDelivery,
        *,
        scope_classification: str | None = None,
        identity_id: str = "ANONYMOUS",
        now: datetime | None = None,
    ) -> OastCallbackAdmissionResult:
        if isinstance(callback, OastCallbackDelivery):
            return self._execute_delivery(callback)
        return self._execute_legacy_token_callback(
            callback, occurred_at=now if now is not None else self._clock()
        )

    def _execute_delivery(
        self, delivery: OastCallbackDelivery
    ) -> OastCallbackAdmissionResult:
        with self._uow_factory.open() as uow:
            correlation = uow.oast_correlations.get(delivery.correlation_id)
            if correlation is None:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=delivery.received_at,
                        actor_id="control-plane:oast-admitter",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="OAST_CALLBACK_UNKNOWN_TOKEN",
                        subject_type="OAST_CALLBACK",
                        subject_id=delivery.delivery_id,
                        correlation_id=delivery.correlation_id,
                        payload={
                            "reason_code": "OAST_CORRELATION_NOT_FOUND",
                            "correlation_status": "UNKNOWN_TOKEN",
                            "normalized_digest": delivery.normalized_digest,
                            "not_evidence": True,
                        },
                    )
                )
                uow.commit()
                return self._rejected("OAST_CORRELATION_NOT_FOUND")

            attempt = uow.execution_attempts.get(correlation.attempt_id)
            plan = uow.experiment_plans.get(correlation.experiment_id)
            run = uow.research_runs.get(correlation.research_run_id)
            hypothesis = uow.hypotheses.get(plan.hypothesis_id) if plan else None
            reason = self._validate_binding(
                delivery.received_at, correlation, attempt, plan, run, hypothesis
            )
            if reason == "OAST_CORRELATION_EXPIRED":
                try:
                    uow.oast_callback_deliveries.insert(
                        OastCallbackDeliveryRecord(
                            delivery_id=delivery.delivery_id,
                            correlation_id=delivery.correlation_id,
                            provider_adapter_id=delivery.provider_adapter_id,
                            provider_event_id=delivery.provider_event_id,
                            received_at=delivery.received_at,
                            normalized_payload=delivery.normalized_payload,
                            normalized_digest=delivery.normalized_digest,
                        )
                    )
                except PersistenceConflictError:
                    pass
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=delivery.received_at,
                        actor_id="control-plane:oast-admitter",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="OAST_CALLBACK_EXPIRED",
                        subject_type="research_run",
                        subject_id=correlation.research_run_id,
                        correlation_id=correlation.correlation_id,
                        payload={
                            "reason_code": reason,
                            "correlation_id": correlation.correlation_id,
                            "normalized_digest": delivery.normalized_digest,
                            "not_evidence": True,
                        },
                    )
                )
                uow.commit()
                return self._rejected(reason)
            if reason is not None:
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=delivery.received_at,
                        actor_id="control-plane:oast-admitter",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="OAST_CALLBACK_REJECTED",
                        subject_type="research_run",
                        subject_id=correlation.research_run_id,
                        correlation_id=correlation.correlation_id,
                        payload={
                            "reason_code": reason,
                            "correlation_id": correlation.correlation_id,
                            "normalized_digest": delivery.normalized_digest,
                            "not_evidence": True,
                            "cross_run_rejected": reason.endswith("RUN_MISMATCH"),
                        },
                    )
                )
                uow.commit()
                return self._rejected(reason)

            delivery_record = OastCallbackDeliveryRecord(
                delivery_id=delivery.delivery_id,
                correlation_id=delivery.correlation_id,
                provider_adapter_id=delivery.provider_adapter_id,
                provider_event_id=delivery.provider_event_id,
                received_at=delivery.received_at,
                normalized_payload=delivery.normalized_payload,
                normalized_digest=delivery.normalized_digest,
            )
            try:
                uow.oast_callback_deliveries.insert(delivery_record)
            except PersistenceConflictError:
                existing_admission = uow.oast_admissions.get_by_correlation(
                    correlation.correlation_id
                )
                if existing_admission is not None:
                    return self._result_from_admission(existing_admission)
                return self._rejected("OAST_CALLBACK_DELIVERY_ALREADY_RECORDED")

            existing_admission = uow.oast_admissions.get_by_correlation(
                correlation.correlation_id
            )
            if existing_admission is not None:
                # A distinct delivery is retained in the append-only ledger,
                # while the correlation remains one-authoritative-admission.
                uow.commit()
                return self._result_from_admission(existing_admission)

            observation_id = _stable_id("observation", correlation.correlation_id)
            fact_id = _stable_id("fact", correlation.correlation_id)
            source_row_id = _stable_id("source", correlation.correlation_id)
            admission_id = _stable_id("admission", correlation.correlation_id)
            observation = build_observation(
                observation_id=observation_id,
                sensor_id=self.SENSOR_ID,
                target_reference=correlation.target_reference,
                research_run_id=correlation.research_run_id,
                payload={
                    "provider_adapter_id": delivery.provider_adapter_id,
                    "provider_event_id": delivery.provider_event_id,
                    "normalized_digest": delivery.normalized_digest,
                    "normalized_payload": dict(delivery.normalized_payload),
                },
                source_metadata={
                    "sensor_id": self.SENSOR_ID,
                    "correlation_id": correlation.correlation_id,
                    "attempt_id": correlation.attempt_id,
                    "provider_adapter_id": delivery.provider_adapter_id,
                    "source_status": "UNTRUSTED_EXTERNAL",
                },
                collected_at=delivery.received_at,
            )
            try:
                uow.sensor_observations.insert(
                    SensorObservationRecord(
                        observation_id=observation.observation_id,
                        research_run_id=observation.research_run_id,
                        sensor_id=observation.sensor_id,
                        target_reference=observation.target_reference,
                        collected_at=observation.collected_at,
                        payload_digest=observation.payload_digest,
                        epistemic_status=observation.epistemic_status.value,
                        source_metadata=dict(observation.source_metadata),
                        payload=dict(observation.payload),
                        created_at=delivery.received_at,
                    )
                )
                admission = AdmitSensorObservations(self._uow_factory).admit_in_uow(
                    uow,
                    observation,
                    research_run_id=correlation.research_run_id,
                    identity_id=correlation.identity_id,
                    # Scope is deliberately application-derived, never callback supplied.
                    scope_classification=ScopeClassification.UNKNOWN.value,
                    fact_id=fact_id,
                    source_row_id=source_row_id,
                    canonical_key=f"oast.correlation:{correlation.correlation_id}",
                    extra_attributes={"oast_semantics": self.OAST_SEMANTICS},
                )
                admission_record = OastAdmissionRecord(
                    admission_id=admission_id,
                    correlation_id=correlation.correlation_id,
                    research_run_id=correlation.research_run_id,
                    sensor_observation_id=admission.observation_id,
                    discovery_fact_id=admission.fact_id,
                    created_at=delivery.received_at,
                )
                uow.oast_admissions.insert(admission_record)
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=delivery.received_at,
                        actor_id="control-plane:oast-admitter",
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="OAST_CALLBACK_ADMITTED",
                        subject_type="research_run",
                        subject_id=correlation.research_run_id,
                        correlation_id=correlation.correlation_id,
                        payload={
                            "reason_code": "OAST_CALLBACK_ADMITTED",
                            "correlation_id": correlation.correlation_id,
                            "observation_id": admission_record.sensor_observation_id,
                            "fact_id": admission_record.discovery_fact_id,
                            "normalized_digest": delivery.normalized_digest,
                            "not_evidence": True,
                        },
                    )
                )
            except PersistenceConflictError:
                existing_admission = uow.oast_admissions.get_by_correlation(
                    correlation.correlation_id
                )
                if existing_admission is not None:
                    return self._result_from_admission(existing_admission)
                return self._rejected("OAST_ADMISSION_CONFLICT")
            except SensorAdmissionError:
                return self._rejected("OAST_ADMISSION_REJECTED")

            uow.commit()
            return OastCallbackAdmissionResult(
                admitted=True,
                observation_id=admission_record.sensor_observation_id,
                fact_id=admission_record.discovery_fact_id,
                reason_code="OAST_CALLBACK_ADMITTED",
            )

    @staticmethod
    def _validate_binding(received_at, correlation, attempt, plan, run, hypothesis) -> str | None:
        if received_at < correlation.armed_at:
            return "OAST_CORRELATION_NOT_ARMED"
        if received_at >= correlation.expires_at:
            return "OAST_CORRELATION_EXPIRED"
        if attempt is None or plan is None or run is None or hypothesis is None:
            return "OAST_EXECUTION_BINDING_NOT_FOUND"
        if attempt.experiment_id != correlation.experiment_id:
            return "OAST_ATTEMPT_EXPERIMENT_MISMATCH"
        if attempt.research_run_id != correlation.research_run_id:
            return "OAST_ATTEMPT_RUN_MISMATCH"
        if plan.research_run_id != correlation.research_run_id:
            return "OAST_PLAN_RUN_MISMATCH"
        if attempt.target_reference != correlation.target_reference:
            return "OAST_ATTEMPT_TARGET_MISMATCH"
        if plan.target_reference != correlation.target_reference:
            return "OAST_PLAN_TARGET_MISMATCH"
        if not hypothesis.identity_id:
            return "OAST_IDENTITY_MISSING"
        if hypothesis.identity_id != correlation.identity_id:
            return "OAST_IDENTITY_MISMATCH"
        plan_identity = plan.arguments.get("identity_id")
        if plan_identity is not None and plan_identity != correlation.identity_id:
            return "OAST_IDENTITY_MISMATCH"
        if not correlation.identity_id:
            return "OAST_IDENTITY_MISSING"
        return None

    def _execute_legacy_token_callback(
        self, callback: OastCallback, *, occurred_at: datetime
    ) -> OastCallbackAdmissionResult:
        with self._uow_factory.open() as uow:
            token_record = uow.oast_tokens.get(callback.token_id)
        if token_record is None:
            self._write_rejection_audit(callback, occurred_at, "OAST_TOKEN_NOT_FOUND", {})
            return self._rejected("OAST_TOKEN_NOT_FOUND")
        if occurred_at > token_record.expires_at:
            self._write_rejection_audit(
                callback,
                occurred_at,
                "OAST_TOKEN_EXPIRED",
                {"research_run_id": token_record.research_run_id},
            )
            return self._rejected("OAST_TOKEN_EXPIRED")
        self._write_rejection_audit(
            callback,
            occurred_at,
            "OAST_LEGACY_TOKEN_NOT_AUTHORITATIVE",
            {"research_run_id": token_record.research_run_id},
        )
        return self._rejected("OAST_LEGACY_TOKEN_NOT_AUTHORITATIVE")

    @staticmethod
    def _rejected(reason_code: str) -> OastCallbackAdmissionResult:
        return OastCallbackAdmissionResult(
            admitted=False, observation_id="", fact_id=None, reason_code=reason_code
        )

    @staticmethod
    def _result_from_admission(admission: Any) -> OastCallbackAdmissionResult:
        return OastCallbackAdmissionResult(
            admitted=True,
            observation_id=admission.sensor_observation_id,
            fact_id=admission.discovery_fact_id,
            reason_code="OAST_CALLBACK_ALREADY_ADMITTED",
        )

    def _write_rejection_audit(
        self,
        callback: OastCallback,
        occurred_at: datetime,
        reason_code: str,
        context: dict[str, Any],
    ) -> None:
        with self._uow_factory.open() as uow:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=occurred_at,
                    actor_id="control-plane:oast-admitter",
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="OAST_CALLBACK_REJECTED",
                    subject_type="OAST_CALLBACK",
                    subject_id=callback.callback_id,
                    correlation_id=callback.token_id,
                    payload={
                        "reason_code": reason_code,
                        "callback_id": callback.callback_id,
                        "token_id": callback.token_id,
                        "context": context,
                    },
                )
            )
            uow.commit()
