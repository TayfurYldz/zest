"""Arm one provider-neutral OAST correlation for an authorized attempt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from zest.application.identity import new_opaque_id
from zest.application.ports import UnitOfWorkFactory
from zest.data.errors import PersistenceConflictError
from zest.data.records import OastCorrelationRecord


DEFAULT_OAST_TTL = timedelta(minutes=15)


class OastCorrelationArmError(Exception):
    """The persisted execution spine cannot safely arm an OAST correlation."""


@dataclass(frozen=True)
class OastCorrelationArmResult:
    correlation_id: str
    attempt_id: str
    experiment_id: str
    research_run_id: str
    target_reference: str
    identity_id: str
    armed_at: datetime
    expires_at: datetime
    existing: bool = False


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class ArmOastCorrelation:
    """Derive correlation authority only from persisted execution records."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        max_ttl: timedelta = DEFAULT_OAST_TTL,
    ) -> None:
        if max_ttl <= timedelta(0):
            raise ValueError("max_ttl must be positive")
        self._uow_factory = uow_factory
        self._clock = clock or _default_clock
        self._max_ttl = max_ttl

    def execute(
        self,
        attempt_id: str,
        *,
        armed_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> OastCorrelationArmResult:
        arm_time = armed_at if armed_at is not None else self._clock()
        self._require_aware(arm_time, "armed_at")
        expiry = expires_at if expires_at is not None else arm_time + self._max_ttl
        self._require_aware(expiry, "expires_at")
        if expiry <= arm_time or expiry - arm_time > self._max_ttl:
            raise OastCorrelationArmError("OAST correlation TTL is outside the bounded window")

        with self._uow_factory.open() as uow:
            attempt = uow.execution_attempts.get(attempt_id)
            if attempt is None:
                raise OastCorrelationArmError("execution attempt is not found")
            plan = uow.experiment_plans.get(attempt.experiment_id)
            run = uow.research_runs.get(attempt.research_run_id)
            hypothesis = uow.hypotheses.get(plan.hypothesis_id) if plan else None
            if plan is None or run is None or hypothesis is None:
                raise OastCorrelationArmError("execution spine is incomplete")
            if plan.research_run_id != attempt.research_run_id:
                raise OastCorrelationArmError("experiment plan is bound to another research run")
            if plan.target_reference != attempt.target_reference:
                raise OastCorrelationArmError("attempt and plan targets do not match")
            identity_id = hypothesis.identity_id
            if not identity_id:
                raise OastCorrelationArmError("target identity binding is missing")
            plan_identity = plan.arguments.get("identity_id")
            if plan_identity is not None and plan_identity != identity_id:
                raise OastCorrelationArmError("plan identity binding does not match hypothesis")

            existing = uow.oast_correlations.get_by_attempt_id(attempt_id)
            if existing is not None:
                return self._result(existing, existing=True)

            record = OastCorrelationRecord(
                correlation_id=new_opaque_id(),
                attempt_id=attempt.attempt_id,
                experiment_id=attempt.experiment_id,
                research_run_id=attempt.research_run_id,
                target_reference=attempt.target_reference,
                identity_id=identity_id,
                armed_at=arm_time,
                expires_at=expiry,
                created_at=self._clock(),
            )
            try:
                uow.oast_correlations.insert(record)
            except PersistenceConflictError:
                existing = uow.oast_correlations.get_by_attempt_id(attempt_id)
                if existing is None:
                    raise
                return self._result(existing, existing=True)
            uow.commit()
            return self._result(record)

    @staticmethod
    def _require_aware(value: datetime, field_name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise OastCorrelationArmError(f"{field_name} must be timezone-aware")

    @staticmethod
    def _result(record: OastCorrelationRecord, *, existing: bool = False) -> OastCorrelationArmResult:
        return OastCorrelationArmResult(
            correlation_id=record.correlation_id,
            attempt_id=record.attempt_id,
            experiment_id=record.experiment_id,
            research_run_id=record.research_run_id,
            target_reference=record.target_reference,
            identity_id=record.identity_id,
            armed_at=record.armed_at,
            expires_at=record.expires_at,
            existing=existing,
        )
