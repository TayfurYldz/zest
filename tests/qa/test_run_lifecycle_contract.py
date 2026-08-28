from __future__ import annotations

import inspect
import sys
import unittest
from dataclasses import fields
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

for _path in (
    _ROOT,
    _ROOT / "src",
    _ROOT / "tests",
):
    _value = str(_path)
    if _value not in sys.path:
        sys.path.insert(0, _value)

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.discovery.runner import SurfaceDiscoveryRunner
from zest.application.program_research_context import (
    ProgramPolicyView,
    derive_loopback_only,
)
from zest.application.reconstruct_run_command import (
    reconstruct_start_command,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope_compiler import (
    CompiledScope,
    CompiledScopeRule,
)
from zest.research.orchestration import (
    ORCHESTRATION_PHASES,
    TERMINAL_ORCHESTRATION_STATES,
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)


STOP_REASON_QA = {
    StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value:
        "bounded research exhausts eligible work",
    StopReason.BUDGET_EXHAUSTED.value:
        "experiment/model/worker hard bound exhaustion",
    StopReason.MAX_CYCLES_REACHED.value:
        "cycle bound reaches terminal completion",
    StopReason.MAX_DURATION_REACHED.value:
        "elapsed-time hard bound",
    StopReason.REQUIRE_HUMAN_REVIEW.value:
        "ambiguous authorization requires human review",
    StopReason.NO_COMPATIBLE_RUNTIME.value:
        "runtime routing has no compatible candidate",
    StopReason.CORE_BLOCKED.value:
        "Core scope/policy/side-effect authorization deny",
    StopReason.OPERATOR_PAUSED.value:
        "operator pauses runnable orchestration",
    StopReason.OPERATOR_CANCELLED.value:
        "operator cancels non-terminal orchestration",
    StopReason.OPERATIONAL_FAILURE.value:
        "unknown outcome/runtime/supervisor operational failure",
    StopReason.CONTENT_POLICY_BLOCKED.value:
        "model/provider content-policy block",
    StopReason.AUTH_REQUIRED.value:
        "provider/session authentication required",
    StopReason.RATE_LIMITED.value:
        "rate limit denies further execution",
    StopReason.CANCELLED.value:
        "bounded cancellation outcome",
}


STATE_QA = {
    OrchestrationState.READY.value:
        "START created and ready for bounded work",
    OrchestrationState.RUNNING.value:
        "active owned orchestration tick",
    OrchestrationState.PAUSED.value:
        "operator pause is durable",
    OrchestrationState.WAITING_HUMAN.value:
        "human-review boundary is durable",
    OrchestrationState.BLOCKED.value:
        "policy/Core bounded block",
    OrchestrationState.BUDGET_EXHAUSTED.value:
        "hard resource bound terminal state",
    OrchestrationState.COMPLETED.value:
        "normal/cancel/bounded terminal completion",
    OrchestrationState.FAILED_OPERATIONAL.value:
        "runtime/ownership/unknown operational failure",
}


PHASE_QA = {
    OrchestrationPhase.CYCLE_READY.value:
        "new or completed-cycle checkpoint",
    OrchestrationPhase.OPPORTUNITY_SELECTED.value:
        "selection persisted before hypothesis",
    OrchestrationPhase.HYPOTHESIS_ADMITTED.value:
        "hypothesis persisted before experiment",
    OrchestrationPhase.EXPERIMENT_PLANNED.value:
        "experiment plan durable before authorization",
    OrchestrationPhase.AUTHORIZATION_REQUESTED.value:
        "Core authorization boundary",
    OrchestrationPhase.ATTEMPT_AUTHORIZED.value:
        "authorized attempt durable before dispatch",
    OrchestrationPhase.DISPATCHING.value:
        "dispatch intent durable; crash becomes unknown",
    OrchestrationPhase.WORKER_RESULT_RECORDED.value:
        "WorkerResult persisted before research interpretation",
    OrchestrationPhase.TRANSITION_A_COMPLETE.value:
        "Observation normalization boundary",
    OrchestrationPhase.ASSESSMENT_COMPLETE.value:
        "experiment feedback persisted",
    OrchestrationPhase.TRANSITION_B_COMPLETE.value:
        "promotion/research continuation boundary",
    OrchestrationPhase.CYCLE_COMPLETE.value:
        "cycle closed before next bounded decision",
}


REQUIRED_QA_MODULES = (
    "tests/unit/research/test_orchestration.py",
    "tests/unit/application/test_autonomous_research_controller.py",
    "tests/unit/application/test_local_run_supervisor.py",
    "tests/unit/application/test_orchestration_recovery.py",
    "tests/unit/application/test_execute_planned_experiment.py",
    "tests/unit/application/test_preflight.py",
    "tests/unit/application/test_runtime_outcomes.py",
    "tests/unit/application/test_research_run_control.py",
    "tests/unit/application/test_zestd.py",
    "tests/unit/application/test_discovery_surface.py",
    "tests/unit/application/test_program_research_context.py",
    "tests/unit/application/test_http_transaction.py",
    "tests/integration/test_gate13.py",
    "tests/integration/test_operator_staging.py",
    "tests/integration/test_zestd.py",
    "tests/integration/test_zestd_sigterm.py",
    "tests/integration/test_orchestration_terminal_immutability.py",
    "tests/integration/test_orchestration_lease.py",
    "tests/integration/test_mr5_durability_seal.py",
    "tests/e2e/test_gate21_browser_page.py",
    "tests/e2e/test_gate22_surface_discovery.py",
)


class RunLifecycleContractTests(unittest.TestCase):
    def test_every_stop_reason_has_an_explicit_qa_scenario(self) -> None:
        runtime = {item.value for item in StopReason}

        self.assertEqual(
            set(STOP_REASON_QA),
            runtime,
            msg=(
                "StopReason changed without updating the START→STOP "
                "QA coverage manifest"
            ),
        )

    def test_every_orchestration_state_has_an_explicit_qa_scenario(self) -> None:
        runtime = {item.value for item in OrchestrationState}

        self.assertEqual(
            set(STATE_QA),
            runtime,
            msg=(
                "OrchestrationState changed without updating lifecycle QA"
            ),
        )

    def test_every_durable_phase_has_recovery_coverage_contract(self) -> None:
        runtime = set(ORCHESTRATION_PHASES)

        self.assertEqual(
            set(PHASE_QA),
            runtime,
            msg=(
                "OrchestrationPhase changed without updating recovery QA"
            ),
        )

        self.assertEqual(
            runtime,
            {item.value for item in OrchestrationPhase},
        )

    def test_terminal_state_contract_is_known_to_qa(self) -> None:
        runtime_states = {item.value for item in OrchestrationState}

        self.assertTrue(
            set(TERMINAL_ORCHESTRATION_STATES)
            <= runtime_states
        )

        for state in TERMINAL_ORCHESTRATION_STATES:
            self.assertIn(state, STATE_QA)

    def test_all_required_lifecycle_qa_modules_exist(self) -> None:
        root = Path(__file__).resolve().parents[2]

        missing = [
            path
            for path in REQUIRED_QA_MODULES
            if not (root / path).is_file()
        ]

        self.assertEqual(
            missing,
            [],
            msg=f"lifecycle QA modules missing: {missing}",
        )

    def test_start_command_carries_authoritative_program_policy(self) -> None:
        names = {
            field.name
            for field in fields(StartAutonomousResearchCommand)
        }

        self.assertIn("program_policy", names)

    def test_surface_discovery_accepts_program_policy(self) -> None:
        signature = inspect.signature(
            SurfaceDiscoveryRunner.run_cycle
        )

        self.assertIn(
            "program_policy",
            signature.parameters,
        )

    def test_reconstruction_propagates_sor_policy(self) -> None:
        source = inspect.getsource(
            reconstruct_start_command
        )

        self.assertIn(
            "program_policy=policy",
            source,
        )

    def test_arc_propagates_policy_into_surface_discovery(self) -> None:
        source = inspect.getsource(
            AutonomousResearchController._step_surface_discovery
        )

        self.assertIn(
            "program_policy=command.program_policy",
            source,
        )

    def test_arc_propagates_policy_into_normal_execution(self) -> None:
        source = inspect.getsource(
            AutonomousResearchController.step
        )

        self.assertIn(
            "program_policy=command.program_policy",
            source,
        )

    def test_arc_propagates_policy_into_resume_execution(self) -> None:
        source = inspect.getsource(
            AutonomousResearchController._resume_planned_experiment
        )

        self.assertIn(
            "program_policy=command.program_policy",
            source,
        )

    def test_authorized_network_recovery_requires_original_envelope(
        self,
    ) -> None:
        source = inspect.getsource(
            AutonomousResearchController._resume_authorized
        )

        self.assertIn(
            "HTTP_SCOPE_CAPABILITIES",
            source,
        )
        self.assertIn(
            "resume_network_envelope_not_durable",
            source,
        )

        # Recovery must not bind an old authorization reference
        # to a newly-derived mutable network envelope.
        self.assertNotIn(
            "authorize_http_transaction_plan(",
            source,
        )

    def test_non_loopback_program_does_not_become_loopback_only(self) -> None:
        compiled = CompiledScope(
            rules=(
                CompiledScopeRule(
                    rule_id="qa-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="sandbox.example.test",
                    host_pattern=None,
                    port=443,
                    path_prefix=None,
                    source_reference="qa",
                    expires_at=None,
                ),
            )
        )

        policy = ProgramPolicyView(
            loopback_fixture=False,
            max_response_bytes=4096,
            timeout_ms=2000,
            action_policy={},
        )

        self.assertFalse(
            derive_loopback_only(
                program_policy=policy,
                compiled_scope=compiled,
            )
        )

    def test_missing_program_policy_remains_fail_closed(self) -> None:
        compiled = CompiledScope(
            rules=(
                CompiledScopeRule(
                    rule_id="qa-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="sandbox.example.test",
                    host_pattern=None,
                    port=443,
                    path_prefix=None,
                    source_reference="qa",
                    expires_at=None,
                ),
            )
        )

        self.assertTrue(
            derive_loopback_only(
                program_policy=None,
                compiled_scope=compiled,
            )
        )


if __name__ == "__main__":
    unittest.main()
