"""Runtime routing policy tests.

Preserves all pre-SD-G4 routing behavior guarantees and adds SD-G4 price-class
policy tests. No historical test function is removed; new tests are appended.
"""

from __future__ import annotations

import json
import unittest

import pathsetup  # noqa: F401

from zest.research.model_port import ModelRole
from zest.research.model_runtime import (
    AuthMode,
    ModelPriceClass,
    ModelRuntimeIdentity,
    RuntimeClass,
    RuntimeKind,
    RuntimeOutcome,
    api_runtime_identity,
    cli_session_runtime_identity,
)
from zest.research.routing import (
    ROUTING_POLICY_VERSION,
    CandidateLocality,
    LocalityConstraint,
    RoutingBudget,
    RoutingOutcome,
    RoutingReasonCode,
    RoutingRequest,
    RuntimeCandidate,
    RuntimeQualityObservation,
    RuntimeSelectionDecision,
    reconsider_runtime,
    select_runtime,
)
from zest.research.types import ResearchInputError


# --- Pre-SD-G4 behavior helpers ------------------------------------------------


def _api(adapter_id: str, *, available: bool = True, **kwargs) -> RuntimeCandidate:
    values = dict(
        identity=api_runtime_identity(adapter_id=adapter_id, runtime_id=adapter_id),
        available=available,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.REMOTE,
    )
    values.update(kwargs)
    return RuntimeCandidate(**values)


def _agent(adapter_id: str = "codex.cli.session", **kwargs) -> RuntimeCandidate:
    values = dict(
        identity=cli_session_runtime_identity(adapter_id=adapter_id, runtime_id="codex-cli"),
        available=True,
        authenticated=True,
        structured_output_compatible=True,
        allowed_capabilities=("codex.diagnostic.structured_output",),
        locality=CandidateLocality.REMOTE,
    )
    values.update(kwargs)
    return RuntimeCandidate(**values)


def _request(*candidates: RuntimeCandidate, **kwargs) -> RoutingRequest:
    values = dict(
        role=ModelRole.GENERATOR,
        candidates=candidates,
        budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=1),
    )
    values.update(kwargs)
    return RoutingRequest(**values)


# --- SD-G4 price-class helpers -------------------------------------------------


def _price_identity(
    adapter_id: str,
    *,
    price_class: ModelPriceClass = ModelPriceClass.CHEAP,
) -> ModelRuntimeIdentity:
    return ModelRuntimeIdentity(
        runtime_kind=RuntimeKind.API,
        runtime_class=RuntimeClass.INFERENCE_RUNTIME,
        adapter_id=adapter_id,
        runtime_id=adapter_id,
        auth_mode=AuthMode.API_KEY,
        configuration_fingerprint="fp",
        price_class=price_class,
    )


def _price_candidate(
    adapter_id: str,
    *,
    price_class: ModelPriceClass = ModelPriceClass.CHEAP,
    available: bool = True,
) -> RuntimeCandidate:
    return RuntimeCandidate(
        identity=_price_identity(adapter_id, price_class=price_class),
        available=available,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.REMOTE,
    )


def _price_request(
    *,
    task_class: str | None = None,
    escalation_reason: str | None = None,
    candidates: tuple[RuntimeCandidate, ...] = (),
) -> RoutingRequest:
    return RoutingRequest(
        role=ModelRole.GENERATOR,
        candidates=candidates,
        budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=1),
        task_class=task_class,
        escalation_reason=escalation_reason,
    )


# --- Pre-SD-G4 behavior tests (15 tests) ---------------------------------------


