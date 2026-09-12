"""Admit diagnostic-echo Evidence. Application coordinates; Research decides.

Does not create Candidate, Finding, or Verification. Does not invoke a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.research_truth_provenance import (
    require_promotable_hypothesis,
)
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    EvidenceAdmissionRecord,
    EvidenceRecord,
    ExecutionAttemptRecord,
    HypothesisAssessmentRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from zest.data.uniqueness import (
    UQ_EVIDENCE_EXPERIMENT_SUPPORTING,
    is_uniqueness_conflict,
)
from zest.research.assessment import (
    AssessmentOutcome,
    DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
    HTTP_STATE_TRANSITION_EVALUATION_STRATEGY,
)
from zest.research.evidence import (
    EVIDENCE_ADMISSION_POLICY_VERSION,
    HTTP_AUTHORIZATION_CONTROL_HELD_CLAIM,
    HTTP_STATE_TRANSITION_CONTROL_HELD_CLAIM,
    EvidenceAdmissionContext,
    EvidenceAdmissionDecision,
    EvidenceAdmissionOutcome,
    EvidenceObservationRef,
    EvidencePolarity,
    EvidenceProposal,
    admit_evidence,
    propose_authorization_differential_evidence,
    propose_diagnostic_echo_evidence,
    propose_state_transition_evidence,
)


@dataclass(frozen=True)
class AdmitDiagnosticEvidenceCommand:
    experiment_id: str
    assessment_id: str | None = None
    proposal: EvidenceProposal | None = None


@dataclass(frozen=True)
class AdmitDiagnosticEvidenceResult:
    outcome: EvidenceAdmissionOutcome
    admission_record_id: str
    evidence_id: str | None
    reason_codes: tuple[str, ...]
    proposal_id: str


class AdmitDiagnosticEvidence:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(self, command: AdmitDiagnosticEvidenceCommand) -> AdmitDiagnosticEvidenceResult:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")

            require_promotable_hypothesis(
                uow,
                experiment.hypothesis_id,
            )

            plan = uow.experiment_plans.get(command.experiment_id)
            if plan is None:
                raise ApplicationError("durable experiment plan not found")
            if plan.evaluation_strategy not in {
                DIAGNOSTIC_ECHO_EVALUATION_STRATEGY,
                HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY,
                HTTP_STATE_TRANSITION_EVALUATION_STRATEGY,
            }:
                raise ApplicationError("AdmitDiagnosticEvidence has no evaluator for this strategy")
            assessments = uow.hypothesis_assessments.list_for_experiment(experiment.experiment_id)
            assessment = _select_assessment(assessments, command.assessment_id)
            if command.proposal is None and assessment is not None:
                existing = _admitted_for_assessment(
                    uow.evidence_admissions.list_for_research_run(experiment.research_run_id),
                    assessment.assessment_id,
                )
                if existing is not None:
                    uow.rollback()
                    return _result_from_admission(existing)
            observations = uow.observations.list_for_experiment(experiment.experiment_id)
            attempts = uow.execution_attempts.list_for_experiment(experiment.experiment_id)
            attempt = attempts[0] if attempts else None
            results = uow.worker_results.list_for_experiment(experiment.experiment_id)
            worker_result = results[0] if results else None
            context = _admission_context(
                experiment_id=experiment.experiment_id,
                research_run_id=experiment.research_run_id,
                hypothesis_id=experiment.hypothesis_id,
                evaluation_strategy=plan.evaluation_strategy,
                assessment=assessment,
                observations=tuple(observations),
                attempt=attempt,
                worker_result=worker_result,
                experiment_execution_state=experiment.execution_state,
                requested_observation_ids=(
                    command.proposal.observation_ids if command.proposal is not None else ()
                ),
            )
            proposal = command.proposal
            if proposal is None:
                if plan.evaluation_strategy == HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY:
                    proposal = propose_authorization_differential_evidence(
                        context, proposal_id=new_opaque_id()
                    )
                elif plan.evaluation_strategy == HTTP_STATE_TRANSITION_EVALUATION_STRATEGY:
                    proposal = propose_state_transition_evidence(
                        context, proposal_id=new_opaque_id()
                    )
                else:
                    proposal = propose_diagnostic_echo_evidence(
                        context, proposal_id=new_opaque_id()
                    )
            if proposal is None and context.execution_unusable:
                proposal = EvidenceProposal(
                    proposal_id=new_opaque_id(),
                    research_run_id=context.research_run_id,
                    hypothesis_id=context.hypothesis_id,
                    experiment_id=context.experiment_id,
                    observation_ids=tuple(item.observation_id for item in context.observations),
                    assessment_ids=() if context.assessment_id is None else (context.assessment_id,),
                    polarity=EvidencePolarity.NEUTRAL,
                    claim_scope="diagnostic execution was unusable; no Evidence",
                    rationale={"reason_code": "EXECUTION_UNUSABLE", "not_vulnerability_evidence": True},
                    provenance={"source": "diagnostic.echo.unusable"},
                )
            if (
                proposal is None
                and plan.evaluation_strategy == HTTP_AUTHORIZATION_DIFFERENTIAL_EVALUATION_STRATEGY
            ):
                proposal = EvidenceProposal(
                    proposal_id=new_opaque_id(),
                    research_run_id=context.research_run_id,
                    hypothesis_id=context.hypothesis_id,
                    experiment_id=context.experiment_id,
                    observation_ids=tuple(item.observation_id for item in context.observations),
                    assessment_ids=() if context.assessment_id is None else (context.assessment_id,),
                    polarity=EvidencePolarity.NEUTRAL,
                    claim_scope=HTTP_AUTHORIZATION_CONTROL_HELD_CLAIM,
                    rationale={
                        "reason_code": "DIFFERENTIAL_DOES_NOT_ESTABLISH_MISSING_OBJECT_ACCESS_CONTROL"
                    },
                    provenance={"source": "http.authorization.differential.rejected"},
                )
            if (
                proposal is None
                and plan.evaluation_strategy == HTTP_STATE_TRANSITION_EVALUATION_STRATEGY
            ):
                proposal = EvidenceProposal(
                    proposal_id=new_opaque_id(),
                    research_run_id=context.research_run_id,
                    hypothesis_id=context.hypothesis_id,
                    experiment_id=context.experiment_id,
                    observation_ids=tuple(item.observation_id for item in context.observations),
                    assessment_ids=() if context.assessment_id is None else (context.assessment_id,),
                    polarity=EvidencePolarity.NEUTRAL,
                    claim_scope=HTTP_STATE_TRANSITION_CONTROL_HELD_CLAIM,
                    rationale={
                        "reason_code": "STATE_TRANSITION_DOES_NOT_ESTABLISH_WORKFLOW_BYPASS"
                    },
                    provenance={"source": "http.state_transition.rejected"},
                )
            if proposal is None:
                raise ApplicationError(
                    "no diagnostic EvidenceProposal can be built from this experiment"
                )
            decision = admit_evidence(proposal, context)
            admission_record_id = new_opaque_id()
            evidence_id = new_opaque_id() if decision.creates_evidence else None
            if evidence_id is not None:
                try:
                    uow.evidence.insert(
                        EvidenceRecord(
                            evidence_id=evidence_id,
                            research_run_id=proposal.research_run_id,
                            hypothesis_id=proposal.hypothesis_id,
                            experiment_id=proposal.experiment_id,
                            admission_record_id=admission_record_id,
                            polarity=proposal.polarity.value,
                            claim_scope=proposal.claim_scope,
                            observation_ids=proposal.observation_ids,
                            assessment_ids=proposal.assessment_ids,
                            created_at=self._clock.now(),
                        )
                    )
                except PersistenceConflictError as exc:
                    if not is_uniqueness_conflict(
                        exc, UQ_EVIDENCE_EXPERIMENT_SUPPORTING
                    ):
                        raise
                    uow.rollback()
                    return self._reload_admitted(command.experiment_id, command.assessment_id)
            uow.evidence_admissions.insert(
                _admission_record(
                    admission_record_id=admission_record_id,
                    evidence_id=evidence_id,
                    decision=decision,
                    evaluator_version=(
                        assessment.evaluator_version if assessment is not None else "none"
                    ),
                    created_at=self._clock.now(),
                )
            )
            uow.commit()
        return AdmitDiagnosticEvidenceResult(
            outcome=decision.outcome,
            admission_record_id=admission_record_id,
            evidence_id=evidence_id,
            reason_codes=decision.reason_codes,
            proposal_id=proposal.proposal_id,
        )

    def _reload_admitted(
        self, experiment_id: str, assessment_id: str | None
    ) -> AdmitDiagnosticEvidenceResult:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")
            if assessment_id is not None:
                existing = _admitted_for_assessment(
                    uow.evidence_admissions.list_for_research_run(
                        experiment.research_run_id
                    ),
                    assessment_id,
                )
                if existing is not None:
                    uow.rollback()
                    return _result_from_admission(existing)
            supporting = [
                item
                for item in uow.evidence.list_for_experiment(experiment_id)
                if item.polarity == EvidencePolarity.SUPPORTING.value
            ]
            uow.rollback()
            if not supporting:
                raise ApplicationError(
                    "evidence uniqueness conflict but no supporting Evidence exists"
                )
            record = sorted(supporting, key=lambda item: item.created_at)[0]
            return AdmitDiagnosticEvidenceResult(
                outcome=EvidenceAdmissionOutcome.ADMITTED,
                admission_record_id=record.admission_record_id,
                evidence_id=record.evidence_id,
                reason_codes=("EVIDENCE_ALREADY_ADMITTED",),
                proposal_id=record.admission_record_id,
            )


def _admitted_for_assessment(
    records: list[EvidenceAdmissionRecord], assessment_id: str
) -> EvidenceAdmissionRecord | None:
    matches = [
        record
        for record in records
        if assessment_id in record.assessment_ids and record.outcome == "ADMITTED"
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.created_at)[0]


def _result_from_admission(record: EvidenceAdmissionRecord) -> AdmitDiagnosticEvidenceResult:
    return AdmitDiagnosticEvidenceResult(
        outcome=EvidenceAdmissionOutcome(record.outcome),
        admission_record_id=record.admission_record_id,
        evidence_id=record.admitted_evidence_id,
        reason_codes=record.reason_codes,
        proposal_id=record.proposal_id,
    )


def _select_assessment(
    assessments: list[HypothesisAssessmentRecord],
    assessment_id: str | None,
) -> HypothesisAssessmentRecord | None:
    if not assessments:
        return None
    if assessment_id is None:
        return assessments[-1]
    for item in assessments:
        if item.assessment_id == assessment_id:
            return item
    raise ApplicationError("assessment not found")


def _admission_context(
    *,
    experiment_id: str,
    research_run_id: str,
    hypothesis_id: str,
    evaluation_strategy: str,
    assessment: HypothesisAssessmentRecord | None,
    observations: tuple[ObservationRecord, ...],
    attempt: ExecutionAttemptRecord | None,
    worker_result: WorkerResultRecord | None,
    experiment_execution_state: str | None,
    requested_observation_ids: tuple[str, ...],
) -> EvidenceAdmissionContext:
    refs = tuple(
        EvidenceObservationRef(
            observation_id=item.observation_id,
            research_run_id=research_run_id,
            worker_result_id=item.worker_result_id,
            observation_kind=item.observation_kind,
        )
        for item in observations
    )
    present = {item.observation_id for item in refs}
    missing = tuple(item for item in requested_observation_ids if item not in present)
    outcome = None
    if assessment is not None:
        outcome = AssessmentOutcome(assessment.assessment_outcome)
    return EvidenceAdmissionContext(
        research_run_id=research_run_id,
        hypothesis_id=hypothesis_id,
        experiment_id=experiment_id,
        evaluation_strategy=evaluation_strategy,
        observations=refs,
        missing_source_ids=missing,
        assessment_id=None if assessment is None else assessment.assessment_id,
        assessment_outcome=outcome,
        attempt_state=None if attempt is None else attempt.state,
        worker_status=None if worker_result is None else worker_result.status,
        invocation_status=None,
        execution_outcome=None,
        experiment_execution_state=experiment_execution_state,
    )


def _admission_record(
    *,
    admission_record_id: str,
    evidence_id: str | None,
    decision: EvidenceAdmissionDecision,
    evaluator_version: str,
    created_at,
) -> EvidenceAdmissionRecord:
    proposal = decision.proposal
    return EvidenceAdmissionRecord(
        admission_record_id=admission_record_id,
        proposal_id=proposal.proposal_id,
        research_run_id=proposal.research_run_id,
        outcome=decision.outcome.value,
        reason_codes=decision.reason_codes,
        observation_ids=proposal.observation_ids,
        assessment_ids=proposal.assessment_ids,
        admission_policy_version=EVIDENCE_ADMISSION_POLICY_VERSION,
        evaluator_version=evaluator_version,
        created_at=created_at,
        admitted_evidence_id=evidence_id,
        claim_scope=proposal.claim_scope,
        polarity=proposal.polarity.value,
    )
