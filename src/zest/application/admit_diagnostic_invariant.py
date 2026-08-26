"""Admit a diagnostic invariant hypothesis. Never a fact, ScopeRule, or vulnerability."""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.target_views import load_target_observation_views
from zest.core.enums import ActorType
from zest.data.records import AuditEventRecord, InvariantHypothesisRecord
from zest.research.differential import (
    DifferentialDimension,
    DifferentialInterpretation,
    DifferentialObservation,
)
from zest.research.invariant import (
    InvariantAdmissionDecision,
    InvariantAdmissionOutcome,
    admit_invariant,
    propose_diagnostic_echo_invariant,
)


@dataclass(frozen=True)
class AdmitDiagnosticInvariantCommand:
    research_run_id: str
    differential_id: str | None = None
    proposal_id: str | None = None


class AdmitDiagnosticInvariant:
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

    def execute(self, command: AdmitDiagnosticInvariantCommand) -> InvariantAdmissionDecision:
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            views = load_target_observation_views(uow, command.research_run_id)
            differential = None
            if command.differential_id is not None:
                record = uow.differential_observations.get(command.differential_id)
                if record is None:
                    uow.commit()
                    return InvariantAdmissionDecision(
                        outcome=InvariantAdmissionOutcome.NEEDS_MORE_CONTEXT,
                        reason_codes=("HALLUCINATED_SOURCE",),
                        hypothesis=None,
                    )
                if record.research_run_id != command.research_run_id:
                    uow.commit()
                    return InvariantAdmissionDecision(
                        outcome=InvariantAdmissionOutcome.REJECTED_CROSS_RUN,
                        reason_codes=("CROSS_RUN_SOURCE",),
                        hypothesis=None,
                    )
                differential = DifferentialObservation(
                    differential_id=record.differential_id,
                    research_run_id=record.research_run_id,
                    case_id=record.case_id,
                    baseline_observation_ids=record.baseline_observation_ids,
                    variant_observation_ids=record.variant_observation_ids,
                    changed_dimensions=tuple(
                        DifferentialDimension(item) for item in record.changed_dimensions
                    ),
                    common_dimensions=tuple(
                        DifferentialDimension(item) for item in record.common_dimensions
                    ),
                    observed_differences=dict(record.observed_differences),
                    observed_similarities=dict(record.observed_similarities),
                    interpretation=DifferentialInterpretation(record.interpretation),
                    source_refs=record.source_refs,
                    strategy_version=record.strategy_version,
                    alternative_explanation_slots=record.alternative_explanation_slots,
                )
            proposal = propose_diagnostic_echo_invariant(
                command.research_run_id,
                views,
                proposal_id=command.proposal_id or new_opaque_id(),
                differential=differential,
            )
            if proposal is None:
                uow.commit()
                return InvariantAdmissionDecision(
                    outcome=InvariantAdmissionOutcome.REJECTED_UNTESTABLE,
                    reason_codes=("EMPTY_DIAGNOSTIC_OBSERVATIONS",),
                    hypothesis=None,
                )
            resolvable = frozenset(view.observation_id for view in views)
            if differential is not None:
                resolvable = resolvable | {differential.differential_id}
            contradicting = frozenset(
                view.observation_id
                for view in views
                if view.submitted_input is not None
                and view.payload.get("echoed") != view.submitted_input
            )
            decision = admit_invariant(
                proposal,
                research_run_id=command.research_run_id,
                resolvable_source_ids=resolvable,
                contradicting_source_ids=contradicting,
            )
            if decision.admitted and decision.hypothesis is not None:
                hypothesis = decision.hypothesis
                uow.invariant_hypotheses.insert(
                    InvariantHypothesisRecord(
                        invariant_id=hypothesis.invariant_id,
                        research_run_id=hypothesis.research_run_id,
                        invariant_kind=hypothesis.invariant_kind.value,
                        status=hypothesis.status.value,
                        subject_refs=hypothesis.subject_refs,
                        expected_behavior=hypothesis.expected_behavior,
                        source_refs=hypothesis.source_refs,
                        applicability_context=dict(hypothesis.applicability_context),
                        assumptions=hypothesis.assumptions,
                        counterexample_refs=hypothesis.counterexample_refs,
                        falsification_direction=hypothesis.falsification_direction,
                        proposer_provenance=hypothesis.proposer_provenance,
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
                        event_type="INVARIANT_HYPOTHESIS_ADMITTED",
                        subject_type="invariant_hypothesis",
                        subject_id=hypothesis.invariant_id,
                        payload={
                            "status": hypothesis.status.value,
                            "not_a_fact": True,
                            "not_authorization": True,
                        },
                    )
                )
            uow.commit()
        return decision
