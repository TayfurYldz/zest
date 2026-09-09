"""Count dangling lifecycle references. Does not delete rows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleOrphanInventory:
    selection_without_opportunity: int
    experiment_without_hypothesis: int
    worker_result_without_attempt: int
    verification_without_candidate: int
    proposal_without_candidate: int
    callback_without_correlation: int
    total: int


def lifecycle_orphan_inventory(uow, research_run_id: str) -> LifecycleOrphanInventory:
    opportunities = {
        item.opportunity_id
        for item in _list(uow, "research_opportunities", research_run_id)
    }
    selection_without_opportunity = 0
    if hasattr(uow, "research_selections"):
        for item in uow.research_selections.list_for_research_run(research_run_id):
            if getattr(item, "outcome", None) != "SELECT":
                continue
            if item.opportunity_id and item.opportunity_id not in opportunities:
                selection_without_opportunity += 1
    hypotheses = {item.hypothesis_id for item in _list(uow, "hypotheses", research_run_id)}
    experiment_without_hypothesis = sum(
        1
        for item in _list(uow, "experiments", research_run_id)
        if item.hypothesis_id not in hypotheses
    )
    attempts_by_request = {
        item.request_id: item for item in _list(uow, "execution_attempts", research_run_id)
    }
    worker_result_without_attempt = 0
    for item in _list(uow, "worker_results", research_run_id):
        request_id = getattr(item, "request_id", None)
        if request_id and request_id not in attempts_by_request:
            worker_result_without_attempt += 1
    candidates = {item.candidate_id for item in _list(uow, "candidates", research_run_id)}
    verification_without_candidate = sum(
        1
        for item in _list(uow, "verifications", research_run_id)
        if getattr(item, "candidate_id", None) not in candidates
    )
    proposal_without_candidate = sum(
        1
        for item in _list(uow, "finding_proposals", research_run_id)
        if getattr(item, "candidate_id", None) not in candidates
    )
    correlations = {
        item.correlation_id for item in _list(uow, "oast_correlations", research_run_id)
    }
    callback_without_correlation = 0
    deliveries_repo = getattr(uow, "oast_callback_deliveries", None)
    if deliveries_repo is not None and hasattr(deliveries_repo, "list_for_research_run"):
        for item in deliveries_repo.list_for_research_run(research_run_id):
            if item.correlation_id not in correlations:
                callback_without_correlation += 1
    total = (
        selection_without_opportunity
        + experiment_without_hypothesis
        + worker_result_without_attempt
        + verification_without_candidate
        + proposal_without_candidate
        + callback_without_correlation
    )
    return LifecycleOrphanInventory(
        selection_without_opportunity=selection_without_opportunity,
        experiment_without_hypothesis=experiment_without_hypothesis,
        worker_result_without_attempt=worker_result_without_attempt,
        verification_without_candidate=verification_without_candidate,
        proposal_without_candidate=proposal_without_candidate,
        callback_without_correlation=callback_without_correlation,
        total=total,
    )


def _list(uow, attr: str, research_run_id: str) -> list:
    repo = getattr(uow, attr, None)
    if repo is None or not hasattr(repo, "list_for_research_run"):
        return []
    return list(repo.list_for_research_run(research_run_id))
