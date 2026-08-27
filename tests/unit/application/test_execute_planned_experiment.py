from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import UUID

import pathsetup  # noqa: F401

from zest.application.execute_planned_experiment import (
    BUDGET_CONSUMPTION_LEDGER_IMPLEMENTED,
    AuthorizedDispatch,
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.program_research_context import ProgramPolicyView
from zest.application.ingest_worker_invocation import IngestionStatus
from zest.application.retry_policy import automatic_retry_allowed
from zest.core.enums import (
    ExecutionDecisionKind,
    ReasonCode,
    ScopeRuleEffect,
)
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceConflictError, PersistenceError
from zest.data.records import ExecutionAttemptRecord, ExecutionAttemptState, HypothesisRecord, RateLimitProfileRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.planning import human_seeded_hypothesis, plan_diagnostic_echo
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    invocation_outcome,
)
from support.spine import CREATED_AT, DIAGNOSTIC_CLAIM, seed_authorization_run, seed_spine


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _empty_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(matches=(), ambiguous=False)


def _ambiguous_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=True,
    )


def _plan(message: str = "ping", **overrides):
    values = dict(
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message=message,
    )
    values.update(overrides)
    return plan_diagnostic_echo(**values)


def _command(scope=None, plan=None, **kwargs) -> ExecutePlannedExperimentCommand:
    return ExecutePlannedExperimentCommand(
        experiment_id="exp-1",
        plan=plan or _plan(),
        scope=scope or _allow_scope(),
        **kwargs,
    )


def _use_case(
    store: _Store | None = None,
    *,
    fail_on: str | None = None,
    worker: RecordingWorkerPort | None = None,
):
    factory = FakeUnitOfWorkFactory(store=store or _Store(), fail_on=fail_on)
    if store is None:
        seed_spine(factory.store)
    port = worker or RecordingWorkerPort(store=factory.store)
    use_case = ExecutePlannedExperiment(factory, port, clock=FixedClock())
    return use_case, factory, port


