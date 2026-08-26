"""Map durable ExperimentPlan records to Research ExperimentPlan. Not authorization."""

from __future__ import annotations

from datetime import datetime

from zest.data.records import ExperimentPlanRecord, ExperimentRecord
from zest.research.planning import plans_equivalent
from zest.research.types import ExperimentPlan


def experiment_plan_from_record(record: ExperimentPlanRecord) -> ExperimentPlan:
    return ExperimentPlan(
        hypothesis_id=record.hypothesis_id,
        required_capability=record.required_capability,
        action=record.action,
        target_reference=record.target_reference,
        side_effect_level=record.side_effect_level,
        arguments=dict(record.arguments),
        requested_budget_id=record.requested_budget_id,
        expected_observation=record.expected_observation,
        disconfirming_observation=record.disconfirming_observation,
        evaluation_strategy=record.evaluation_strategy,
        capability_version=record.capability_version,
        capability_definition_fingerprint=record.capability_definition_fingerprint,
    )


def experiment_plan_record_for(
    experiment: ExperimentRecord,
    plan: ExperimentPlan,
    *,
    created_at: datetime,
) -> ExperimentPlanRecord:
    return ExperimentPlanRecord(
        experiment_id=experiment.experiment_id,
        research_run_id=experiment.research_run_id,
        hypothesis_id=plan.hypothesis_id,
        required_capability=plan.required_capability,
        action=plan.action,
        target_reference=plan.target_reference,
        side_effect_level=plan.side_effect_level,
        arguments=dict(plan.arguments),
        requested_budget_id=plan.requested_budget_id,
        expected_observation=plan.expected_observation,
        disconfirming_observation=plan.disconfirming_observation,
        evaluation_strategy=plan.evaluation_strategy,
        created_at=created_at,
        capability_version=plan.capability_version,
        capability_definition_fingerprint=plan.capability_definition_fingerprint,
    )


def durable_plan_matches(record: ExperimentPlanRecord, plan: ExperimentPlan) -> bool:
    return plans_equivalent(experiment_plan_from_record(record), plan)
