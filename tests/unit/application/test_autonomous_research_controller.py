from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.execute_planned_experiment import ResearchLoopStatus
from zest.application.execute_planned_experiment import ResearchLoopOutcome
from zest.application.orchestration_obligations import unresolved_control_obligations
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.records import ExecutionAttemptRecord, IssuedBudgetRecord
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUsageLimitError,
)
from zest.research.model_runtime import api_runtime_identity
from zest.research.orchestration import (
    CycleOutcome,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
    StopReason,
)
from zest.research.routing import (
    CandidateLocality,
    RoutingBudget,
    RoutingRequest,
    RuntimeCandidate,
)
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.spine import CREATED_AT, seed_authorization_run
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "scope-src"),),
        ambiguous=False,
    )


def _ambiguous_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=True,
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=2,
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


def _seed_large_budget(store: _Store) -> None:
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


def _controller(store: _Store, *, worker=None, model=None):
    factory = FakeUnitOfWorkFactory(store=store)
    port = worker or RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory,
        port,
        model or ScriptedModelPort(),
        clock=FixedClock(),
    )
    return controller, factory, port


class AutonomousResearchControllerTests(unittest.TestCase):
    def test_cycle_complete_releases_active_cycle_identity(
        self,
    ) -> None:
        store = _Store()
        _seed_large_budget(store)

        controller, _, _ = _controller(store)
        command = _command()

        controller.start(command)

        current = store.research_orchestrations["run-1"]

        current = controller._checkpoint(
            current,
            phase=OrchestrationPhase.OPPORTUNITY_SELECTED,
            active_cycle_id="cycle-first",
        )

        self.assertEqual(
            current.active_cycle_id,
            "cycle-first",
        )

        result = controller._complete_cycle(
            current,
            CycleOutcome.CONTINUE,
            "test-cycle-complete",
            increment_cycle=True,
            current_phase=OrchestrationPhase.CYCLE_COMPLETE,
        )

        persisted = store.research_orchestrations[
            "run-1"
        ]

        self.assertEqual(
            result.cycle_number,
            1,
        )
        self.assertEqual(
            persisted.current_phase,
            OrchestrationPhase.CYCLE_COMPLETE.value,
        )
        self.assertIsNone(
            persisted.active_cycle_id,
        )

        # A later cycle may now receive a distinct budget identity.
        next_checkpoint = controller._checkpoint(
            persisted,
            phase=OrchestrationPhase.OPPORTUNITY_SELECTED,
            active_cycle_id="cycle-second",
        )

        self.assertEqual(
            next_checkpoint.active_cycle_id,
            "cycle-second",
        )
        self.assertNotEqual(
            next_checkpoint.active_cycle_id,
            current.active_cycle_id,
        )

    def test_reauthorization_required_holds_run_without_false_completion(self) -> None:
        store = _Store()
        _seed_large_budget(store)

        def reauthorization(request):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=CREATED_AT,
                completed_at=CREATED_AT,
                worker_result={
                    "contract_version": "v1",
                    "correlation": dict(request["correlation"]),
                    "worker_id": "fixture-worker",
                    "status": "REAUTHORIZATION_REQUIRED",
                    "started_at": "2026-08-16T21:00:00Z",
                    "completed_at": "2026-08-16T21:00:01Z",
                    "raw_result": {"stopped": True},
                    "diagnostics": {
                        "response_url": "https://example.test/",
                        "raw_location": "https://other.test/next",
                        "requires_core_re_evaluation": True,
                    },
                },
                exit_code=0,
            )

        worker = RecordingWorkerPort(store=store, handler=reauthorization)
        controller, _, _ = _controller(store, worker=worker)
        command = _command(
            bounds=_bounds(
                allow_repeated_control_experiments=False,
            )
        )

        controller.start(command)

        result = controller.step(command)

        self.assertEqual(
            result.state,
            OrchestrationState.WAITING_HUMAN.value,
        )
        self.assertEqual(
            result.stop_reason,
            StopReason.REQUIRE_HUMAN_REVIEW.value,
        )

        experiment = next(iter(store.experiments.values()))
        self.assertEqual(
            experiment.execution_state,
            "AUTHORIZATION_CHECK",
        )
        self.assertEqual(len(store.observations), 0)
        self.assertEqual(len(store.evidence), 0)
        self.assertEqual(len(worker.calls), 1)

        repeated = controller.step(command)

        self.assertEqual(
            repeated.state,
            OrchestrationState.WAITING_HUMAN.value,
        )
        self.assertEqual(
            repeated.stop_reason,
            StopReason.REQUIRE_HUMAN_REVIEW.value,
        )
        self.assertEqual(len(worker.calls), 1)

        worker_result_id = next(iter(store.worker_results))

        denied = controller.deny_reauthorization(
            "run-1",
            worker_result_id=worker_result_id,
            operator_id="operator-1",
        )

        self.assertEqual(
            denied.state,
            OrchestrationState.READY.value,
        )
        self.assertIsNone(denied.stop_reason)

        blocked = next(iter(store.experiments.values()))
        self.assertEqual(
            blocked.execution_state,
            "BLOCKED",
        )

        obligations = unresolved_control_obligations(
            attempts=tuple(store.execution_attempts.values()),
            experiments=tuple(store.experiments.values()),
            worker_results=tuple(store.worker_results.values()),
        )
        self.assertEqual(obligations, ())

        decisions = [
            event
            for event in store.audit_events.values()
            if event.event_type
            == "REAUTHORIZATION_DENIED_BY_HUMAN"
        ]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            decisions[0].actor_type,
            "HUMAN_OPERATOR",
        )
        self.assertFalse(
            decisions[0].payload["scope_changed"]
        )
        self.assertFalse(
            decisions[0].payload["new_authority_granted"]
        )
        self.assertFalse(
            decisions[0].payload["redispatch_authorized"]
        )

        # The BLOCKED hypothesis must not be regenerated as an automatic
        # follow-up. No second Worker dispatch is allowed.
        finished = controller.step(command)

        self.assertEqual(
            finished.state,
            OrchestrationState.COMPLETED.value,
        )
        self.assertEqual(
            finished.stop_reason,
            StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value,
        )
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(len(store.observations), 0)
        self.assertEqual(len(store.evidence), 0)

        # Human decision is idempotent.
        again = controller.deny_reauthorization(
            "run-1",
            worker_result_id=worker_result_id,
            operator_id="operator-1",
        )
        self.assertEqual(
            again.state,
            OrchestrationState.COMPLETED.value,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in store.audit_events.values()
                    if event.event_type
                    == "REAUTHORIZATION_DENIED_BY_HUMAN"
                ]
            ),
            1,
        )

    def test_unhandled_loop_status_fails_closed_without_completion(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)
        controller.start(_command())
        unknown = ResearchLoopOutcome(
            status=object(),
            hypothesis_id="hypothesis-unknown",
            experiment_id="experiment-unknown",
            experiment_execution_state="UNKNOWN",
        )

        with mock.patch.object(controller._execute, "execute", return_value=unknown):
            result = controller.step(_command())

        self.assertEqual(result.state, OrchestrationState.FAILED_OPERATIONAL.value)
        self.assertEqual(result.stop_reason, StopReason.OPERATIONAL_FAILURE.value)
        self.assertNotEqual(result.state, OrchestrationState.COMPLETED.value)

    def test_max_cycles_zero_executes_none(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        result = controller.run_bounded(_command(bounds=_bounds(max_cycles=0)))
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, StopReason.MAX_CYCLES_REACHED.value)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(len(store.experiments), 0)

    def test_bounded_cycles_terminate(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        command = _command(bounds=_bounds(max_cycles=2))
        result = controller.start(command)
        for _ in range(6):
            result = controller.step(command)
            if result.state not in {
                OrchestrationState.READY.value,
                OrchestrationState.RUNNING.value,
            }:
                break
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, StopReason.MAX_CYCLES_REACHED.value)
        self.assertGreaterEqual(len(port.calls), 2)
        self.assertGreaterEqual(len(store.experiments), 2)
        self.assertEqual(store.findings, {})

    def test_pause_resume_and_cancel(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        controller.start(_command())
        paused = controller.pause("run-1")
        self.assertEqual(paused.state, OrchestrationState.PAUSED.value)
        step = controller.step(_command())
        self.assertEqual(step.state, OrchestrationState.PAUSED.value)
        self.assertEqual(len(port.calls), 0)
        controller.resume("run-1")
        controller.step(_command())
        self.assertEqual(len(port.calls), 2)
        cancelled = controller.cancel("run-1")
        self.assertEqual(cancelled.stop_reason, StopReason.OPERATOR_CANCELLED.value)
        self.assertNotEqual(
            store.execution_attempts[next(iter(store.execution_attempts))].state,
            "CANCELLED",
        )

    def test_start_is_idempotent_and_creates_one_orchestration(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)

        first = controller.start(_command())
        second = controller.start(_command())

        self.assertEqual(first.research_run_id, second.research_run_id)
        self.assertEqual(first.state, second.state)
        self.assertEqual(len(store.research_orchestrations), 1)

    def test_restart_reloads_durable_state(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        first, factory, _ = _controller(store)
        first.start(_command())
        first.step(_command())
        second = AutonomousResearchController(
            factory, RecordingWorkerPort(store=store), ScriptedModelPort(), clock=FixedClock()
        )
        reloaded = second.step(_command())
        self.assertIn(reloaded.state, {OrchestrationState.READY.value, OrchestrationState.COMPLETED.value})
        self.assertEqual(len(store.hypotheses), 2)

    def test_core_deny_stops_dispatch(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        controller.start(_command(scope=_deny_scope()))
        result = controller.step(_command(scope=_deny_scope()))
        self.assertEqual(result.stop_reason, StopReason.CORE_BLOCKED.value)
        self.assertEqual(len(port.calls), 0)

    def test_human_review_still_blocks(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        controller.start(_command(scope=_ambiguous_scope()))
        result = controller.step(_command(scope=_ambiguous_scope()))
        self.assertEqual(result.state, OrchestrationState.WAITING_HUMAN.value)
        self.assertEqual(len(port.calls), 0)

    def test_policy_block_is_not_bypassed(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        model = ScriptedModelPort(error=ContentPolicyBlockedError("blocked"))
        controller, _, _ = _controller(store, model=model)
        controller.start(_command())
        result = controller.step(_command())
        self.assertEqual(result.stop_reason, StopReason.CONTENT_POLICY_BLOCKED.value)
        self.assertEqual(len(model.calls), 1)

    def test_auth_failed_is_not_policy_block(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        model = ScriptedModelPort(error=ProviderAuthError("auth"))
        controller, _, _ = _controller(store, model=model)
        controller.start(_command())
        result = controller.step(_command())
        self.assertEqual(result.stop_reason, StopReason.AUTH_REQUIRED.value)
        self.assertNotEqual(result.stop_reason, StopReason.CONTENT_POLICY_BLOCKED.value)
        self.assertEqual(len(model.calls), 1)

    def test_exhausted_transient_rate_limit_gets_one_durable_checkpoint_retry_then_blocks(
        self,
    ) -> None:
        store = _Store()
        _seed_large_budget(store)

        model = ScriptedModelPort(
            error=ProviderRateLimitError(
                "rate"
            )
        )

        controller, factory, _ = _controller(
            store,
            model=model,
        )

        command = _command(
            bounds=_bounds(
                max_cycles=2,
                max_runtime_fallback=0,
            )
        )

        controller.start(command)

        with mock.patch(
            "zest.application."
            "bounded_model_failover.sleep",
            return_value=None,
        ):
            first = controller.step(command)

        self.assertEqual(
            first.state,
            OrchestrationState.READY.value,
        )
        self.assertIsNone(
            first.stop_reason,
        )
        self.assertEqual(
            first.cycle_number,
            0,
        )
        self.assertEqual(
            len(model.calls),
            2,
        )

        checkpoint = store.research_orchestrations[
            "run-1"
        ]

        self.assertIsNotNone(
            checkpoint.active_cycle_id,
        )
        self.assertEqual(
            checkpoint.current_phase,
            "OPPORTUNITY_SELECTED",
        )

        grants = [
            event
            for event in store.audit_events.values()
            if event.event_type
            == "MODEL_RUNTIME_RATE_LIMIT_CHECKPOINT_RETRY"
        ]

        self.assertEqual(
            len(grants),
            1,
        )
        self.assertEqual(
            grants[0].payload["retry_ordinal"],
            1,
        )
        self.assertFalse(
            grants[0].payload["cycle_incremented"]
        )

        # Process/supervisor restart must not reset the durable retry bound.
        restarted = AutonomousResearchController(
            factory,
            RecordingWorkerPort(store=store),
            model,
            clock=FixedClock(),
        )

        with mock.patch(
            "zest.application."
            "bounded_model_failover.sleep",
            return_value=None,
        ):
            second = restarted.step(command)

        self.assertEqual(
            second.state,
            OrchestrationState.BLOCKED.value,
        )
        self.assertEqual(
            second.stop_reason,
            StopReason.RATE_LIMITED.value,
        )

        # Two attempts on initial tick + two attempts on the one checkpoint
        # retry. No third checkpoint retry is possible.
        self.assertEqual(
            len(model.calls),
            4,
        )

        grants = [
            event
            for event in store.audit_events.values()
            if event.event_type
            == "MODEL_RUNTIME_RATE_LIMIT_CHECKPOINT_RETRY"
        ]

        self.assertEqual(
            len(grants),
            1,
        )

        model_consumptions = [
            row
            for row
            in store.budget_consumptions.values()
            if row.resource_type == "MODEL_CALL"
        ]

        self.assertEqual(
            len(model_consumptions),
            4,
        )
        self.assertEqual(
            len({
                row.request_id
                for row in model_consumptions
            }),
            4,
        )
        self.assertTrue(
            any(
                "checkpoint-retry-1"
                in row.request_id
                for row in model_consumptions
            )
        )

        # Once BLOCKED, another step cannot consume another model attempt.
        before = len(model.calls)
        again = restarted.step(command)

        self.assertEqual(
            again.state,
            OrchestrationState.BLOCKED.value,
        )
        self.assertEqual(
            len(model.calls),
            before,
        )

    def test_transient_rate_limit_checkpoint_retry_can_recover_same_opportunity(
        self,
    ) -> None:
        store = _Store()
        _seed_large_budget(store)

        class FailTwoThenSucceed:
            def __init__(self):
                self.calls = []
                self.delegate = ScriptedModelPort()

            def complete(self, request):
                self.calls.append(request)

                if len(self.calls) <= 2:
                    raise ProviderRateLimitError(
                        "transient rate"
                    )

                return self.delegate.complete(
                    request
                )

        model = FailTwoThenSucceed()

        controller, factory, worker = _controller(
            store,
            model=model,
        )

        command = _command(
            bounds=_bounds(
                max_cycles=2,
                max_runtime_fallback=0,
            )
        )

        controller.start(command)

        with mock.patch(
            "zest.application."
            "bounded_model_failover.sleep",
            return_value=None,
        ):
            first = controller.step(command)

        self.assertEqual(
            first.state,
            OrchestrationState.READY.value,
        )
        self.assertEqual(
            first.cycle_number,
            0,
        )

        first_cycle_id = (
            store.research_orchestrations[
                "run-1"
            ].active_cycle_id
        )

        self.assertIsNotNone(
            first_cycle_id,
        )

        restarted = AutonomousResearchController(
            factory,
            worker,
            model,
            clock=FixedClock(),
        )

        with mock.patch(
            "zest.application."
            "bounded_model_failover.sleep",
            return_value=None,
        ):
            recovered = restarted.step(command)

        self.assertNotEqual(
            recovered.state,
            OrchestrationState.BLOCKED.value,
        )
        self.assertNotEqual(
            recovered.stop_reason,
            StopReason.RATE_LIMITED.value,
        )

        self.assertEqual(
            len(model.calls),
            4,
        )
        self.assertEqual(
            len(store.hypotheses),
            1,
        )
        # Default test bounds explicitly permit repeated control
        # experiments. Recovery must therefore preserve the normal two-
        # experiment control topology rather than collapsing it to one.
        self.assertEqual(
            len(worker.calls),
            2,
        )
        self.assertEqual(
            len(store.experiments),
            2,
        )

        hypothesis_ids = {
            experiment.hypothesis_id
            for experiment in store.experiments.values()
        }

        self.assertEqual(
            hypothesis_ids,
            set(store.hypotheses),
        )
        self.assertEqual(
            len(hypothesis_ids),
            1,
        )

        worker_request_ids = [
            call["request"]["correlation"]["request_id"]
            for call in worker.calls
        ]

        self.assertEqual(
            len(set(worker_request_ids)),
            2,
        )

        attempt_experiment_ids = {
            attempt.experiment_id
            for attempt in store.execution_attempts.values()
        }

        self.assertEqual(
            attempt_experiment_ids,
            set(store.experiments),
        )

        worker_result_experiment_ids = {
            result.experiment_id
            for result in store.worker_results.values()
        }

        self.assertEqual(
            worker_result_experiment_ids,
            set(store.experiments),
        )

        admissions = list(
            store.research_admissions.values()
        )

        self.assertEqual(
            len(admissions),
            2,
        )
        self.assertEqual(
            admissions[0].reason_code,
            "MODEL_RATE_LIMITED",
        )
        self.assertEqual(
            admissions[1].outcome,
            "ADMITTED",
        )

        model_consumptions = [
            row
            for row
            in store.budget_consumptions.values()
            if row.resource_type == "MODEL_CALL"
        ]

        self.assertEqual(
            len(model_consumptions),
            4,
        )
        self.assertEqual(
            len({
                row.request_id
                for row in model_consumptions
            }),
            4,
        )

        grants = [
            event
            for event in store.audit_events.values()
            if event.event_type
            == "MODEL_RUNTIME_RATE_LIMIT_CHECKPOINT_RETRY"
        ]

        self.assertEqual(
            len(grants),
            1,
        )
        self.assertEqual(
            grants[0].payload[
                "active_cycle_id"
            ],
            first_cycle_id,
        )

    def test_usage_limit_does_not_enter_checkpoint_retry_loop(
        self,
    ) -> None:
        store = _Store()
        _seed_large_budget(store)

        model = ScriptedModelPort(
            error=ProviderUsageLimitError(
                "usage exhausted"
            )
        )

        controller, _, _ = _controller(
            store,
            model=model,
        )

        command = _command(
            bounds=_bounds(
                max_runtime_fallback=0,
            )
        )

        controller.start(command)

        result = controller.step(command)

        self.assertEqual(
            result.state,
            OrchestrationState.BLOCKED.value,
        )
        self.assertEqual(
            result.stop_reason,
            StopReason.RATE_LIMITED.value,
        )

        # R2 skips the meaningless same-runtime immediate retry.
        self.assertEqual(
            len(model.calls),
            1,
        )

        failed = list(
            store.research_admissions.values()
        )

        self.assertEqual(
            len(failed),
            1,
        )
        self.assertEqual(
            failed[0].reason_code,
            "MODEL_USAGE_LIMITED",
        )

        grants = [
            event
            for event in store.audit_events.values()
            if event.event_type
            == "MODEL_RUNTIME_RATE_LIMIT_CHECKPOINT_RETRY"
        ]

        self.assertEqual(
            grants,
            [],
        )

    def test_routing_unavailable_stops_cleanly(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        request = RoutingRequest(
            role=ModelRole.GENERATOR,
            candidates=(),
            budget=RoutingBudget(max_runtime_attempts=1, max_fallback_attempts=0),
        )
        controller.start(_command(routing_request=request))
        result = controller.step(_command(routing_request=request))
        self.assertEqual(result.stop_reason, StopReason.NO_COMPATIBLE_RUNTIME.value)
        self.assertEqual(len(port.calls), 0)

    def test_unknown_outcome_is_not_retried(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        worker = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.PROTOCOL_ERROR),
        )
        # PROTOCOL_ERROR is failed not unknown. Inject DISPATCHING leftover instead.
        controller, _, _ = _controller(store)
        controller.start(_command())
        controller.step(_command())
        attempt_id = next(iter(store.execution_attempts))
        current = store.execution_attempts[attempt_id]
        store.execution_attempts[attempt_id] = ExecutionAttemptRecord(
            attempt_id=current.attempt_id,
            request_id=current.request_id,
            experiment_id=current.experiment_id,
            research_run_id=current.research_run_id,
            correlation_id=current.correlation_id,
            worker_capability=current.worker_capability,
            action=current.action,
            target_reference=current.target_reference,
            budget_id=current.budget_id,
            side_effect_level=current.side_effect_level,
            authorization_decision_reference=current.authorization_decision_reference,
            state="UNKNOWN_OUTCOME",
            created_at=current.created_at,
            authorized_at=current.authorized_at,
            dispatch_started_at=current.dispatch_started_at,
            completed_at=current.completed_at,
        )
        before = len(store.experiments)
        restarted = AutonomousResearchController(
            FakeUnitOfWorkFactory(store=store),
            worker,
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        result = restarted.step(_command())
        self.assertEqual(result.stop_reason, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW.value)
        self.assertEqual(len(store.experiments), before)

    def test_worker_not_invoked_inside_open_transaction(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, port = _controller(store)
        controller.run_bounded(_command(bounds=_bounds(max_cycles=1)))
        self.assertGreaterEqual(len(port.calls), 1)


class TerminalStateImmutabilityTests(unittest.TestCase):
    """RT-A: a terminal orchestration checkpoint must never be overwritten by
    an operator command, and the rejection itself must be audited."""

    def _terminal_record(self, store: _Store, *, state: str, stop_reason: str):
        controller, factory, _ = _controller(store)
        controller.start(_command())
        from dataclasses import replace

        current = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            current,
            state=state,
            stop_reason=stop_reason,
        )
        return AutonomousResearchController(
            factory, RecordingWorkerPort(store=store), ScriptedModelPort(), clock=FixedClock()
        )

    def test_pause_rejected_on_each_terminal_state(self) -> None:
        for state, stop_reason in (
            (OrchestrationState.COMPLETED.value, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value),
            (OrchestrationState.BUDGET_EXHAUSTED.value, StopReason.BUDGET_EXHAUSTED.value),
            (OrchestrationState.FAILED_OPERATIONAL.value, StopReason.OPERATIONAL_FAILURE.value),
        ):
            with self.subTest(state=state):
                store = _Store()
                _seed_large_budget(store)
                controller = self._terminal_record(store, state=state, stop_reason=stop_reason)
                result = controller.pause("run-1")
                self.assertEqual(result.state, state)
                self.assertEqual(result.stop_reason, stop_reason)
                self.assertEqual(store.research_orchestrations["run-1"].state, state)
                self.assertEqual(store.research_orchestrations["run-1"].stop_reason, stop_reason)
                rejections = [
                    event
                    for event in store.audit_events.values()
                    if event.event_type == "ORCHESTRATION_OPERATOR_COMMAND_REJECTED"
                ]
                self.assertEqual(len(rejections), 1)
                self.assertEqual(rejections[0].payload["current_state"], state)
                self.assertEqual(rejections[0].payload["requested_state"], "PAUSED")

    def test_cancel_rejected_on_each_terminal_state(self) -> None:
        for state, stop_reason in (
            (OrchestrationState.COMPLETED.value, StopReason.MAX_CYCLES_REACHED.value),
            (OrchestrationState.BUDGET_EXHAUSTED.value, StopReason.BUDGET_EXHAUSTED.value),
            (OrchestrationState.FAILED_OPERATIONAL.value, StopReason.OPERATIONAL_FAILURE.value),
        ):
            with self.subTest(state=state):
                store = _Store()
                _seed_large_budget(store)
                controller = self._terminal_record(store, state=state, stop_reason=stop_reason)
                result = controller.cancel("run-1")
                self.assertEqual(result.state, state)
                self.assertEqual(result.stop_reason, stop_reason)
                self.assertEqual(store.research_orchestrations["run-1"].state, state)
                self.assertEqual(store.research_orchestrations["run-1"].stop_reason, stop_reason)
                rejections = [
                    event
                    for event in store.audit_events.values()
                    if event.event_type == "ORCHESTRATION_OPERATOR_COMMAND_REJECTED"
                ]
                self.assertEqual(len(rejections), 1)
                self.assertEqual(rejections[0].payload["requested_state"], "COMPLETED")
                self.assertEqual(rejections[0].payload["requested_stop_reason"], "OPERATOR_CANCELLED")

    def test_pause_and_cancel_still_work_on_non_terminal_states(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)
        controller.start(_command())
        paused = controller.pause("run-1")
        self.assertEqual(paused.state, OrchestrationState.PAUSED.value)
        cancelled = controller.cancel("run-1")
        self.assertEqual(cancelled.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(cancelled.stop_reason, StopReason.OPERATOR_CANCELLED.value)

    def test_repository_save_rejects_write_to_terminal_row(self) -> None:
        from dataclasses import replace

        from zest.data.errors import TerminalOrchestrationStateError

        store = _Store()
        _seed_large_budget(store)
        controller, factory, _ = _controller(store)
        controller.start(_command())
        current = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            current, state=OrchestrationState.COMPLETED.value
        )
        with factory.open() as uow:
            terminal = uow.research_orchestrations.get("run-1")
            with self.assertRaises(TerminalOrchestrationStateError):
                uow.research_orchestrations.save(replace(terminal, last_phase="tampered"))
            uow.rollback()
        self.assertNotEqual(store.research_orchestrations["run-1"].last_phase, "tampered")


class MarkOperationalFailureTests(unittest.TestCase):
    """RT-B support: the controller entry point used by reconciliation to
    close out a crash-left RUNNING checkpoint, without becoming a second
    authority over research-run lifecycle state."""

    def test_marks_running_checkpoint_as_failed_operational(self) -> None:
        from dataclasses import replace

        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)
        controller.start(_command())
        current = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            current, state=OrchestrationState.RUNNING.value
        )
        result = controller.mark_operational_failure(
            "run-1", reason="stale RUNNING checkpoint after process restart"
        )
        self.assertEqual(result.state, OrchestrationState.FAILED_OPERATIONAL.value)
        self.assertEqual(result.stop_reason, StopReason.OPERATIONAL_FAILURE.value)
        self.assertEqual(
            store.research_orchestrations["run-1"].state,
            OrchestrationState.FAILED_OPERATIONAL.value,
        )
        reconciled = [
            event
            for event in store.audit_events.values()
            if event.event_type == "ORCHESTRATION_RECONCILED_OPERATIONAL_FAILURE"
        ]
        self.assertEqual(len(reconciled), 1)

    def test_is_noop_when_not_running(self) -> None:
        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)
        controller.start(_command())
        result = controller.mark_operational_failure("run-1", reason="should not apply")
        self.assertEqual(result.state, OrchestrationState.READY.value)
        self.assertEqual(store.research_orchestrations["run-1"].state, OrchestrationState.READY.value)

    def test_is_noop_when_already_terminal(self) -> None:
        from dataclasses import replace

        store = _Store()
        _seed_large_budget(store)
        controller, _, _ = _controller(store)
        controller.start(_command())
        current = store.research_orchestrations["run-1"]
        store.research_orchestrations["run-1"] = replace(
            current,
            state=OrchestrationState.COMPLETED.value,
            stop_reason=StopReason.MAX_CYCLES_REACHED.value,
        )
        result = controller.mark_operational_failure("run-1", reason="ignored")
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, StopReason.MAX_CYCLES_REACHED.value)


if __name__ == "__main__":
    unittest.main()