class AuthorizationGateTests(unittest.TestCase):
    def test_active_explicit_allow_level0_dispatches_worker(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(outcome.core_decision, ExecutionDecisionKind.ALLOW)
        self.assertEqual(outcome.experiment_execution_state, "EXECUTION_SUCCEEDED")

    def test_missing_authorization_does_not_invoke_worker(self) -> None:
        factory_store = _Store()
        seed_spine(factory_store)
        factory_store.authorization_sources.clear()
        use_case, _, port = _use_case(factory_store)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.AUTHORIZATION_MISSING)
        self.assertEqual(len(port.calls), 0)
        self.assertIsNone(outcome.request_id)
        self.assertEqual(factory_store.experiments["exp-1"].execution_state, "BLOCKED")

    def test_revoked_authorization_does_not_invoke_worker(self) -> None:
        store = _Store()
        seed_spine(store, authorization_state="REVOKED")
        use_case, _, port = _use_case(store)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.AUTHORIZATION_INACTIVE)
        self.assertEqual(len(port.calls), 0)

    def test_scope_deny_does_not_invoke_worker(self) -> None:
        use_case, _, port = _use_case()
        outcome = use_case.execute(_command(scope=_deny_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_DENIED)
        self.assertEqual(len(port.calls), 0)

    def test_no_explicit_allow_does_not_invoke_worker(self) -> None:
        use_case, _, port = _use_case()
        outcome = use_case.execute(_command(scope=_empty_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(len(port.calls), 0)

    def test_ambiguous_scope_does_not_invoke_worker(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(_command(scope=_ambiguous_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.HUMAN_REVIEW_REQUIRED)
        self.assertEqual(outcome.core_decision, ExecutionDecisionKind.REQUIRE_HUMAN_REVIEW)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(
            factory.store.experiments["exp-1"].execution_state, "AUTHORIZATION_CHECK"
        )
        self.assertEqual(len(factory.store.execution_attempts), 0)

    def test_level2_without_approval_does_not_invoke_worker(self) -> None:
        use_case, _, port = _use_case()
        plan = _plan()
        object.__setattr__(plan, "side_effect_level", 2)
        outcome = use_case.execute(_command(plan=plan))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)
        self.assertEqual(len(port.calls), 0)

    def test_level3_does_not_invoke_worker(self) -> None:
        use_case, _, port = _use_case()
        plan = _plan()
        object.__setattr__(plan, "side_effect_level", 3)
        outcome = use_case.execute(_command(plan=plan))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.RISK_EXCEEDS_CAPABILITY)
        self.assertEqual(len(port.calls), 0)


class DecisionProvenanceTests(unittest.TestCase):
    def test_core_decision_is_persisted_before_dispatch(self) -> None:
        use_case, factory, port = _use_case()
        authorized = use_case.authorize(_command())
        self.assertIsInstance(authorized, AuthorizedDispatch)
        assert isinstance(authorized, AuthorizedDispatch)
        self.assertEqual(len(port.calls), 0)
        audit = factory.store.audit_events[authorized.authorization_decision_reference]
        self.assertEqual(audit.event_type, "EXECUTION_DECISION")
        self.assertEqual(audit.payload["decision"], "ALLOW")
        self.assertEqual(audit.payload["reason_code"], "ALLOWED")
        self.assertEqual(audit.payload["authorization_source_id"], "as-1")
        self.assertEqual(audit.payload["matched_scope_rule_ids"], ["rule-allow"])
        self.assertEqual(audit.payload["budget_id"], "budget-1")
        self.assertEqual(audit.payload["side_effect_level"], 0)
        self.assertNotIn("token", audit.payload)
        attempt = factory.store.execution_attempts[authorized.attempt_id]
        self.assertEqual(attempt.state, ExecutionAttemptState.AUTHORIZED.value)
        self.assertEqual(
            attempt.authorization_decision_reference,
            authorized.authorization_decision_reference,
        )

    def test_worker_request_decision_ref_resolves_to_durable_audit(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(len(port.calls), 1)
        request = port.calls[0]["request"]
        ref = request["authorization_decision_reference"]
        audit = factory.store.audit_events[ref]
        self.assertEqual(audit.payload["decision"], "ALLOW")
        self.assertEqual(outcome.authorization_decision_reference, ref)

    def test_plan_arguments_cannot_invent_decision_ref_or_request_id(self) -> None:
        use_case, _, port = _use_case()
        plan = _plan()
        object.__setattr__(
            plan,
            "arguments",
            {
                "message": "ping",
                "request_id": "attacker-chosen",
                "authorization_decision_reference": "forged-authz",
            },
        )
        outcome = use_case.execute(_command(plan=plan))
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        request = port.calls[0]["request"]
        self.assertNotEqual(request["correlation"]["request_id"], "attacker-chosen")
        self.assertNotEqual(
            request["authorization_decision_reference"], "forged-authz"
        )
        UUID(request["correlation"]["request_id"])


class PlanDurabilityTests(unittest.TestCase):
    def test_executed_plan_is_persisted_and_cannot_silently_change(self) -> None:
        use_case, factory, _ = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        record = factory.store.experiment_plans["exp-1"]
        self.assertEqual(record.expected_observation, "echoed value matches input")
        self.assertEqual(record.disconfirming_observation, "no result or mismatched value")
        self.assertEqual(record.evaluation_strategy, "diagnostic.echo.v1")
        mutated = _plan(message="other")
        retry = use_case.execute(_command(plan=mutated))
        self.assertEqual(retry.status, ResearchLoopStatus.ALREADY_TERMINAL)
        reloaded = factory.store.experiment_plans["exp-1"]
        self.assertEqual(reloaded.arguments["message"], "ping")
        self.assertEqual(reloaded.expected_observation, record.expected_observation)

    def test_prepare_then_mutated_plan_is_rejected(self) -> None:
        from zest.application.prepare_planned_experiment import (
            PreparePlannedExperiment,
            PreparePlannedExperimentCommand,
        )

        store = _Store()
        seed_authorization_run(store)
        store.hypotheses["hyp-1"] = HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim=DIAGNOSTIC_CLAIM,
            origin_reference="human-seed-1",
            created_at=CREATED_AT,
        )
        factory = FakeUnitOfWorkFactory(store)
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id="exp-1",
                research_run_id="run-1",
                plan=_plan(message="ping"),
            )
        )
        use_case = ExecutePlannedExperiment(
            factory, RecordingWorkerPort(store=store), clock=FixedClock()
        )
        outcome = use_case.execute(_command(plan=_plan(message="mutated")))
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(store.experiment_plans["exp-1"].arguments["message"], "ping")
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)