class RuntimeRoutingTests(unittest.TestCase):
    def test_unavailable_runtime_is_never_selected(self) -> None:
        decision = select_runtime(_request(_api("openai.responses", available=False)))
        self.assertEqual(decision.outcome, RoutingOutcome.UNAVAILABLE)
        self.assertIsNone(decision.selected_identity)

    def test_agent_runtime_rejected_for_inference_role(self) -> None:
        decision = select_runtime(_request(_agent(), _api("openai.responses")))
        self.assertTrue(decision.selected)
        assert decision.selected_identity is not None
        self.assertEqual(decision.selected_identity.adapter_id, "openai.responses")
        self.assertEqual(decision.selected_identity.runtime_class, RuntimeClass.INFERENCE_RUNTIME)
        self.assertIn("AGENT_NOT_PERMITTED_FOR_INFERENCE_ROLE", json.dumps(decision.to_mapping()))

    def test_unrestricted_agent_capability_is_rejected(self) -> None:
        decision = select_runtime(
            _request(
                _agent(allowed_capabilities=("*",)),
                required_runtime_class=RuntimeClass.AGENT_RUNTIME,
                allow_agent_runtime=True,
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.NO_COMPATIBLE_RUNTIME)

    def test_generator_and_falsifier_may_select_differently(self) -> None:
        cheap_unsafe = _api(
            "fast.remote",
            quality=RuntimeQualityObservation(grounding_safety_hard_failures=4, latency_ms=10),
        )
        careful = _api(
            "careful.remote",
            quality=RuntimeQualityObservation(grounding_safety_hard_failures=0, latency_ms=5_000),
        )
        generator = select_runtime(
            _request(cheap_unsafe, careful, role=ModelRole.GENERATOR, require_operator_on_tie=False)
        )
        falsifier = select_runtime(
            _request(
                cheap_unsafe,
                careful,
                role=ModelRole.FALSIFIER,
                operator_preference_order=("fast.remote",),
            )
        )
        self.assertEqual(generator.selected_identity.adapter_id, "careful.remote")
        self.assertEqual(falsifier.selected_identity.adapter_id, "fast.remote")
        self.assertNotEqual(generator.role, falsifier.role)

    def test_hard_safety_failure_beats_cheap_fast_preference(self) -> None:
        cheap = _api(
            "cheap.fast",
            quality=RuntimeQualityObservation(grounding_safety_hard_failures=3, latency_ms=5, cost_amount=0.01),
        )
        safer = _api(
            "safer.slow",
            quality=RuntimeQualityObservation(grounding_safety_hard_failures=0, latency_ms=9_000, cost_amount=2.0),
        )
        decision = select_runtime(_request(cheap, safer, require_operator_on_tie=False))
        self.assertEqual(decision.selected_identity.adapter_id, "safer.slow")
        mapping = decision.to_mapping()
        self.assertTrue(mapping["no_aggregate_model_score"])
        self.assertNotIn("weighted_score", mapping)
        self.assertNotIn("model_score", mapping)

    def test_zero_fallback_means_none(self) -> None:
        first = select_runtime(
            _request(
                _api("openai.responses"),
                _api("anthropic.messages"),
                budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=0),
                operator_preference_order=("openai.responses", "anthropic.messages"),
            )
        )
        self.assertTrue(first.selected)
        second = reconsider_runtime(
            _request(
                _api("openai.responses"),
                _api("anthropic.messages"),
                budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=0),
                operator_preference_order=("openai.responses", "anthropic.messages"),
            ),
            first,
            RuntimeOutcome.UNAVAILABLE,
        )
        self.assertEqual(second.outcome, RoutingOutcome.UNAVAILABLE)
        self.assertIn("ZERO_FALLBACK_ALLOWANCE", second.reason_codes)

    def test_max_fallback_is_respected(self) -> None:
        request = _request(
            _api("a.runtime"),
            _api("b.runtime"),
            _api("c.runtime"),
            budget=RoutingBudget(max_runtime_attempts=3, max_fallback_attempts=1),
            operator_preference_order=("a.runtime", "b.runtime", "c.runtime"),
        )
        first = select_runtime(request)
        self.assertEqual(first.selected_identity.adapter_id, "a.runtime")
        second = reconsider_runtime(request, first, RuntimeOutcome.UNAVAILABLE)
        self.assertEqual(second.selected_identity.adapter_id, "b.runtime")
        third = reconsider_runtime(request, second, RuntimeOutcome.UNAVAILABLE)
        self.assertEqual(third.outcome, RoutingOutcome.UNAVAILABLE)
        self.assertIn("FALLBACK_EXHAUSTED", third.reason_codes)

    def test_content_policy_block_does_not_hop(self) -> None:
        request = _request(
            _api("blocked.runtime"),
            _api("other.runtime"),
            budget=RoutingBudget(max_runtime_attempts=3, max_fallback_attempts=2),
            operator_preference_order=("blocked.runtime", "other.runtime"),
        )
        first = select_runtime(request)
        second = reconsider_runtime(request, first, RuntimeOutcome.CONTENT_POLICY_BLOCKED)
        self.assertEqual(second.outcome, RoutingOutcome.BLOCKED_POLICY)
        self.assertIsNone(second.selected_identity)
        self.assertIn("CONTENT_POLICY_BLOCK_NO_BYPASS", second.reason_codes)
        third = reconsider_runtime(request, second, RuntimeOutcome.CONTENT_POLICY_BLOCKED)
        self.assertEqual(third.outcome, RoutingOutcome.BLOCKED_POLICY)
        self.assertIsNone(third.selected_identity)

    def test_zero_runtime_allowance_selects_nothing(self) -> None:
        decision = select_runtime(
            _request(_api("openai.responses"), budget=RoutingBudget(0, 0))
        )
        self.assertEqual(decision.outcome, RoutingOutcome.UNAVAILABLE)
        self.assertIn("ZERO_RUNTIME_ALLOWANCE", decision.reason_codes)

    def test_negative_budget_is_invalid(self) -> None:
        with self.assertRaises(ResearchInputError):
            RoutingBudget(max_runtime_attempts=-1, max_fallback_attempts=0)

    def test_strix_is_not_a_model_runtime(self) -> None:
        decision = select_runtime(_request(_api("strix.runtime", is_strix=True)))
        self.assertEqual(decision.outcome, RoutingOutcome.NO_COMPATIBLE_RUNTIME)

    def test_same_observations_are_deterministic(self) -> None:
        candidates = (
            _api("alpha.runtime", quality=RuntimeQualityObservation(grounding_safety_hard_failures=1)),
            _api("beta.runtime", quality=RuntimeQualityObservation(grounding_safety_hard_failures=0)),
        )
        first = select_runtime(_request(*candidates, require_operator_on_tie=False))
        second = select_runtime(_request(*candidates, require_operator_on_tie=False))
        self.assertEqual(first.to_mapping(), second.to_mapping())
        self.assertEqual(first.policy_version, ROUTING_POLICY_VERSION)
        self.assertEqual(first.selected_identity.adapter_id, "beta.runtime")

    def test_local_required_filters_remote(self) -> None:
        local = _api(
            "local.model.contract",
            locality=CandidateLocality.LOCAL,
        )
        remote = _api("openai.responses")
        decision = select_runtime(
            _request(local, remote, locality=LocalityConstraint.LOCAL_REQUIRED)
        )
        self.assertEqual(decision.selected_identity.adapter_id, "local.model.contract")

    def test_select_does_not_claim_winner_or_secrets(self) -> None:
        decision = select_runtime(_request(_api("openai.responses")))
        payload = json.dumps(decision.to_mapping())
        self.assertNotIn("WINNER", payload)
        self.assertNotIn("sk-", payload)
        self.assertNotIn("api_key", payload)

    def test_selected_identity_invalid_on_non_select(self) -> None:
        with self.assertRaises(ResearchInputError):
            RuntimeSelectionDecision(
                outcome=RoutingOutcome.UNAVAILABLE,
                policy_version=ROUTING_POLICY_VERSION,
                role=ModelRole.GENERATOR,
                reason_codes=("UNAVAILABLE",),
                selected_identity=api_runtime_identity(adapter_id="x", runtime_id="x"),
            )


