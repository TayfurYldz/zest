"""Evaluate ExperimentFeedback and persist a context-bound HypothesisAssessment.

Does not create Evidence, Candidate, or Finding. Does not plan the next experiment.
Does not invoke a model. Core is not involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from zest.application.errors import ApplicationError
from zest.application.identity import new_opaque_id
from zest.application.oast_source import OAST_CALLBACK_EVALUATION_STRATEGY
from zest.application.oast_timeout import OAST_NO_CALLBACK_TIMEOUT
from zest.application.plan_records import experiment_plan_from_record
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.data.records import (
    ExecutionAttemptRecord,
    ExperimentRecord,
    HypothesisAssessmentRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from zest.research.assessment import (
    AssessmentOutcome,
    ExperimentEvaluatorRegistry,
    ResearchFeedback,
    default_evaluator_registry,
)
from zest.research.feedback import ExperimentFeedback, ObservedFact
from zest.research.evaluators.oast_callback import (
    OAST_CALLBACK_OBSERVATION_KIND,
    OAST_NO_CALLBACK_OBSERVATION_KIND,
)
from zest.research.types import ExperimentPlan


@dataclass(frozen=True)
class EvaluateExperimentFeedbackCommand:
    experiment_id: str
    execution_outcome: str | None = None
    invocation_status: str | None = None
    attempt_state: str | None = None
    experiment_execution_state: str | None = None


class EvaluateExperimentFeedback:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        registry: ExperimentEvaluatorRegistry | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._registry = registry or default_evaluator_registry()

    def execute(self, command: EvaluateExperimentFeedbackCommand) -> ResearchFeedback:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(command.experiment_id)
            if experiment is None:
                raise ApplicationError("experiment not found")
            plan_record = uow.experiment_plans.get(command.experiment_id)
            if plan_record is None:
                raise ApplicationError("durable experiment plan not found")
            plan = experiment_plan_from_record(plan_record)
            attempts = uow.execution_attempts.list_for_experiment(experiment.experiment_id)
            attempt = attempts[0] if attempts else None
            results = uow.worker_results.list_for_experiment(experiment.experiment_id)
            worker_result = results[0] if results else None
            observations = uow.observations.list_for_experiment(experiment.experiment_id)
            existing_assessments = uow.hypothesis_assessments.list_for_experiment(
                experiment.experiment_id
            )
            if existing_assessments:
                record = existing_assessments[0]
                uow.rollback()
                outcome = AssessmentOutcome(record.assessment_outcome)
                return ResearchFeedback(
                    hypothesis_id=record.hypothesis_id,
                    experiment_id=record.experiment_id,
                    assessment_id=record.assessment_id,
                    assessment_outcome=outcome,
                    observation_ids=tuple(record.observation_ids),
                    execution_usable=outcome is not AssessmentOutcome.EXECUTION_UNUSABLE,
                    evaluation_strategy=record.evaluation_strategy,
                    research_run_id=record.research_run_id,
                )
            feedback = reconstruct_experiment_feedback(
                experiment=experiment,
                plan=plan,
                observations=tuple(observations),
                attempt=attempt,
                worker_result=worker_result,
                execution_outcome=command.execution_outcome,
                invocation_status=command.invocation_status,
                attempt_state=command.attempt_state,
                experiment_execution_state=command.experiment_execution_state,
            )
            if plan.evaluation_strategy == OAST_CALLBACK_EVALUATION_STRATEGY and attempt is not None:
                extra = _oast_observed_facts(uow, attempt)
                if extra:
                    feedback = ExperimentFeedback(
                        hypothesis_id=feedback.hypothesis_id,
                        experiment_id=feedback.experiment_id,
                        research_run_id=feedback.research_run_id,
                        expected_observation=feedback.expected_observation,
                        disconfirming_observation=feedback.disconfirming_observation,
                        evaluation_strategy=feedback.evaluation_strategy,
                        execution_outcome=feedback.execution_outcome,
                        observations=feedback.observations + extra,
                        submitted_value=feedback.submitted_value,
                        invocation_status=feedback.invocation_status,
                        experiment_execution_state=feedback.experiment_execution_state,
                        attempt_state=feedback.attempt_state,
                    )
            evaluator = self._registry.get(plan.evaluation_strategy)
            assessment = evaluator.evaluate(plan, feedback)
            assessment_id = new_opaque_id()
            uow.hypothesis_assessments.insert(
                HypothesisAssessmentRecord(
                    assessment_id=assessment_id,
                    hypothesis_id=assessment.hypothesis_id,
                    experiment_id=assessment.experiment_id,
                    research_run_id=assessment.research_run_id,
                    assessment_outcome=assessment.outcome.value,
                    observation_ids=assessment.observation_ids,
                    evaluator_kind=assessment.evaluator_kind.value,
                    evaluator_version=assessment.evaluator_version,
                    rationale=dict(assessment.rationale),
                    evaluation_strategy=assessment.evaluation_strategy,
                    created_at=self._clock.now(),
                )
            )
            uow.commit()
        return ResearchFeedback(
            hypothesis_id=assessment.hypothesis_id,
            experiment_id=assessment.experiment_id,
            assessment_id=assessment_id,
            assessment_outcome=assessment.outcome,
            observation_ids=assessment.observation_ids,
            execution_usable=assessment.execution_usable,
            evaluation_strategy=assessment.evaluation_strategy,
            research_run_id=assessment.research_run_id,
        )


def reconstruct_experiment_feedback(
    *,
    experiment: ExperimentRecord,
    plan: ExperimentPlan,
    observations: tuple[ObservationRecord, ...],
    attempt: ExecutionAttemptRecord | None,
    worker_result: WorkerResultRecord | None,
    execution_outcome: str | None = None,
    invocation_status: str | None = None,
    attempt_state: str | None = None,
    experiment_execution_state: str | None = None,
) -> ExperimentFeedback:
    submitted = plan.arguments.get("message")
    submitted_value = submitted if isinstance(submitted, str) else None
    derived_attempt_state = attempt.state if attempt is not None else None
    derived_invocation = worker_result.status if worker_result is not None else None
    if derived_invocation is None and derived_attempt_state == "TIMED_OUT":
        derived_invocation = "TIMED_OUT"
    derived_execution = execution_outcome or _derive_execution_outcome(
        experiment.execution_state,
        derived_attempt_state,
        observations,
    )
    return ExperimentFeedback(
        hypothesis_id=experiment.hypothesis_id,
        experiment_id=experiment.experiment_id,
        research_run_id=experiment.research_run_id,
        expected_observation=plan.expected_observation,
        disconfirming_observation=plan.disconfirming_observation,
        evaluation_strategy=plan.evaluation_strategy,
        execution_outcome=derived_execution,
        observations=tuple(
            ObservedFact(
                observation_id=item.observation_id,
                observation_kind=item.observation_kind,
                payload=dict(item.payload),
            )
            for item in observations
        ),
        submitted_value=submitted_value,
        invocation_status=invocation_status or derived_invocation,
        experiment_execution_state=experiment_execution_state or experiment.execution_state,
        attempt_state=attempt_state or derived_attempt_state,
    )


def _derive_execution_outcome(
    experiment_state: str,
    attempt_state: str | None,
    observations: tuple[ObservationRecord, ...],
) -> str:
    if attempt_state in {"FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN_OUTCOME"}:
        if attempt_state == "UNKNOWN_OUTCOME":
            return "UNKNOWN_OUTCOME"
        return "INVOCATION_FAILED"
    if experiment_state == "BLOCKED":
        return "DISPATCH_DENIED"
    if experiment_state == "BUDGET_EXHAUSTED":
        return "DISPATCH_DENIED"
    if experiment_state == "EXECUTION_SUCCEEDED":
        if observations:
            return "OBSERVATION_PRODUCED"
        return "NO_OBSERVATION"
    if experiment_state == "EXECUTION_FAILED":
        return "INVOCATION_FAILED"
    return experiment_state


def _oast_observed_facts(uow, attempt) -> tuple[ObservedFact, ...]:
    correlation = uow.oast_correlations.get_by_attempt_id(attempt.attempt_id)
    if correlation is None:
        return ()
    facts: list[ObservedFact] = []
    admission = uow.oast_admissions.get_by_correlation(correlation.correlation_id)
    if admission is not None:
        deliveries = uow.oast_callback_deliveries.list_for_correlation(correlation.correlation_id)
        digest = deliveries[0].normalized_digest if deliveries else ""
        channel = "http"
        if deliveries:
            payload = dict(deliveries[0].normalized_payload or {})
            if payload.get("callback_channel"):
                channel = str(payload.get("callback_channel"))
        facts.append(
            ObservedFact(
                observation_id=admission.sensor_observation_id,
                observation_kind=OAST_CALLBACK_OBSERVATION_KIND,
                payload={
                    "correlation_id": correlation.correlation_id,
                    "normalized_digest": digest,
                    "callback_channel": channel,
                    "correlation_status": "CORRELATED",
                },
            )
        )
        return tuple(facts)
    audits = uow.audit_events.list_for_subject("research_run", attempt.research_run_id)
    timed_out = any(
        item.event_type == OAST_NO_CALLBACK_TIMEOUT
        and (item.payload or {}).get("correlation_id") == correlation.correlation_id
        for item in audits
    )
    if timed_out:
        facts.append(
            ObservedFact(
                observation_id=f"oast-timeout:{correlation.correlation_id}",
                observation_kind=OAST_NO_CALLBACK_OBSERVATION_KIND,
                payload={"correlation_status": "NO_CALLBACK_TIMEOUT"},
            )
        )
    return tuple(facts)
