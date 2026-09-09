"""Engine-specific compile adapters for selected research work.

Compilers do not dispatch Workers. ARC owns PreparePlannedExperiment /
ExecutePlannedExperiment. V3 queue dispatch is not a START-path owner.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from zest.application.discovery.compile_plan import (
    ReobserveRequired,
    UnsupportedDiscoveryCapability,
    compile_frontier_plan,
)
from zest.application.http_transaction_authorization import authorize_http_transaction_plan
from zest.application.hunt_validation import side_effect_for_family
from zest.application.identity import new_opaque_id
from zest.application.oast_source import (
    FAMILY_XXE,
    OAST_CALLBACK_EVALUATION_STRATEGY,
    OAST_DEFAULT_TTL,
)
from zest.application.research_identity_catalog import (
    active_session_for_identity,
    load_research_identity_catalog,
)
from zest.data.errors import PersistenceConflictError
from zest.data.records import OastTokenRecord, ResearchOpportunityRecord
from zest.research.compiler_registry import (
    CompilerOutcome,
    CompilerRequest,
    ExperimentCompilerRegistry,
)
from zest.research.discovery.frontier import DISCOVERY_EXECUTABLE_CAPABILITIES
from zest.research.discovery.types import DiscoveryGoalKind
from zest.research.exploration import OpportunityKind
from zest.research.http_authentication import plan_http_login
from zest.research.http_transaction import plan_http_transaction_read
from zest.research.identity_session import Identity, SessionState
from zest.research.orchestration import OrchestrationBounds
from zest.research.planning import plan_authorization_differential, plan_state_transition
from zest.research.types import ExperimentPlan
from zest.tools.capabilities import (
    HTTP_AUTHENTICATION_CAPABILITY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
)


class ResearchCompileStatus(Enum):
    USE_MODEL = "USE_MODEL"
    EXECUTE_PLAN = "EXECUTE_PLAN"
    EVALUATE_EXISTING = "EVALUATE_EXISTING"
    DEFERRED_ENGINE_WIRING = "DEFERRED_ENGINE_WIRING"
    MISSING_PRECONDITION = "MISSING_PRECONDITION"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    BLOCKED_SCOPE = "BLOCKED_SCOPE"
    BLOCKED_SIDE_EFFECT_CEILING = "BLOCKED_SIDE_EFFECT_CEILING"
    ENGINE_DEPENDENCY_PENDING = "ENGINE_DEPENDENCY_PENDING"


@dataclass(frozen=True)
class ResearchCompileDecision:
    status: ResearchCompileStatus
    source_engine: str
    reason_codes: tuple[str, ...]
    plan: ExperimentPlan | None = None
    required_capability: str | None = None
    side_effect_class: int | None = None
    hypothesis_id: str | None = None
    compiler_id: str | None = None
    forensic: dict | None = None


class ResearchWorkPlanner(Protocol):
    source_engine: str

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        """Compile selected work or return an explicit non-execute disposition."""


class ModelResearchPlanner:
    source_engine = "MODEL_RESEARCH"

    def plan(self, uow, opportunity, **kwargs) -> ResearchCompileDecision:
        return ResearchCompileDecision(
            status=ResearchCompileStatus.USE_MODEL,
            source_engine=self.source_engine,
            reason_codes=("MODEL_CONTEXT_PATH",),
        )


class DeferredEnginePlanner:
    def __init__(self, source_engine: str) -> None:
        self.source_engine = source_engine

    def plan(self, uow, opportunity, **kwargs) -> ResearchCompileDecision:
        return ResearchCompileDecision(
            status=ResearchCompileStatus.DEFERRED_ENGINE_WIRING,
            source_engine=self.source_engine,
            reason_codes=("DEFERRED_ENGINE_WIRING", "PHASE_6_TEMPORARY"),
        )


class DiscoveryHandoffPlanner:
    source_engine = "DISCOVERY_HANDOFF"

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        frontier_id = opportunity.source_refs[0]
        item = uow.frontier_items.get(frontier_id)
        if item is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "FRONTIER_NOT_FOUND"),
            )
        if item.proposed_capability not in DISCOVERY_EXECUTABLE_CAPABILITIES:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.DEFERRED_ENGINE_WIRING,
                source_engine=self.source_engine,
                reason_codes=("DEFERRED_ENGINE_WIRING", "CAPABILITY_NOT_YET_CONNECTED"),
                required_capability=item.proposed_capability,
                side_effect_class=item.expected_side_effect,
            )
        if item.goal_kind == DiscoveryGoalKind.INSPECT_CONTROL.value:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "PAGE_CONTEXT_ABSENT"),
                required_capability=item.proposed_capability,
                side_effect_class=item.expected_side_effect,
            )
        if item.expected_side_effect > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "SIDE_EFFECT_ABOVE_CEILING"),
                required_capability=item.proposed_capability,
                side_effect_class=item.expected_side_effect,
            )
        try:
            plan = compile_frontier_plan(
                item,
                hypothesis_id=hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
            )
        except ReobserveRequired as exc:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", str(exc)),
                required_capability=item.proposed_capability,
                side_effect_class=item.expected_side_effect,
            )
        except UnsupportedDiscoveryCapability:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.DEFERRED_ENGINE_WIRING,
                source_engine=self.source_engine,
                reason_codes=("DEFERRED_ENGINE_WIRING", "UNSUPPORTED_DISCOVERY_COMPILE"),
                required_capability=item.proposed_capability,
                side_effect_class=item.expected_side_effect,
            )
        scope_decision = authorize_http_transaction_plan(
            plan, compiled_scope, program_policy=program_policy
        )
        if not scope_decision.accepted:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SCOPE,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SCOPE",),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
            )
        if plan.side_effect_level > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "COMPILED_SIDE_EFFECT_ABOVE_CEILING"),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
            )
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EXECUTE_PLAN,
            source_engine=self.source_engine,
            reason_codes=("COMPILED_FOR_ARC_EXECUTE",),
            plan=plan,
            required_capability=plan.required_capability,
            side_effect_class=plan.side_effect_level,
        )


class HunterCoveragePlanner:
    """Compile selected Hunter/Coverage work through ExperimentCompilerRegistry.

    Does not dispatch Workers. Does not call DispatchApprovedV3Queue.
    """

    source_engine = "HUNTER"

    def __init__(self, registry: ExperimentCompilerRegistry | None = None) -> None:
        self._registry = registry or ExperimentCompilerRegistry()

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        if len(opportunity.source_refs) < 3:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "HUNTER_SOURCE_REFS_INCOMPLETE"),
            )
        family_id, node_key, identity_id = opportunity.source_refs[:3]
        family = _hunter_family(uow, family_id)
        if family is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "HUNTER_FAMILY_NOT_FOUND"),
            )
        native_se = side_effect_for_family(family.name)
        forensic = {
            "hunter_family": family.name,
            "family_id": family.family_id,
            "coverage_cell_id": f"{node_key}:{identity_id}:{family_id}",
            "native_side_effect": native_se,
        }
        if native_se > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "NATIVE_SIDE_EFFECT_ABOVE_CEILING"),
                required_capability=family.name,
                side_effect_class=native_se,
                forensic=forensic,
            )
        queue = _v3_queue_for_cell(uow, opportunity.research_run_id, family_id, node_key, identity_id)
        if queue is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "HUNT_V3_QUEUE_ABSENT"),
                side_effect_class=native_se,
                forensic=forensic,
            )
        if queue.state == "RUN":
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_POLICY,
                source_engine=self.source_engine,
                reason_codes=("ALREADY_EXECUTED",),
                side_effect_class=native_se,
                hypothesis_id=queue.hypothesis_id,
                forensic={**forensic, "hunt_hypothesis_id": queue.hypothesis_id, "queue_id": queue.queue_id},
            )
        compile_arguments = _compile_arguments(queue)
        compiled = self._registry.compile(
            CompilerRequest(
                hypothesis_id=queue.hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
                family_id=family.family_id,
                family_name=family.name,
                arguments=compile_arguments,
                requested_side_effect=native_se,
            )
        )
        forensic = {
            **forensic,
            "hunt_hypothesis_id": queue.hypothesis_id,
            "queue_id": queue.queue_id,
            "compiler": compiled.compiler_id,
            "tier_state": "V3_QUEUED",
        }
        if compiled.outcome is CompilerOutcome.BLOCKED_MISSING_SEMANTICS:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", compiled.reason_code),
                required_capability=compiled.family_name,
                side_effect_class=native_se,
                hypothesis_id=queue.hypothesis_id,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        if not compiled.compiled or compiled.plan is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_POLICY,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_POLICY", compiled.reason_code),
                required_capability=compiled.family_name,
                side_effect_class=native_se,
                hypothesis_id=queue.hypothesis_id,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        plan = compiled.plan
        if plan.side_effect_level > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "COMPILED_SIDE_EFFECT_ABOVE_CEILING"),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
                hypothesis_id=queue.hypothesis_id,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        scope_decision = authorize_http_transaction_plan(
            plan, compiled_scope, program_policy=program_policy
        )
        if not scope_decision.accepted:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SCOPE,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SCOPE",),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
                hypothesis_id=queue.hypothesis_id,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        forensic = {
            **forensic,
            "native_capability": plan.required_capability,
            "native_side_effect": plan.side_effect_level,
        }
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EXECUTE_PLAN,
            source_engine=self.source_engine,
            reason_codes=("COMPILED_FOR_ARC_EXECUTE",),
            plan=plan,
            required_capability=plan.required_capability,
            side_effect_class=plan.side_effect_level,
            hypothesis_id=queue.hypothesis_id,
            compiler_id=compiled.compiler_id,
            forensic=forensic,
        )


class AuthenticationPlanner:
    source_engine = "AUTHENTICATION"

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        catalog = load_research_identity_catalog(uow, opportunity.research_run_id)
        identity_id = opportunity.source_refs[0] if opportunity.source_refs else ""
        origin = opportunity.source_refs[1] if len(opportunity.source_refs) > 1 else target_reference
        identity = catalog.identity(identity_id)
        if identity is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_IDENTITY_PRECONDITION",),
                forensic={"identity_id": identity_id},
            )
        profile = catalog.profile(identity.authentication_profile_reference)
        if profile is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_CREDENTIAL", "AUTHENTICATION_PROFILE_ABSENT"),
                forensic={"identity_id": identity.identity_id},
            )
        now = datetime.now(timezone.utc)
        existing = active_session_for_identity(
            uow,
            research_run_id=opportunity.research_run_id,
            identity_id=identity.identity_id,
            origin=origin,
            now=now,
        )
        if existing is not None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_POLICY,
                source_engine=self.source_engine,
                reason_codes=("SESSION_REUSED", "LOGIN_NOT_REQUIRED"),
                required_capability=HTTP_AUTHENTICATION_CAPABILITY,
                side_effect_class=0,
                forensic={
                    "identity_id": identity.identity_id,
                    "session_id": existing.session_context_id,
                    "secret_reference": existing.secret_name,
                },
            )
        failed = [
            item
            for item in uow.session_contexts.list_for_research_run(opportunity.research_run_id)
            if item.identity_id == identity.identity_id
            and item.state == SessionState.FAILED.value
        ]
        if failed:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("BAD_CREDENTIAL", "AUTH_FAILED"),
                forensic={"identity_id": identity.identity_id},
            )
        session_context_id = new_opaque_id()
        plan = plan_http_login(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=identity.target_reference,
            identity=identity,
            profile=profile,
            username=identity.actor_reference,
            authorized_origin=origin,
            session_context_id=session_context_id,
        )
        if plan.side_effect_level > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING",),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
            )
        scope_decision = authorize_http_transaction_plan(
            plan, compiled_scope, program_policy=program_policy
        )
        if not scope_decision.accepted:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SCOPE,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SCOPE", "SCOPE_BLOCKED"),
                plan=plan,
                required_capability=plan.required_capability,
                side_effect_class=plan.side_effect_level,
            )
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EXECUTE_PLAN,
            source_engine=self.source_engine,
            reason_codes=("COMPILED_FOR_ARC_EXECUTE",),
            plan=plan,
            required_capability=plan.required_capability,
            side_effect_class=plan.side_effect_level,
            forensic={
                "identity_id": identity.identity_id,
                "session_id": session_context_id,
                "secret_reference": profile.password_secret_name,
                "native_capability": HTTP_AUTHENTICATION_CAPABILITY,
            },
        )


class AuthorizationDifferentialPlanner:
    source_engine = "AUTHORIZATION"

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        refs = opportunity.source_refs
        if len(refs) < 4:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("OBJECT_RELATIONSHIP_UNKNOWN",),
            )
        origin, actor, own_object, cross_object = refs[:4]
        if not own_object or not cross_object:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("OBJECT_RELATIONSHIP_UNKNOWN",),
                forensic={"ownership": "UNKNOWN"},
            )
        catalog = load_research_identity_catalog(uow, opportunity.research_run_id)
        identity = catalog.identity_by_actor(actor)
        if identity is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_IDENTITY_PRECONDITION",),
                forensic={"actor": actor},
            )
        mode = refs[4] if len(refs) > 4 else "vulnerable"
        plan = plan_authorization_differential(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=identity.target_reference,
            authorized_origin=origin,
            actor=actor,
            own_object=own_object,
            cross_object=cross_object,
            mode=mode,
        )
        plan = _attach_active_session(
            uow,
            plan,
            identity=identity,
            origin=origin,
            research_run_id=opportunity.research_run_id,
        )
        return _finish_native_plan(
            plan,
            compiled_scope=compiled_scope,
            program_policy=program_policy,
            bounds=bounds,
            source_engine=self.source_engine,
            forensic={
                "actor": actor,
                "own_object": own_object,
                "cross_object": cross_object,
                "ownership": "KNOWN",
                "native_capability": HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
            },
        )


class WorkflowStateTransitionPlanner:
    source_engine = "WORKFLOW"

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        refs = opportunity.source_refs
        if len(refs) < 4:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "WORKFLOW_CONTEXT_INCOMPLETE"),
            )
        origin, actor, resource_id, transition = refs[:4]
        catalog = load_research_identity_catalog(uow, opportunity.research_run_id)
        identity = catalog.identity_by_actor(actor)
        if identity is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_IDENTITY_PRECONDITION",),
                forensic={"actor": actor},
            )
        area = refs[4] if len(refs) > 4 else "workflow"
        plan = plan_state_transition(
            hypothesis_id,
            budget_id=budget_id,
            target_reference=identity.target_reference,
            authorized_origin=origin,
            actor=actor,
            resource_id=resource_id,
            transition=transition,
            area=area,
        )
        plan = _attach_active_session(
            uow,
            plan,
            identity=identity,
            origin=origin,
            research_run_id=opportunity.research_run_id,
        )
        native_se = plan.side_effect_level
        return _finish_native_plan(
            plan,
            compiled_scope=compiled_scope,
            program_policy=program_policy,
            bounds=bounds,
            source_engine=self.source_engine,
            forensic={
                "actor": actor,
                "resource_id": resource_id,
                "transition": transition,
                "native_capability": HTTP_STATE_TRANSITION_CAPABILITY,
                "native_side_effect": native_se,
            },
        )


class MutationVariantPlanner:
    source_engine = "MUTATION"

    def __init__(self, registry: ExperimentCompilerRegistry | None = None) -> None:
        self._registry = registry or ExperimentCompilerRegistry()

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        variant_id = opportunity.source_refs[0] if opportunity.source_refs else ""
        payload = _mutation_bound_payload(uow, opportunity.research_run_id, variant_id)
        if payload is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "MUTATION_VARIANT_BIND_ABSENT"),
                forensic={"variant_id": variant_id},
            )
        native_se = 1 if payload.get("action") == "mutate" else 0
        forensic = {
            "variant_id": variant_id,
            "family_id": payload.get("family_id"),
            "mutation_rule_id": payload.get("mutation_rule_id"),
            "baseline_ref": payload.get("baseline_ref"),
            "native_capability": payload.get("capability_id"),
            "native_side_effect": native_se,
        }
        if native_se > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "NATIVE_SIDE_EFFECT_ABOVE_CEILING"),
                required_capability=str(payload.get("capability_id") or "http.transaction"),
                side_effect_class=native_se,
                forensic=forensic,
            )
        arguments = dict(payload.get("arguments") or {})
        arguments["capability_id"] = payload.get("capability_id")
        arguments["action"] = payload.get("action")
        arguments["mutation_rule_id"] = payload.get("mutation_rule_id")
        compiled = self._registry.compile(
            CompilerRequest(
                hypothesis_id=hypothesis_id,
                budget_id=budget_id,
                target_reference=str(payload.get("target_reference") or target_reference),
                family_id=str(payload.get("family_id") or ""),
                family_name=str(payload.get("family_id") or ""),
                arguments=arguments,
                requested_side_effect=native_se,
            )
        )
        if not compiled.compiled or compiled.plan is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", compiled.reason_code),
                required_capability=str(payload.get("capability_id") or ""),
                side_effect_class=native_se,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        return _finish_native_plan(
            compiled.plan,
            compiled_scope=compiled_scope,
            program_policy=program_policy,
            bounds=bounds,
            source_engine=self.source_engine,
            forensic={
                **forensic,
                "compiler": compiled.compiler_id,
                "native_capability": compiled.plan.required_capability,
                "native_side_effect": compiled.plan.side_effect_level,
            },
        )


class ProtocolStepPlanner:
    source_engine = "PROTOCOL"

    def __init__(self, registry: ExperimentCompilerRegistry | None = None) -> None:
        self._registry = registry or ExperimentCompilerRegistry()

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        if len(opportunity.source_refs) < 3:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "PROTOCOL_SOURCE_REFS_INCOMPLETE"),
            )
        family_id, node_key, identity_id = opportunity.source_refs[:3]
        family = _hunter_family(uow, family_id)
        if family is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "HUNTER_FAMILY_NOT_FOUND"),
            )
        native_se = side_effect_for_family(family.name)
        forensic = {
            "hunter_family": family.name,
            "family_id": family.family_id,
            "node_canonical_key": node_key,
            "identity_id": identity_id,
            "coverage_cell_id": f"{node_key}:{identity_id}:{family_id}",
            "native_side_effect": native_se,
            "native_capability": "http.raw_exchange",
        }
        if native_se > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "NATIVE_SIDE_EFFECT_ABOVE_CEILING"),
                required_capability="http.raw_exchange",
                side_effect_class=native_se,
                forensic=forensic,
            )
        compile_arguments = _protocol_compile_arguments(
            uow,
            opportunity.research_run_id,
            family=family,
            node_key=node_key,
            identity_id=identity_id,
            target_reference=target_reference,
        )
        if compile_arguments is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "PROTOCOL_PARSER_PLAN_UNAVAILABLE"),
                required_capability="http.raw_exchange",
                side_effect_class=native_se,
                forensic=forensic,
            )
        compiled = self._registry.compile(
            CompilerRequest(
                hypothesis_id=hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
                family_id=family.family_id,
                family_name=family.name,
                arguments=compile_arguments,
                requested_side_effect=native_se,
            )
        )
        forensic = {**forensic, "compiler": compiled.compiler_id}
        if compiled.outcome is CompilerOutcome.BLOCKED_MISSING_SEMANTICS:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", compiled.reason_code),
                required_capability="http.raw_exchange",
                side_effect_class=native_se,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        if not compiled.compiled or compiled.plan is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_POLICY,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_POLICY", compiled.reason_code),
                required_capability="http.raw_exchange",
                side_effect_class=native_se,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        return _finish_native_plan(
            compiled.plan,
            compiled_scope=compiled_scope,
            program_policy=program_policy,
            bounds=bounds,
            source_engine=self.source_engine,
            forensic={
                **forensic,
                "native_capability": compiled.plan.required_capability,
                "native_side_effect": compiled.plan.side_effect_level,
            },
        )


def _mutation_bound_payload(uow, research_run_id: str, variant_id: str) -> dict | None:
    if not variant_id:
        return None
    events = uow.audit_events.list_for_subject("research_run", research_run_id)
    matches = [
        item
        for item in events
        if item.event_type == "MUTATION_VARIANT_BOUND"
        and ((item.payload or {}).get("variant_id") == variant_id or item.correlation_id == variant_id)
    ]
    if not matches:
        return None
    return dict(sorted(matches, key=lambda item: item.occurred_at)[-1].payload or {})


def _protocol_compile_arguments(
    uow,
    research_run_id: str,
    *,
    family,
    node_key: str,
    identity_id: str,
    target_reference: str,
) -> dict | None:
    queue = _v3_queue_for_cell(uow, research_run_id, family.family_id, node_key, identity_id)
    if queue is not None:
        arguments = _compile_arguments(queue)
        if arguments.get("step_id") and arguments.get("authorized_origin"):
            return arguments
    from zest.application.hunter_coverage_opportunity_source import _fact_attributes_for_node
    from zest.research.protocol.parser_plan import build_protocol_parser_plan
    from zest.research.selection import HunterFamilyView

    view = HunterFamilyView(
        family_id=family.family_id,
        name=family.name,
        target_node_kinds=family.target_node_kinds,
        preconditions=family.preconditions,
        claim_template=family.claim_template,
        evidence_requirements=family.evidence_requirements,
        validation_tier=family.validation_tier,
        enabled=family.enabled,
        version=family.version,
    )
    try:
        plan = build_protocol_parser_plan(view)
    except Exception:
        return None
    if not plan.steps:
        return None
    step = plan.steps[0]
    attrs = _fact_attributes_for_node(uow, research_run_id, node_key)
    origin = attrs.get("authorized_origin") or attrs.get("origin") or target_reference
    path = attrs.get("path") or "/"
    return {
        "step_id": step.step_id,
        "control": step.control,
        "authorized_origin": origin,
        "path": path,
        "protocol_lane": plan.lane,
        "dimension_values": dict(step.dimension_values),
    }


def _attach_active_session(
    uow,
    plan: ExperimentPlan,
    *,
    identity: Identity,
    origin: str,
    research_run_id: str,
) -> ExperimentPlan:
    arguments = dict(plan.arguments)
    arguments["identity_id"] = identity.identity_id
    session = active_session_for_identity(
        uow,
        research_run_id=research_run_id,
        identity_id=identity.identity_id,
        origin=origin,
        now=datetime.now(timezone.utc),
    )
    if session is not None:
        arguments["session_context_reference"] = session.session_context_id
    return replace(plan, arguments=arguments)


def _finish_native_plan(
    plan: ExperimentPlan,
    *,
    compiled_scope,
    program_policy,
    bounds: OrchestrationBounds,
    source_engine: str,
    forensic: dict,
) -> ResearchCompileDecision:
    if plan.side_effect_level > bounds.side_effect_ceiling:
        return ResearchCompileDecision(
            status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
            source_engine=source_engine,
            reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "NATIVE_SIDE_EFFECT_ABOVE_CEILING"),
            plan=plan,
            required_capability=plan.required_capability,
            side_effect_class=plan.side_effect_level,
            forensic=forensic,
        )
    scope_decision = authorize_http_transaction_plan(
        plan, compiled_scope, program_policy=program_policy
    )
    if not scope_decision.accepted:
        return ResearchCompileDecision(
            status=ResearchCompileStatus.BLOCKED_SCOPE,
            source_engine=source_engine,
            reason_codes=("BLOCKED_SCOPE",),
            plan=plan,
            required_capability=plan.required_capability,
            side_effect_class=plan.side_effect_level,
            forensic=forensic,
        )
    return ResearchCompileDecision(
        status=ResearchCompileStatus.EXECUTE_PLAN,
        source_engine=source_engine,
        reason_codes=("COMPILED_FOR_ARC_EXECUTE",),
        plan=plan,
        required_capability=plan.required_capability,
        side_effect_class=plan.side_effect_level,
        forensic={**forensic, "native_side_effect": plan.side_effect_level},
    )


def _hunter_family(uow, family_id: str):
    for record in uow.hunter_families.list_enabled():
        if record.family_id == family_id:
            return record
    return None


def _v3_queue_for_cell(uow, research_run_id: str, family_id: str, node_key: str, identity_id: str):
    matches = [
        item
        for item in uow.hunt_v3_queue.list_for_research_run(research_run_id)
        if item.family_id == family_id
        and item.node_canonical_key == node_key
        and (item.identity_id or "ANONYMOUS") == identity_id
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.created_at)[-1]


def _compile_arguments(queue) -> dict:
    arguments = dict(queue.arguments or {})
    cells = arguments.get("cells")
    if isinstance(cells, list) and cells and not arguments.get("cell_id"):
        first = cells[0]
        if isinstance(first, dict) and first.get("cell_id"):
            arguments["cell_id"] = first["cell_id"]
            if first.get("dimension_values") is not None:
                arguments["dimension_values"] = first["dimension_values"]
            if first.get("control") is not None:
                arguments["control"] = first["control"]
    steps = arguments.get("steps")
    if isinstance(steps, list) and steps and not arguments.get("step_id"):
        first = steps[0]
        if isinstance(first, dict) and first.get("step_id"):
            arguments["step_id"] = first["step_id"]
            if first.get("dimension_values") is not None:
                arguments["dimension_values"] = first["dimension_values"]
            if first.get("control") is not None:
                arguments["control"] = first["control"]
    return arguments


class OastInteractionPlanner:
    source_engine = "OAST"

    def __init__(self, registry: ExperimentCompilerRegistry | None = None) -> None:
        self._registry = registry or ExperimentCompilerRegistry()

    def plan(
        self,
        uow,
        opportunity: ResearchOpportunityRecord,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds: OrchestrationBounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        if len(opportunity.source_refs) < 4:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "OAST_SOURCE_REFS_INCOMPLETE"),
            )
        family_id, node_key, identity_id, sink = opportunity.source_refs[:4]
        native_se = int((opportunity.dimensions or {}).get("side_effect_requirement") or 0)
        requires_session = "requires_session:true" in opportunity.assumptions
        callback_id = ""
        expires_raw = ""
        for item in opportunity.assumptions:
            if item.startswith("callback_id:"):
                callback_id = item.split(":", 1)[1]
            if item.startswith("expires_at:"):
                expires_raw = item.split(":", 1)[1]
        if not callback_id:
            callback_id = str((opportunity.dimensions or {}).get("oast_callback_id") or "")
        forensic = {
            "family_id": family_id,
            "node_canonical_key": node_key,
            "identity_id": identity_id,
            "sink": sink,
            "native_side_effect": native_se,
            "native_capability": "http.transaction",
            "callback_id": callback_id,
        }
        if not callback_id:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "OAST_CALLBACK_ID_ABSENT"),
                side_effect_class=native_se,
                forensic=forensic,
            )
        if native_se > bounds.side_effect_ceiling:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING,
                source_engine=self.source_engine,
                reason_codes=("BLOCKED_SIDE_EFFECT_CEILING", "NATIVE_SIDE_EFFECT_ABOVE_CEILING"),
                required_capability="http.transaction",
                side_effect_class=native_se,
                forensic=forensic,
            )
        from zest.application.hunter_coverage_opportunity_source import _fact_attributes_for_node

        attrs = _fact_attributes_for_node(uow, opportunity.research_run_id, node_key)
        listener = str(attrs.get("oast_listener_origin") or "").rstrip("/")
        origin = str(attrs.get("authorized_origin") or attrs.get("origin") or "")
        path = str(attrs.get("path") or "/")
        if not listener:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "OAST_LISTENER_ORIGIN_ABSENT"),
                required_capability="http.transaction",
                side_effect_class=native_se,
                forensic=forensic,
            )
        callback_url = f"{listener}/oast/{callback_id}"
        if len(callback_url) > 128:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "OAST_CALLBACK_URL_TOO_LONG"),
                side_effect_class=native_se,
                forensic=forensic,
            )
        if requires_session:
            catalog = load_research_identity_catalog(uow, opportunity.research_run_id)
            identity = catalog.identity(identity_id)
            if identity is None:
                return ResearchCompileDecision(
                    status=ResearchCompileStatus.MISSING_PRECONDITION,
                    source_engine=self.source_engine,
                    reason_codes=("MISSING_IDENTITY_PRECONDITION",),
                    forensic=forensic,
                )
            session = active_session_for_identity(
                uow,
                research_run_id=opportunity.research_run_id,
                identity_id=identity.identity_id,
                origin=origin or identity.target_reference,
                now=datetime.now(timezone.utc),
            )
            if session is None:
                return ResearchCompileDecision(
                    status=ResearchCompileStatus.ENGINE_DEPENDENCY_PENDING,
                    source_engine=self.source_engine,
                    reason_codes=("ENGINE_DEPENDENCY_PENDING", "AUTHENTICATION_PREREQUISITE"),
                    forensic=forensic,
                )
            http_session = session.session_context_id
        else:
            http_session = None
        action = "mutate" if native_se >= 1 or family_id == FAMILY_XXE else "read"
        method = "POST" if action == "mutate" else "GET"
        http_arguments: dict = {
            "authorized_origin": origin or target_reference,
            "method": method,
            "path": path or "/",
            "query": {sink: callback_url},
            "action": action,
            "oast_family": family_id,
        }
        if http_session is not None:
            http_arguments["session_context_reference"] = http_session
        if action == "mutate":
            http_arguments["body"] = callback_url
            http_arguments["content_type"] = (
                "application/xml" if family_id == FAMILY_XXE else "application/x-www-form-urlencoded"
            )
        compiled = self._registry.compile(
            CompilerRequest(
                hypothesis_id=hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
                family_id=family_id,
                family_name=family_id,
                arguments=http_arguments,
                requested_side_effect=native_se,
            )
        )
        forensic = {**forensic, "compiler": compiled.compiler_id}
        if not compiled.compiled or compiled.plan is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", compiled.reason_code),
                required_capability="http.transaction",
                side_effect_class=native_se,
                compiler_id=compiled.compiler_id,
                forensic=forensic,
            )
        now = datetime.now(timezone.utc)
        expires_at = now + OAST_DEFAULT_TTL
        if expires_raw:
            try:
                expires_at = datetime.fromisoformat(expires_raw)
            except ValueError:
                expires_at = now + OAST_DEFAULT_TTL
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if uow.oast_tokens.get(callback_id) is None:
            try:
                uow.oast_tokens.insert(
                    OastTokenRecord(
                        token_id=callback_id,
                        research_run_id=opportunity.research_run_id,
                        hypothesis_id=hypothesis_id,
                        target_reference=target_reference,
                        expires_at=expires_at,
                        created_at=now,
                    )
                )
            except PersistenceConflictError:
                pass
        return _finish_native_plan(
            compiled.plan,
            compiled_scope=compiled_scope,
            program_policy=program_policy,
            bounds=bounds,
            source_engine=self.source_engine,
            forensic={
                **forensic,
                "native_capability": compiled.plan.required_capability,
                "native_side_effect": compiled.plan.side_effect_level,
                "evaluation_strategy": OAST_CALLBACK_EVALUATION_STRATEGY,
                "callback_channel": "http",
            },
        )


class DifferentialPlanner:
    source_engine = "DIFFERENTIAL"

    def plan(self, uow, opportunity, **kwargs) -> ResearchCompileDecision:
        if len(opportunity.source_refs) < 2:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "DIFFERENTIAL_PAIR_INCOMPLETE"),
            )
        left = uow.observations.get(opportunity.source_refs[0])
        right = uow.observations.get(opportunity.source_refs[1])
        if left is None or right is None:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "OBSERVATION_NOT_FOUND"),
            )
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EVALUATE_EXISTING,
            source_engine=self.source_engine,
            reason_codes=("EVALUATE_EXISTING_PAIR",),
            side_effect_class=0,
            forensic={
                "native_side_effect": 0,
                "evaluation_strategy": "differential.controlled.v1",
                "left_observation_id": opportunity.source_refs[0],
                "right_observation_id": opportunity.source_refs[1],
            },
        )


class InvariantPlanner:
    source_engine = "INVARIANT"

    def plan(
        self,
        uow,
        opportunity,
        *,
        hypothesis_id: str,
        budget_id: str,
        target_reference: str,
        bounds,
        compiled_scope,
        program_policy,
    ) -> ResearchCompileDecision:
        assumptions = {
            item.split(":", 1)[0]: item.split(":", 1)[1]
            for item in opportunity.assumptions
            if ":" in item
        }
        if assumptions.get("authority") == "BLOCKED":
            return ResearchCompileDecision(
                status=ResearchCompileStatus.EVALUATE_EXISTING,
                source_engine=self.source_engine,
                reason_codes=("AUTHORITY_BLOCK_EXPLICIT",),
                side_effect_class=3,
                forensic={
                    "native_capability": "http.raw_exchange",
                    "native_side_effect": 3,
                    "evaluation_strategy": "invariant.security_property.v1",
                },
            )
        if assumptions.get("mode") == "execute":
            origin = assumptions.get("origin") or target_reference
            path = assumptions.get("path") or "/"
            plan = plan_http_transaction_read(
                hypothesis_id,
                budget_id=budget_id,
                target_reference=target_reference,
                authorized_origin=origin,
                path=path,
                method=assumptions.get("method") or "GET",
            )
            return _finish_native_plan(
                plan,
                compiled_scope=compiled_scope,
                program_policy=program_policy,
                bounds=bounds,
                source_engine=self.source_engine,
                forensic={
                    "native_capability": plan.required_capability,
                    "native_side_effect": plan.side_effect_level,
                    "evaluation_strategy": "invariant.security_property.v1",
                    "execute_unauthenticated_probe": True,
                },
            )
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EVALUATE_EXISTING,
            source_engine=self.source_engine,
            reason_codes=("EVALUATE_EXISTING_PROPERTY",),
            side_effect_class=0,
            forensic={
                "native_side_effect": 0,
                "evaluation_strategy": "invariant.security_property.v1",
                "property_id": assumptions.get("property"),
            },
        )


class ChainPlanner:
    source_engine = "CHAIN"

    def plan(self, uow, opportunity, **kwargs) -> ResearchCompileDecision:
        if len(opportunity.source_refs) < 2:
            return ResearchCompileDecision(
                status=ResearchCompileStatus.MISSING_PRECONDITION,
                source_engine=self.source_engine,
                reason_codes=("MISSING_PRECONDITION", "CHAIN_NODES_INCOMPLETE"),
            )
        return ResearchCompileDecision(
            status=ResearchCompileStatus.EVALUATE_EXISTING,
            source_engine=self.source_engine,
            reason_codes=("EVALUATE_EXISTING_CHAIN",),
            side_effect_class=0,
            forensic={
                "native_side_effect": 0,
                "evaluation_strategy": "chain.causal.v1",
                "left_assessment_id": opportunity.source_refs[0],
                "right_assessment_id": opportunity.source_refs[1],
            },
        )


class ResearchWorkPlannerRegistry:
    """Kind → planner. Default is the existing model/diagnostic path."""

    def __init__(self, planners: dict[OpportunityKind, ResearchWorkPlanner] | None = None) -> None:
        self._planners = dict(planners or {})

    def register(self, kind: OpportunityKind, planner: ResearchWorkPlanner) -> None:
        self._planners[kind] = planner

    def planner_for(self, kind: str) -> ResearchWorkPlanner:
        try:
            parsed = OpportunityKind(kind)
        except ValueError:
            return ModelResearchPlanner()
        return self._planners.get(parsed, ModelResearchPlanner())

    def plan(self, uow, opportunity: ResearchOpportunityRecord, **kwargs) -> ResearchCompileDecision:
        return self.planner_for(opportunity.opportunity_kind).plan(
            uow, opportunity, **kwargs
        )


def default_research_work_planner_registry() -> ResearchWorkPlannerRegistry:
    registry = ResearchWorkPlannerRegistry()
    registry.register(OpportunityKind.DISCOVERY_HANDOFF, DiscoveryHandoffPlanner())
    registry.register(OpportunityKind.HUNTER_COVERAGE_GAP, HunterCoveragePlanner())
    registry.register(OpportunityKind.AUTHENTICATION, AuthenticationPlanner())
    registry.register(
        OpportunityKind.AUTHORIZATION_DIFFERENTIAL, AuthorizationDifferentialPlanner()
    )
    registry.register(
        OpportunityKind.WORKFLOW_STATE_TRANSITION, WorkflowStateTransitionPlanner()
    )
    registry.register(
        OpportunityKind.SURFACE_DISCOVERY, DeferredEnginePlanner("COVERAGE")
    )
    registry.register(OpportunityKind.MUTATION_VARIANT, MutationVariantPlanner())
    registry.register(OpportunityKind.PROTOCOL_STEP, ProtocolStepPlanner())
    registry.register(OpportunityKind.OAST_INTERACTION, OastInteractionPlanner())
    registry.register(OpportunityKind.DIFFERENTIAL, DifferentialPlanner())
    registry.register(OpportunityKind.INVARIANT, InvariantPlanner())
    registry.register(OpportunityKind.CHAIN, ChainPlanner())
    return registry
