"""Application use case for operator control of one autonomous run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    OrchestrationTickResult,
    StartAutonomousResearchCommand,
)
from zest.application.local_run_supervisor import LocalRunSupervisorRegistry
from zest.application.reconcile_research_run import (
    ReconcileResearchRun,
    ReconcileResearchRunCommand,
    ReconciliationResolution,
)
from zest.data.errors import LeaseFencingError


@dataclass
class ResearchRunControl:
    """Coordinates controller lifecycle and the local supervisor only."""

    controller: AutonomousResearchController
    supervisors: LocalRunSupervisorRegistry
    uow_factory: object
    cadence_seconds: float = 0.25
    prepare_start: Callable[[str], None] | None = None
    reconciler: ReconcileResearchRun | None = None

    def start(self, command: StartAutonomousResearchCommand) -> OrchestrationTickResult:
        if self.prepare_start is not None:
            self.prepare_start(command.research_run_id)
        self._reconcile_stale_running(command.research_run_id)
        result = self.controller.start(command)
        if result.state == "READY":
            self.supervisors.start(
                research_run_id=command.research_run_id,
                controller=self.controller,
                command=command,
                uow_factory=self.uow_factory,
                cadence_seconds=self.cadence_seconds,
            )
        return result

    def _reconcile_stale_running(self, research_run_id: str) -> None:
        """Fail closed a crash-left RUNNING checkpoint before attaching a new owner.

        Only acts when this process has no live supervisor thread for the run
        (i.e. we are about to become its first local owner since this process
        started). A run already owned by a live thread in this process is
        left untouched; that is normal in-flight operation, not a crash.
        """
        if self.reconciler is None:
            return
        if self.supervisors.is_active(research_run_id):
            return
        outcome = self.reconciler.execute(
            ReconcileResearchRunCommand(
                research_run_id=research_run_id, stale_running=True
            )
        )
        for item in outcome.items:
            if (
                item.subject_type == "research_orchestration"
                and item.resolution is ReconciliationResolution.MARK_OPERATIONAL_FAILURE
            ):
                try:
                    self.controller.mark_operational_failure(
                        research_run_id, reason=item.reason
                    )
                except LeaseFencingError:
                    # A different runtime instance holds a live lease on this
                    # run right now: it is not actually stale, only
                    # unsupervised by *this* process. Leave it to its real
                    # owner rather than overriding it.
                    pass

    def pause(self, research_run_id: str) -> OrchestrationTickResult:
        return self.controller.pause(research_run_id)

    def resume(
        self,
        command: StartAutonomousResearchCommand,
    ) -> OrchestrationTickResult:
        result = self.controller.resume(command.research_run_id)
        if result.state == "READY":
            self.supervisors.start(
                research_run_id=command.research_run_id,
                controller=self.controller,
                command=command,
                uow_factory=self.uow_factory,
                cadence_seconds=self.cadence_seconds,
            )
        return result

    def cancel(self, research_run_id: str) -> OrchestrationTickResult:
        result = self.controller.cancel(research_run_id)
        self.supervisors.stop(research_run_id)
        return result
