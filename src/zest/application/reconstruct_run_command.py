"""Rebuild StartAutonomousResearchCommand from PostgreSQL only.

Client payloads are ignored. Persisted orchestration bounds/budget/target
cannot be widened by the caller. compiled_scope is always loaded from the
current program context because exploratory Core authorization requires it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from zest.application.autonomous_research_controller import (
    StartAutonomousResearchCommand,
)
from zest.application.discovery.runner import SurfaceDiscoveryStart
from zest.application.errors import ApplicationError
from zest.application.http_transaction_authorization import (
    scope_evaluation_from_compiled_check,
)
from zest.application.orchestration_config import (
    assert_command_matches_configuration,
    configuration_from_record,
)
from zest.application.ports import UnitOfWorkFactory
from zest.application.program_research_context import load_program_research_context
from zest.core.scope_compiler import evaluate_scope_candidate
from zest.platform.url_normalize import normalize_url
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.orchestration import OrchestrationBounds


def reconstruct_start_command(
    uow_factory: UnitOfWorkFactory,
    research_run_id: str,
    *,
    recovery: bool,
) -> StartAutonomousResearchCommand:
    """Return a command whose research configuration comes only from SoR."""

    with uow_factory.open() as uow:
        run = uow.research_runs.get(research_run_id)
        if run is None:
            uow.rollback()
            raise ApplicationError("research run not found")
        orchestration = uow.research_orchestrations.get(research_run_id)
        context = load_program_research_context(uow, run.program_id)
        policy = context.policy if context is not None else None
        budgets = uow.issued_budgets.list_for_research_run(research_run_id)
        uow.rollback()
    if context is None or policy is None:
        raise ApplicationError("program research context is unavailable")
    if orchestration is not None:
        config = configuration_from_record(orchestration)
        target_reference = config.target_reference
        research_question = config.research_question
        bounds = config.bounds
        budget_id = config.budget_id
    else:
        run_config = dict(policy.action_policy.get("run", {}))
        orchestration_cfg = dict(policy.action_policy.get("orchestration", {}))
        target_reference = str(run_config.get("target_reference", "")).strip()
        research_question = str(run_config.get("research_question", "")).strip()
        if not target_reference or not research_question:
            raise ApplicationError("persisted run configuration is incomplete")
        try:
            bounds = OrchestrationBounds(
                max_cycles=int(orchestration_cfg["max_cycles"]),
                max_experiments=int(orchestration_cfg["max_experiments"]),
                max_model_calls=int(orchestration_cfg["max_model_calls"]),
                max_worker_invocations=int(orchestration_cfg["max_worker_invocations"]),
                max_elapsed_ms=int(orchestration_cfg["max_elapsed_ms"]),
                max_selected_opportunities=int(
                    orchestration_cfg["max_selected_opportunities"]
                ),
                max_runtime_fallback=int(orchestration_cfg["max_runtime_fallback"]),
                side_effect_ceiling=int(orchestration_cfg["side_effect_ceiling"]),
                allow_repeated_control_experiments=bool(
                    orchestration_cfg["allow_repeated_control_experiments"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ApplicationError("persisted orchestration configuration is invalid") from exc
        if orchestration is None and len(budgets) != 1:
            raise ApplicationError("research run must have exactly one issued budget")
        budget_id = budgets[0].budget_id
    candidate = normalize_url(target_reference)
    check = evaluate_scope_candidate(candidate, context.compiled_scope)
    scope = scope_evaluation_from_compiled_check(check, context.compiled_scope)
    if orchestration is not None:
        assert_command_matches_configuration(
            config=configuration_from_record(orchestration),
            bounds=bounds,
            budget_id=budget_id,
            target_reference=target_reference,
            research_question=research_question,
            scope=scope,
        )
    surface_discovery = None
    if not recovery:
        if candidate.normalized_scheme is None or candidate.normalized_host is None:
            raise ApplicationError("target reference cannot seed surface discovery")
        default_port = 80 if candidate.normalized_scheme == "http" else 443
        normalized_origin = f"{candidate.normalized_scheme}://{candidate.normalized_host}"
        if candidate.normalized_port != default_port:
            normalized_origin += f":{candidate.normalized_port}"
        discovery_bounds = DiscoveryBounds(
            max_discovery_cycles=bounds.max_cycles,
            max_frontier_items=bounds.max_worker_invocations,
            max_new_facts_per_cycle=bounds.max_worker_invocations,
            max_browser_actions=bounds.max_worker_invocations,
            max_http_transactions=bounds.max_worker_invocations,
            max_per_route_revisit=1,
            max_identity_variants=0,
            max_transition_depth=1,
            max_graph_depth_from_seed=2,
            max_template_inference_fanout=4,
            max_duplicate_observations=1,
        )
        surface_discovery = SurfaceDiscoveryStart(
            config=DiscoveryRunConfig(
                research_run_id=research_run_id,
                seed_target_reference=target_reference,
                normalized_origin=normalized_origin,
                normalized_path=candidate.raw_path,
                bounds=discovery_bounds,
            ),
            compiled_scope=context.compiled_scope,
        )
    return StartAutonomousResearchCommand(
        research_run_id=research_run_id,
        budget_id=budget_id,
        target_reference=target_reference,
        scope=scope,
        bounds=bounds,
        research_question=research_question,
        surface_discovery=surface_discovery,
        compiled_scope=context.compiled_scope,
        program_policy=policy,
    )


def allocate_daily_budget_if_required(
    uow_factory: UnitOfWorkFactory,
    research_run_id: str,
) -> None:
    """Issue today's program daily LLM budget when policy requires it.

    Does not change research bounds. Mirrors the dashboard prepare_start path
    so first START through zestd has the same SoR prerequisites.
    """

    from zest.application.program_daily_budget import (
        AllocateProgramDailyBudget,
        AllocateProgramDailyBudgetCommand,
    )

    with uow_factory.open() as uow:
        run = uow.research_runs.get(research_run_id)
        if run is None:
            uow.rollback()
            raise ApplicationError("research run not found")
        policy = uow.program_policies.get(run.program_id)
        uow.rollback()
    if policy is None or policy.daily_llm_budget_microdollars is None:
        return
    AllocateProgramDailyBudget(uow_factory).execute(
        AllocateProgramDailyBudgetCommand(
            program_id=run.program_id,
            budget_date=datetime.now(timezone.utc).date().isoformat(),
            limit_microdollars=policy.daily_llm_budget_microdollars,
        )
    )
