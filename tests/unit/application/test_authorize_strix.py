from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.authorize_strix_execution import (
    AuthorizeStrixExecution,
    AuthorizeStrixExecutionCommand,
)
from zest.integrations.strix.adapter import (
    StrixDiagnosticAdapter,
    probe_strix_runtime,
)
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.core.enums import ExecutionDecisionKind, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.platform.strix import (
    StrixExecutionOutcome,
    StrixExecutionRequest,
    StrixRuntimeStatus,
)
from zest.research.admission import AdmissionOutcome
from zest.research.model_port import ContentPolicyBlockedError
from zest.tools.capabilities import STRIX_DIAGNOSTIC_PING_CAPABILITY
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


class RecordingStrix:
    def __init__(self, outcome: StrixExecutionOutcome | None = None) -> None:
        self.calls: list[StrixExecutionRequest] = []
        self._outcome = outcome

    def execute(self, request: StrixExecutionRequest) -> StrixExecutionOutcome:
        self.calls.append(request)
        if self._outcome is not None:
            return self._outcome
        return StrixExecutionOutcome(
            status=StrixRuntimeStatus.COMPLETED,
            untrusted=True,
            capability=request.capability,
            reason_codes=("STRIX_DIAGNOSTIC_PING",),
            payload={"not_observation": True, "not_evidence": True},
        )


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _command(**overrides) -> AuthorizeStrixExecutionCommand:
    values = dict(
        research_run_id="run-1",
        experiment_id="exp-1",
        capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
        target_reference="target-1",
        budget_id="budget-1",
        side_effect_level=0,
        scope=_allow_scope(),
        allowed_capabilities=(STRIX_DIAGNOSTIC_PING_CAPABILITY,),
    )
    values.update(overrides)
    return AuthorizeStrixExecutionCommand(**values)


