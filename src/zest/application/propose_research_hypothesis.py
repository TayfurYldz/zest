"""Propose a research Hypothesis through Generator, Falsifier, and admission.

Persists reasoning and admission provenance for every completed cycle.
Rejected proposals never become a Hypothesis. Does not execute a Worker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.pack_research_reasoning_context import pack_research_reasoning_context
from zest.application.registry_external_anomaly_source import (
    load_identity_anomaly_context,
)
from zest.application.runtime_outcomes import runtime_outcome_from_exception
from zest.core.enums import ActorType
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    AuditEventRecord,
    HypothesisRecord,
    ResearchAdmissionRecord,
    ResearchReasoningRecord,
)
from zest.data.unit_of_work import UnitOfWork
from zest.research.admission import AdmissionDecision, AdmissionOutcome, admit_hypothesis
from zest.research.context import (
    ChainContextSource,
    ChangeEventContextSource,
    ContextBudget,
    DifferentialContextSource,
    ExperimentSource,
    ExternalContentSource,
    HypothesisSource,
    InferenceSource,
    InvariantContextSource,
    ObservationSource,
    OpportunityContextSource,
    ResearchContext,
    ResearchContextBuilder,
)
from zest.research.cycle import generate_challenge, generate_proposal
from zest.research.exploration import OpportunityKind
from zest.research.identity_anomaly import (
    compile_identity_anomaly_experiment,
    exploratory_hypothesis_origin,
    identity_anomaly_proposal_and_challenge,
)
from zest.research.model_port import ModelCallResult, ModelPort, ModelPortError, ModelRole, ContentPolicyBlockedError
from zest.research.model_runtime import RuntimeOutcome
from zest.research.planning import plan_admitted_hypothesis
from zest.research.proposals import (
    HypothesisChallenge,
    HypothesisProposal,
    ProposalAuthorityError,
    parse_hypothesis_challenge,
    parse_hypothesis_proposal,
)
from zest.research.types import ExperimentPlan, ResearchInputError


@dataclass(frozen=True)
class ProposeResearchHypothesisCommand:
    research_run_id: str
    research_question: str
    budget_id: str
    target_reference: str
    correlation_id: str
    untrusted_external: tuple[ExternalContentSource, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    echo_message: str = "ping"
    context_budget: ContextBudget | None = None
    differential_id: str | None = None
    invariant_id: str | None = None
    chain_id: str | None = None
    opportunity_id: str | None = None
    change_event_id: str | None = None


@dataclass(frozen=True)
class ProposeResearchHypothesisResult:
    admission: AdmissionDecision
    context: ResearchContext
    experiment_plan: ExperimentPlan | None
    hypothesis_id: str | None
    generator_reasoning_id: str | None
    falsifier_reasoning_id: str | None
    admission_record_id: str | None
    generator_calls: int
    falsifier_calls: int
    failed_role: ModelRole | None = None
    runtime_outcome: RuntimeOutcome | None = None
    runtime_identity: str | None = None
    invocation_reference: str | None = None
    reason_code: str | None = None

    @property
    def outcome(self) -> AdmissionOutcome:
        return self.admission.outcome


class ProposeResearchHypothesis:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        model: ModelPort,
        *,
        clock: Clock | None = None,
        context_builder: ResearchContextBuilder | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._model = model
        self._clock = clock or SystemClock()
        self._builder = context_builder or ResearchContextBuilder()
        self._cycle_uow: UnitOfWork | None = None
        self._persist_hook: Callable[..., None] | None = None

    def execute(
        self,
        command: ProposeResearchHypothesisCommand,
        *,
        unit_of_work: UnitOfWork | None = None,
        persist_hook: Callable[..., None] | None = None,
    ) -> ProposeResearchHypothesisResult:
        self._cycle_uow = unit_of_work
        self._persist_hook = persist_hook
        try:
            return self._execute_body(command)
        finally:
            self._cycle_uow = None
            self._persist_hook = None

    def _execute_body(
        self, command: ProposeResearchHypothesisCommand
    ) -> ProposeResearchHypothesisResult:
        exploratory_opportunity = None
        with self._uow_factory.open() as uow:
            run = uow.research_runs.get(command.research_run_id)
            if run is None:
                raise ApplicationError("research run not found")
            budget = uow.issued_budgets.get(command.budget_id)
            if budget is None or budget.research_run_id != command.research_run_id:
                raise ApplicationError("issued budget not found for research run")
            if command.opportunity_id is not None:
                opportunity = uow.research_opportunities.get(command.opportunity_id)
                if opportunity is None:
                    raise ApplicationError("research opportunity not found")
                if opportunity.research_run_id != command.research_run_id:
                    raise ApplicationError("research opportunity is cross-run")
                if (
                    opportunity.opportunity_kind
                    == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
                ):
                    exploratory_opportunity = opportunity
            if exploratory_opportunity is not None:
                uow.rollback()
            else:
                self._require_pinned_context(uow, command)
                packed = pack_research_reasoning_context(
                    uow,
                    research_run_id=command.research_run_id,
                    research_question=command.research_question,
                    extra_unresolved=command.unresolved_questions,
                    opportunity_id=command.opportunity_id,
                    differential_id=command.differential_id,
                    invariant_id=command.invariant_id,
                    chain_id=command.chain_id,
                    change_event_id=command.change_event_id,
                )
                context = self._builder.build(
                    research_run_id=command.research_run_id,
                    research_question=command.research_question,
                    observations=packed.observations,
                    prior_hypotheses=packed.prior_hypotheses,
                    experiments=packed.experiments,
                    untrusted_external=command.untrusted_external,
                    inferences=packed.inferences,
                    differentials=packed.differentials,
                    invariant_hypotheses=packed.invariant_hypotheses,
                    chain_hypotheses=packed.chain_hypotheses,
                    research_opportunities=packed.research_opportunities,
                    change_events=packed.change_events,
                    engine_signals=packed.engine_signals,
                    unresolved_questions=packed.unresolved_questions,
                    budget=command.context_budget,
                )
                replayed = self._replay_matching_fingerprint(uow, command, context)
                uow.rollback()
                if replayed is not None:
                    return replayed

        if exploratory_opportunity is not None:
            return self._admit_registry_external(command, exploratory_opportunity)

        proposal: HypothesisProposal | None = None
        challenge: HypothesisChallenge | None = None
        generator_result: ModelCallResult | None = None
        falsifier_result: ModelCallResult | None = None
        generator_calls = 0
        falsifier_calls = 0

        try:
            generated = generate_proposal(
                context, self._model, correlation_id=command.correlation_id
            )
            generator_calls = 1
            generator_result = generated.model_result
            proposal = generated.proposal
        except ContentPolicyBlockedError as exc:
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
                reason=str(exc),
                reason_code="CONTENT_POLICY_BLOCKED",
                proposal=None,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
                failed_role=ModelRole.GENERATOR,
                runtime_outcome=runtime_outcome_from_exception(exc),
            )
        except ModelPortError as exc:
            outcome = runtime_outcome_from_exception(exc)
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
                reason=str(exc),
                reason_code=(
                    "CONTENT_POLICY_BLOCKED"
                    if outcome is RuntimeOutcome.CONTENT_POLICY_BLOCKED
                    else "MODEL_INVOCATION_FAILED"
                ),
                proposal=None,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
                failed_role=ModelRole.GENERATOR,
                runtime_outcome=outcome,
            )
        except ProposalAuthorityError as exc:
            generator_result = getattr(exc, "model_result", None)
            generator_calls = 1 if generator_result is not None else generator_calls
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_POLICY_CONFLICT,
                reason=str(exc),
                reason_code="POLICY_CONFLICT",
                proposal=None,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                generator_result=generator_result,
                generator_structured=_structured_or_raw(generator_result),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
            )
        except ResearchInputError as exc:
            generator_result = getattr(exc, "model_result", None)
            generator_calls = 1 if generator_result is not None else generator_calls
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
                reason=str(exc),
                reason_code="INVALID_STRUCTURED_OUTPUT",
                proposal=None,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                generator_result=generator_result,
                generator_structured=_structured_or_raw(generator_result),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
            )

        try:
            challenged = generate_challenge(
                context,
                proposal,
                self._model,
                correlation_id=command.correlation_id,
            )
            falsifier_calls = 1
            falsifier_result = challenged.model_result
            challenge = challenged.challenge
        except ContentPolicyBlockedError as exc:
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
                reason=str(exc),
                reason_code="CONTENT_POLICY_BLOCKED",
                proposal=proposal,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                proposal=proposal,
                generator_result=generator_result,
                generator_structured=proposal.to_mapping(),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
                failed_role=ModelRole.FALSIFIER,
                runtime_outcome=runtime_outcome_from_exception(exc),
            )
        except ModelPortError as exc:
            outcome = runtime_outcome_from_exception(exc)
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.MODEL_INVOCATION_FAILED,
                reason=str(exc),
                reason_code=(
                    "CONTENT_POLICY_BLOCKED"
                    if outcome is RuntimeOutcome.CONTENT_POLICY_BLOCKED
                    else "MODEL_INVOCATION_FAILED"
                ),
                proposal=proposal,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                proposal=proposal,
                generator_result=generator_result,
                generator_structured=proposal.to_mapping(),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
                failed_role=ModelRole.FALSIFIER,
                runtime_outcome=runtime_outcome_from_exception(exc),
            )
        except ProposalAuthorityError as exc:
            falsifier_result = getattr(exc, "model_result", None)
            falsifier_calls = 1 if falsifier_result is not None else falsifier_calls
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_POLICY_CONFLICT,
                reason=str(exc),
                reason_code="POLICY_CONFLICT",
                proposal=proposal,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                proposal=proposal,
                generator_result=generator_result,
                generator_structured=proposal.to_mapping(),
                falsifier_result=falsifier_result,
                falsifier_structured=_structured_or_raw(falsifier_result),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
            )
        except ResearchInputError as exc:
            falsifier_result = getattr(exc, "model_result", None)
            falsifier_calls = 1 if falsifier_result is not None else falsifier_calls
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
                reason=str(exc),
                reason_code="INVALID_STRUCTURED_OUTPUT",
                proposal=proposal,
                challenge=None,
            )
            return self._persist_cycle(
                command,
                context,
                admission,
                proposal=proposal,
                generator_result=generator_result,
                generator_structured=proposal.to_mapping(),
                falsifier_result=falsifier_result,
                falsifier_structured=_structured_or_raw(falsifier_result),
                generator_calls=generator_calls,
                falsifier_calls=falsifier_calls,
            )

        admission = admit_hypothesis(context, proposal, challenge)
        return self._persist_cycle(
            command,
            context,
            admission,
            proposal=proposal,
            challenge=challenge,
            generator_result=generator_result,
            generator_structured=proposal.to_mapping(),
            falsifier_result=falsifier_result,
            falsifier_structured=challenge.to_mapping() if challenge is not None else None,
            generator_calls=generator_calls,
            falsifier_calls=falsifier_calls,
        )

    def _require_pinned_context(self, uow, command: ProposeResearchHypothesisCommand) -> None:
        if command.differential_id is not None:
            differential = uow.differential_observations.get(command.differential_id)
            if differential is None:
                raise ApplicationError("differential observation not found")
            if differential.research_run_id != command.research_run_id:
                raise ApplicationError("differential observation is cross-run")
        if command.invariant_id is not None:
            invariant = uow.invariant_hypotheses.get(command.invariant_id)
            if invariant is None:
                raise ApplicationError("invariant hypothesis not found")
            if invariant.research_run_id != command.research_run_id:
                raise ApplicationError("invariant hypothesis is cross-run")
        if command.chain_id is not None:
            chain = uow.chain_hypotheses.get(command.chain_id)
            if chain is None:
                raise ApplicationError("chain hypothesis not found")
            if chain.research_run_id != command.research_run_id:
                raise ApplicationError("chain hypothesis is cross-run")
        if command.change_event_id is not None:
            change = uow.change_events.get(command.change_event_id)
            if change is None:
                raise ApplicationError("change event not found")
            if change.research_run_id != command.research_run_id:
                raise ApplicationError("change event is cross-run")

    def _replay_matching_fingerprint(
        self,
        uow: UnitOfWork,
        command: ProposeResearchHypothesisCommand,
        context: ResearchContext,
    ) -> ProposeResearchHypothesisResult | None:
        matches = [
            item
            for item in uow.research_admissions.list_for_research_run(command.research_run_id)
            if item.context_fingerprint == context.fingerprint
        ]
        if not matches:
            by_correlation = [
                item
                for item in uow.research_reasoning.list_for_research_run(command.research_run_id)
                if item.correlation_id == command.correlation_id
            ]
            if by_correlation:
                linked = {
                    item.reasoning_record_id for item in by_correlation
                }
                matches = [
                    item
                    for item in uow.research_admissions.list_for_research_run(
                        command.research_run_id
                    )
                    if item.generator_reasoning_record_id in linked
                    or item.falsifier_reasoning_record_id in linked
                ]
        if not matches:
            return None
        record = sorted(matches, key=lambda item: item.created_at)[-1]
        proposal = None
        challenge = None
        if record.generator_reasoning_record_id is not None:
            generated = uow.research_reasoning.get(record.generator_reasoning_record_id)
            if generated is not None:
                try:
                    proposal = parse_hypothesis_proposal(generated.structured_output)
                except (ResearchInputError, ProposalAuthorityError):
                    proposal = None
        if record.falsifier_reasoning_record_id is not None:
            challenged = uow.research_reasoning.get(record.falsifier_reasoning_record_id)
            if challenged is not None:
                try:
                    challenge = parse_hypothesis_challenge(challenged.structured_output)
                except (ResearchInputError, ProposalAuthorityError):
                    challenge = None
        admission = AdmissionDecision(
            outcome=AdmissionOutcome(record.outcome),
            reason=record.reason,
            reason_code=record.reason_code,
            proposal=proposal,
            challenge=challenge,
        )
        plan = None
        if (
            admission.admitted
            and proposal is not None
            and challenge is not None
            and record.admitted_hypothesis_id is not None
        ):
            try:
                plan = plan_admitted_hypothesis(
                    record.admitted_hypothesis_id,
                    proposal,
                    challenge,
                    budget_id=command.budget_id,
                    target_reference=command.target_reference,
                    message=command.echo_message,
                )
            except ResearchInputError:
                plan = None
        return ProposeResearchHypothesisResult(
            admission=admission,
            context=context,
            experiment_plan=plan,
            hypothesis_id=record.admitted_hypothesis_id,
            generator_reasoning_id=record.generator_reasoning_record_id,
            falsifier_reasoning_id=record.falsifier_reasoning_record_id,
            admission_record_id=record.admission_record_id,
            generator_calls=0,
            falsifier_calls=0,
            reason_code=admission.reason_code,
        )

    def _admit_registry_external(
        self, command: ProposeResearchHypothesisCommand, opportunity
    ) -> ProposeResearchHypothesisResult:
        """Deterministic identity-anomaly admission. Does not invoke ModelPort."""

        now = self._clock.now()
        with self._uow_factory.open() as uow:
            if not opportunity.source_refs:
                raise ApplicationError("exploratory opportunity is missing source_refs")
            source_id = opportunity.source_refs[0]
            anomaly = load_identity_anomaly_context(
                uow, research_run_id=command.research_run_id, source_id=source_id
            )
            origin = exploratory_hypothesis_origin(anomaly.structural_identity())
            existing = [
                item
                for item in uow.hypotheses.list_for_research_run(command.research_run_id)
                if item.origin_reference == origin
            ]
            observation_sources = tuple(
                ObservationSource(
                    observation_id=record.observation_id,
                    observation_kind=record.observation_kind,
                    payload=dict(record.payload),
                )
                for record in uow.observations.list_for_research_run(command.research_run_id)
                if record.observation_id in set(anomaly.observation_ids)
                or record.observation_id == source_id
            )
            differential_sources: tuple[DifferentialContextSource, ...] = ()
            if anomaly.source_kind == "DIFFERENTIAL":
                differential = uow.differential_observations.get(source_id)
                if differential is not None:
                    differential_sources = (
                        DifferentialContextSource(
                            differential_id=differential.differential_id,
                            statement=(
                                "Identity differential comparison. Difference is not a "
                                "vulnerability and not Evidence."
                            ),
                            source_references=differential.source_refs,
                            interpretation=differential.interpretation,
                            payload={
                                "changed_dimensions": list(differential.changed_dimensions),
                                "observed_differences": dict(differential.observed_differences),
                            },
                        ),
                    )
            opportunity_sources = (
                OpportunityContextSource(
                    opportunity_id=opportunity.opportunity_id,
                    statement=(
                        "Selected registry-external identity-anomaly opportunity. "
                        "Selection is not Hypothesis truth and not Core authorization."
                    ),
                    source_references=opportunity.source_refs,
                    payload={
                        "opportunity_kind": opportunity.opportunity_kind,
                        "mode": opportunity.mode,
                        "structural_identity": opportunity.structural_identity,
                        "registry_external": True,
                    },
                ),
            )
            uow.rollback()

        context = self._builder.build(
            research_run_id=command.research_run_id,
            research_question=command.research_question,
            observations=observation_sources,
            differentials=differential_sources,
            research_opportunities=opportunity_sources,
            budget=command.context_budget,
        )
        if not anomaly.registry_external:
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_UNSUPPORTED,
                reason="identity anomaly is not registry-external",
                reason_code=anomaly.reason_code or "NOT_REGISTRY_EXTERNAL",
                proposal=None,
                challenge=None,
            )
            return self._persist_exploratory(
                command, context, admission, origin_reference=origin, now=now
            )
        try:
            proposal, challenge = identity_anomaly_proposal_and_challenge(anomaly)
        except ResearchInputError as exc:
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
                reason=str(exc),
                reason_code="INVALID_IDENTITY_ANOMALY",
                proposal=None,
                challenge=None,
            )
            return self._persist_exploratory(
                command, context, admission, origin_reference=origin, now=now
            )
        admission = admit_hypothesis(context, proposal, challenge)
        if not admission.admitted:
            return self._persist_exploratory(
                command,
                context,
                admission,
                proposal=proposal,
                challenge=challenge,
                origin_reference=origin,
                now=now,
            )
        hypothesis_id = existing[0].hypothesis_id if existing else new_opaque_id()
        try:
            plan = compile_identity_anomaly_experiment(
                anomaly,
                hypothesis_id=hypothesis_id,
                budget_id=command.budget_id,
                target_reference=command.target_reference,
            )
        except ResearchInputError as exc:
            admission = AdmissionDecision(
                outcome=AdmissionOutcome.REJECTED_UNTESTABLE,
                reason=str(exc),
                reason_code="IDENTITY_ANOMALY_COMPILE_REJECTED",
                proposal=proposal,
                challenge=challenge,
            )
            return self._persist_exploratory(
                command,
                context,
                admission,
                proposal=proposal,
                challenge=challenge,
                origin_reference=origin,
                now=now,
                hypothesis_id=hypothesis_id if existing else None,
            )
        return self._persist_exploratory(
            command,
            context,
            admission,
            proposal=proposal,
            challenge=challenge,
            origin_reference=origin,
            now=now,
            hypothesis_id=hypothesis_id,
            plan=plan,
            persist_existing=bool(existing),
        )

    def _persist_exploratory(
        self,
        command: ProposeResearchHypothesisCommand,
        context: ResearchContext,
        admission: AdmissionDecision,
        *,
        origin_reference: str,
        now,
        proposal: HypothesisProposal | None = None,
        challenge: HypothesisChallenge | None = None,
        hypothesis_id: str | None = None,
        plan: ExperimentPlan | None = None,
        persist_existing: bool = False,
    ) -> ProposeResearchHypothesisResult:
        del challenge
        admission_record_id = new_opaque_id()
        persist_id = hypothesis_id if admission.admitted else None

        def _persist(uow: UnitOfWork) -> None:
            nonlocal persist_id
            if persist_id is not None and proposal is not None and not persist_existing:
                record = HypothesisRecord(
                    hypothesis_id=persist_id,
                    research_run_id=command.research_run_id,
                    claim=proposal.proposed_claim,
                    created_at=now,
                    origin_reference=origin_reference,
                )
                try:
                    uow.hypotheses.insert(record)
                except PersistenceConflictError:
                    matched = [
                        item
                        for item in uow.hypotheses.list_for_research_run(
                            command.research_run_id
                        )
                        if item.origin_reference == origin_reference
                    ]
                    if not matched:
                        raise
                    persist_id = matched[0].hypothesis_id
            uow.research_admissions.insert(
                ResearchAdmissionRecord(
                    admission_record_id=admission_record_id,
                    research_run_id=command.research_run_id,
                    outcome=admission.outcome.value,
                    reason=admission.reason,
                    reason_code=admission.reason_code,
                    context_fingerprint=context.fingerprint,
                    created_at=now,
                    generator_reasoning_record_id=None,
                    falsifier_reasoning_record_id=None,
                    admitted_hypothesis_id=persist_id,
                )
            )
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=new_opaque_id(),
                    occurred_at=now,
                    actor_id="control-plane:registry-external-anomaly",
                    actor_type=ActorType.CONTROL_PLANE.value,
                    event_type=(
                        "REGISTRY_EXTERNAL_HYPOTHESIS_ADMITTED"
                        if persist_id is not None
                        else "REGISTRY_EXTERNAL_HYPOTHESIS_NOT_ADMITTED"
                    ),
                    subject_type="research_run",
                    subject_id=command.research_run_id,
                    payload={
                        "hypothesis_id": persist_id,
                        "opportunity_id": command.opportunity_id,
                        "origin_reference": origin_reference,
                        "admitted": persist_id is not None,
                        "reason_code": admission.reason_code,
                        "registry_external": True,
                        "not_authorization": True,
                        "not_a_vulnerability": True,
                        "not_hunter_family_write": True,
                    },
                    correlation_id=command.correlation_id,
                )
            )
            if self._persist_hook is not None:
                self._persist_hook(uow, hypothesis_id=persist_id)

        if self._cycle_uow is None:
            with self._uow_factory.open() as uow:
                _persist(uow)
                uow.commit()
        else:
            _persist(self._cycle_uow)
        return ProposeResearchHypothesisResult(
            admission=admission,
            context=context,
            experiment_plan=plan if persist_id is not None else None,
            hypothesis_id=persist_id,
            generator_reasoning_id=None,
            falsifier_reasoning_id=None,
            admission_record_id=admission_record_id,
            generator_calls=0,
            falsifier_calls=0,
            reason_code=admission.reason_code,
        )

    def _persist_cycle(
        self,
        command: ProposeResearchHypothesisCommand,
        context: ResearchContext,
        admission: AdmissionDecision,
        *,
        proposal: HypothesisProposal | None = None,
        challenge: HypothesisChallenge | None = None,
        generator_result: ModelCallResult | None = None,
        generator_structured: Mapping[str, Any] | None = None,
        falsifier_result: ModelCallResult | None = None,
        falsifier_structured: Mapping[str, Any] | None = None,
        generator_calls: int,
        falsifier_calls: int,
        failed_role: ModelRole | None = None,
        runtime_outcome: RuntimeOutcome | None = None,
        runtime_identity: str | None = None,
        invocation_reference: str | None = None,
    ) -> ProposeResearchHypothesisResult:
        now = self._clock.now()
        admitted = admission.admitted
        hypothesis_id = new_opaque_id() if admitted else None
        generator_reasoning_id = new_opaque_id() if generator_result is not None else None
        falsifier_reasoning_id = new_opaque_id() if falsifier_result is not None else None
        admission_record_id = new_opaque_id()
        plan = None
        if admitted and proposal is not None and challenge is not None and hypothesis_id is not None:
            try:
                plan = plan_admitted_hypothesis(
                    hypothesis_id,
                    proposal,
                    challenge,
                    budget_id=command.budget_id,
                    target_reference=command.target_reference,
                    message=command.echo_message,
                )
            except ResearchInputError as exc:
                # Model output is research input, never execution authority.
                # Unsupported capabilities are distinguished from other
                # planning/input failures so the audit record remains truthful.
                message = str(exc)
                unsupported = (
                    message
                    == "unknown or unsupported capability cannot be planned"
                )
                admission = AdmissionDecision(
                    outcome=(
                        AdmissionOutcome.REJECTED_UNSUPPORTED
                        if unsupported
                        else AdmissionOutcome.REJECTED_UNTESTABLE
                    ),
                    reason=message,
                    reason_code=(
                        "UNSUPPORTED_CAPABILITY"
                        if unsupported
                        else "PLANNING_INPUT_REJECTED"
                    ),
                    proposal=proposal,
                    challenge=challenge,
                )
                hypothesis_id = None
        identity = runtime_identity
        if identity is None:
            source = generator_result or falsifier_result
            if source is not None:
                identity = source.adapter_identity
        reference = invocation_reference or command.correlation_id

        def _persist(uow: UnitOfWork) -> None:
            self._write_cycle(
                uow,
                command,
                context,
                admission,
                now,
                hypothesis_id,
                proposal,
                generator_reasoning_id,
                generator_result,
                generator_structured,
                falsifier_reasoning_id,
                falsifier_result,
                falsifier_structured,
                admission_record_id,
            )
            if self._persist_hook is not None:
                self._persist_hook(uow, hypothesis_id=hypothesis_id)

        if self._cycle_uow is None:
            with self._uow_factory.open() as uow:
                _persist(uow)
                uow.commit()
        else:
            _persist(self._cycle_uow)
        return ProposeResearchHypothesisResult(
            admission=admission,
            context=context,
            experiment_plan=plan,
            hypothesis_id=hypothesis_id,
            generator_reasoning_id=generator_reasoning_id,
            falsifier_reasoning_id=falsifier_reasoning_id,
            admission_record_id=admission_record_id,
            generator_calls=generator_calls,
            falsifier_calls=falsifier_calls,
            failed_role=failed_role,
            runtime_outcome=runtime_outcome,
            runtime_identity=identity,
            invocation_reference=reference,
            reason_code=admission.reason_code,
        )

    def _write_cycle(
        self,
        uow: UnitOfWork,
        command: ProposeResearchHypothesisCommand,
        context: ResearchContext,
        admission: AdmissionDecision,
        now,
        hypothesis_id: str | None,
        proposal: HypothesisProposal | None,
        generator_reasoning_id: str | None,
        generator_result: ModelCallResult | None,
        generator_structured: Mapping[str, Any] | None,
        falsifier_reasoning_id: str | None,
        falsifier_result: ModelCallResult | None,
        falsifier_structured: Mapping[str, Any] | None,
        admission_record_id: str,
    ) -> None:
        if hypothesis_id is not None and proposal is not None:
            uow.hypotheses.insert(
                HypothesisRecord(
                    hypothesis_id=hypothesis_id,
                    research_run_id=command.research_run_id,
                    claim=proposal.proposed_claim,
                    created_at=now,
                    origin_reference=generator_reasoning_id,
                )
            )
        if generator_reasoning_id is not None and generator_result is not None:
            uow.research_reasoning.insert(
                self._reasoning_record(
                    reasoning_record_id=generator_reasoning_id,
                    research_run_id=command.research_run_id,
                    hypothesis_id=hypothesis_id,
                    role=ModelRole.GENERATOR,
                    generated=generator_result,
                    structured_output=dict(generator_structured or generator_result.structured_output),
                    fingerprint=context.fingerprint,
                    correlation_id=command.correlation_id,
                    created_at=now,
                )
            )
        if falsifier_reasoning_id is not None and falsifier_result is not None:
            uow.research_reasoning.insert(
                self._reasoning_record(
                    reasoning_record_id=falsifier_reasoning_id,
                    research_run_id=command.research_run_id,
                    hypothesis_id=hypothesis_id,
                    role=ModelRole.FALSIFIER,
                    generated=falsifier_result,
                    structured_output=dict(
                        falsifier_structured or falsifier_result.structured_output
                    ),
                    fingerprint=context.fingerprint,
                    correlation_id=command.correlation_id,
                    created_at=now,
                )
            )
        uow.research_admissions.insert(
            ResearchAdmissionRecord(
                admission_record_id=admission_record_id,
                research_run_id=command.research_run_id,
                outcome=admission.outcome.value,
                reason=admission.reason,
                reason_code=admission.reason_code,
                context_fingerprint=context.fingerprint,
                created_at=now,
                generator_reasoning_record_id=generator_reasoning_id,
                falsifier_reasoning_record_id=falsifier_reasoning_id,
                admitted_hypothesis_id=hypothesis_id,
            )
        )

    def _reasoning_record(
        self,
        *,
        reasoning_record_id: str,
        research_run_id: str,
        hypothesis_id: str | None,
        role: ModelRole,
        generated: ModelCallResult,
        structured_output: dict[str, object],
        fingerprint: str,
        correlation_id: str,
        created_at,
    ) -> ResearchReasoningRecord:
        return ResearchReasoningRecord(
            reasoning_record_id=reasoning_record_id,
            research_run_id=research_run_id,
            hypothesis_id=hypothesis_id,
            role=role.value,
            adapter_identity=generated.adapter_identity,
            provider_adapter_identity=generated.provider_adapter_identity,
            correlation_id=correlation_id,
            context_fingerprint=fingerprint,
            structured_output=structured_output,
            created_at=created_at,
            model_id=generated.model_id,
            model_version=generated.model_version,
        )


def _structured_or_raw(result: ModelCallResult | None) -> Mapping[str, Any] | None:
    if result is None:
        return None
    return dict(result.structured_output)
