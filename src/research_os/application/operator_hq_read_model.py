"""Bounded sanitized analytical projection for Research OS HQ.

Projection only. PostgreSQL remains authoritative. This module creates no
research state, grants no authority, dispatches no Worker, and calls no Model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from research_os.application.operator_errors import OperatorError, OperatorErrorCode
from research_os.application.ports import UnitOfWorkFactory
from research_os.safe_data import redact_secret_keys


HQ_SCHEMA_VERSION = "hq.run.analysis.v1"
MAX_ITEMS_PER_COLLECTION = 250
MAX_STRING_LENGTH = 8192
MAX_SERIALIZATION_DEPTH = 8
_LIMITED_READ_METHODS = frozenset(
    {
        "list_for_research_run",
        "list_for_correlation",
        "list_for_subject",
        "get_nodes",
        "get_edges",
    }
)


class _BoundedRepositoryProxy:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._repository, name)
        if name not in _LIMITED_READ_METHODS:
            return method

        def bounded_call(*args: Any, **kwargs: Any) -> Any:
            kwargs["limit"] = MAX_ITEMS_PER_COLLECTION
            return method(*args, **kwargs)

        return bounded_call


class _BoundedUnitOfWorkProxy:
    def __init__(self, uow: Any) -> None:
        self._uow = uow

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._uow, name)
        if name in {"rollback", "commit"}:
            return value
        return _BoundedRepositoryProxy(value)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_SERIALIZATION_DEPTH:
        return {"truncated": "max_depth"}

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    if isinstance(value, Enum):
        return _safe_value(value.value, depth=depth + 1)

    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "length": len(value),
            "raw_exposed": False,
        }

    if isinstance(value, str):
        if len(value) <= MAX_STRING_LENGTH:
            return value
        return value[:MAX_STRING_LENGTH] + "...[truncated]"

    if is_dataclass(value):
        return {
            field.name: _safe_value(
                getattr(value, field.name),
                depth=depth + 1,
            )
            for field in fields(value)
        }

    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return {
            str(key): _safe_value(item, depth=depth + 1)
            for key, item in items[:MAX_ITEMS_PER_COLLECTION]
        }

    if isinstance(value, (list, tuple, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            value = sorted(
                value,
                key=lambda item: (type(item).__name__, repr(item)),
            )
        return [
            _safe_value(item, depth=depth + 1)
            for item in list(value)[:MAX_ITEMS_PER_COLLECTION]
        ]

    return {
        "type": type(value).__name__,
        "raw_exposed": False,
    }


def _bundle(records: Sequence[Any]) -> dict[str, Any]:
    total = len(records)
    visible = list(records[:MAX_ITEMS_PER_COLLECTION])
    return {
        "count": total,
        "shown": len(visible),
        "truncated": total > len(visible),
        "items": [_safe_value(item) for item in visible],
    }


def build_hq_run_analysis(
    uow_factory: UnitOfWorkFactory,
    research_run_id: str,
) -> dict[str, Any]:
    """Build one bounded, read-only analytical snapshot for an authorized run."""

    with uow_factory.open() as uow:
        uow = _BoundedUnitOfWorkProxy(uow)
        run = uow.research_runs.get(research_run_id)
        if run is None:
            uow.rollback()
            raise OperatorError(
                OperatorErrorCode.RUN_NOT_FOUND,
                "research run not found",
            )

        hypotheses = uow.hypotheses.list_for_research_run(research_run_id)
        experiments = uow.experiments.list_for_research_run(research_run_id)
        attempts = uow.execution_attempts.list_for_research_run(research_run_id)
        worker_results = uow.worker_results.list_for_research_run(research_run_id)
        observations = uow.observations.list_for_research_run(research_run_id)

        reasoning = uow.research_reasoning.list_for_research_run(research_run_id)
        research_admissions = uow.research_admissions.list_for_research_run(
            research_run_id
        )
        assessments = uow.hypothesis_assessments.list_for_research_run(
            research_run_id
        )

        evidence = uow.evidence.list_for_research_run(research_run_id)
        evidence_admissions = uow.evidence_admissions.list_for_research_run(
            research_run_id
        )
        candidates = uow.candidates.list_for_research_run(research_run_id)
        candidate_admissions = uow.candidate_admissions.list_for_research_run(
            research_run_id
        )
        promotion_runs = uow.promotion_runs.list_for_research_run(research_run_id)
        verifications = uow.verifications.list_for_research_run(research_run_id)
        proposals = uow.finding_proposals.list_for_research_run(research_run_id)
        findings = uow.findings.list_for_research_run(research_run_id)

        opportunities = uow.research_opportunities.list_for_research_run(
            research_run_id
        )
        selections = uow.research_selections.list_for_research_run(
            research_run_id
        )
        opportunity_candidates = (
            uow.opportunity_selection_candidates.list_for_research_run(
                research_run_id
            )
        )
        hunt_v3 = uow.hunt_v3_queue.list_for_research_run(research_run_id)

        sensor_observations = uow.sensor_observations.list_for_research_run(
            research_run_id
        )
        control_events = uow.control_events.list_for_research_run(research_run_id)
        discovery_facts = uow.discovery_facts.list_for_research_run(research_run_id)
        discovery_inferences = uow.discovery_inferences.list_for_research_run(
            research_run_id
        )
        frontier_items = uow.frontier_items.list_for_research_run(research_run_id)
        frontier_events = uow.frontier_events.list_for_research_run(research_run_id)
        snapshots = uow.snapshots.list_for_research_run(research_run_id)
        change_events = uow.change_events.list_for_research_run(research_run_id)
        attack_surface_snapshots = (
            uow.attack_surface_snapshots.list_for_research_run(research_run_id)
        )
        coverage = uow.coverage_debt_snapshots.list_for_research_run(
            research_run_id
        )
        target_inferences = uow.target_inferences.list_for_research_run(
            research_run_id
        )
        differentials = uow.differential_observations.list_for_research_run(
            research_run_id
        )
        invariants = uow.invariant_hypotheses.list_for_research_run(research_run_id)
        chain_hypotheses = uow.chain_hypotheses.list_for_research_run(
            research_run_id
        )
        impact_chains = uow.impact_chains.list_for_research_run(research_run_id)

        research_cycles = uow.research_cycles.list_for_research_run(research_run_id)
        budget_consumptions = uow.budget_consumptions.list_for_research_run(
            research_run_id
        )
        preflights = uow.preflight_reports.list_for_research_run(research_run_id)
        audits = uow.audit_events.list_for_subject("research_run", research_run_id)

        oast_correlations = uow.oast_correlations.list_for_research_run(
            research_run_id
        )
        oast_admissions = uow.oast_admissions.list_for_research_run(
            research_run_id
        )

        experiment_plans = []
        for experiment in experiments:
            plan = uow.experiment_plans.get(experiment.experiment_id)
            if plan is not None:
                experiment_plans.append(plan)

        oast_deliveries = []
        for correlation in oast_correlations:
            oast_deliveries.extend(
                uow.oast_callback_deliveries.list_for_correlation(
                    correlation.correlation_id
                )
            )

        reviews = []
        approvals = []
        for proposal in proposals:
            review = uow.human_reviews.get_for_proposal(proposal.proposal_id)
            if review is not None:
                reviews.append(review)
            approval = uow.approvals.get_by_subject(proposal.proposal_id)
            if approval is not None:
                approvals.append(approval)

        impact_rows = []
        for chain in impact_chains[:MAX_ITEMS_PER_COLLECTION]:
            nodes = uow.impact_chains.get_nodes(
                chain.chain_id,
                limit=MAX_ITEMS_PER_COLLECTION,
            )
            edges = uow.impact_chains.get_edges(
                chain.chain_id,
                limit=MAX_ITEMS_PER_COLLECTION,
            )
            impact_rows.append(
                {
                    "chain": _safe_value(chain),
                    "nodes": _safe_value(nodes),
                    "edges": _safe_value(edges),
                    "nodes_bounded": len(nodes) >= MAX_ITEMS_PER_COLLECTION,
                    "edges_bounded": len(edges) >= MAX_ITEMS_PER_COLLECTION,
                }
            )

        uow.rollback()

    browser_attempts = [
        item
        for item in attempts
        if "browser" in str(getattr(item, "worker_capability", "")).lower()
    ]

    payload = {
        "schema": HQ_SCHEMA_VERSION,
        "research_run_id": research_run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projection_only": True,
        "not_research_truth": True,
        "authority": {
            "postgresql_source_of_truth": True,
            "creates_state": False,
            "authorizes_execution": False,
            "dispatches_worker": False,
            "calls_model": False,
        },
        "limits": {
            "max_items_per_collection": MAX_ITEMS_PER_COLLECTION,
            "max_nested_items": MAX_ITEMS_PER_COLLECTION,
            "max_string_length": MAX_STRING_LENGTH,
            "raw_bytes_exposed": False,
        },
        "engine_summary": {
            "hunter_opportunities": len(opportunities),
            "hunter_selections": len(selections),
            "model_reasoning_records": len(reasoning),
            "hypotheses": len(hypotheses),
            "experiments": len(experiments),
            "execution_attempts": len(attempts),
            "browser_attempts": len(browser_attempts),
            "observations": len(observations),
            "evidence": len(evidence),
            "candidates": len(candidates),
            "verifications": len(verifications),
            "finding_proposals": len(proposals),
            "findings": len(findings),
            "surface_facts": len(discovery_facts),
            "surface_inferences": len(discovery_inferences),
            "oast_correlations": len(oast_correlations),
            "oast_deliveries": len(oast_deliveries),
        },
        "hunter": {
            "opportunities": _bundle(opportunities),
            "selections": _bundle(selections),
            "selection_candidates": _bundle(opportunity_candidates),
            "v3_queue": _bundle(hunt_v3),
        },
        "research": {
            "reasoning": _bundle(reasoning),
            "admissions": _bundle(research_admissions),
            "hypotheses": _bundle(hypotheses),
            "assessments": _bundle(assessments),
            "cycles": _bundle(research_cycles),
        },
        "execution": {
            "experiments": _bundle(experiments),
            "plans": _bundle(experiment_plans),
            "attempts": _bundle(attempts),
            "worker_results": _bundle(worker_results),
            "observations": _bundle(observations),
            "budget_consumptions": _bundle(budget_consumptions),
            "preflights": _bundle(preflights),
        },
        "browser": {
            "attempts": _bundle(browser_attempts),
        },
        "oast": {
            "correlations": _bundle(oast_correlations),
            "deliveries": _bundle(oast_deliveries),
            "admissions": _bundle(oast_admissions),
        },
        "surface": {
            "sensor_observations": _bundle(sensor_observations),
            "control_events": _bundle(control_events),
            "facts": _bundle(discovery_facts),
            "inferences": _bundle(discovery_inferences),
            "frontier_items": _bundle(frontier_items),
            "frontier_events": _bundle(frontier_events),
            "snapshots": _bundle(snapshots),
            "change_events": _bundle(change_events),
            "attack_surface_snapshots": _bundle(attack_surface_snapshots),
            "coverage_debt": _bundle(coverage),
            "target_inferences": _bundle(target_inferences),
            "differentials": _bundle(differentials),
            "invariants": _bundle(invariants),
            "chain_hypotheses": _bundle(chain_hypotheses),
            "impact_chains": {
                "count": len(impact_chains),
                "shown": len(impact_rows),
                "truncated": len(impact_chains) > len(impact_rows),
                "items": impact_rows,
            },
        },
        "evidence_chain": {
            "evidence": _bundle(evidence),
            "evidence_admissions": _bundle(evidence_admissions),
            "candidates": _bundle(candidates),
            "candidate_admissions": _bundle(candidate_admissions),
            "promotion_runs": _bundle(promotion_runs),
            "verifications": _bundle(verifications),
            "finding_proposals": _bundle(proposals),
            "human_reviews": _bundle(reviews),
            "approvals": _bundle(approvals),
            "findings": _bundle(findings),
        },
        "audit": {
            "events": _bundle(audits),
        },
    }

    return redact_secret_keys(payload)
