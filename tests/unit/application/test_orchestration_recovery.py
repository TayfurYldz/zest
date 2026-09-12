from __future__ import annotations

import unittest
from dataclasses import replace

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.errors import OrchestrationIntegrityError
from zest.application.program_research_context import ProgramPolicyView
from zest.application.orchestration_config import (
    configuration_from_record,
    fingerprint_for_start,
    scope_fingerprint,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import (
    ExecutionAttemptRecord,
    ExperimentPlanRecord,
    ExperimentRecord,
    HypothesisAssessmentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ResearchOpportunityRecord,
    ResearchOrchestrationRecord,
)
from zest.research.orchestration import OrchestrationBounds, OrchestrationPhase, OrchestrationState
from zest.research.planning import plan_diagnostic_echo
from zest.research.browser_page import plan_browser_observe
from zest.application.plan_records import experiment_plan_record_for
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, DIAGNOSTIC_CLAIM, seed_authorization_run


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=1,
        max_experiments=2,
        max_model_calls=20,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _seed(store: _Store) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=20,
        max_tool_calls=20,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _controller(store: _Store):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory,
        port,
        ScriptedModelPort(),
        clock=lambda: CREATED_AT if False else type("C", (), {"now": staticmethod(lambda: CREATED_AT)})(),
    )
    return controller, port


class FixedClock:
    def now(self):
        return CREATED_AT


def _controller2(store: _Store):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock()
    )
    return controller, port


