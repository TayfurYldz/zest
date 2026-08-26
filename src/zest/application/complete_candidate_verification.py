"""Complete Candidate verification. Application coordinates; Research decides.

Does not invent VALIDATED. Does not create Finding. Worker execution stays outside.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.errors import PersistenceConflictError
from zest.data.records import (
    EvidenceRecord,
    ExecutionAttemptRecord,
    ExperimentRecord,
    ObservationRecord,
    VerificationRecord,
    WorkerResultRecord,
)
from zest.data.uniqueness import UQ_VERIFICATION_CANDIDATE, is_uniqueness_conflict
from zest.research.assessment import (
    UNUSABLE_ATTEMPT_STATES,
    UNUSABLE_EXPERIMENT_STATES,
    AssessmentOutcome,
)
from zest.research.candidate import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
    CandidateState,
)
from zest.research.types import ResearchInputError
from zest.research.verification import (
    VerificationContext,
    VerificationEvidenceRef,
    VerificationOutcome,
    apply_verification_to_candidate,
    evaluate_authorization_differential_verification,
    evaluate_diagnostic_verification,
    evaluate_state_transition_verification,
    plan_authorization_differential_verification,
    plan_diagnostic_verification,
    plan_state_transition_verification,
)


@dataclass(frozen=True)
class CompleteCandidateVerificationCommand:
    candidate_id: str
    reproduction_experiment_id: str | None = None
    negative_control_experiment_id: str | None = None
    authoritative_out_of_scope: bool = False
    duplicate_of_candidate_id: str | None = None


@dataclass(frozen=True)
class CompleteCandidateVerificationResult:
    candidate_id: str
    verification_id: str
    outcome: VerificationOutcome
    state: CandidateState
    reason_codes: tuple[str, ...]


class CompleteCandidateVerification:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self, command: CompleteCandidateVerificationCommand
    ) -> CompleteCandidateVerificationResult:
        with self._uow_factory.open() as uow:
            candidate = uow.candidates.get(command.candidate_id)
            if candidate is None:
                raise ApplicationError("candidate not found")
            existing = uow.verifications.list_for_candidate(command.candidate_id)
            if existing:
                record = existing[-1]
                try:
                    state = CandidateState(candidate.state)
                except ValueError as exc:
                    raise ApplicationError("candidate state is not a CandidateState") from exc
                uow.rollback()
                return CompleteCandidateVerificationResult(
                    candidate_id=command.candidate_id,
                    verification_id=record.verification_id,
                    outcome=VerificationOutcome(record.outcome),
                    state=state,
                    reason_codes=("VERIFICATION_ALREADY_RECORDED",),
                )
            try:
                current = CandidateState(candidate.state)
            except ValueError as exc:
                raise ApplicationError("candidate state is not a CandidateState") from exc
            original = _originating_evidence(uow.evidence.get, candidate.evidence_ids)
            original_ref = _evidence_ref(uow, original)
            reproduction = None
            reproduction_unusable = False
            reproduction_assessment_outcome = None
            reproduction_experiment_id = None
            reproduction_request_id = None
            reproduction_observation_ids: tuple[str, ...] = ()
            if command.reproduction_experiment_id is not None:
                experiment = uow.experiments.get(command.reproduction_experiment_id)
                if experiment is None:
                    raise ApplicationError("reproduction experiment not found")
                if experiment.research_run_id != candidate.research_run_id:
                    raise ApplicationError("reproduction experiment is not in this research run")
                items = uow.evidence.list_for_experiment(command.reproduction_experiment_id)
                attempts = tuple(
                    uow.execution_attempts.list_for_experiment(
                        command.reproduction_experiment_id
                    )
                )
                worker_results = tuple(
                    uow.worker_results.list_for_experiment(
                        command.reproduction_experiment_id
                    )
                )
                observations = tuple(
                    uow.observations.list_for_experiment(command.reproduction_experiment_id)
                )
                assessments = uow.hypothesis_assessments.list_for_experiment(
                    command.reproduction_experiment_id
                )
                reproduction_unusable = _execution_unusable(
                    experiment=experiment,
                    attempts=attempts,
                    worker_results=worker_results,
                )
                reproduction_experiment_id = experiment.experiment_id
                if attempts:
                    reproduction_request_id = attempts[0].request_id
                elif worker_results:
                    reproduction_request_id = worker_results[0].request_id
                reproduction_observation_ids = tuple(
                    item.observation_id for item in observations
                )
                if assessments:
                    reproduction_assessment_outcome = AssessmentOutcome(
                        assessments[-1].assessment_outcome
                    )
                if items:
                    reproduction = _evidence_ref(uow, items[0])
            control = None
            if command.negative_control_experiment_id is not None:
                items = uow.evidence.list_for_experiment(
                    command.negative_control_experiment_id
                )
                if items:
                    control = _evidence_ref(uow, items[0])
            known_duplicate_exists = False
            if command.duplicate_of_candidate_id is not None:
                known = uow.candidates.get(command.duplicate_of_candidate_id)
                known_duplicate_exists = known is not None
            if candidate.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION:
                plan = plan_authorization_differential_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
                evaluate_fn = evaluate_authorization_differential_verification
            elif candidate.classification == HTTP_STATE_TRANSITION_CLASSIFICATION:
                plan = plan_state_transition_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
                evaluate_fn = evaluate_state_transition_verification
            else:
                plan = plan_diagnostic_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
                evaluate_fn = evaluate_diagnostic_verification
            context = VerificationContext(
                candidate_id=candidate.candidate_id,
                candidate_state=current,
                research_run_id=candidate.research_run_id,
                hypothesis_id=candidate.hypothesis_id,
                claim=candidate.claim,
                plan=plan,
                original_evidence=original_ref,
                reproduction_evidence=reproduction,
                negative_control_evidence=control,
                reproduction_execution_unusable=reproduction_unusable,
                authoritative_out_of_scope=command.authoritative_out_of_scope,
                duplicate_of_candidate_id=command.duplicate_of_candidate_id,
                known_duplicate_exists=known_duplicate_exists,
                reproduction_assessment_outcome=reproduction_assessment_outcome,
                reproduction_experiment_id=reproduction_experiment_id,
                reproduction_request_id=reproduction_request_id,
                reproduction_observation_ids=reproduction_observation_ids,
            )
            result = evaluate_fn(context)
            try:
                next_state = apply_verification_to_candidate(current, result)
            except ResearchInputError as exc:
                raise ApplicationError(str(exc)) from exc
            verification_id = new_opaque_id()
            try:
                uow.verifications.insert(
                    VerificationRecord(
                        verification_id=verification_id,
                        candidate_id=candidate.candidate_id,
                        research_run_id=candidate.research_run_id,
                        strategy=result.strategy,
                        outcome=result.outcome.value,
                        proposed_candidate_state=result.proposed_candidate_state.value,
                        original_evidence_ids=result.original_evidence_ids,
                        reproduction_evidence_ids=result.reproduction_evidence_ids,
                        negative_control_evidence_ids=result.negative_control_evidence_ids,
                        alternative_explanation_checks=result.alternative_explanation_checks,
                        verifier_kind=result.verifier_kind,
                        verifier_identity=result.verifier_identity,
                        created_at=self._clock.now(),
                    )
                )
            except PersistenceConflictError as exc:
                if not is_uniqueness_conflict(exc, UQ_VERIFICATION_CANDIDATE):
                    raise
                uow.rollback()
                return self._reload_existing(command.candidate_id)
            uow.candidates.set_state(candidate.candidate_id, next_state.value)
            uow.commit()
        return CompleteCandidateVerificationResult(
            candidate_id=candidate.candidate_id,
            verification_id=verification_id,
            outcome=result.outcome,
            state=next_state,
            reason_codes=result.reason_codes,
        )

    def _reload_existing(self, candidate_id: str) -> CompleteCandidateVerificationResult:
        with self._uow_factory.open() as uow:
            candidate = uow.candidates.get(candidate_id)
            existing = uow.verifications.list_for_candidate(candidate_id)
            uow.rollback()
        if candidate is None:
            raise ApplicationError("candidate not found")
        if not existing:
            raise ApplicationError(
                "verification uniqueness conflict but no Verification exists"
            )
        record = existing[-1]
        return CompleteCandidateVerificationResult(
            candidate_id=candidate_id,
            verification_id=record.verification_id,
            outcome=VerificationOutcome(record.outcome),
            state=CandidateState(candidate.state),
            reason_codes=("VERIFICATION_ALREADY_RECORDED",),
        )


def _originating_evidence(getter, evidence_ids: tuple[str, ...]) -> EvidenceRecord:
    if not evidence_ids:
        raise ApplicationError("candidate has no originating Evidence")
    evidence = getter(evidence_ids[0])
    if evidence is None:
        raise ApplicationError("originating Evidence not found")
    return evidence


def _evidence_ref(uow, evidence: EvidenceRecord) -> VerificationEvidenceRef:
    attempts = uow.execution_attempts.list_for_experiment(evidence.experiment_id)
    results = uow.worker_results.list_for_experiment(evidence.experiment_id)
    request_id = None
    if attempts:
        request_id = attempts[0].request_id
    elif results:
        request_id = results[0].request_id
    if request_id is None:
        raise ApplicationError("cannot resolve request_id for Evidence experiment")
    observations = uow.observations.list_for_experiment(evidence.experiment_id)
    echoed = _echoed_value(observations, evidence.observation_ids)
    facts = _observation_facts(observations, evidence.observation_ids)
    return VerificationEvidenceRef(
        evidence_id=evidence.evidence_id,
        research_run_id=evidence.research_run_id,
        experiment_id=evidence.experiment_id,
        request_id=request_id,
        observation_ids=evidence.observation_ids,
        polarity=evidence.polarity,
        claim_scope=evidence.claim_scope,
        observed_echo=echoed,
        observed_facts=facts or None,
    )


def _observation_facts(
    observations: list[ObservationRecord], observation_ids: tuple[str, ...]
) -> dict:
    wanted = set(observation_ids)
    for item in observations:
        if item.observation_id not in wanted:
            continue
        if item.observation_kind in {
            "HTTP_AUTHORIZATION_DIFFERENTIAL",
            "HTTP_STATE_TRANSITION_AUTHORIZATION",
        }:
            return dict(item.payload)
    return {}


def _echoed_value(
    observations: list[ObservationRecord], observation_ids: tuple[str, ...]
) -> str | None:
    wanted = set(observation_ids)
    for item in observations:
        if item.observation_id not in wanted:
            continue
        raw = item.payload.get("echoed")
        if isinstance(raw, str) and raw.strip():
            return raw
    return None


def _execution_unusable(
    *,
    experiment: ExperimentRecord,
    attempts: tuple[ExecutionAttemptRecord, ...],
    worker_results: tuple[WorkerResultRecord, ...],
) -> bool:
    if experiment.execution_state in UNUSABLE_EXPERIMENT_STATES:
        return True
    if attempts and attempts[0].state in UNUSABLE_ATTEMPT_STATES:
        return True
    if worker_results and worker_results[0].status in {
        "EXECUTION_FAILED",
        "TIMED_OUT",
        "CANCELLED",
    }:
        return True
    return False
