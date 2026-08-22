from __future__ import annotations

import unittest
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from research_os.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from research_os.data.records import AuthorizationSourceRecord, IssuedBudgetRecord
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.exploration import OpportunityKind, ResearchPolicyBudget
from research_os.research.identity_anomaly import (
    IDENTITY_ANOMALY_CLAIM,
    is_exploratory_hypothesis_origin,
)
from research_os.research.orchestration import OrchestrationBounds
from research_os.research.planning import plan_authorization_differential
from research_os.tools.capabilities import HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_spine


ORIGIN = "http://127.0.0.1:9"


class FixedClock:
    def now(self):
        return CREATED_AT


class NeverInvokedModel:
    def complete(self, request):
        raise AssertionError(
            "ModelPort must not be invoked for registry-external identity admission"
        )


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(origin: str):
    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix="/",
                source_reference="scope-src",
            ),
        )
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=1,
        max_experiments=6,
        max_model_calls=20,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _authz_handler(mode: str):
    def handler(request):
        args = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        actor = str(args.get("actor") or "alice")
        own = str(args.get("own_object") or "alice")
        cross = str(args.get("cross_object") or "bob")
        origin = str(args.get("authorized_origin") or ORIGIN)
        if mode == "vulnerable":
            cross_status, cross_owner = 200, cross
        elif mode == "secure_only":
            cross_status, cross_owner = 403, None
        else:
            cross_status, cross_owner = 200, actor
        correlation = request.get("correlation") if isinstance(request.get("correlation"), dict) else {}
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=CREATED_AT,
            completed_at=CREATED_AT,
            worker_result={
                "contract_version": "v1",
                "correlation": dict(correlation),
                "worker_id": "local-python-http",
                "status": "SUCCEEDED",
                "started_at": "2026-08-22T00:00:00Z",
                "completed_at": "2026-08-22T00:00:01Z",
                "raw_result": {
                    "mode": mode if mode in {"vulnerable", "secure_only", "redirect"} else "vulnerable",
                    "authorized_origin": origin,
                    "owner_request": {"status": 200, "object_owner": own},
                    "cross_object_request": {
                        "status": cross_status,
                        "object_owner": cross_owner,
                        **(
                            {"object_visibility": "PUBLIC"}
                            if mode == "deceptive"
                            else {}
                        ),
                    },
                    "secure_control": {"status": 403},
                    "unauthenticated_control": {"status": 401},
                },
            },
            exit_code=0,
        )

    return handler


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
        selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
        compiled_scope=_compiled_scope(ORIGIN),
        research_question="Is unexplained identity divergence an unmodelled access invariant?",
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _produce_source(store: _Store, *, mode: str = "vulnerable") -> RecordingWorkerPort:
    seed_spine(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=40,
        max_tool_calls=40,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    worker = RecordingWorkerPort(store=store, handler=_authz_handler(mode))
    ExecutePlannedExperiment(
        FakeUnitOfWorkFactory(store), worker, clock=FixedClock()
    ).execute(
        ExecutePlannedExperimentCommand(
            experiment_id="exp-1",
            plan=plan_authorization_differential(
                "hyp-1",
                budget_id="budget-1",
                target_reference="target-1",
                authorized_origin=ORIGIN,
                actor="alice",
                own_object="alice",
                cross_object="bob",
                mode="vulnerable" if mode == "deceptive" else mode,
            ),
            scope=_allow_scope(),
            compiled_scope=_compiled_scope(ORIGIN),
        )
    )
    return worker


class ArcRegistryExternalAnomalyTests(unittest.TestCase):
    def test_source_probe_then_arc_selects_exploratory_without_model_or_compile_args(self) -> None:
        store = _Store()
        source_worker = _produce_source(store, mode="vulnerable")
        self.assertGreaterEqual(len(source_worker.calls), 1)
        self.assertTrue(store.observations)
        kinds = {item.observation_kind for item in store.observations.values()}
        self.assertIn("HTTP_AUTHORIZATION_DIFFERENTIAL", kinds)

        worker = RecordingWorkerPort(store=store, handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            FakeUnitOfWorkFactory(store),
            worker,
            NeverInvokedModel(),
            clock=FixedClock(),
        )
        controller.start(_command())
        result = controller.step(_command())

        exploratory = [
            item
            for item in store.research_opportunities.values()
            if item.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
        ]
        self.assertEqual(len(exploratory), 1)
        self.assertIsNotNone(result.hypothesis_id)
        hypothesis = store.hypotheses[result.hypothesis_id]
        self.assertTrue(is_exploratory_hypothesis_origin(hypothesis.origin_reference))
        self.assertEqual(hypothesis.claim, IDENTITY_ANOMALY_CLAIM)
        self.assertIsNotNone(result.experiment_id)
        plan = store.experiment_plans[result.experiment_id]
        self.assertEqual(plan.required_capability, HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY)
        self.assertEqual(plan.arguments["actor"], "alice")
        self.assertEqual(plan.arguments["cross_object"], "bob")
        self.assertNotIn("body", plan.arguments)
        self.assertGreaterEqual(len(worker.calls), 1)
        self.assertNotIn("ExploratorySignalInput", store.hypotheses[result.hypothesis_id].claim)
        self.assertEqual(len(store.findings), 0)

    def test_ten_cycles_do_not_duplicate_exploratory_hypothesis(self) -> None:
        store = _Store()
        _produce_source(store, mode="vulnerable")
        controller = AutonomousResearchController(
            FakeUnitOfWorkFactory(store),
            RecordingWorkerPort(store=store, handler=_authz_handler("vulnerable")),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        command = _command(bounds=_bounds(max_cycles=12, max_experiments=20))
        controller.start(command)
        for _ in range(10):
            controller.step(command)
        exploratory_hyps = [
            item
            for item in store.hypotheses.values()
            if is_exploratory_hypothesis_origin(item.origin_reference)
        ]
        self.assertEqual(len(exploratory_hyps), 1)
        exploratory_ops = [
            item
            for item in store.research_opportunities.values()
            if item.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
        ]
        self.assertEqual(len(exploratory_ops), 1)

    def test_out_of_scope_compiled_scope_does_not_dispatch_worker(self) -> None:
        store = _Store()
        _produce_source(store, mode="vulnerable")
        worker = RecordingWorkerPort(store=store, handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            FakeUnitOfWorkFactory(store),
            worker,
            NeverInvokedModel(),
            clock=FixedClock(),
        )
        oos = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-other",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="http",
                    host="other.example",
                    port=9,
                    path_prefix="/",
                    source_reference="scope-src",
                ),
            )
        )
        command = _command(compiled_scope=oos)
        controller.start(command)
        result = controller.step(command)
        self.assertEqual(len(worker.calls), 0)
        self.assertEqual(result.stop_reason, "CORE_BLOCKED")

    def test_expired_authorization_source_does_not_dispatch_worker(self) -> None:
        store = _Store()
        _produce_source(store, mode="vulnerable")
        current = store.authorization_sources["as-1"]
        store.authorization_sources["as-1"] = AuthorizationSourceRecord(
            authorization_source_id=current.authorization_source_id,
            program_id=current.program_id,
            state="EXPIRED",
            provenance_reference=current.provenance_reference,
            created_at=current.created_at,
            effective_from=current.effective_from,
            effective_until=current.effective_until,
        )
        worker = RecordingWorkerPort(store=store, handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            FakeUnitOfWorkFactory(store),
            worker,
            NeverInvokedModel(),
            clock=FixedClock(),
        )
        command = _command()
        controller.start(command)
        result = controller.step(command)
        self.assertEqual(len(worker.calls), 0)
        self.assertEqual(result.stop_reason, "CORE_BLOCKED")

    def test_arc_does_not_import_execute_exploratory_research(self) -> None:
        import inspect
        from research_os.application import autonomous_research_controller as module

        source = inspect.getsource(module)
        self.assertNotIn("execute_exploratory_research", source)
        self.assertNotIn("ExploratorySignalInput", source)
        self.assertNotIn("compile_arguments", source)


if __name__ == "__main__":
    unittest.main()