class AttemptIdentityTests(unittest.TestCase):
    def test_control_plane_generates_globally_unique_request_id(self) -> None:
        use_case, factory, port = _use_case()
        outcome = use_case.execute(_command())
        self.assertIsNotNone(outcome.request_id)
        assert outcome.request_id is not None
        UUID(outcome.request_id)
        self.assertEqual(port.calls[0]["request"]["correlation"]["request_id"], outcome.request_id)
        self.assertEqual(len(factory.store.execution_attempts), 1)

    def test_request_id_uniqueness_is_enforced(self) -> None:
        use_case, factory, _ = _use_case()
        authorized = use_case.authorize(_command())
        assert isinstance(authorized, AuthorizedDispatch)
        existing = factory.store.execution_attempts[authorized.attempt_id]
        with FakeUnitOfWorkFactory(factory.store).open() as uow:
            with self.assertRaises(PersistenceConflictError):
                uow.execution_attempts.insert(
                    ExecutionAttemptRecord(
                        attempt_id="ea:other",
                        request_id=existing.request_id,
                        experiment_id=existing.experiment_id,
                        research_run_id=existing.research_run_id,
                        correlation_id="corr-dup",
                        worker_capability=existing.worker_capability,
                        action=existing.action,
                        target_reference=existing.target_reference,
                        budget_id=existing.budget_id,
                        side_effect_level=0,
                        authorization_decision_reference=existing.authorization_decision_reference,
                        state=ExecutionAttemptState.AUTHORIZED.value,
                        created_at=CREATED_AT,
                    )
                )


class TransactionPhaseTests(unittest.TestCase):
    def test_worker_not_invoked_before_durable_intent(self) -> None:
        use_case, factory, port = _use_case()
        authorized = use_case.authorize(_command())
        self.assertIsInstance(authorized, AuthorizedDispatch)
        self.assertEqual(len(port.calls), 0)
        attempt = next(iter(factory.store.execution_attempts.values()))
        self.assertEqual(attempt.state, "AUTHORIZED")

    def test_persistence_failure_before_dispatch_skips_worker(self) -> None:
        store = _Store()
        seed_spine(store)
        use_case, _, port = _use_case(store, fail_on="execution_attempts")
        with self.assertRaises(PersistenceError):
            use_case.execute(_command())
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(len(store.execution_attempts), 0)

    def test_result_persistence_failure_does_not_ingest(self) -> None:
        store = _Store()
        seed_spine(store)
        use_case, _, port = _use_case(store, fail_on="attempt_outcome")
        outcome = use_case.execute(_command())
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(outcome.status, ResearchLoopStatus.INVOCATION_FAILED)
        self.assertEqual(len(store.observations), 0)
        self.assertEqual(len(store.worker_results), 0)
        attempt = next(iter(store.execution_attempts.values()))
        self.assertEqual(attempt.state, "DISPATCHING")


class WorkerResultClassificationTests(unittest.TestCase):
    def test_completed_blocked_worker_result_does_not_mark_experiment_succeeded(self) -> None:
        store = _Store()
        seed_spine(store)

        def blocked_worker(request):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=CREATED_AT,
                completed_at=CREATED_AT,
                worker_result={
                    "contract_version": "v1",
                    "correlation": dict(request["correlation"]),
                    "worker_id": "blocked-test-worker",
                    "status": "BLOCKED",
                    "started_at": CREATED_AT.isoformat(),
                    "completed_at": CREATED_AT.isoformat(),
                    "raw_result": {},
                    "diagnostics": {
                        "error": "policy blocked before target contact",
                        "contacted": False,
                        "self_authorized": False,
                    },
                },
                exit_code=0,
            )

        worker = RecordingWorkerPort(
            store=store,
            handler=blocked_worker,
        )
        use_case, factory, _ = _use_case(store, worker=worker)

        outcome = use_case.execute(_command())

        self.assertEqual(outcome.status, ResearchLoopStatus.NO_OBSERVATION)
        self.assertEqual(outcome.attempt_state, ExecutionAttemptState.COMPLETED.value)
        self.assertEqual(outcome.experiment_execution_state, "BLOCKED")
        self.assertEqual(factory.store.experiments["exp-1"].execution_state, "BLOCKED")
        self.assertEqual(len(factory.store.observations), 0)

        results = list(factory.store.worker_results.values())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "BLOCKED")