# --- SD-G4 price-class policy tests (5 tests) ----------------------------------


class PriceClassRoutingTests(unittest.TestCase):
    def test_default_selects_cheap(self) -> None:
        decision = select_runtime(
            _price_request(
                candidates=(
                    _price_candidate("cheap-1"),
                    _price_candidate("expensive-1", price_class=ModelPriceClass.EXPENSIVE),
                )
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.SELECT)
        self.assertEqual(decision.selected_identity.adapter_id, "cheap-1")
        self.assertIn(RoutingReasonCode.CHEAP_CLASS_SELECTED.value, decision.reason_codes)

    def test_monitoring_task_class_blocks_all_calls(self) -> None:
        decision = select_runtime(
            _price_request(
                task_class="monitoring",
                candidates=(_price_candidate("cheap-1"), _price_candidate("expensive-1", price_class=ModelPriceClass.EXPENSIVE)),
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.BLOCKED_POLICY)
        self.assertIn(RoutingReasonCode.MONITORING_CLASS_DISABLED.value, decision.reason_codes)
        self.assertIsNone(decision.selected_identity)

    def test_expensive_task_class_selects_expensive(self) -> None:
        decision = select_runtime(
            _price_request(
                task_class="finding_proposal_qa",
                candidates=(
                    _price_candidate("cheap-1"),
                    _price_candidate("expensive-1", price_class=ModelPriceClass.EXPENSIVE),
                ),
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.SELECT)
        self.assertEqual(decision.selected_identity.adapter_id, "expensive-1")
        self.assertIn(RoutingReasonCode.EXPENSIVE_CLASS_SELECTED.value, decision.reason_codes)

    def test_escalation_reason_selects_expensive(self) -> None:
        decision = select_runtime(
            _price_request(
                escalation_reason="cheap_model_returned_escalation_needed",
                candidates=(
                    _price_candidate("cheap-1"),
                    _price_candidate("expensive-1", price_class=ModelPriceClass.EXPENSIVE),
                ),
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.SELECT)
        self.assertEqual(decision.selected_identity.adapter_id, "expensive-1")
        self.assertIn(RoutingReasonCode.ESCALATION_REQUIRED.value, decision.reason_codes)

    def test_expensive_required_but_no_expensive_candidate_blocks(self) -> None:
        decision = select_runtime(
            _price_request(
                task_class="finding_proposal_qa",
                candidates=(_price_candidate("cheap-1"),),
            )
        )
        self.assertEqual(decision.outcome, RoutingOutcome.BLOCKED_POLICY)


if __name__ == "__main__":
    unittest.main()
