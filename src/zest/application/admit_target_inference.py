"""Admit a target-model inference. Never OBSERVED. Not authorization truth."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, TargetInferenceRecord
from zest.research.target_model import (
    TargetInferenceDecision,
    TargetInferenceDraft,
    TargetInferenceOutcome,
    admit_target_inference,
)


@dataclass(frozen=True)
class AdmitTargetInferenceCommand:
    draft: TargetInferenceDraft


class AdmitTargetInference:
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

    def execute(self, command: AdmitTargetInferenceCommand) -> TargetInferenceDecision:
        draft = command.draft
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(draft.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            views = load_target_observation_views(uow, draft.research_run_id)
            for ref in draft.source_refs:
                observation = uow.observations.get(ref)
                if observation is None:
                    continue
                result = uow.worker_results.get(observation.worker_result_id)
                if result is not None and result.research_run_id != draft.research_run_id:
                    uow.commit()
                    return TargetInferenceDecision(
                        outcome=TargetInferenceOutcome.REJECTED_CROSS_RUN,
                        reason_codes=("CROSS_RUN_SOURCE",),
                        element=None,
                    )
            resolvable = frozenset(view.observation_id for view in views)
            decision = admit_target_inference(
                draft,
                research_run_id=draft.research_run_id,
                resolvable_source_ids=resolvable,
            )
            if decision.admitted and decision.element is not None:
                uow.target_inferences.insert(
                    TargetInferenceRecord(
                        inference_id=decision.element.element_id,
                        research_run_id=decision.element.research_run_id,
                        kind=decision.element.kind.value,
                        epistemic_status=decision.element.epistemic_status.value,
                        opaque_ref=decision.element.opaque_ref,
                        statement=decision.element.statement,
                        source_refs=decision.element.source_refs,
                        attributes=dict(decision.element.attributes),
                        strategy_version=decision.element.strategy_version,
                        created_at=self._clock.now(),
                    )
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="TARGET_INFERENCE_ADMITTED",
                        subject_type="target_inference",
                        subject_id=decision.element.element_id,
                        payload={"epistemic_status": decision.element.epistemic_status.value},
                    )
                )
            uow.commit()
        return decision
