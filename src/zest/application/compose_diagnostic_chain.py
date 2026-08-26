"""Compose a bounded diagnostic chain hypothesis. Does not dispatch a Worker."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, ChainHypothesisRecord
from zest.research.chain import (
    ChainDecision,
    ChainOutcome,
    ChainSearchLimits,
    compose_diagnostic_echo_chains,
    experiment_plan_for_chain_step,
)
from zest.research.types import ExperimentPlan


@dataclass(frozen=True)
class ComposeDiagnosticChainCommand:
    research_run_id: str
    invariant_id: str | None = None
    limits: ChainSearchLimits | None = None
    budget_id: str | None = None
    target_reference: str | None = None
    hypothesis_id: str | None = None


@dataclass(frozen=True)
class ComposeDiagnosticChainResult:
    decisions: tuple[ChainDecision, ...]
    suggested_plans: tuple[ExperimentPlan, ...]

    @property
    def admitted(self) -> tuple[ChainDecision, ...]:
        return tuple(item for item in self.decisions if item.admitted)


class ComposeDiagnosticChain:
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

    def execute(self, command: ComposeDiagnosticChainCommand) -> ComposeDiagnosticChainResult:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            if command.invariant_id is not None:
                invariant = uow.invariant_hypotheses.get(command.invariant_id)
                if invariant is None:
                    raise ApplicationError("invariant hypothesis not found")
                if invariant.research_run_id != command.research_run_id:
                    uow.commit()
                    return ComposeDiagnosticChainResult(
                        decisions=(
                            ChainDecision(
                                outcome=ChainOutcome.REJECTED_CROSS_RUN,
                                reason_codes=("CROSS_RUN_SOURCE",),
                                hypothesis=None,
                            ),
                        ),
                        suggested_plans=(),
                    )
            views = load_target_observation_views(uow, command.research_run_id)
            decisions = compose_diagnostic_echo_chains(
                command.research_run_id,
                views,
                chain_id_prefix=new_opaque_id(),
                invariant_id=command.invariant_id,
                limits=command.limits,
            )
            plans: list[ExperimentPlan] = []
            for decision in decisions:
                if not decision.admitted or decision.hypothesis is None:
                    continue
                hypothesis = decision.hypothesis
                uow.chain_hypotheses.insert(
                    ChainHypothesisRecord(
                        chain_id=hypothesis.chain_id,
                        research_run_id=hypothesis.research_run_id,
                        structural_identity=hypothesis.structural_identity,
                        steps=tuple(step.to_mapping() for step in hypothesis.steps),
                        source_refs=hypothesis.source_refs,
                        preconditions=hypothesis.preconditions,
                        expected_resulting_capability=hypothesis.expected_resulting_capability,
                        unresolved_assumptions=hypothesis.unresolved_assumptions,
                        falsification_points=hypothesis.falsification_points,
                        descriptive_features=hypothesis.descriptive_features(),
                        strategy_version=hypothesis.strategy_version,
                        created_at=self._clock.now(),
                    )
                )
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="CHAIN_HYPOTHESIS_ADMITTED",
                        subject_type="chain_hypothesis",
                        subject_id=hypothesis.chain_id,
                        payload={
                            "not_an_exploit": True,
                            "not_evidence": True,
                            "depth": hypothesis.depth,
                        },
                    )
                )
                last = hypothesis.steps[-1]
                if (
                    command.budget_id is not None
                    and command.target_reference is not None
                    and command.hypothesis_id is not None
                ):
                    plans.append(
                        experiment_plan_for_chain_step(
                            last,
                            hypothesis_id=command.hypothesis_id,
                            budget_id=command.budget_id,
                            target_reference=command.target_reference,
                            message="chain-next",
                        )
                    )
            uow.commit()
        return ComposeDiagnosticChainResult(
            decisions=decisions, suggested_plans=tuple(plans)
        )
