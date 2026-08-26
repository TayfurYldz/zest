from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.research.compiler import ExperimentCompileError, compile_experiment_intent
from zest.research.http_transaction import (
    HttpRequestTemplate,
    baseline_http_transaction,
    control_http_transaction,
    plan_http_transaction_mutate,
    plan_http_transaction_read,
    replay_http_transaction_plan,
    variant_http_transaction,
)
from zest.research.selection import (
    DiscriminationLevel,
    ExperimentOption,
    ExperimentPurpose,
    plan_from_option,
)
from zest.research.types import ResearchInputError
from zest.tools.http_transaction_policy import validate_http_transaction_arguments
from zest.tools.registry import load_capability_registry


ORIGIN = "http://127.0.0.1:9"


def _read_plan(**overrides):
    values = dict(
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        authorized_origin=ORIGIN,
        path="/ok",
        method="GET",
    )
    values.update(overrides)
    return plan_http_transaction_read(**values)


class HttpTransactionPolicyTests(unittest.TestCase):
    def test_absolute_url_path_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {"authorized_origin": ORIGIN, "method": "GET", "path": "http://evil.example/x"},
        )
        self.assertIsNotNone(issue)

    def test_protocol_relative_path_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {"authorized_origin": ORIGIN, "method": "GET", "path": "//evil.example/x"},
        )
        self.assertIsNotNone(issue)

    def test_encoded_slash_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {"authorized_origin": ORIGIN, "method": "GET", "path": "/ok%2fsecret"},
        )
        self.assertIsNotNone(issue)

    def test_dot_segments_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {"authorized_origin": ORIGIN, "method": "GET", "path": "/ok/../secret"},
        )
        self.assertIsNotNone(issue)

    def test_crlf_header_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {
                "authorized_origin": ORIGIN,
                "method": "GET",
                "path": "/ok",
                "headers": {"Accept": "text/plain\r\nHost: evil"},
            },
        )
        self.assertIsNotNone(issue)

    def test_host_override_denied(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {
                "authorized_origin": ORIGIN,
                "method": "GET",
                "path": "/ok",
                "headers": {"Host": "evil.example"},
            },
        )
        self.assertIsNotNone(issue)

    def test_cookie_and_authorization_denied(self) -> None:
        for name in ("Cookie", "Authorization"):
            issue = validate_http_transaction_arguments(
                "read",
                {
                    "authorized_origin": ORIGIN,
                    "method": "GET",
                    "path": "/ok",
                    "headers": {name: "secret"},
                },
            )
            self.assertIsNotNone(issue)

    def test_session_reference_is_opaque_id_not_credential(self) -> None:
        issue = validate_http_transaction_arguments(
            "read",
            {
                "authorized_origin": ORIGIN,
                "method": "GET",
                "path": "/ok",
                "session_context_reference": "session-1",
            },
        )
        self.assertIsNone(issue)


