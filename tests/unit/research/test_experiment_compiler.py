from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.compiler import (
    ExperimentCompileError,
    ExperimentIntent,
    compile_experiment_intent,
)
from zest.research.planning import plan_admitted_hypothesis, plan_diagnostic_echo
from zest.research.proposals import parse_hypothesis_challenge, parse_hypothesis_proposal
from zest.research.types import ResearchInputError
from zest.tools.registry import load_capability_registry, registry_from_documents


def _intent(**overrides) -> ExperimentIntent:
    values = dict(
        hypothesis_id="hyp-1",
        capability_id="diagnostic.echo",
        action="echo",
        target_reference="target-1",
        arguments={"message": "ping"},
        requested_budget_id="budget-1",
        expected_observation="echoed value matches input",
        disconfirming_observation="no result or mismatched value",
        evaluation_strategy="diagnostic.echo.v1",
    )
    values.update(overrides)
    return ExperimentIntent(**values)


class ExperimentCompilerTests(unittest.TestCase):
    def test_unknown_capability_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(capability_id="http.scanner"))
        self.assertEqual(ctx.exception.reason_code, "UNKNOWN_CAPABILITY")

    def test_unknown_action_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(action="scan"))
        self.assertEqual(ctx.exception.reason_code, "UNKNOWN_ACTION")

    def test_missing_required_argument_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(arguments={}))
        self.assertEqual(ctx.exception.reason_code, "MISSING_REQUIRED_ARGUMENT")

    def test_extra_argument_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(arguments={"message": "ping", "extra": "x"}))
        self.assertEqual(ctx.exception.reason_code, "UNEXPECTED_ARGUMENT")

    def test_wrong_type_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(arguments={"message": 1}))
        self.assertEqual(ctx.exception.reason_code, "INVALID_ARGUMENT_TYPE")

    def test_nested_malformed_input_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                _intent(
                    capability_id="http.authorization.differential",
                    action="probe",
                    arguments={
                        "authorized_origin": "http://127.0.0.1:1",
                        "actor": "a",
                        "own_object": "1",
                        "cross_object": "2",
                        "mode": {"nested": True},
                    },
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="http.authorization.differential.v1",
                )
            )

    def test_requested_risk_below_minimum_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(
                _intent(
                    capability_id="http.state_transition",
                    action="probe",
                    arguments={
                        "authorized_origin": "http://127.0.0.1:1",
                        "actor": "a",
                        "resource_id": "r1",
                        "transition": "submit",
                        "area": "workflow",
                    },
                    requested_side_effect=0,
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="http.state_transition.v1",
                )
            )
        self.assertEqual(ctx.exception.reason_code, "RISK_UNDERSTATEMENT")

    def test_requested_risk_above_maximum_rejects(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(requested_side_effect=2))
        self.assertEqual(ctx.exception.reason_code, "RISK_EXCEEDS_CAPABILITY")

    def test_wrong_target_type_rejects(self) -> None:
        registry = load_capability_registry()
        echo = registry.get("diagnostic.echo")
        assert echo is not None
        action = echo.actions["echo"]
        document = {
            "capability_id": "diagnostic.typed",
            "version": "1",
            "implementation_reference": "diagnostic.typed",
            "executor_class": "WORKER",
            "actions": {
                "echo": {
                    "action_id": "echo",
                    "argument_schema": dict(action.argument_schema),
                    "result_schema": dict(action.result_schema),
                    "minimum_side_effect_level": 0,
                    "maximum_side_effect_level": 0,
                    "target_types": ["http_origin"],
                    "network_policy": None,
                    "requirements": [],
                    "supports_reproduction": True,
                    "supports_negative_control": False,
                    "normalizer_reference": None,
                }
            },
        }
        typed = registry_from_documents([document])
        with self.assertRaises(ExperimentCompileError) as ctx:
            compile_experiment_intent(_intent(capability_id="diagnostic.typed"), registry=typed)
        self.assertEqual(ctx.exception.reason_code, "WRONG_TARGET_TYPE")

    def test_unsupported_requirement_rejects_at_registry_construction(self) -> None:
        document = {
            "capability_id": "diagnostic.dns",
            "version": "1",
            "implementation_reference": "diagnostic.dns",
            "executor_class": "WORKER",
            "actions": {
                "echo": {
                    "action_id": "echo",
                    "argument_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    "result_schema": {"type": "object"},
                    "minimum_side_effect_level": 0,
                    "maximum_side_effect_level": 0,
                    "target_types": ["opaque_reference"],
                    "network_policy": None,
                    "requirements": ["dns"],
                    "supports_reproduction": False,
                    "supports_negative_control": False,
                    "normalizer_reference": None,
                }
            },
        }
        from zest.tools.registry import CapabilityRegistryError

        with self.assertRaises(CapabilityRegistryError):
            registry_from_documents([document])

    def test_unknown_capability_cannot_become_empty_level_0_plan(self) -> None:
        proposal = parse_hypothesis_proposal(
            {
                "proposed_claim": "scanner finds bugs",
                "rationale": "claim",
                "source_references": ["proc:research-question"],
                "suggested_disconfirming_test": "mismatch",
                "suggested_capability": "nuclei.scan",
            }
        )
        challenge = parse_hypothesis_challenge(
            {
                "alternative_explanations": ["none"],
                "proposed_disconfirming_observation": "no result",
            }
        )
        with self.assertRaises(ResearchInputError):
            plan_admitted_hypothesis(
                "hyp-bad",
                proposal,
                challenge,
                budget_id="budget-1",
                target_reference="target-1",
            )

    def test_specialized_planner_is_compiler_backed(self) -> None:
        plan = plan_diagnostic_echo(
            "hyp-1", budget_id="budget-1", target_reference="target-1", message="ping"
        )
        echo = load_capability_registry().get("diagnostic.echo")
        assert echo is not None
        self.assertEqual(plan.capability_version, echo.version)
        self.assertEqual(plan.capability_definition_fingerprint, echo.definition_fingerprint)
        self.assertEqual(plan.side_effect_level, 0)

    def test_strix_and_codex_cannot_compile_to_worker_plan(self) -> None:
        for capability_id, action in (
            ("strix.diagnostic.ping", "ping"),
            ("codex.diagnostic.structured_output", "emit"),
        ):
            with self.subTest(capability_id=capability_id):
                with self.assertRaises(ExperimentCompileError) as ctx:
                    compile_experiment_intent(
                        _intent(capability_id=capability_id, action=action, arguments={})
                    )
                self.assertEqual(ctx.exception.reason_code, "UNKNOWN_CAPABILITY")
        proposal = parse_hypothesis_proposal(
            {
                "proposed_claim": "strix finds bugs",
                "rationale": "claim",
                "source_references": ["proc:research-question"],
                "suggested_disconfirming_test": "mismatch",
                "suggested_capability": "strix.diagnostic.ping",
            }
        )
        challenge = parse_hypothesis_challenge(
            {
                "alternative_explanations": ["none"],
                "proposed_disconfirming_observation": "no result",
            }
        )
        with self.assertRaises(ResearchInputError):
            plan_admitted_hypothesis(
                "hyp-strix",
                proposal,
                challenge,
                budget_id="budget-1",
                target_reference="target-1",
            )


if __name__ == "__main__":
    unittest.main()
