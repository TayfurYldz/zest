from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.core.capability import CapabilityAuthorizationView
from zest.core.enums import ExecutionDecisionKind, ReasonCode, SideEffectLevel
from zest.core.execution import evaluate_execution
from zest.tools.registry import load_capability_registry, registry_from_documents
from fixtures import base_request, capability_view_for_side_effect, human_approval


def _view(**overrides) -> CapabilityAuthorizationView:
    registry = load_capability_registry()
    echo = registry.get("diagnostic.echo")
    assert echo is not None
    action = echo.action("echo")
    assert action is not None
    values = dict(
        capability_id=echo.capability_id,
        action="echo",
        capability_version=echo.version,
        definition_fingerprint=echo.definition_fingerprint,
        authoritative_minimum_side_effect=action.minimum_side_effect_level,
        effective_side_effect=action.minimum_side_effect_level,
    )
    values.update(overrides)
    return CapabilityAuthorizationView(**values)


class CapabilityAuthorizationTests(unittest.TestCase):
    def test_missing_view_denies(self) -> None:
        decision = evaluate_execution(base_request(capability=None))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.CAPABILITY_AUTHORIZATION_MISSING)

    def test_unknown_capability_denies_even_at_level_0(self) -> None:
        decision = evaluate_execution(
            base_request(
                capability=_view(capability_id="not.a.capability"),
            )
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.UNKNOWN_CAPABILITY)

    def test_unknown_action_denies(self) -> None:
        decision = evaluate_execution(base_request(capability=_view(action="scan")))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.UNKNOWN_ACTION)

    def test_wrong_version_denies(self) -> None:
        decision = evaluate_execution(base_request(capability=_view(capability_version="999")))
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.UNSUPPORTED_CAPABILITY_VERSION)

    def test_wrong_fingerprint_denies(self) -> None:
        decision = evaluate_execution(
            base_request(capability=_view(definition_fingerprint="a" * 64))
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.DEFINITION_FINGERPRINT_MISMATCH)

    def test_view_minimum_below_registry_denies(self) -> None:
        registry = load_capability_registry()
        state = registry.get("http.state_transition")
        assert state is not None
        view = CapabilityAuthorizationView(
            capability_id=state.capability_id,
            action="probe",
            capability_version=state.version,
            definition_fingerprint=state.definition_fingerprint,
            authoritative_minimum_side_effect=0,
            effective_side_effect=0,
        )
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_0, capability=view)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_UNDERSTATEMENT)

    def test_compiler_bypass_level_0_on_level_1_capability_denies(self) -> None:
        registry = load_capability_registry()
        state = registry.get("http.state_transition")
        assert state is not None
        action = state.action("probe")
        assert action is not None
        view = CapabilityAuthorizationView(
            capability_id=state.capability_id,
            action="probe",
            capability_version=state.version,
            definition_fingerprint=state.definition_fingerprint,
            authoritative_minimum_side_effect=action.minimum_side_effect_level,
            effective_side_effect=0,
        )
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_0, capability=view)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_UNDERSTATEMENT)

    def test_request_side_effect_must_match_view_effective(self) -> None:
        view = capability_view_for_side_effect(0)
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_1, capability=view)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.SIDE_EFFECT_BINDING_MISMATCH)

    def test_level_3_still_denied(self) -> None:
        decision = evaluate_execution(
            base_request(side_effect_level=SideEffectLevel.LEVEL_3)
        )
        self.assertEqual(decision.decision, ExecutionDecisionKind.DENY)
        self.assertEqual(decision.reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)

    def test_level_2_approval_semantics_when_capability_allows(self) -> None:
        document = {
            "capability_id": "diagnostic.approval_fixture",
            "version": "1",
            "implementation_reference": "diagnostic.approval_fixture",
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
                    "minimum_side_effect_level": 2,
                    "maximum_side_effect_level": 2,
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
        definition = registry.get("diagnostic.approval_fixture")
        assert definition is not None
        view = CapabilityAuthorizationView(
            capability_id=definition.capability_id,
            action="hold",
            capability_version=definition.version,
            definition_fingerprint=definition.definition_fingerprint,
            authoritative_minimum_side_effect=2,
            effective_side_effect=2,
        )
        denied = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_2,
                capability=view,
                approval=None,
            ),
            capability_registry=registry,
        )
        self.assertEqual(denied.decision, ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW)
        allowed = evaluate_execution(
            base_request(
                side_effect_level=SideEffectLevel.LEVEL_2,
                capability=view,
                approval=human_approval(),
            ),
            capability_registry=registry,
        )
        self.assertEqual(allowed.decision, ExecutionDecisionKind.ALLOW)

    def test_level_3_denied_when_capability_allows_that_level(self) -> None:
        document = {
            "capability_id": "diagnostic.destructive_fixture",
            "version": "1",
            "implementation_reference": "diagnostic.destructive_fixture",
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
                    "minimum_side_effect_level": 3,
                    "maximum_side_effect_level": 3,
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
        definition = registry.get("diagnostic.destructive_fixture")
        assert definition is not None
        view = CapabilityAuthorizationView(
            capability_id=definition.capability_id,
            action="hold",
            capability_version=definition.version,
            definition_fingerprint=definition.definition_fingerprint,
            authoritative_minimum_side_effect=3,
            effective_side_effect=3,
        )
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


if __name__ == "__main__":
    unittest.main()
