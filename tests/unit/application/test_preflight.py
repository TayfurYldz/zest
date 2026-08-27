from __future__ import annotations

import unittest
from datetime import timedelta

import pathsetup  # noqa: F401

from zest.application.preflight import (
    ModelReadinessInput,
    Preflight,
    PreflightCheckName,
    PreflightCommand,
    PreflightStatus,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.core.enums import ScopeRuleEffect
from zest.data.records import (
    ExecutionAttemptRecord,
    ResearchOrchestrationRecord,
    ScopeRuleV2Record,
)
from zest.platform.health import ComponentHealth, HealthCheck
from zest.research.model_runtime import api_runtime_identity
from zest.research.routing import CandidateLocality, RuntimeCandidate
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run

TARGET = "https://example.com/"


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_target(store: _Store, *, host: str = "example.com") -> None:
    store.scope_rules_v2["rule-allow"] = ScopeRuleV2Record(
        rule_id="rule-allow",
        program_id="prog-1",
        effect=ScopeRuleEffect.ALLOW,
        scheme="https",
        host=host,
        source_reference="scope-src",
        created_at=CREATED_AT,
    )


def _healthy_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "diagnostic echo ok"),
        available_capabilities=frozenset({"diagnostic.echo"}),
    )


def _healthy_browser_worker() -> WorkerReadinessInput:
    return WorkerReadinessInput(
        health=HealthCheck("worker", ComponentHealth.HEALTHY, "diagnostic echo ok"),
        available_capabilities=frozenset({"diagnostic.echo", "browser.page"}),
        browser_containment=HealthCheck("browser-containment", ComponentHealth.HEALTHY, "ready"),
    )


def _healthy_model() -> ModelReadinessInput:
    candidate = RuntimeCandidate(
        identity=api_runtime_identity(adapter_id="openai.responses", runtime_id="openai.responses"),
        available=True,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.REMOTE,
    )
    return ModelReadinessInput(
        candidate=candidate,
        health=HealthCheck("model", ComponentHealth.HEALTHY, "auth ok"),
    )


def _ok_schema() -> SchemaHealthInput:
    return SchemaHealthInput(at_expected_head=True, detail="ok")


def _command(**overrides) -> PreflightCommand:
    values = dict(
        research_run_id="run-1",
        target_reference=TARGET,
        schema=_ok_schema(),
        worker=_healthy_worker(),
        model=_healthy_model(),
        required_worker_capabilities=frozenset({"diagnostic.echo"}),
    )
    values.update(overrides)
    return PreflightCommand(**values)


def _checks_by_name(report, name: PreflightCheckName):
    return [c for c in report.checks if c.name is name]


class _RaisingUnitOfWorkFactory:
    def open(self):  # noqa: D401 - simulates a dead connection pool
        raise ConnectionError("could not connect to server")