class ResultAndFalsePositiveTests(unittest.TestCase):
    def test_completed_diagnostic_enters_transition_a(self) -> None:
        use_case, factory, _ = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(outcome.ingestion_status, IngestionStatus.INGESTED)
        self.assertEqual(len(outcome.observation_ids), 1)
        observation = factory.store.observations[outcome.observation_ids[0]]
        self.assertEqual(observation.payload, {"echoed": "ping"})
        hypothesis = factory.store.hypotheses["hyp-1"]
        self.assertEqual(hypothesis.claim, DIAGNOSTIC_CLAIM)
        self.assertTrue(outcome.hypothesis_claim_unchanged)

    def test_logical_human_seed_loop(self) -> None:
        draft = human_seeded_hypothesis(DIAGNOSTIC_CLAIM)
        self.assertEqual(draft.origin, "human")
        self.assertNotIn("vulnerability", draft.statement)
        use_case, factory, port = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(outcome.core_decision, ExecutionDecisionKind.ALLOW)
        self.assertEqual(port.calls[0]["request"]["worker_capability"], "diagnostic.echo")
        self.assertEqual(factory.store.hypotheses["hyp-1"].claim, draft.statement)
        self.assertIsNone(getattr(outcome, "finding_id", None))
        self.assertTrue(BUDGET_CONSUMPTION_LEDGER_IMPLEMENTED)

    def test_hypothesis_is_not_fact_after_execution_success(self) -> None:
        use_case, factory, _ = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.experiment_execution_state, "EXECUTION_SUCCEEDED")
        self.assertEqual(factory.store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertNotEqual(outcome.status.value, "SUPPORTED")
        self.assertNotEqual(outcome.status.value, "VALIDATED")

    def test_deny_is_not_a_research_conclusion(self) -> None:
        use_case, factory, _ = _use_case()
        outcome = use_case.execute(_command(scope=_deny_scope()))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(factory.store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertNotEqual(outcome.status.value, "REJECTED")


class InvocationFailureTests(unittest.TestCase):
    def test_start_failed_creates_no_observation(self) -> None:
        store = _Store()
        seed_spine(store)
        port = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.START_FAILED),
        )
        use_case, _, _ = _use_case(store, worker=port)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.INVOCATION_FAILED)
        self.assertEqual(outcome.invocation_status, InvocationStatus.START_FAILED)
        self.assertEqual(outcome.observation_ids, ())
        self.assertIsNone(outcome.worker_result_id)
        self.assertEqual(outcome.attempt_state, "FAILED")
        self.assertEqual(outcome.experiment_execution_state, "EXECUTION_FAILED")
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertEqual(len(store.run_faults), 1)
        fault = next(iter(store.run_faults.values()))
        self.assertEqual(fault.fault_code, "INVOCATION_START_FAILED")
        self.assertEqual(fault.attempt_id, outcome.attempt_id)

    def test_timed_out_is_not_hypothesis_rejection(self) -> None:
        store = _Store()
        seed_spine(store)
        port = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT),
        )
        use_case, _, _ = _use_case(store, worker=port)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.attempt_state, "TIMED_OUT")
        self.assertEqual(outcome.experiment_execution_state, "EXECUTION_FAILED")
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)
        self.assertNotEqual(outcome.status.value, "REJECTED")

    def test_process_failed_does_not_reject_hypothesis(self) -> None:
        store = _Store()
        seed_spine(store)
        port = RecordingWorkerPort(
            store=store,
            outcome=invocation_outcome(InvocationStatus.PROCESS_FAILED),
        )
        use_case, _, _ = _use_case(store, worker=port)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.attempt_state, "FAILED")
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)


class UnknownOutcomeTests(unittest.TestCase):
    def test_stalled_dispatching_becomes_unknown_and_is_not_retried(self) -> None:
        use_case, factory, port = _use_case()
        authorized = use_case.authorize(_command())
        assert isinstance(authorized, AuthorizedDispatch)
        with factory.open() as uow:
            uow.execution_attempts.set_state(
                authorized.attempt_id,
                ExecutionAttemptState.DISPATCHING.value,
                dispatch_started_at=CREATED_AT,
            )
            uow.experiments.set_execution_state("exp-1", "RUNNING")
            uow.commit()
        port.calls.clear()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.UNKNOWN_OUTCOME)
        self.assertEqual(outcome.attempt_state, "UNKNOWN_OUTCOME")
        self.assertEqual(outcome.experiment_execution_state, "RUNNING")
        self.assertEqual(len(port.calls), 0)
        self.assertFalse(
            automatic_retry_allowed(attempt_state="UNKNOWN_OUTCOME", side_effect_level=2)
        )
        self.assertFalse(
            automatic_retry_allowed(attempt_state="DISPATCHING", side_effect_level=0)
        )

    def test_authorized_intent_is_not_blindly_retried_by_execute(self) -> None:
        use_case, _, port = _use_case()
        authorized = use_case.authorize(_command())
        self.assertIsInstance(authorized, AuthorizedDispatch)
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.AUTHORIZED_NOT_DISPATCHED)
        self.assertEqual(len(port.calls), 0)

    def test_unknown_outcome_is_not_classified_as_execution_failed(self) -> None:
        use_case, factory, port = _use_case()
        authorized = use_case.authorize(_command())
        assert isinstance(authorized, AuthorizedDispatch)
        with factory.open() as uow:
            uow.execution_attempts.set_state(
                authorized.attempt_id,
                ExecutionAttemptState.DISPATCHING.value,
            )
            uow.commit()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.UNKNOWN_OUTCOME)
        self.assertNotEqual(outcome.experiment_execution_state, "EXECUTION_FAILED")
        self.assertEqual(len(port.calls), 0)


