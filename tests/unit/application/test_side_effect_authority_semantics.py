"""Phase 6.2 side-effect authority: ceiling is not an approval threshold."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.research_work_planners import (
    DiscoveryHandoffPlanner,
    ResearchCompileStatus,
)
from zest.core import (
    ExecutionDecisionKind,
    ReasonCode,
    SideEffectLevel,
    evaluate_execution,
)
from zest.core.capability import CapabilityAuthorizationView
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import FrontierItemRecord
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.orchestration import OrchestrationBounds
from zest.tools.registry import registry_from_documents
from core.fixtures import base_request, human_approval
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run


def _se_registry(*, level: int):
    document = {
        "capability_id": "diagnostic.authority_fixture",
        "version": "1",
        "implementation_reference": "diagnostic.authority_fixture",
        "executor_class": "WORKER",
        "actions": {
            "hold": {
                "action_id": "hold",
                "argument_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                "result_schema": {"type": "object"},
                "minimum_side_effect_level": level,
                "maximum_side_effect_level": level,
                "target_types": ["opaque_reference"],
                "network_policy": None,
                "requirements": [],
                "supports_reproduction": False,
                "supports_negative_control": False,
                "normalizer_reference": None,
            }
        },
    }
    registry = registry_from_documents([document])
    definition = registry.get("diagnostic.authority_fixture")
    assert definition is not None
    view = CapabilityAuthorizationView(
        capability_id=definition.capability_id,
        action="hold",
        capability_version=definition.version,
        definition_fingerprint=definition.definition_fingerprint,
        authoritative_minimum_side_effect=level,
        effective_side_effect=level,
    )
    return registry, view


class SideEffectAuthoritySemanticsTests(unittest.TestCase):
    def test_core_se3_denies_even_with_human_approval(self) -> None:
        registry, view = _se_registry(level=3)
        decision = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_3,
                capability=view,
                approval=human_approval(),
            ),
            capability_registry=registry,
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SIDE_EFFECT_LEVEL_DENIED)

    def test_core_se2_allows_with_recorded_human_approval(self) -> None:
        registry, view = _se_registry(level=2)
        decision = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_2,
                capability=view,
                approval=human_approval(),
            ),
            capability_registry=registry,
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.ALLOW)

    def test_core_se2_requires_human_review_without_approval(self) -> None:
        registry, view = _se_registry(level=2)
        decision = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_2,
                capability=view,
                approval=None,
            ),
            capability_registry=registry,
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW)

    def test_orchestration_ceiling_is_blocked_not_approvable(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.frontier_items["front-se3"] = FrontierItemRecord(
            frontier_id="front-se3",
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            goal_kind="CHARACTERIZE_HTTP_OPERATION",
            candidate_origin="http://127.0.0.1:9",
            candidate_path="/handoff",
            identity_id="ANONYMOUS",
            proposed_capability="http.transaction",
            proposed_action="read",
            expected_side_effect=3,
            budget_class=3,
            structural_signature="sig-front-se3",
            dedupe_identity="dedupe-front-se3",
            created_at=CREATED_AT,
            current_state="DEFERRED_TO_RESEARCH",
            state_version=3,
            attributes={"method": "GET"},
        )
        class _Opp:
            source_refs = ("front-se3",)

        bounds = OrchestrationBounds(
            max_cycles=6,
            max_experiments=8,
            max_model_calls=50,
            max_worker_invocations=10,
            max_elapsed_ms=60_000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
            allow_repeated_control_experiments=False,
        )
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            decision = DiscoveryHandoffPlanner().plan(
                uow,
                _Opp(),
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="http://127.0.0.1:9/",
                bounds=bounds,
                compiled_scope=CompiledScope(
                    rules=(
                        CompiledScopeRule(
                            rule_id="rule-allow",
                            effect=ScopeRuleEffect.ALLOW,
                            scheme="http",
                            host="127.0.0.1",
                            host_pattern=None,
                            port=9,
                            path_prefix=None,
                            source_reference="scope-src",
                            expires_at=None,
                        ),
                    )
                ),
                program_policy=None,
            )
            uow.rollback()
        self.assertEqual(decision.status, ResearchCompileStatus.BLOCKED_SIDE_EFFECT_CEILING)
        self.assertNotEqual(decision.status, ResearchCompileStatus.APPROVAL_REQUIRED)