class PreflightTests(unittest.TestCase):
    def _store(self) -> _Store:
        store = _Store()
        seed_authorization_run(store)
        _allow_target(store)
        return store

    def test_all_green_inputs_are_ready_to_start(self) -> None:
        store = self._store()
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.READY_TO_START)
        self.assertTrue(report.is_ready)
        self.assertEqual(report.reasons, ())
        self.assertTrue(all(c.passed for c in report.checks))

    def test_missing_authorization_source_denies(self) -> None:
        store = self._store()
        del store.authorization_sources["as-1"]
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        checks = _checks_by_name(report, PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE)
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].passed)

    def test_inactive_authorization_source_denies(self) -> None:
        store = self._store()
        store.authorization_sources["as-1"] = store.authorization_sources["as-1"].__class__(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="REVOKED",
            provenance_reference="letter-1",
            created_at=CREATED_AT,
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE)[0].passed)

    def test_expired_authorization_source_denies(self) -> None:
        store = self._store()
        store.authorization_sources["as-1"] = store.authorization_sources["as-1"].__class__(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="letter-1",
            created_at=CREATED_AT,
            effective_from=CREATED_AT - timedelta(days=30),
            effective_until=CREATED_AT - timedelta(days=1),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE)[0].passed)

    def test_not_yet_effective_authorization_source_denies(self) -> None:
        store = self._store()
        store.authorization_sources["as-1"] = store.authorization_sources["as-1"].__class__(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="letter-1",
            created_at=CREATED_AT,
            effective_from=CREATED_AT + timedelta(days=1),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE)[0].passed)

    def test_target_not_in_scope_denies_without_touching_scope_compiles(self) -> None:
        store = _Store()
        seed_authorization_run(store)  # no scope rules seeded at all
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertTrue(_checks_by_name(report, PreflightCheckName.SCOPE_COMPILES)[0].passed)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.TARGET_IN_SCOPE)[0].passed)

    def test_ambiguous_target_normalization_denies(self) -> None:
        store = self._store()
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(target_reference="not a url")
        )
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.TARGET_IN_SCOPE)[0].passed)

    def test_missing_issued_budget_denies(self) -> None:
        store = self._store()
        del store.issued_budgets["budget-1"]
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.BUDGET_AVAILABLE)[0].passed)

    def test_exhausted_budget_denies(self) -> None:
        from zest.data.records import BudgetConsumptionRecord

        store = self._store()
        store.budget_consumptions["c-1"] = BudgetConsumptionRecord(
            consumption_id="c-1",
            budget_id="budget-1",
            research_run_id="run-1",
            experiment_id=None,
            request_id="req-1",
            resource_type="REQUEST",
            amount=1,
            unit="count",
            occurred_at=CREATED_AT,
            provenance="test",
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.BUDGET_AVAILABLE)[0].passed)

    def test_terminal_orchestration_denies(self) -> None:
        store = self._store()
        store.research_orchestrations["run-1"] = _orchestration(state="COMPLETED", stop_reason="MAX_CYCLES_REACHED")
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        recoverable_checks = _checks_by_name(report, PreflightCheckName.ORCHESTRATION_RECOVERABLE)
        self.assertTrue(any(not c.passed for c in recoverable_checks))

    def test_non_terminal_orchestration_is_fine(self) -> None:
        store = self._store()
        store.research_orchestrations["run-1"] = _orchestration(state="RUNNING")
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        recoverable_checks = _checks_by_name(report, PreflightCheckName.ORCHESTRATION_RECOVERABLE)
        self.assertTrue(all(c.passed for c in recoverable_checks))

    def test_blocking_reconciliation_item_denies(self) -> None:
        store = self._store()
        store.execution_attempts["ea-1"] = ExecutionAttemptRecord(
            attempt_id="ea-1",
            request_id="req-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="corr-1",
            worker_capability="diagnostic.echo",
            action="echo",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=2,
            authorization_decision_reference="ae-1",
            state="UNKNOWN_OUTCOME",
            created_at=CREATED_AT,
            authorized_at=CREATED_AT,
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        recoverable_checks = _checks_by_name(report, PreflightCheckName.ORCHESTRATION_RECOVERABLE)
        self.assertTrue(any(not c.passed for c in recoverable_checks))

    def test_live_lease_held_by_another_owner_denies(self) -> None:
        store = self._store()
        store.research_orchestrations["run-1"] = _orchestration(
            state="RUNNING",
            owner_runtime_instance_id="owner-a",
            lease_epoch=1,
            lease_expires_at=CREATED_AT + timedelta(seconds=90),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(requesting_owner_runtime_instance_id="owner-b")
        )
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.NO_CONFLICTING_LEASE)[0].passed)

    def test_live_lease_held_by_requesting_owner_is_fine(self) -> None:
        store = self._store()
        store.research_orchestrations["run-1"] = _orchestration(
            state="RUNNING",
            owner_runtime_instance_id="owner-a",
            lease_epoch=1,
            lease_expires_at=CREATED_AT + timedelta(seconds=90),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(requesting_owner_runtime_instance_id="owner-a")
        )
        self.assertTrue(_checks_by_name(report, PreflightCheckName.NO_CONFLICTING_LEASE)[0].passed)

    def test_expired_lease_is_not_a_conflict(self) -> None:
        store = self._store()
        store.research_orchestrations["run-1"] = _orchestration(
            state="RUNNING",
            owner_runtime_instance_id="owner-a",
            lease_epoch=1,
            lease_expires_at=CREATED_AT - timedelta(seconds=1),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(requesting_owner_runtime_instance_id="owner-b")
        )
        self.assertTrue(_checks_by_name(report, PreflightCheckName.NO_CONFLICTING_LEASE)[0].passed)

    def test_missing_required_worker_capability_denies(self) -> None:
        store = self._store()
        command = _command(
            worker=WorkerReadinessInput(
                health=HealthCheck("worker", ComponentHealth.HEALTHY, "ok"),
                available_capabilities=frozenset(),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.WORKER_CAPABILITIES_PRESENT)[0].passed)

    def test_browser_discovery_requires_resource_containment(self) -> None:
        store = self._store()
        command = _command(
            worker=WorkerReadinessInput(
                health=HealthCheck("worker", ComponentHealth.HEALTHY, "diagnostic echo ok"),
                available_capabilities=frozenset({"diagnostic.echo", "browser.page"}),
                browser_containment=HealthCheck(
                    "browser-containment", ComponentHealth.UNAVAILABLE, "delegation unavailable"
                ),
            ),
            required_worker_capabilities=frozenset({"browser.page"}),
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        check = _checks_by_name(report, PreflightCheckName.BROWSER_RESOURCE_CONTAINMENT)[0]
        self.assertFalse(check.passed)

    def test_browser_discovery_is_ready_when_containment_is_ready(self) -> None:
        store = self._store()
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(
                worker=_healthy_browser_worker(),
                required_worker_capabilities=frozenset({"browser.page"}),
            )
        )
        self.assertEqual(report.status, PreflightStatus.READY_TO_START)
        self.assertTrue(_checks_by_name(report, PreflightCheckName.BROWSER_RESOURCE_CONTAINMENT)[0].passed)

    def test_non_browser_preflight_does_not_require_containment_probe(self) -> None:
        store = self._store()
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.READY_TO_START)
        self.assertEqual(_checks_by_name(report, PreflightCheckName.BROWSER_RESOURCE_CONTAINMENT), [])

    def test_unavailable_worker_denies(self) -> None:
        store = self._store()
        command = _command(
            worker=WorkerReadinessInput(
                health=HealthCheck("worker", ComponentHealth.UNAVAILABLE, "process failed to start"),
                available_capabilities=frozenset({"diagnostic.echo"}),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.WORKER_RUNTIME_HEALTHY)[0].passed)

    def test_no_model_candidate_denies(self) -> None:
        store = self._store()
        command = _command(
            model=ModelReadinessInput(
                candidate=None,
                health=HealthCheck("model", ComponentHealth.UNAVAILABLE, "not configured"),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.MODEL_RUNTIME_READY)[0].passed)

    def test_model_auth_failure_denies(self) -> None:
        store = self._store()
        candidate = RuntimeCandidate(
            identity=api_runtime_identity(adapter_id="openai.responses", runtime_id="openai.responses"),
            available=True,
            authenticated=False,
            structured_output_compatible=True,
        )
        command = _command(
            model=ModelReadinessInput(
                candidate=candidate,
                health=HealthCheck("model", ComponentHealth.AUTH_REQUIRED, "token expired"),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.MODEL_RUNTIME_READY)[0].passed)

    def test_model_rate_limited_denies(self) -> None:
        store = self._store()
        command = _command(
            model=ModelReadinessInput(
                candidate=_healthy_model().candidate,
                health=HealthCheck("model", ComponentHealth.RATE_LIMITED, "429 backoff active"),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.MODEL_RUNTIME_READY)[0].passed)

    def test_model_not_structured_output_compatible_denies(self) -> None:
        store = self._store()
        candidate = RuntimeCandidate(
            identity=api_runtime_identity(adapter_id="openai.responses", runtime_id="openai.responses"),
            available=True,
            authenticated=True,
            structured_output_compatible=False,
        )
        command = _command(
            model=ModelReadinessInput(
                candidate=candidate,
                health=HealthCheck("model", ComponentHealth.HEALTHY, "ok"),
            )
        )
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.MODEL_RUNTIME_READY)[0].passed)

    def test_schema_mismatch_denies(self) -> None:
        store = self._store()
        command = _command(schema=SchemaHealthInput(at_expected_head=False, detail="drifted"))
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.SCHEMA_AT_EXPECTED_HEAD)[0].passed)

    def test_missing_research_run_denies_but_still_runs_independent_checks(self) -> None:
        store = _Store()
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            _command(research_run_id="does-not-exist")
        )
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertFalse(_checks_by_name(report, PreflightCheckName.RUN_CONFIGURATION_EXISTS)[0].passed)
        # Checks that do not depend on the run still ran and passed.
        self.assertTrue(_checks_by_name(report, PreflightCheckName.SCHEMA_AT_EXPECTED_HEAD)[0].passed)
        self.assertTrue(_checks_by_name(report, PreflightCheckName.WORKER_RUNTIME_HEALTHY)[0].passed)
        self.assertTrue(_checks_by_name(report, PreflightCheckName.MODEL_RUNTIME_READY)[0].passed)
        # No fabricated checks for data that was never loaded.
        self.assertEqual(_checks_by_name(report, PreflightCheckName.AUTHORIZATION_SOURCE_ACTIVE), [])

    def test_unreachable_database_denies_closed_and_short_circuits(self) -> None:
        report = Preflight(_RaisingUnitOfWorkFactory(), clock=FixedClock()).execute(_command())
        self.assertEqual(report.status, PreflightStatus.NOT_READY)
        self.assertEqual(len(report.checks), 1)
        self.assertFalse(report.checks[0].passed)
        self.assertEqual(report.checks[0].name, PreflightCheckName.DATABASE_REACHABLE)

    def test_reasons_lists_only_failing_checks(self) -> None:
        store = self._store()
        command = _command(schema=SchemaHealthInput(at_expected_head=False, detail="drifted"))
        report = Preflight(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(command)
        self.assertEqual(len(report.reasons), 1)
        self.assertIn("SCHEMA_AT_EXPECTED_HEAD", report.reasons[0])
        self.assertIn("drifted", report.reasons[0])


def _orchestration(**overrides) -> ResearchOrchestrationRecord:
    values = dict(
        research_run_id="run-1",
        state="RUNNING",
        cycle_number=1,
        last_phase="running",
        policy_version="orchestration.bounded.v1",
        max_cycles=3,
        max_experiments=3,
        max_model_calls=12,
        max_worker_invocations=3,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=False,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        checkpoint_at=CREATED_AT,
        budget_id="budget-1",
        target_reference="target-1",
        research_question="q",
        configuration_fingerprint="0" * 64,
        current_phase="CYCLE_READY",
    )
    values.update(overrides)
    return ResearchOrchestrationRecord(**values)


if __name__ == "__main__":
    unittest.main()
