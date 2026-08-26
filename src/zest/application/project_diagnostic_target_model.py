"""Project a diagnostic Target Model from SoR records. Does not create Evidence."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.ports import UnitOfWorkFactory
from zest.application.target_views import load_target_observation_views
from zest.research.target_model import (
    TargetElement,
    TargetElementKind,
    TargetEpistemicStatus,
    TargetModelProjection,
    project_diagnostic_target_model,
)


@dataclass(frozen=True)
class ProjectDiagnosticTargetModelCommand:
    research_run_id: str


class ProjectDiagnosticTargetModel:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(
        self, command: ProjectDiagnosticTargetModelCommand
    ) -> TargetModelProjection:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            views = load_target_observation_views(uow, command.research_run_id)
            inferences: list[TargetElement] = []
            for record in uow.target_inferences.list_for_research_run(command.research_run_id):
                inferences.append(
                    TargetElement(
                        element_id=record.inference_id,
                        kind=TargetElementKind(record.kind),
                        epistemic_status=TargetEpistemicStatus(record.epistemic_status),
                        research_run_id=record.research_run_id,
                        opaque_ref=record.opaque_ref,
                        statement=record.statement,
                        source_refs=record.source_refs,
                        attributes=dict(record.attributes),
                        strategy_version=record.strategy_version,
                    )
                )
            projection = project_diagnostic_target_model(
                command.research_run_id, views, inferences=tuple(inferences)
            )
            uow.commit()
        return projection
