"""Coordinate Research runtime routing. Does not import Integrations or subprocess."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord
from zest.research.model_port import ModelCallRequest, ModelCallResult, ModelPort, ModelRole
from zest.research.routing import (
    ROUTING_POLICY_VERSION,
    RoutingRequest,
    RuntimeOutcome,
    RuntimeSelectionDecision,
    reconsider_runtime,
    select_runtime,
)


@dataclass(frozen=True)
class SelectResearchRuntimeCommand:
    research_run_id: str
    request: RoutingRequest


@dataclass(frozen=True)
class SelectResearchRuntimeResult:
    decision: RuntimeSelectionDecision
    audit_event_id: str


class SelectResearchRuntime:
    """Application coordinates routing. Core does not select models."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(self, command: SelectResearchRuntimeCommand) -> SelectResearchRuntimeResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            decision = select_runtime(command.request)
            audit_id = f"ae:route:{new_opaque_id()}"
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=audit_id,
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RUNTIME_ROUTING_DECISION",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=decision.to_mapping(),
                )
            )
            uow.commit()
        return SelectResearchRuntimeResult(decision=decision, audit_event_id=audit_id)

    def reconsider(
        self,
        command: SelectResearchRuntimeCommand,
        previous: RuntimeSelectionDecision,
        outcome: RuntimeOutcome,
    ) -> SelectResearchRuntimeResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            decision = reconsider_runtime(command.request, previous, outcome)
            audit_id = f"ae:route:{new_opaque_id()}"
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=audit_id,
                    occurred_at=self._clock.now(),
                    actor_id=self._actor_id,
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type="RUNTIME_ROUTING_RECONSIDER",
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload=decision.to_mapping(),
                )
            )
            uow.commit()
        return SelectResearchRuntimeResult(decision=decision, audit_event_id=audit_id)


class RoleRoutedModelPort:
    """Dispatches an already-selected port per role. Does not re-route or score models."""

    def __init__(self, ports: dict[ModelRole, ModelPort]) -> None:
        if ModelRole.GENERATOR not in ports or ModelRole.FALSIFIER not in ports:
            raise ApplicationError("RoleRoutedModelPort requires GENERATOR and FALSIFIER ports")
        self._ports = dict(ports)
        self.policy_version = ROUTING_POLICY_VERSION

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        port = self._ports.get(request.role)
        if port is None:
            raise ApplicationError(f"no runtime was selected for role {request.role.value}")
        return port.complete(request)