class RateLimitEnforcementTests(unittest.TestCase):
    def test_no_rate_limit_profile_allows_dispatch(self) -> None:
        use_case, _, port = _use_case()
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)

    def test_rate_limit_under_limit_allows_dispatch(self) -> None:
        store = _Store()
        seed_spine(store)
        store.execution_attempts["ea-1"] = ExecutionAttemptRecord(
            attempt_id="ea-1",
            request_id="req-1",
            experiment_id="exp-other",
            research_run_id="run-1",
            correlation_id="corr-1",
            worker_capability="http.transaction",
            action="read",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.AUTHORIZED.value,
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
        )
        use_case, _, port = _use_case(store)
        policy = _rate_limit_policy(max_requests=2, window_seconds=60)
        outcome = use_case.execute(_command(program_policy=policy))
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(port.calls), 1)

    def test_rate_limit_over_limit_denies_dispatch(self) -> None:
        store = _Store()
        seed_spine(store)
        for index in range(2):
            store.execution_attempts[f"ea-{index}"] = ExecutionAttemptRecord(
                attempt_id=f"ea-{index}",
                request_id=f"req-{index}",
                experiment_id="exp-other",
                research_run_id="run-1",
                correlation_id="corr-1",
                worker_capability="http.transaction",
                action="read",
                target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                authorization_decision_reference=f"ad-{index}",
                state=ExecutionAttemptState.AUTHORIZED.value,
                created_at=CREATED_AT,
                authorized_at=CREATED_AT,
            )
        use_case, _, port = _use_case(store)
        policy = _rate_limit_policy(max_requests=2, window_seconds=60)
        outcome = use_case.execute(_command(program_policy=policy))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.RATE_LIMIT_DENIED)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(store.experiments["exp-1"].execution_state, "BLOCKED")
        audit = next(
            (a for a in store.audit_events.values() if a.event_type == "RATE_LIMIT_DENIED"),
            None,
        )
        self.assertIsNotNone(audit)

    def test_rate_limit_zero_max_requests_denies_dispatch(self) -> None:
        store = _Store()
        seed_spine(store)
        use_case, _, port = _use_case(store)
        policy = _rate_limit_policy(max_requests=0, window_seconds=60)
        outcome = use_case.execute(_command(program_policy=policy))
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.RATE_LIMIT_DENIED)
        self.assertEqual(len(port.calls), 0)


class LocalProcessLogicalLoopTests(unittest.TestCase):
    def test_local_diagnostic_worker_completes_the_logical_loop(self) -> None:
        from pathlib import Path

        from zest.platform.local_process_worker import (
            LocalProcessWorkerAdapter,
            LocalProcessWorkerConfig,
        )

        repo = Path(__file__).resolve().parents[3]
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store)
        inner = LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                workers_python_path=repo / "workers" / "python",
                default_timeout_ms=5_000,
            )
        )
        port = RecordingWorkerPort(store=store, inner=inner)
        use_case = ExecutePlannedExperiment(factory, port, clock=FixedClock())
        outcome = use_case.execute(_command())
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(len(outcome.observation_ids), 1)
        self.assertEqual(store.hypotheses["hyp-1"].claim, DIAGNOSTIC_CLAIM)


def _rate_limit_policy(max_requests: int, window_seconds: int) -> ProgramPolicyView:
    profile = RateLimitProfileRecord(
        profile_id="rl-1",
        program_id="prog-1",
        max_requests_per_window=max_requests,
        window_seconds=window_seconds,
        created_at=CREATED_AT,
    )
    return ProgramPolicyView(
        loopback_fixture=False,
        max_response_bytes=4096,
        timeout_ms=2000,
        action_policy={},
        rate_limit_profile=profile,
    )


if __name__ == "__main__":
    unittest.main()
