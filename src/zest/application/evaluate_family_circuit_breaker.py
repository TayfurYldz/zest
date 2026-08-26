"""Evaluate SD-G10 family circuit breaker from append-only telemetry."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.validation.circuit_breaker import (
    CircuitBreakerAction,
    FamilyTelemetry,
    evaluate_family_circuit_breaker,
)


FAMILY_CIRCUIT_BREAKER_EVALUATED = "FAMILY_CIRCUIT_BREAKER_EVALUATED"
CIRCUIT_BREAKER_ACTOR_ID = "control-plane:family-circuit-breaker"


@dataclass(frozen=True)
class EvaluateFamilyCircuitBreakerCommand:
    research_run_id: str
    family_id: str
    minimum_sample: int = 10
    bad_outcome_threshold: float = 0.60


@dataclass(frozen=True)
class EvaluateFamilyCircuitBreakerResult:
    action: CircuitBreakerAction
    throttle: bool
    disable_family: bool
    supported_count: int
    rejected_count: int
    inconclusive_count: int
    bad_outcome_rate: float
    reason_codes: tuple[str, ...]
    audit_event_id: str


class EvaluateFamilyCircuitBreaker:
    def __init__(self, uow_factory: UnitOfWorkFactory, *, clock: Clock | None = None) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self, command: EvaluateFamilyCircuitBreakerCommand
    ) -> EvaluateFamilyCircuitBreakerResult:
        with self._uow_factory.open() as uow:
            supported, rejected, inconclusive = _family_outcome_counts(
                uow,
                research_run_id=command.research_run_id,
                family_id=command.family_id,
            )
            decision = evaluate_family_circuit_breaker(
                FamilyTelemetry(
                    family_id=command.family_id,
                    supported_count=supported,
                    rejected_count=rejected,
                    inconclusive_count=inconclusive,
                    minimum_sample=command.minimum_sample,
                    bad_outcome_threshold=command.bad_outcome_threshold,
                )
            )
            event_id = new_opaque_id()
            payload = {
                "research_run_id": command.research_run_id,
                "family_id": command.family_id,
                "supported_count": supported,
                "rejected_count": rejected,
                "inconclusive_count": inconclusive,
                "bad_outcome_rate": decision.bad_outcome_rate,
                "action": decision.action.value,
                "throttle": decision.throttle,
                "disable_family": decision.disable_family,
                "requires_human_review_to_restore": (
                    decision.requires_human_review_to_restore
                ),
                "reason_codes": decision.reason_codes,
            }
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=event_id,
                    occurred_at=self._clock.now(),
                    actor_id=CIRCUIT_BREAKER_ACTOR_ID,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=FAMILY_CIRCUIT_BREAKER_EVALUATED,
                    subject_type="hunter_family",
                    subject_id=command.family_id,
                    correlation_id=command.research_run_id,
                    payload=payload,
                )
            )
            uow.commit()
            return EvaluateFamilyCircuitBreakerResult(
                action=decision.action,
                throttle=decision.throttle,
                disable_family=decision.disable_family,
                supported_count=supported,
                rejected_count=rejected,
                inconclusive_count=inconclusive,
                bad_outcome_rate=decision.bad_outcome_rate,
                reason_codes=decision.reason_codes,
                audit_event_id=event_id,
            )


def _family_outcome_counts(uow, *, research_run_id: str, family_id: str) -> tuple[int, int, int]:
    latest: dict[str, str] = {}
    events = [
        event
        for event in uow.audit_events.list_for_subject_type("hypothesis")
        if event.correlation_id == research_run_id
        and event.payload.get("family_id") == family_id
        and event.payload.get("outcome") in {"PASSED", "REJECTED", "INCONCLUSIVE"}
    ]
    for event in sorted(events, key=lambda item: (item.occurred_at, item.audit_event_id)):
        latest[event.subject_id] = str(event.payload["outcome"])
    supported = sum(1 for outcome in latest.values() if outcome == "PASSED")
    rejected = sum(1 for outcome in latest.values() if outcome == "REJECTED")
    inconclusive = sum(1 for outcome in latest.values() if outcome == "INCONCLUSIVE")
    return supported, rejected, inconclusive