class AuthorizeStrixTests(unittest.TestCase):
    def test_denied_request_never_reaches_strix(self) -> None:
        store = _Store()
        seed_spine(store, authorization_state="REVOKED")
        strix = RecordingStrix()
        result = AuthorizeStrixExecution(
            FakeUnitOfWorkFactory(store), strix, clock=FixedClock()
        ).execute(_command())
        self.assertEqual(result.core_decision, ExecutionDecisionKind.DENY)
        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        self.assertEqual(store.observations, {})
        self.assertEqual(store.evidence, {})

    def test_unrestricted_capability_is_rejected_before_strix(self) -> None:
        store = _Store()
        seed_spine(store)
        strix = RecordingStrix()
        result = AuthorizeStrixExecution(
            FakeUnitOfWorkFactory(store), strix, clock=FixedClock()
        ).execute(_command(allowed_capabilities=("*",)))
        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        self.assertEqual(result.core_reason_code, "UNRESTRICTED_CAPABILITY")

    def test_redirect_requires_core_reevaluation(self) -> None:
        store = _Store()
        seed_spine(store)
        strix = RecordingStrix()
        result = AuthorizeStrixExecution(
            FakeUnitOfWorkFactory(store), strix, clock=FixedClock()
        ).execute(_command(redirect_or_new_asset=True))
        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        assert result.outcome is not None
        self.assertEqual(result.outcome.status, StrixRuntimeStatus.SCOPE_RECHECK_REQUIRED)

    def test_strix_request_requires_authorization_decision_reference(self) -> None:
        with self.assertRaises(ValueError):
            StrixExecutionRequest(
                research_run_id="run-1",
                experiment_id="exp-1",
                correlation_id="corr-1",
                request_id="req-1",
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                authorized_target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                authorization_decision_reference="",
                allowed_capabilities=(STRIX_DIAGNOSTIC_PING_CAPABILITY,),
            )

    def test_disabled_runtime_never_reaches_strix(self) -> None:
        store = _Store()
        seed_spine(store)
        strix = RecordingStrix()

        result = AuthorizeStrixExecution(
            FakeUnitOfWorkFactory(store),
            strix,
            clock=FixedClock(),
        ).execute(_command())

        self.assertEqual(
            result.core_decision,
            ExecutionDecisionKind.DENY,
        )
        self.assertEqual(
            result.core_reason_code,
            "STRIX_RUNTIME_DISABLED",
        )
        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        assert result.outcome is not None
        self.assertEqual(
            result.outcome.status,
            StrixRuntimeStatus.DENIED,
        )
        self.assertEqual(store.observations, {})
        self.assertEqual(store.evidence, {})

    def test_disabled_runtime_ignores_integration_outcome(self) -> None:
        store = _Store()
        seed_spine(store)
        strix = RecordingStrix(
            StrixExecutionOutcome(
                status=StrixRuntimeStatus.UNAVAILABLE,
                untrusted=True,
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                reason_codes=("SHOULD_NOT_BE_REACHED",),
                payload={"not_observation": True},
            )
        )

        result = AuthorizeStrixExecution(
            FakeUnitOfWorkFactory(store),
            strix,
            clock=FixedClock(),
        ).execute(_command())

        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        self.assertEqual(
            result.core_reason_code,
            "STRIX_RUNTIME_DISABLED",
        )
        self.assertEqual(store.observations, {})
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.findings, {})

    def test_disabled_probe_and_adapter_suppress_process_contact(self) -> None:
        def forbidden_runtime_contact(*_args, **_kwargs):
            raise AssertionError("disabled Strix touched process runtime")

        probe_globals = probe_strix_runtime.__globals__
        old_resolve = probe_globals["resolve_executable"]
        old_run = probe_globals["run_argv"]

        adapter_globals = StrixDiagnosticAdapter.execute.__globals__
        old_adapter_resolve = adapter_globals["resolve_executable"]
        old_adapter_run = adapter_globals["run_argv"]

        probe_globals["resolve_executable"] = forbidden_runtime_contact
        probe_globals["run_argv"] = forbidden_runtime_contact
        adapter_globals["resolve_executable"] = forbidden_runtime_contact
        adapter_globals["run_argv"] = forbidden_runtime_contact

        try:
            probe = probe_strix_runtime()

            request = StrixExecutionRequest(
                research_run_id="run-1",
                experiment_id="exp-1",
                correlation_id="corr-disabled",
                request_id="req-disabled",
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                authorized_target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                authorization_decision_reference="ae-disabled",
                allowed_capabilities=(
                    STRIX_DIAGNOSTIC_PING_CAPABILITY,
                ),
            )

            adapter = StrixDiagnosticAdapter(
                executable="/must-not-execute",
            )
            outcome = adapter.execute(request)
        finally:
            probe_globals["resolve_executable"] = old_resolve
            probe_globals["run_argv"] = old_run
            adapter_globals["resolve_executable"] = old_adapter_resolve
            adapter_globals["run_argv"] = old_adapter_run

        self.assertFalse(probe["available"])
        self.assertTrue(probe["disabled"])
        self.assertTrue(probe["probe_suppressed"])
        self.assertEqual(
            outcome.status,
            StrixRuntimeStatus.DENIED,
        )
        self.assertEqual(adapter.calls, [])

    def test_content_policy_block_does_not_alter_hypothesis_truth(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        result = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store),
            ScriptedModelPort(error=ContentPolicyBlockedError("safety refusal")),
            clock=FixedClock(),
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="Does the diagnostic capability return the submitted value?",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-policy-1",
            )
        )
        self.assertEqual(result.outcome, AdmissionOutcome.MODEL_INVOCATION_FAILED)
        admission = next(iter(store.research_admissions.values()))
        self.assertEqual(admission.reason_code, "CONTENT_POLICY_BLOCKED")
        self.assertEqual(store.hypotheses, {})
        self.assertNotEqual(admission.outcome, "REJECTED_POLICY_CONFLICT")


if __name__ == "__main__":
    unittest.main()