class HttpTransactionCompilerTests(unittest.TestCase):
    def test_authorized_read_plan_binds_fingerprint(self) -> None:
        plan = _read_plan()
        registry = load_capability_registry()
        capability = registry.get("http.transaction")
        assert capability is not None
        self.assertEqual(plan.required_capability, "http.transaction")
        self.assertEqual(plan.action, "read")
        self.assertEqual(plan.side_effect_level, 0)
        self.assertEqual(plan.capability_version, capability.version)
        self.assertEqual(plan.capability_definition_fingerprint, capability.definition_fingerprint)

    def test_mutate_plan_is_level_one(self) -> None:
        plan = plan_http_transaction_mutate(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=ORIGIN,
            path="/ok",
            method="POST",
            body="{}",
            content_type="application/json",
        )
        self.assertEqual(plan.action, "mutate")
        self.assertEqual(plan.side_effect_level, 1)

    def test_unknown_method_denied(self) -> None:
        with self.assertRaises(ExperimentCompileError) as ctx:
            _compile_bad_method()
        self.assertEqual(ctx.exception.reason_code, "INVALID_ARGUMENT_TYPE")

    def test_get_on_mutate_action_denied(self) -> None:
        from zest.research.compiler import ExperimentIntent
        from zest.research.http_transaction import HTTP_TRANSACTION_EVALUATION_STRATEGY

        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="http.transaction",
                    action="mutate",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": ORIGIN,
                        "method": "GET",
                        "path": "/ok",
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy=HTTP_TRANSACTION_EVALUATION_STRATEGY,
                )
            )

    def test_post_on_read_action_denied(self) -> None:
        from zest.research.compiler import ExperimentIntent
        from zest.research.http_transaction import HTTP_TRANSACTION_EVALUATION_STRATEGY

        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="http.transaction",
                    action="read",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": ORIGIN,
                        "method": "POST",
                        "path": "/ok",
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy=HTTP_TRANSACTION_EVALUATION_STRATEGY,
                )
            )

    def test_oversized_header_denied(self) -> None:
        with self.assertRaises(ExperimentCompileError):
            _read_plan(headers={"Accept": "a" * 200})

    def test_oversized_body_denied(self) -> None:
        with self.assertRaises(ExperimentCompileError):
            plan_http_transaction_mutate(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                authorized_origin=ORIGIN,
                path="/ok",
                method="POST",
                body="x" * 5000,
            )

    def test_replay_retains_binding(self) -> None:
        plan = _read_plan()
        replayed = replay_http_transaction_plan(plan)
        self.assertEqual(replayed.capability_definition_fingerprint, plan.capability_definition_fingerprint)
        self.assertEqual(replayed.capability_version, plan.capability_version)
        self.assertEqual(replayed.target_reference, plan.target_reference)
        self.assertEqual(replayed.arguments, plan.arguments)

    def test_replay_fingerprint_drift_denied(self) -> None:
        plan = _read_plan()
        object.__setattr__(plan, "capability_definition_fingerprint", "a" * 64)
        with self.assertRaises(ResearchInputError):
            replay_http_transaction_plan(plan)

    def test_baseline_control_variant_are_distinct_plans(self) -> None:
        template = HttpRequestTemplate(authorized_origin=ORIGIN, method="GET", path="/ok")
        baseline = baseline_http_transaction(
            "hyp-1", budget_id="budget-1", target_reference="target-1", template=template
        )
        control = control_http_transaction(
            "hyp-1", budget_id="budget-1", target_reference="target-1", template=template
        )
        variant = variant_http_transaction(
            "hyp-1", budget_id="budget-1", target_reference="target-1", template=template
        )
        self.assertNotEqual(baseline.expected_observation, control.expected_observation)
        self.assertNotEqual(control.expected_observation, variant.expected_observation)
        self.assertEqual(baseline.arguments, control.arguments)

    def test_plan_from_option_does_not_auto_select_two_action_capability(self) -> None:
        option = ExperimentOption(
            option_id="opt-http",
            hypothesis_id="hyp-1",
            hypothesis_ids=("hyp-1",),
            purpose=ExperimentPurpose.OBJECT_CROSS_PROBE,
            required_capability="http.transaction",
            requested_observation="http facts",
            expected_supporting_observation="observed",
            expected_disconfirming_observation="missing",
            required_negative_control="none",
            unresolved_facts=("status_code",),
            estimated_request_cost=1,
            side_effect_level=0,
            can_falsify_live=False,
            distinguishes_competing_count=0,
            resolves_missing_fact=True,
            provides_missing_negative_control=False,
            authorized_origin=ORIGIN,
            target_reference="target-1",
            in_authorized_origin=True,
            context_signature="ctx-http",
            structural_identity="id-http",
            plan_arguments={
                "authorized_origin": ORIGIN,
                "method": "GET",
                "path": "/ok",
            },
            discrimination=DiscriminationLevel.LOW_DISCRIMINATION,
        )
        with self.assertRaises(ResearchInputError):
            plan_from_option(option, budget_id="budget-1")


def _compile_bad_method():
    from zest.research.compiler import ExperimentIntent
    from zest.research.http_transaction import HTTP_TRANSACTION_EVALUATION_STRATEGY

    return compile_experiment_intent(
        ExperimentIntent(
            hypothesis_id="hyp-1",
            capability_id="http.transaction",
            action="read",
            target_reference="target-1",
            arguments={"authorized_origin": ORIGIN, "method": "TRACE", "path": "/ok"},
            requested_budget_id="budget-1",
            expected_observation="x",
            disconfirming_observation="y",
            evaluation_strategy=HTTP_TRANSACTION_EVALUATION_STRATEGY,
        )
    )


if __name__ == "__main__":
    unittest.main()
