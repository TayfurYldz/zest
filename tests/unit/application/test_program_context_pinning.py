from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta

import pathsetup  # noqa: F401

from application.test_execute_planned_experiment import (
    MutableClock,
    _command,
    _use_case,
)
from application.test_http_transaction import (
    _allow_scope,
    _plan as _http_plan,
)
from zest.application.execute_planned_experiment import (
    AuthorizedDispatch,
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.orchestration_config import (
    EffectiveOrchestrationConfiguration,
    assert_command_matches_configuration,
    compiled_scope_fingerprint,
    program_policy_fingerprint,
)
from zest.application.program_research_context import (
    ProgramPolicyView,
)
from zest.application.errors import (
    OrchestrationIntegrityError,
)
from zest.core.enums import (
    ReasonCode,
    ScopeRuleEffect,
)
from zest.core.scope_compiler import (
    ScopeRuleDefinition,
    compile_scope_rules,
)
from zest.data.records import (
    ProgramPolicyRecord,
    ScopeRuleV2Record,
)
from zest.research.orchestration import (
    OrchestrationBounds,
)
from support.fake_unit_of_work import (
    FakeUnitOfWorkFactory,
    _Store,
)
from support.recording_worker import (
    RecordingWorkerPort,
)
from support.spine import (
    CREATED_AT,
    seed_spine,
)


ORIGIN = "http://127.0.0.1:9"


def _policy_view(
    *,
    forbidden_actions=(),
):
    return ProgramPolicyView(
        loopback_fixture=False,
        max_response_bytes=4096,
        timeout_ms=2000,
        action_policy={
            "forbidden_actions": (
                forbidden_actions
            ),
        },
    )


def _policy_record(
    *,
    forbidden_actions=(),
):
    return ProgramPolicyRecord(
        program_id="prog-1",
        loopback_fixture=False,
        max_response_bytes=4096,
        timeout_ms=2000,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        action_policy={
            "forbidden_actions": (
                list(forbidden_actions)
            ),
        },
    )


def _compiled(
    *,
    expires_at=None,
    include_added=False,
):
    rules = [
        ScopeRuleDefinition(
            rule_id="rule-allow",
            effect=ScopeRuleEffect.ALLOW,
            scheme="http",
            host="127.0.0.1",
            port=9,
            path_prefix="/ok",
            source_reference="scope-src",
            expires_at=expires_at,
        )
    ]

    if include_added:
        rules.append(
            ScopeRuleDefinition(
                rule_id="rule-added",
                effect=ScopeRuleEffect.ALLOW,
                scheme="https",
                host="added.example",
                path_prefix=None,
                source_reference=(
                    "scope-added"
                ),
            )
        )

    return compile_scope_rules(
        tuple(rules)
    )


def _scope_record(
    *,
    expires_at=None,
):
    return ScopeRuleV2Record(
        rule_id="rule-allow",
        program_id="prog-1",
        effect="ALLOW",
        scheme="http",
        host="127.0.0.1",
        host_pattern=None,
        port=9,
        path_prefix="/ok",
        source_reference="scope-src",
        expires_at=expires_at,
        created_at=CREATED_AT,
    )


def _http_command(
    compiled,
):
    return ExecutePlannedExperimentCommand(
        experiment_id="exp-1",
        plan=_http_plan(
            ORIGIN,
            path="/ok",
        ),
        scope=_allow_scope(),
        compiled_scope=compiled,
    )


class ProgramContextPinningTests(
    unittest.TestCase
):
    def test_policy_authorize_then_deny_is_blocked_at_dispatch(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)

        store.program_policies[
            "prog-1"
        ] = _policy_record()

        use_case, _, worker = _use_case(
            store
        )

        authorized = use_case.authorize(
            _command(
                program_policy=_policy_view(),
            )
        )

        self.assertIsInstance(
            authorized,
            AuthorizedDispatch,
        )
        assert isinstance(
            authorized,
            AuthorizedDispatch,
        )

        store.program_policies[
            "prog-1"
        ] = replace(
            store.program_policies[
                "prog-1"
            ],
            updated_at=(
                CREATED_AT
                + timedelta(seconds=1)
            ),
            action_policy={
                "forbidden_actions": [
                    "echo"
                ],
            },
        )

        outcome = use_case.dispatch(
            authorized
        )

        self.assertEqual(
            outcome.status,
            ResearchLoopStatus.DISPATCH_DENIED,
        )
        self.assertEqual(
            outcome.core_reason_code,
            ReasonCode.PROGRAM_POLICY_DENIED,
        )
        self.assertEqual(
            len(worker.calls),
            0,
        )

    def test_scope_expansion_after_authorization_is_blocked(
        self,
    ) -> None:
        store = _Store()
        seed_spine(store)

        store.scope_rules_v2[
            "rule-allow"
        ] = _scope_record()

        factory = FakeUnitOfWorkFactory(
            store
        )
        worker = RecordingWorkerPort(
            store=store
        )
        clock = MutableClock(
            CREATED_AT
        )

        use_case = ExecutePlannedExperiment(
            factory,
            worker,
            clock=clock,
        )

        authorized = use_case.authorize(
            _http_command(
                _compiled()
            )
        )

        self.assertIsInstance(
            authorized,
            AuthorizedDispatch,
        )
        assert isinstance(
            authorized,
            AuthorizedDispatch,
        )

        store.scope_rules_v2[
            "rule-added"
        ] = ScopeRuleV2Record(
            rule_id="rule-added",
            program_id="prog-1",
            effect="ALLOW",
            scheme="https",
            host="added.example",
            host_pattern=None,
            port=None,
            path_prefix=None,
            source_reference="scope-added",
            expires_at=None,
            created_at=CREATED_AT,
        )

        outcome = use_case.dispatch(
            authorized
        )

        self.assertEqual(
            outcome.status,
            ResearchLoopStatus.DISPATCH_DENIED,
        )
        self.assertEqual(
            outcome.core_reason_code,
            ReasonCode
            .SCOPE_NOT_EXPLICITLY_ALLOWED,
        )
        self.assertEqual(
            len(worker.calls),
            0,
        )

    def test_scope_expiry_is_rechecked_at_dispatch(
        self,
    ) -> None:
        expires_at = (
            CREATED_AT
            + timedelta(seconds=1)
        )

        store = _Store()
        seed_spine(store)

        store.scope_rules_v2[
            "rule-allow"
        ] = _scope_record(
            expires_at=expires_at
        )

        factory = FakeUnitOfWorkFactory(
            store
        )
        worker = RecordingWorkerPort(
            store=store
        )
        clock = MutableClock(
            CREATED_AT
        )

        use_case = ExecutePlannedExperiment(
            factory,
            worker,
            clock=clock,
        )

        authorized = use_case.authorize(
            _http_command(
                _compiled(
                    expires_at=expires_at
                )
            )
        )

        self.assertIsInstance(
            authorized,
            AuthorizedDispatch,
        )
        assert isinstance(
            authorized,
            AuthorizedDispatch,
        )

        clock.current = (
            CREATED_AT
            + timedelta(seconds=2)
        )

        outcome = use_case.dispatch(
            authorized
        )

        self.assertEqual(
            outcome.status,
            ResearchLoopStatus.DISPATCH_DENIED,
        )
        self.assertEqual(
            outcome.core_reason_code,
            ReasonCode.SCOPE_EXPIRED,
        )
        self.assertEqual(
            len(worker.calls),
            0,
        )

    def test_full_scope_fingerprint_changes_on_unrelated_allow_rule(
        self,
    ) -> None:
        original = _compiled()
        expanded = _compiled(
            include_added=True
        )

        self.assertNotEqual(
            compiled_scope_fingerprint(
                original
            ),
            compiled_scope_fingerprint(
                expanded
            ),
        )

    def test_program_policy_fingerprint_changes_on_action_deny(
        self,
    ) -> None:
        self.assertNotEqual(
            program_policy_fingerprint(
                _policy_view()
            ),
            program_policy_fingerprint(
                _policy_view(
                    forbidden_actions=(
                        "echo",
                    )
                )
            ),
        )

    def test_persisted_config_rejects_scope_and_policy_revision_change(
        self,
    ) -> None:
        bounds = OrchestrationBounds(
            max_cycles=1,
            max_experiments=1,
            max_model_calls=1,
            max_worker_invocations=1,
            max_elapsed_ms=1000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
        )

        original_scope = _compiled()
        original_policy = (
            _policy_view()
        )

        config = (
            EffectiveOrchestrationConfiguration(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference="target-1",
                research_question="question",
                policy_version="policy-v1",
                routing_policy_version=None,
                scope_fingerprint=None,
                bounds=bounds,
                fingerprint="f" * 64,
                compiled_scope_fingerprint=(
                    compiled_scope_fingerprint(
                        original_scope
                    )
                ),
                program_policy_fingerprint=(
                    program_policy_fingerprint(
                        original_policy
                    )
                ),
            )
        )

        with self.assertRaises(
            OrchestrationIntegrityError
        ):
            assert_command_matches_configuration(
                config=config,
                bounds=bounds,
                budget_id="budget-1",
                target_reference="target-1",
                research_question="question",
                scope=None,
                compiled_scope=_compiled(
                    include_added=True
                ),
                program_policy=original_policy,
            )

        with self.assertRaises(
            OrchestrationIntegrityError
        ):
            assert_command_matches_configuration(
                config=config,
                bounds=bounds,
                budget_id="budget-1",
                target_reference="target-1",
                research_question="question",
                scope=None,
                compiled_scope=original_scope,
                program_policy=_policy_view(
                    forbidden_actions=(
                        "echo",
                    )
                ),
            )


if __name__ == "__main__":
    unittest.main()
