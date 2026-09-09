"""Phase 6.6 Differential + Invariant + Chain production acceptance."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.dic_sources import (
    ChainWorkSource,
    DifferentialWorkSource,
    InvariantWorkSource,
)
from zest.application.evaluate_dic import (
    CHAIN_EVALUATED,
    DIFFERENTIAL_EVALUATED,
    INVARIANT_EVALUATED,
)
from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.research_work_planners import default_research_work_planner_registry
from zest.application.research_work_sources import default_research_work_sources
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.core.enums import ActorType, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import (
    AuditEventRecord,
    ExperimentPlanRecord,
    HypothesisAssessmentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ObservationRecord,
    WorkerResultRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.evaluators.causal_chain import ChainLinkage, ChainNodeView, ChainStepStatus, evaluate_chain_linkage
from zest.research.evaluators.controlled_differential import (
    DifferentialSecurityJudgement,
    compare_controlled_payloads,
)
from zest.research.evaluators.security_invariant import InvariantCheckOutcome, evaluate_access_deny
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.orchestration import OrchestrationBounds
from zest.research.scheduler.fairness import is_plumbing_opportunity
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, STARTED_AT, COMPLETED_AT
from support.spine import CREATED_AT, seed_authorization_run


class FixedClock:
    def now(self):
        return CREATED_AT


def _compiled_scope() -> CompiledScope:
    return CompiledScope(
        rules=(
            CompiledScopeRule(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme="http",
                host="127.0.0.1",
                host_pattern=None,
                port=9,
                path_prefix=None,
                source_reference="scope-src",
                expires_at=None,
            ),
        )
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=12,
        max_experiments=12,
        max_model_calls=50,
        max_worker_invocations=12,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=2,
        allow_repeated_control_experiments=False,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="http://127.0.0.1:9/",
        scope=ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
            ambiguous=False,
        ),
        bounds=_bounds(),
        compiled_scope=_compiled_scope(),
        selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _seed_base(store: _Store) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=40,
        max_tool_calls=40,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )


def _http_pair(
    store: _Store,
    *,
    left_status: int,
    right_status: int,
    left_headers: dict | None = None,
    right_headers: dict | None = None,
    left_body: str = "alice-data",
    right_body: str = "alice-data",
    path: str = "/orders/1",
    left_identity: str = "id-alice",
    right_identity: str = "id-bob",
) -> None:
    _seed_observation(
        store,
        suffix="left",
        identity=left_identity,
        path=path,
        status=left_status,
        headers=left_headers or {},
        body=left_body,
        session=True,
    )
    _seed_observation(
        store,
        suffix="right",
        identity=right_identity,
        path=path,
        status=right_status,
        headers=right_headers or {},
        body=right_body,
        session=bool(right_identity),
    )


def _seed_observation(
    store: _Store,
    *,
    suffix: str,
    identity: str,
    path: str,
    status: int,
    headers: dict,
    body: str,
    session: bool,
) -> None:
    experiment_id = f"exp-{suffix}"
    result_id = f"wr-{suffix}"
    store.hypotheses.setdefault(
        f"hyp-{suffix}",
        HypothesisRecord(
            hypothesis_id=f"hyp-{suffix}",
            research_run_id="run-1",
            claim="controlled observation exists",
            created_at=CREATED_AT,
        ),
    )
    store.experiment_plans[experiment_id] = ExperimentPlanRecord(
        experiment_id=experiment_id,
        research_run_id="run-1",
        hypothesis_id=f"hyp-{suffix}",
        required_capability="http.transaction",
        action="read",
        target_reference="http://127.0.0.1:9/",
        side_effect_level=0,
        arguments={
            "authorized_origin": "http://127.0.0.1:9",
            "method": "GET",
            "path": path,
            "identity_id": identity,
            **({"session_context_reference": f"sess-{suffix}"} if session else {}),
        },
        requested_budget_id="budget-1",
        expected_observation="http facts",
        disconfirming_observation="no http facts",
        evaluation_strategy="http.transaction.v1",
        created_at=CREATED_AT,
    )
    store.worker_results[result_id] = WorkerResultRecord(
        worker_result_id=result_id,
        experiment_id=experiment_id,
        research_run_id="run-1",
        request_id=f"req-{suffix}",
        correlation_id=f"corr-{suffix}",
        worker_capability="http.transaction",
        action="read",
        authorization_decision_reference=f"authz-{suffix}",
        budget_id="budget-1",
        side_effect_level=0,
        contract_version="v1",
        worker_id="local-python-http.transaction",
        status="SUCCEEDED",
        received_at=CREATED_AT,
    )
    store.observations[f"obs-{suffix}"] = ObservationRecord(
        observation_id=f"obs-{suffix}",
        worker_result_id=result_id,
        observation_kind="HTTP_TRANSACTION",
        payload={
            "status_code": status,
            "method": "GET",
            "path": path,
            "body": body,
            "response_headers": headers,
        },
        normalization_version="http.transaction.v1",
        observed_at=CREATED_AT,
        created_at=CREATED_AT,
    )


def _seed_assessments(store: _Store, *, same_path: bool) -> None:
    store.hypotheses["hyp-a"] = HypothesisRecord(
        hypothesis_id="hyp-a",
        research_run_id="run-1",
        claim="authz differential observed",
        created_at=CREATED_AT,
    )
    store.hypotheses["hyp-b"] = HypothesisRecord(
        hypothesis_id="hyp-b",
        research_run_id="run-1",
        claim="workflow transition observed",
        created_at=CREATED_AT,
    )
    for suffix, path, strategy in (
        ("a", "/orders/1", "http.authorization.differential.v1"),
        ("b", "/orders/1" if same_path else "/settings", "http.state_transition.v1"),
    ):
        store.experiment_plans[f"exp-{suffix}"] = ExperimentPlanRecord(
            experiment_id=f"exp-{suffix}",
            research_run_id="run-1",
            hypothesis_id=f"hyp-{suffix}",
            required_capability="http.transaction",
            action="read",
            target_reference="http://127.0.0.1:9/",
            side_effect_level=0,
            arguments={"authorized_origin": "http://127.0.0.1:9", "method": "GET", "path": path},
            requested_budget_id="budget-1",
            expected_observation="http facts",
            disconfirming_observation="no http facts",
            evaluation_strategy=strategy,
            created_at=CREATED_AT,
        )
        store.hypothesis_assessments[f"assess-{suffix}"] = HypothesisAssessmentRecord(
            assessment_id=f"assess-{suffix}",
            hypothesis_id=f"hyp-{suffix}",
            experiment_id=f"exp-{suffix}",
            research_run_id="run-1",
            assessment_outcome="CONSISTENT_WITH_PREDICTION",
            observation_ids=(),
            evaluator_kind="DETERMINISTIC",
            evaluator_version=strategy,
            rationale={"reason_code": "SEEDED_ASSESSMENT"},
            evaluation_strategy=strategy,
            created_at=CREATED_AT,
        )


def _http_handler(status_code: int):
    def handler(request):
        cap = request.get("worker_capability")
        arguments = request.get("arguments") or {}
        correlation = dict(request.get("correlation") or {})
        if cap == "http.transaction":
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result={
                    "contract_version": "v1",
                    "correlation": correlation,
                    "worker_id": "local-python-http.transaction",
                    "status": "SUCCEEDED",
                    "started_at": "2026-08-16T20:00:00Z",
                    "completed_at": "2026-08-16T20:00:01Z",
                    "raw_result": {
                        "status_code": status_code,
                        "method": arguments.get("method") or "GET",
                        "path": arguments.get("path") or "/",
                        "authorized_origin": arguments.get("authorized_origin"),
                        "body_length": 0,
                        "body_digest": "abc",
                    },
                },
                exit_code=0,
            )
        raise AssertionError(f"unexpected capability {cap}")

    return handler


def _controller(store: _Store, *, status_code: int = 403):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store, handler=_http_handler(status_code))
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock()
    )
    return controller, port


def _run_until_event(controller, store, command, event_type: str, limit: int = 8):
    controller.start(command)
    last = None
    for _ in range(limit):
        last = controller.step(command)
        if any(item.event_type == event_type for item in store.audit_events.values()):
            return last
    return last


def _kinds(store: _Store, kind: str):
    return [
        item
        for item in store.opportunity_selection_candidates.values()
        if item.opportunity_kind == kind
    ]


class Phase66DicTests(unittest.TestCase):
    def test_d1_v1_c1_census(self) -> None:
        sources = {item.source_id for item in default_research_work_sources()}
        self.assertEqual(sources & {"DIFFERENTIAL", "INVARIANT", "CHAIN"}, {"DIFFERENTIAL", "INVARIANT", "CHAIN"})
        registry = default_research_work_planner_registry()
        self.assertEqual(registry.planner_for("DIFFERENTIAL").source_engine, "DIFFERENTIAL")
        self.assertEqual(registry.planner_for("INVARIANT").source_engine, "INVARIANT")
        self.assertEqual(registry.planner_for("CHAIN").source_engine, "CHAIN")

    def test_d2_d3_d4_d5_d6_d7_d8_direct_eval_negative(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(
            store,
            left_status=200,
            right_status=200,
            left_headers={"date": "Thu, 01 Jan 2026 00:00:00 GMT"},
            right_headers={"date": "Thu, 01 Jan 2026 00:00:01 GMT"},
            left_body="same",
            right_body="same",
        )
        SelectResearchOpportunities(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(research_run_id="run-1")
        )
        diffs = _kinds(store, OpportunityKind.DIFFERENTIAL.value)
        self.assertTrue(diffs)
        self.assertEqual(diffs[0].source_refs, ("obs-left", "obs-right"))

    def test_d4_d5_d6_d7_d8_scheduler_native_eval_negative(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(
            store,
            left_status=200,
            right_status=200,
            left_headers={"date": "Thu, 01 Jan 2026 00:00:00 GMT"},
            right_headers={"date": "Thu, 01 Jan 2026 00:00:01 GMT"},
            left_body="same",
            right_body="same",
        )
        controller, port = _controller(store)
        _run_until_event(controller, store, _command(), DIFFERENTIAL_EVALUATED)
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("selected_engine") == "DIFFERENTIAL"
        ]
        self.assertTrue(compiled)
        self.assertEqual(compiled[-1].payload.get("compile_status"), "EVALUATE_EXISTING")
        self.assertFalse(port.calls)
        evaluated = [item for item in store.audit_events.values() if item.event_type == DIFFERENTIAL_EVALUATED]
        self.assertTrue(evaluated)
        self.assertIn(evaluated[-1].payload.get("judgement"), {"EQUIVALENT", "NOISE_ONLY"})
        self.assertEqual(evaluated[-1].payload.get("evidence_pipeline"), "EVIDENCE_PIPELINE_PENDING")
        self.assertEqual(len(store.evidence), 0)

    def test_d9_positive_control(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(store, left_status=200, right_status=200, left_body="alice-secret", right_body="alice-secret-from-bob")
        controller, _ = _controller(store)
        _run_until_event(controller, store, _command(), DIFFERENTIAL_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == DIFFERENTIAL_EVALUATED]
        self.assertEqual(evaluated[-1].payload.get("judgement"), "CONTROLLED_SIGNAL")
        self.assertEqual(evaluated[-1].payload.get("assessment"), "SIGNAL_RECORDED")
        self.assertEqual(len(store.evidence), 0)

    def test_d10_v9_c10_feedback(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(store, left_status=200, right_status=403, left_body="own", right_body="denied")
        controller, _ = _controller(store)
        _run_until_event(controller, store, _command(), "DIC_COVERAGE_UPDATED")
        self.assertTrue(any(item.event_type == "DIC_COVERAGE_UPDATED" for item in store.audit_events.values()))
        self.assertTrue(any(item.event_type == "DIC_MODEL_CONTEXT_READY" for item in store.audit_events.values()))

    def test_d11_v10_c11_dedupe(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(store, left_status=200, right_status=403)
        factory = FakeUnitOfWorkFactory(store)
        with factory.open() as uow:
            a = DifferentialWorkSource().harvest(uow, research_run_id="run-1", now=CREATED_AT)
            b = DifferentialWorkSource().harvest(uow, research_run_id="run-1", now=CREATED_AT)
            uow.commit()
        self.assertGreaterEqual(a.created, 1)
        self.assertEqual(b.created, 0)
        self.assertGreaterEqual(b.skipped_duplicate, 1)
        self.assertEqual(len(_kinds(store, OpportunityKind.DIFFERENTIAL.value)), a.created)

    def test_d12_v11_c12_recovery_no_duplicate_eval(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(store, left_status=200, right_status=403)
        controller, _ = _controller(store)
        _run_until_event(controller, store, _command(), DIFFERENTIAL_EVALUATED)
        first = [item for item in store.audit_events.values() if item.event_type == DIFFERENTIAL_EVALUATED]
        controller.step(_command())
        second = [item for item in store.audit_events.values() if item.event_type == DIFFERENTIAL_EVALUATED]
        self.assertEqual(len(first), len(second))
        self.assertEqual(len(store.differential_observations), 1)

    def test_v2_v3_v4_v5_holds(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_observation(
            store, suffix="authed", identity="id-alice", path="/me", status=200, headers={}, body="me", session=True
        )
        _seed_observation(
            store, suffix="anon", identity="", path="/me", status=401, headers={}, body="denied", session=False
        )
        controller, port = _controller(store)
        _run_until_event(controller, store, _command(), INVARIANT_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == INVARIANT_EVALUATED]
        self.assertTrue(evaluated)
        self.assertEqual(evaluated[-1].payload.get("outcome"), "HOLDS")
        self.assertEqual(len(store.evidence), 0)
        self.assertFalse(any(call["request"]["worker_capability"] == "diagnostic.echo" for call in port.calls))

    def test_v6_violation(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_observation(
            store, suffix="authed", identity="id-alice", path="/me", status=200, headers={}, body="me", session=True
        )
        _seed_observation(
            store, suffix="anon", identity="", path="/me", status=200, headers={}, body="me", session=False
        )
        controller, _ = _controller(store)
        _run_until_event(controller, store, _command(), INVARIANT_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == INVARIANT_EVALUATED]
        self.assertEqual(evaluated[-1].payload.get("outcome"), "VIOLATED")
        self.assertEqual(len(store.evidence), 0)

    def test_v7_unknown_insufficient(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_observation(
            store, suffix="authed", identity="id-alice", path="/me", status=200, headers={}, body="me", session=True
        )
        inv = []
        with FakeUnitOfWorkFactory(store).open() as uow:
            InvariantWorkSource().harvest(uow, research_run_id="run-1", now=CREATED_AT)
            uow.commit()
        inv = _kinds(store, OpportunityKind.INVARIANT.value)
        self.assertTrue(inv)
        self.assertTrue(any(item == "mode:execute" for item in inv[0].assumptions))
        controller, port = _controller(store, status_code=403)
        _run_until_event(controller, store, _command(), INVARIANT_EVALUATED)
        self.assertTrue(port.calls)
        self.assertFalse(any(call["request"]["worker_capability"] == "diagnostic.echo" for call in port.calls))
        evaluated = [item for item in store.audit_events.values() if item.event_type == INVARIANT_EVALUATED]
        self.assertTrue(evaluated)
        self.assertEqual(evaluated[-1].payload.get("outcome"), "HOLDS")

    def test_v8_authority_block(self) -> None:
        store = _Store()
        _seed_base(store)
        store.audit_events["core-deny-1"] = AuditEventRecord(
            audit_event_id="core-deny-1",
            occurred_at=CREATED_AT,
            actor_id="control-plane",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type="RESEARCH_WORK_CORE_DENIED",
            subject_type="research_run",
            subject_id="run-1",
            payload={
                "selected_work_id": "work-proto-1",
                "native_capability": "http.raw_exchange",
                "compiled_capability": "http.raw_exchange",
                "selected_engine": "PROTOCOL",
            },
        )
        controller, port = _controller(store)
        _run_until_event(controller, store, _command(), INVARIANT_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == INVARIANT_EVALUATED]
        self.assertEqual(evaluated[-1].payload.get("outcome"), "BLOCKED_BY_AUTHORITY")
        self.assertFalse(port.calls)

    def test_c2_c3_c4_c5_c6_negative_linkage(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_assessments(store, same_path=False)
        controller, port = _controller(store)
        _run_until_event(controller, store, _command(), CHAIN_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == CHAIN_EVALUATED]
        self.assertTrue(evaluated)
        self.assertEqual(evaluated[-1].payload.get("linkage"), "INSUFFICIENT_LINKAGE")
        self.assertEqual(evaluated[-1].payload.get("edges"), [])
        self.assertEqual(len(store.evidence), 0)
        self.assertFalse(port.calls)
        chain = next(iter(store.chain_hypotheses.values()))
        self.assertEqual(len(chain.steps), 2)
        self.assertTrue(chain.preconditions)

    def test_c7_c8_c9_positive_linkage_impact(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_assessments(store, same_path=True)
        controller, _ = _controller(store)
        _run_until_event(controller, store, _command(), CHAIN_EVALUATED)
        evaluated = [item for item in store.audit_events.values() if item.event_type == CHAIN_EVALUATED]
        self.assertEqual(evaluated[-1].payload.get("linkage"), "SUPPORTED")
        self.assertEqual(evaluated[-1].payload.get("impact_status"), "HYPOTHESIS")
        self.assertEqual(evaluated[-1].payload.get("left_step_status"), "PROVEN")
        self.assertEqual(evaluated[-1].payload.get("right_step_status"), "PROVEN")
        self.assertTrue(evaluated[-1].payload.get("impact_not_verified"))
        self.assertEqual(len(store.evidence), 0)

    def test_normalization_unit(self) -> None:
        result = compare_controlled_payloads(
            {"status_code": 200, "body": "ok", "response_headers": {"date": "a"}},
            {"status_code": 200, "body": "ok", "response_headers": {"date": "b"}},
            changed_identity=True,
            same_resource=True,
        )
        self.assertEqual(result.judgement, DifferentialSecurityJudgement.NOISE_ONLY)

    def test_unknown_is_not_violation(self) -> None:
        result = evaluate_access_deny(
            property_id="UNAUTHENTICATED_MUST_NOT_ACCESS",
            allowed_status=200,
            denied_status=None,
            has_allowed_observation=True,
            has_denied_observation=False,
        )
        self.assertEqual(result.outcome, InvariantCheckOutcome.INSUFFICIENT_DATA)

    def test_chain_untested_not_proven(self) -> None:
        result = evaluate_chain_linkage(
            ChainNodeView("n1", "a", "/x", None, "AUTHORIZED", ChainStepStatus.UNTESTED),
            ChainNodeView("n2", "b", "/x", "CONSISTENT_WITH_PREDICTION", "AUTHORIZED", ChainStepStatus.PROVEN),
            claimed_edge="ENABLES",
        )
        self.assertEqual(result.linkage, ChainLinkage.REJECTED)

    def test_not_plumbing(self) -> None:
        from zest.research.exploration import (
            OpportunityDimensions,
            OpportunityMode,
            OrdinalLevel,
            ResearchOpportunity,
            opportunity_structural_identity,
        )

        kind = OpportunityKind.DIFFERENTIAL
        direction = "Compare controlled observations."
        identity = opportunity_structural_identity(
            kind=kind,
            source_refs=("obs-left", "obs-right"),
            context_signature="differential:GET:/x:a:b",
            proposed_direction=direction,
        )
        opp = ResearchOpportunity(
            opportunity_id="opp-d",
            research_run_id="run-1",
            opportunity_kind=kind,
            mode=OpportunityMode.EXPLORATION,
            source_refs=("obs-left", "obs-right"),
            proposed_direction=direction,
            unresolved_question="relation?",
            expected_information_value_description="pair",
            assumptions=(),
            dimensions=OpportunityDimensions(
                expected_information_value=OrdinalLevel.HIGH,
                security_relevance_potential=OrdinalLevel.HIGH,
                novelty_composition=OrdinalLevel.MEDIUM,
                unresolved_uncertainty=OrdinalLevel.HIGH,
                chain_potential=OrdinalLevel.MEDIUM,
                evidence_coverage=OrdinalLevel.LOW,
                execution_cost=OrdinalLevel.LOW,
                side_effect_requirement=0,
                duplicate_risk=OrdinalLevel.LOW,
                previous_failed_attempts=0,
            ),
            context_signature="differential:GET:/x:a:b",
            novelty_composition_marker=False,
            prior_attempt_refs=(),
            strategy_version="differential.controlled.opportunity.v1",
            structural_identity=identity,
        )
        self.assertFalse(is_plumbing_opportunity(opp))

    def test_global_audit_ints_and_blocks(self) -> None:
        store = _Store()
        _seed_base(store)
        _http_pair(store, left_status=200, right_status=403)
        with FakeUnitOfWorkFactory(store).open() as uow:
            DifferentialWorkSource().harvest(uow, research_run_id="run-1", now=CREATED_AT)
            uow.commit()
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.differential_pending, int)
        self.assertIsInstance(audit.invariant_pending, int)
        self.assertIsInstance(audit.chain_pending, int)
        self.assertNotEqual(audit.differential_pending, "NOT_YET_CONNECTED")
        self.assertGreater(audit.differential_pending, 0)
        self.assertIn("DIFFERENTIAL_PENDING", audit.completion_block_reasons)
        self.assertFalse(audit.completion_allowed)

    def test_native_se_preserved_on_execute(self) -> None:
        store = _Store()
        _seed_base(store)
        _seed_observation(
            store, suffix="authed", identity="id-alice", path="/me", status=200, headers={}, body="me", session=True
        )
        controller, port = _controller(store, status_code=401)
        _run_until_event(controller, store, _command(), "RESEARCH_WORK_COMPILED")
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("selected_engine") == "INVARIANT"
        ]
        self.assertTrue(compiled)
        self.assertEqual(compiled[-1].payload.get("native_side_effect"), compiled[-1].payload.get("side_effect"))
        self.assertEqual(port.calls[-1]["request"]["worker_capability"], "http.transaction")


if __name__ == "__main__":
    unittest.main()
