"""Shared spine seeding for Application tests. Not a PostgreSQL substitute."""

from __future__ import annotations

from datetime import datetime, timezone

from zest.data.records import (
    AuthorizationSourceRecord,
    ExperimentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from support.fake_unit_of_work import _Store

CREATED_AT = datetime(2026, 8, 16, 21, 0, tzinfo=timezone.utc)

DIAGNOSTIC_CLAIM = "diagnostic runtime returns the provided echo value"


def seed_authorization_run(store: _Store) -> None:
    """Program + AuthorizationSource + ResearchRun + IssuedBudget. No Hypothesis."""
    store.programs["prog-1"] = ProgramRecord(program_id="prog-1", created_at=CREATED_AT)
    store.authorization_sources["as-1"] = AuthorizationSourceRecord(
        authorization_source_id="as-1",
        program_id="prog-1",
        state="ACTIVE",
        provenance_reference="letter-1",
        created_at=CREATED_AT,
    )
    store.research_runs["run-1"] = ResearchRunRecord(
        research_run_id="run-1",
        program_id="prog-1",
        authorization_source_id="as-1",
        initiated_by_actor_id="operator-1",
        initiated_by_actor_type="HUMAN_OPERATOR",
        started_at=CREATED_AT,
    )
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=1,
        max_tool_calls=1,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )


def seed_spine(
    store: _Store,
    *,
    authorization_state: str = "ACTIVE",
    experiment_state: str = "PLANNED",
    hypothesis_claim: str = DIAGNOSTIC_CLAIM,
) -> None:
    store.programs["prog-1"] = ProgramRecord(program_id="prog-1", created_at=CREATED_AT)
    store.authorization_sources["as-1"] = AuthorizationSourceRecord(
        authorization_source_id="as-1",
        program_id="prog-1",
        state=authorization_state,
        provenance_reference="letter-1",
        created_at=CREATED_AT,
    )
    store.research_runs["run-1"] = ResearchRunRecord(
        research_run_id="run-1",
        program_id="prog-1",
        authorization_source_id="as-1",
        initiated_by_actor_id="operator-1",
        initiated_by_actor_type="HUMAN_OPERATOR",
        started_at=CREATED_AT,
    )
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=10,
        max_tool_calls=10,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    store.hypotheses["hyp-1"] = HypothesisRecord(
        hypothesis_id="hyp-1",
        research_run_id="run-1",
        claim=hypothesis_claim,
        origin_reference="human-seed-1",
        created_at=CREATED_AT,
    )
    store.experiments["exp-1"] = ExperimentRecord(
        experiment_id="exp-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        execution_state=experiment_state,
        created_at=CREATED_AT,
    )
