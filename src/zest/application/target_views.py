"""Assemble typed target-observation views from SoR. Application-only."""

from __future__ import annotations

from zest.data.unit_of_work import UnitOfWork
from zest.research.target_model import TargetObservationView


def load_target_observation_views(
    uow: UnitOfWork, research_run_id: str
) -> tuple[TargetObservationView, ...]:
    views: list[TargetObservationView] = []
    for result in uow.worker_results.list_for_research_run(research_run_id):
        plan = uow.experiment_plans.get(result.experiment_id)
        resource_handle = result.experiment_id
        submitted = None
        if plan is not None:
            resource_handle = plan.target_reference
            message = plan.arguments.get("message")
            if isinstance(message, str) and message.strip():
                submitted = message
        for observation in uow.observations.list_for_worker_result(result.worker_result_id):
            views.append(
                TargetObservationView(
                    observation_id=observation.observation_id,
                    research_run_id=result.research_run_id,
                    experiment_id=result.experiment_id,
                    observation_kind=observation.observation_kind,
                    payload=dict(observation.payload),
                    capability=result.worker_capability,
                    action=result.action,
                    actor_handle=result.worker_id,
                    resource_handle=resource_handle,
                    submitted_input=submitted,
                )
            )
    return tuple(views)