class PersistedBoundsTests(unittest.TestCase):
    def test_step_cannot_widen_max_cycles(self) -> None:
        store = _Store()
        _seed(store)
        controller, _ = _controller2(store)
        controller.start(_command(bounds=_bounds(max_cycles=1)))
        with self.assertRaises(OrchestrationIntegrityError):
            controller.step(_command(bounds=_bounds(max_cycles=3)))
        record = store.research_orchestrations["run-1"]
        self.assertEqual(record.max_cycles, 1)

    def test_mismatch_for_each_hard_bound(self) -> None:
        store = _Store()
        _seed(store)
        controller, _ = _controller2(store)
        controller.start(_command())
        fields = {
            "max_cycles": 9,
            "max_experiments": 9,
            "max_model_calls": 9,
            "max_worker_invocations": 9,
            "max_elapsed_ms": 1,
            "max_selected_opportunities": 9,
            "max_runtime_fallback": 9,
            "side_effect_ceiling": 2,
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                with self.assertRaises(OrchestrationIntegrityError):
                    controller.step(_command(bounds=_bounds(**{field: value})))

    def test_fingerprint_stable_over_reload(self) -> None:
        store = _Store()
        _seed(store)
        controller, _ = _controller2(store)
        controller.start(_command())
        record = store.research_orchestrations["run-1"]
        first = configuration_from_record(record).fingerprint
        second = configuration_from_record(record).fingerprint
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_fingerprint_mismatch_fails_closed(self) -> None:
        store = _Store()
        _seed(store)
        controller, _ = _controller2(store)
        controller.start(_command())
        record = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(record, configuration_fingerprint="0" * 64)
        with self.assertRaises(OrchestrationIntegrityError):
            controller.step(_command())


class CrashRecoveryTests(unittest.TestCase):
    def _start(self, *, max_cycles: int = 2) -> _Store:
        store = _Store()
        _seed(store)
        controller, _ = _controller2(store)
        controller.start(_command(bounds=_bounds(max_cycles=max_cycles)))
        return store

    def _set_phase(self, store: _Store, phase: str, **fields) -> None:
        record = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            record,
            state=OrchestrationState.RUNNING.value,
            current_phase=phase,
            last_phase=phase,
            **fields,
        )

    def _plant_hypothesis(self, store: _Store, hypothesis_id: str = "hyp-resume-1") -> str:
        store.hypotheses[hypothesis_id] = HypothesisRecord(
            hypothesis_id=hypothesis_id,
            research_run_id="run-1",
            claim=DIAGNOSTIC_CLAIM,
            origin_reference="crash-recovery",
            created_at=CREATED_AT,
        )
        return hypothesis_id

    def _plant_experiment(
        self,
        store: _Store,
        *,
        hypothesis_id: str = "hyp-resume-1",
        experiment_id: str = "exp-resume-1",
    ) -> str:
        self._plant_hypothesis(store, hypothesis_id)
        experiment = ExperimentRecord(
            experiment_id=experiment_id,
            research_run_id="run-1",
            hypothesis_id=hypothesis_id,
            budget_id="budget-1",
            execution_state="PLANNED",
            created_at=CREATED_AT,
        )
        store.experiments[experiment_id] = experiment
        plan = plan_diagnostic_echo(
            hypothesis_id,
            budget_id="budget-1",
            target_reference="target-1",
            message="ping-1",
        )
        store.experiment_plans[experiment_id] = experiment_plan_record_for(
            experiment, plan, created_at=CREATED_AT
        )
        return experiment_id

    def _plant_attempt(self, store: _Store, state: str, experiment_id: str = "exp-resume-1") -> None:
        store.execution_attempts["ea-resume-1"] = ExecutionAttemptRecord(
            attempt_id="ea-resume-1",
            request_id="req-resume-1",
            experiment_id=experiment_id,
            research_run_id="run-1",
            correlation_id="corr-resume-1",
            worker_capability="diagnostic.echo",
            action="echo",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="ae-resume-1",
            state=state,
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
        )
        store.execution_attempts_by_request["req-resume-1"] = "ea-resume-1"

    def test_crash_after_opportunity_selection_recompiles_durable_selected_work(self) -> None:
        store = self._start()
        hyp_id = self._plant_hypothesis(store)

        store.research_opportunities["opp-1"] = ResearchOpportunityRecord(
            opportunity_id="opp-1",
            research_run_id="run-1",
            opportunity_kind="SURFACE_DISCOVERY",
            mode="EXPLORATION",
            source_refs=("source-1",),
            proposed_direction="Continue durable selected research work.",
            unresolved_question="What should the selected work evaluate?",
            expected_information_value_description="Recovery-path regression fixture.",
            assumptions=(),
            dimensions={},
            context_signature="ctx-opp-1",
            novelty_composition_marker=False,
            prior_attempt_refs=(),
            structural_identity="struct-opp-1",
            strategy_version="crash-recovery.v1",
            created_at=CREATED_AT,
        )

        self._set_phase(
            store,
            OrchestrationPhase.OPPORTUNITY_SELECTED.value,
            last_opportunity_id="opp-1",
            last_hypothesis_id=hyp_id,
        )

        restarted, port = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))

        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(next(iter(store.hypotheses)), hyp_id)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(
            store.research_orchestrations["run-1"].last_opportunity_id,
            "opp-1",
        )

    def test_crash_after_hypothesis_persistence_reuses_hypothesis(self) -> None:
        store = self._start()
        hyp_id = self._plant_hypothesis(store)
        self._set_phase(
            store,
            OrchestrationPhase.HYPOTHESIS_ADMITTED.value,
            last_hypothesis_id=hyp_id,
        )
        restarted, _ = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(store.hypotheses), 1)
        self.assertLessEqual(len(store.experiments), 2)

    def test_bootstrap_checkpoint_without_opportunity_can_resume_hypothesis(self) -> None:
        store = self._start()
        hyp_id = self._plant_hypothesis(store)

        self._set_phase(
            store,
            OrchestrationPhase.OPPORTUNITY_SELECTED.value,
            last_opportunity_id=None,
            last_hypothesis_id=hyp_id,
        )

        restarted, _ = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))

        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(
            next(iter(store.hypotheses)),
            hyp_id,
        )

    def test_opportunity_selected_with_missing_durable_record_fails_closed(self) -> None:
        store = self._start()
        hyp_id = self._plant_hypothesis(store)

        self._set_phase(
            store,
            OrchestrationPhase.OPPORTUNITY_SELECTED.value,
            last_opportunity_id="opp-missing",
            last_hypothesis_id=hyp_id,
        )

        restarted, port = _controller2(store)
        result = restarted.step(_command(bounds=_bounds(max_cycles=2)))

        self.assertEqual(
            result.state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        self.assertEqual(
            result.last_phase,
            "selected_opportunity_missing",
        )
        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(len(port.calls), 0)

    def test_crash_after_experiment_planned_reuses_experiment(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._set_phase(
            store,
            OrchestrationPhase.EXPERIMENT_PLANNED.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
        )
        restarted, _ = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertIn(experiment_id, store.experiments)
        self.assertEqual(store.research_orchestrations["run-1"].last_experiment_id, experiment_id)
        self.assertLessEqual(len(store.experiments), 2)

    def test_crash_after_authorization_requested_reuses_experiment(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._set_phase(
            store,
            OrchestrationPhase.AUTHORIZATION_REQUESTED.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
        )
        restarted, _ = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(store.hypotheses), 1)
        self.assertIn(experiment_id, store.experiments)
        self.assertLessEqual(len(store.experiments), 2)

    def test_crash_after_attempt_authorized_does_not_create_second_attempt(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._plant_attempt(store, "AUTHORIZED", experiment_id)
        self._set_phase(
            store,
            OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
            last_attempt_id="ea-resume-1",
        )
        restarted, _ = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(store.execution_attempts), 1)
        self.assertEqual(len(store.experiments), 1)

    def test_crash_after_browser_attempt_authorized_fails_closed_without_dispatch(
        self,
    ) -> None:
        store = self._start()

        hypothesis_id = self._plant_hypothesis(
            store,
            "hyp-browser-resume-1",
        )

        experiment_id = "exp-browser-resume-1"

        experiment = ExperimentRecord(
            experiment_id=experiment_id,
            research_run_id="run-1",
            hypothesis_id=hypothesis_id,
            budget_id="budget-1",
            execution_state="READY",
            created_at=CREATED_AT,
        )

        store.experiments[
            experiment_id
        ] = experiment

        plan = plan_browser_observe(
            hypothesis_id,
            budget_id="budget-1",
            target_reference="https://example.com/",
            authorized_origin="https://example.com",
            path="/",
        )

        store.experiment_plans[
            experiment_id
        ] = experiment_plan_record_for(
            experiment,
            plan,
            created_at=CREATED_AT,
        )

        attempt = ExecutionAttemptRecord(
            attempt_id="ea-browser-resume-1",
            request_id="req-browser-resume-1",
            experiment_id=experiment_id,
            research_run_id="run-1",
            correlation_id="corr-browser-resume-1",
            worker_capability="browser.page",
            action="observe",
            target_reference="https://example.com/",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference=(
                "ae-browser-resume-1"
            ),
            state="AUTHORIZED",
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
        )

        store.execution_attempts[
            attempt.attempt_id
        ] = attempt

        store.execution_attempts_by_request[
            attempt.request_id
        ] = attempt.attempt_id

        self._set_phase(
            store,
            OrchestrationPhase.ATTEMPT_AUTHORIZED.value,
            last_hypothesis_id=hypothesis_id,
            last_experiment_id=experiment_id,
            last_attempt_id=attempt.attempt_id,
        )

        # Even if current scope/policy would allow a fresh dispatch,
        # recovery must not synthesize a new envelope for an old
        # authorization decision.
        compiled_scope = CompiledScope(
            rules=(
                CompiledScopeRule(
                    rule_id="rule-browser-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="https",
                    host="example.com",
                    host_pattern=None,
                    port=443,
                    path_prefix=None,
                    source_reference="scope-src",
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

        restarted, port = _controller2(store)

        result = restarted.step(
            _command(
                bounds=_bounds(max_cycles=2),
                compiled_scope=compiled_scope,
                program_policy=policy,
            )
        )

        self.assertEqual(
            result.state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        self.assertEqual(
            result.stop_reason,
            "OPERATIONAL_FAILURE",
        )
        self.assertEqual(
            result.last_phase,
            "resume_network_envelope_not_durable",
        )

        # Critical invariant: no second dispatch.
        self.assertEqual(
            len(port.calls),
            0,
        )

        # The historical attempt remains truthful: it was authorized,
        # but there is no evidence that dispatch started.
        self.assertEqual(
            store.execution_attempts[
                attempt.attempt_id
            ].state,
            "AUTHORIZED",
        )

        self.assertEqual(
            len(store.worker_results),
            0,
        )
        self.assertEqual(
            len(store.observations),
            0,
        )
        self.assertEqual(
            len(store.evidence),
            0,
        )

    def test_dispatching_remains_unknown_outcome(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._plant_attempt(store, "DISPATCHING", experiment_id)
        self._set_phase(
            store,
            OrchestrationPhase.DISPATCHING.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
            last_attempt_id="ea-resume-1",
        )
        restarted, _ = _controller2(store)
        result = restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(result.stop_reason, "UNKNOWN_OUTCOME_REQUIRES_REVIEW")
        self.assertEqual(len(store.execution_attempts), 1)
        self.assertEqual(len(store.experiments), 1)

    def test_crash_after_worker_result_does_not_redispatch(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._plant_attempt(store, "COMPLETED", experiment_id)
        self._set_phase(
            store,
            OrchestrationPhase.WORKER_RESULT_RECORDED.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
            last_attempt_id="ea-resume-1",
        )
        restarted, port = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(len(store.experiments), 1)
        self.assertEqual(len(store.hypothesis_assessments), 1)

    def test_crash_after_transition_a_does_not_duplicate_assessment(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        self._set_phase(
            store,
            OrchestrationPhase.TRANSITION_A_COMPLETE.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
        )
        restarted, port = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(len(store.experiments), 1)
        self.assertEqual(len(store.hypothesis_assessments), 1)

    def test_crash_after_assessment_does_not_create_new_cycle_work(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)
        store.hypothesis_assessments["assess-1"] = HypothesisAssessmentRecord(
            assessment_id="assess-1",
            hypothesis_id="hyp-resume-1",
            experiment_id=experiment_id,
            research_run_id="run-1",
            assessment_outcome="INCONCLUSIVE",
            observation_ids=(),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="diagnostic.echo.v1",
            rationale={"note": "planted"},
            evaluation_strategy="diagnostic.echo.v1",
            created_at=CREATED_AT,
        )
        self._set_phase(
            store,
            OrchestrationPhase.ASSESSMENT_COMPLETE.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
            last_assessment_id="assess-1",
        )
        restarted, port = _controller2(store)
        restarted.step(_command(bounds=_bounds(max_cycles=2)))
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(len(store.hypotheses), 1)
        self.assertEqual(len(store.experiments), 1)
        self.assertEqual(len(store.hypothesis_assessments), 1)


    def test_crash_after_transition_b_does_not_redispatch(self) -> None:
        store = self._start()
        experiment_id = self._plant_experiment(store)

        self._plant_attempt(
            store,
            "COMPLETED",
            experiment_id,
        )

        self._set_phase(
            store,
            OrchestrationPhase.TRANSITION_B_COMPLETE.value,
            last_hypothesis_id="hyp-resume-1",
            last_experiment_id=experiment_id,
            last_attempt_id="ea-resume-1",
        )

        restarted, port = _controller2(store)

        result = restarted.step(
            _command(
                bounds=_bounds(max_cycles=2)
            )
        )

        self.assertEqual(
            len(port.calls),
            0,
            "TRANSITION_B_COMPLETE must never redispatch old work",
        )
        self.assertEqual(
            len(store.experiments),
            1,
        )
        self.assertEqual(
            len(store.execution_attempts),
            1,
        )
        self.assertIn(
            result.state,
            {
                OrchestrationState.READY.value,
                OrchestrationState.COMPLETED.value,
            },
        )



if __name__ == "__main__":
    unittest.main()
