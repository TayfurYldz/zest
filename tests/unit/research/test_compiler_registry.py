"""MR-2 compiler registry: known families bypass the generic planner."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.core.enums import ScopeClassification
from research_os.research.compiler_registry import (
    COMPILER_AUTHORIZATION_DIFFERENTIAL,
    COMPILER_GENERIC_PLANNER,
    COMPILER_MUTATION_MATRIX_CELL,
    COMPILER_MUTATION_VARIANT,
    COMPILER_PROTOCOL_STEP,
    COMPILER_STATE_TRANSITION,
    MUTATION_MATRIX_FAMILIES,
    PROTOCOL_FAMILIES,
    CompilerOutcome,
    CompilerRequest,
    ExperimentCompilerRegistry,
    GenericPlannerCompiler,
    assert_plan_not_understated,
)
from research_os.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from research_os.research.discovery.types import AttackSurfaceNodeKind
from research_os.research.mutation import mutate_for_node
from research_os.research.mutation.matrix import build_mutation_matrix
from research_os.research.planning import plan_admitted_hypothesis
from research_os.research.proposals import parse_hypothesis_challenge, parse_hypothesis_proposal
from research_os.research.selection import HunterFamilyView
from research_os.research.target_model import TargetEpistemicStatus
from research_os.data.postgres.hunter_family_seed import SEED_FAMILIES
from research_os.research.protocol.parser_plan import build_protocol_parser_plan
from research_os.tools.capabilities import (
    DIAGNOSTIC_ECHO_CAPABILITY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_RAW_EXCHANGE_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
    HTTP_TRANSACTION_CAPABILITY,
)


def _authz_args(**overrides) -> dict:
    values = {
        "authorized_origin": "http://127.0.0.1:8094",
        "actor": "alice",
        "own_object": "1",
        "cross_object": "2",
        "mode": "vulnerable",
    }
    values.update(overrides)
    return values


def _workflow_args(**overrides) -> dict:
    values = {
        "authorized_origin": "http://127.0.0.1:8095",
        "actor": "alice",
        "resource_id": "wf-1",
        "transition": "approve",
        "area": "workflow",
    }
    values.update(overrides)
    return values


def _seed_family(family_id: str) -> HunterFamilyView:
    row = next(item for item in SEED_FAMILIES if item["family_id"] == family_id)
    return HunterFamilyView(
        family_id=str(row["family_id"]),
        name=str(row["name"]),
        target_node_kinds=tuple(str(item) for item in row["target_node_kinds"]),
        preconditions=dict(row["preconditions"]),
        claim_template=str(row["claim_template"]),
        evidence_requirements=dict(row["evidence_requirements"]),
        validation_tier=str(row["validation_tier"]),
        enabled=bool(row["enabled"]),
        version=int(row["version"]),
    )


def _mutation_node() -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id="op-1",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="origin:http://127.0.0.1:8090|path:/api/users|method:GET",
        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
        identity_ids=(),
        provenance_refs=("sensor_observation:so-1",),
        scope_classification=ScopeClassification.IN_SCOPE,
        attributes={
            "origin": "http://127.0.0.1:8090",
            "path": "/api/users",
            "method": "GET",
            "query_params": ["id"],
        },
    )


class CompilerRegistryKnownFamilyTests(unittest.TestCase):
    def test_object_authorization_bypasses_generic_planner(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_id="hf-object-authz",
                family_name="OBJECT_AUTHORIZATION",
                arguments=_authz_args(),
            )
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_AUTHORIZATION_DIFFERENTIAL)
        self.assertNotEqual(result.compiler_id, COMPILER_GENERIC_PLANNER)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY)
        self.assertEqual(result.plan.action, "probe")
        self.assertEqual(result.plan.side_effect_level, 0)
        self.assertIsNotNone(result.plan.capability_version)
        self.assertIsNotNone(result.plan.capability_definition_fingerprint)
        assert_plan_not_understated(result.plan)

    def test_workflow_state_transition_bypasses_generic_planner(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_id="hf-workflow-trans",
                family_name="WORKFLOW_STATE_TRANSITION",
                arguments=_workflow_args(),
            )
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_STATE_TRANSITION)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_STATE_TRANSITION_CAPABILITY)
        self.assertEqual(result.plan.side_effect_level, 1)
        assert_plan_not_understated(result.plan)

    def test_known_family_does_not_use_generic_even_if_diagnostic_args_are_present(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_name="OBJECT_AUTHORIZATION",
                arguments={
                    "capability_id": DIAGNOSTIC_ECHO_CAPABILITY,
                    "action": "echo",
                    "message": "ping",
                    "expected_observation": "echoed value matches input",
                    "disconfirming_observation": "no result or mismatched value",
                },
            )
        )
        self.assertEqual(result.compiler_id, COMPILER_AUTHORIZATION_DIFFERENTIAL)
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_MISSING_SEMANTICS)
        self.assertIsNone(result.plan)

    def test_authorization_compiler_is_deterministic(self) -> None:
        registry = ExperimentCompilerRegistry()
        request = CompilerRequest(
            hypothesis_id="hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            family_name="OBJECT_AUTHORIZATION",
            arguments=_authz_args(),
        )
        first = registry.compile(request)
        second = registry.compile(request)
        self.assertEqual(first.plan, second.plan)

    def test_missing_authorization_fields_fail_closed(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_name="OBJECT_AUTHORIZATION",
                arguments={"authorized_origin": "http://127.0.0.1:8094"},
            )
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_MISSING_SEMANTICS)
        self.assertIsNone(result.plan)


class CompilerRegistryGenericFallbackTests(unittest.TestCase):
    def test_unknown_family_falls_back_to_generic_planner(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_name="NOT_A_REGISTERED_FAMILY",
                arguments={
                    "capability_id": DIAGNOSTIC_ECHO_CAPABILITY,
                    "action": "echo",
                    "message": "ping",
                    "expected_observation": "echoed value matches input",
                    "disconfirming_observation": "no result or mismatched value",
                    "evaluation_strategy": "diagnostic.echo.v1",
                },
            )
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_GENERIC_PLANNER)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, DIAGNOSTIC_ECHO_CAPABILITY)
        assert_plan_not_understated(result.plan)

    def test_planning_alias_is_not_a_worker_capability(self) -> None:
        registry = ExperimentCompilerRegistry()
        result = registry.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                arguments={
                    "capability_id": "mutation.matrix",
                    "action": "plan",
                    "expected_observation": "x",
                    "disconfirming_observation": "y",
                },
            )
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNSUPPORTED_CAPABILITY)
        self.assertEqual(result.reason_code, "PLANNING_ALIAS_IS_NOT_A_WORKER_CAPABILITY")
        self.assertIsNone(result.plan)

    def test_generic_does_not_understate_side_effect(self) -> None:
        compiler = GenericPlannerCompiler()
        result = compiler.compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                arguments={
                    "capability_id": HTTP_STATE_TRANSITION_CAPABILITY,
                    "action": "probe",
                    "authorized_origin": "http://127.0.0.1:8095",
                    "actor": "alice",
                    "resource_id": "wf-1",
                    "transition": "approve",
                    "area": "workflow",
                    "expected_observation": "state changed",
                    "disconfirming_observation": "denied",
                    "evaluation_strategy": "http.state_transition.v1",
                },
                requested_side_effect=0,
            )
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_INVALID_INPUT)
        self.assertEqual(result.reason_code, "RISK_UNDERSTATEMENT")

    def test_proposal_path_matches_plan_admitted_hypothesis_for_diagnostic(self) -> None:
        proposal = parse_hypothesis_proposal(
            {
                "proposed_claim": "The diagnostic capability returns the submitted value.",
                "rationale": "round-trip",
                "source_references": ["proc:research-question"],
                "assumptions": ["available"],
                "unresolved_questions": ["protocol"],
                "suggested_disconfirming_test": "submit a value and observe mismatch",
                "suggested_capability": DIAGNOSTIC_ECHO_CAPABILITY,
                "expected_security_relevance": None,
                "novelty_basis": "UNCLASSIFIED",
            }
        )
        challenge = parse_hypothesis_challenge(
            {
                "alternative_explanations": ["protocol mismatch"],
                "missing_preconditions": [],
                "contradictory_source_references": [],
                "required_negative_controls": ["repeat"],
                "reasons_not_to_test": [],
                "proposed_disconfirming_observation": "no result or mismatched value",
                "ambiguity": "not a security conclusion",
            }
        )
        expected = plan_admitted_hypothesis(
            "hyp-1",
            proposal,
            challenge,
            budget_id="budget-1",
            target_reference="target-1",
        )
        result = ExperimentCompilerRegistry().compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                proposal=proposal,
                challenge=challenge,
            )
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_GENERIC_PLANNER)
        self.assertEqual(result.plan, expected)


class MutationAndProtocolCompilerTests(unittest.TestCase):
    def test_every_mutation_matrix_family_compiles_selected_cell_to_http_transaction(self) -> None:
        registry = ExperimentCompilerRegistry()
        for name in sorted(MUTATION_MATRIX_FAMILIES):
            family_id = next(item["family_id"] for item in SEED_FAMILIES if item["name"] == name)
            family = _seed_family(str(family_id))
            matrix = build_mutation_matrix(family)
            cell = matrix.cells[0]
            result = registry.compile(
                CompilerRequest(
                    hypothesis_id="hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    family_name=name,
                    arguments={
                        "cell_id": cell.cell_id,
                        "dimension_values": dict(cell.dimension_values),
                        "control": cell.control,
                        "authorized_origin": "http://127.0.0.1:8090",
                        "path": "/api/users",
                        "method": "GET",
                        "body": "attacker-supplied",
                        "query": {"injected": "no"},
                    },
                )
            )
            with self.subTest(family=name):
                self.assertEqual(result.compiler_id, COMPILER_MUTATION_MATRIX_CELL)
                self.assertTrue(result.compiled)
                assert result.plan is not None
                self.assertEqual(result.plan.required_capability, HTTP_TRANSACTION_CAPABILITY)
                self.assertNotEqual(result.plan.arguments.get("body"), "attacker-supplied")
                self.assertNotIn("injected", result.plan.arguments.get("query") or {})
                self.assertEqual(result.plan.evaluation_strategy, "mutation.matrix.v1")
                self.assertTrue(result.plan.disconfirming_observation)
                assert_plan_not_understated(result.plan)

    def test_known_mutation_cell_without_caller_dimensions_compiles_from_matrix(self) -> None:
        result = ExperimentCompilerRegistry().compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_name="SQL_INJECTION",
                arguments={
                    "cell_id": "hf-sqli:cell:000",
                    "authorized_origin": "http://127.0.0.1:8090",
                    "path": "/api/users",
                },
            )
        )
        self.assertTrue(result.compiled)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_TRANSACTION_CAPABILITY)

    def test_fabricated_mutation_cell_with_complete_dimensions_is_unknown_cell(self) -> None:
        family = _seed_family("hf-sqli")
        matrix = build_mutation_matrix(family)
        cell = matrix.cells[0]
        result = ExperimentCompilerRegistry().compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                family_name="SQL_INJECTION",
                arguments={
                    "cell_id": "fabricated-cell-not-in-matrix",
                    "dimension_values": dict(cell.dimension_values),
                    "control": cell.control,
                    "authorized_origin": "http://127.0.0.1:8090",
                    "path": "/api/users",
                },
            )
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")
        self.assertIsNone(result.plan)

    def test_mutation_engine_variant_compiles_to_http_transaction(self) -> None:
        node = _mutation_node()
        graph = AttackSurfaceGraph(
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            nodes=(node,),
            edges=(),
        )
        variants = mutate_for_node(node, graph, variant_id_prefix="prefix")
        self.assertTrue(variants)
        variant = variants[0]
        result = ExperimentCompilerRegistry().compile(
            CompilerRequest(
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                target_reference=variant.target_reference,
                arguments={
                    "mutation_rule_id": variant.mutation_rule_id,
                    "capability_id": variant.capability_id,
                    "action": variant.action,
                    **dict(variant.arguments),
                },
            )
        )
        self.assertTrue(result.compiled)
        self.assertEqual(result.compiler_id, COMPILER_MUTATION_VARIANT)
        assert result.plan is not None
        self.assertEqual(result.plan.required_capability, HTTP_TRANSACTION_CAPABILITY)
        self.assertEqual(result.plan.hypothesis_id, "hyp-1")
        assert_plan_not_understated(result.plan)

    def test_protocol_families_compile_selected_step_to_raw_exchange(self) -> None:
        registry = ExperimentCompilerRegistry()
        family_ids = {
            "HTTP_REQUEST_SMUGGLING_DESYNC": "hf-http-smuggling-desync",
            "HTTP_CACHE_POISONING_DECEPTION": "hf-cache-poison-deception",
        }
        for name in sorted(PROTOCOL_FAMILIES):
            family = _seed_family(family_ids[name])
            plan = build_protocol_parser_plan(family)
            step = plan.steps[0]
            result = registry.compile(
                CompilerRequest(
                    hypothesis_id="hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    family_name=name,
                    arguments={
                        "step_id": step.step_id,
                        "dimension_values": dict(step.dimension_values),
                        "control": step.control,
                        "protocol_lane": plan.lane,
                        "capability_id": "http.transaction",
                        "action": "read",
                        "authorized_origin": "http://127.0.0.1:8090",
                        "method": "GET",
                        "path": "/",
                        "body": "attacker-supplied",
                    },
                )
            )
            with self.subTest(family=name):
                self.assertEqual(result.compiler_id, COMPILER_PROTOCOL_STEP)
                self.assertTrue(result.compiled)
                assert result.plan is not None
                self.assertEqual(result.plan.required_capability, HTTP_RAW_EXCHANGE_CAPABILITY)
                self.assertEqual(result.plan.action, "probe")
                self.assertNotIn("body", result.plan.arguments)
                self.assertEqual(result.plan.side_effect_level, 0)
                self.assertTrue(result.plan.disconfirming_observation)
                assert_plan_not_understated(result.plan)


if __name__ == "__main__":
    unittest.main()
