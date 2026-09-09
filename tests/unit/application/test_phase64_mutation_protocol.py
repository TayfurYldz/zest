"""Phase 6.4 Mutation + Protocol production acceptance (M1–M16, P1–P16)."""

from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.global_research_work_audit import (
    NOT_YET_CONNECTED,
    global_research_work_audit,
)
from zest.application.produce_hunter_coverage_work import ProduceHunterCoverageWork
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, CompiledScopeRule
from zest.data.records import (
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
    FrontierEventRecord,
    FrontierItemRecord,
    HunterFamilyRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ObservationRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.compiler_registry import MUTATION_MATRIX_FAMILIES, PROTOCOL_FAMILIES
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.orchestration import OrchestrationBounds, StopReason
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, STARTED_AT, COMPLETED_AT
from support.spine import CREATED_AT, seed_authorization_run
from tests.unit.application.test_phase62_hunter_coverage import (
    _add_http_fact,
    _protocol_family,
    _sql_family,
)


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


def _cache_family() -> HunterFamilyRecord:
    return HunterFamilyRecord(
        family_id="hf-cache-poison-deception",
        name="HTTP_CACHE_POISONING_DECEPTION",
        target_node_kinds=("HTTP_OPERATION", "SERVICE", "TECH"),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template="Cache-key deception on {canonical_key} requires parser-plan evidence.",
        evidence_requirements={
            "protocol_lane": "http_cache_poisoning_deception",
            "required_surface_signals": ["reverse_proxy", "cdn"],
            "required_controls": ["cache_miss_control", "vary_control"],
            "required_protocol_dimensions": [
                "cache_key_dimension",
                "cache_behavior",
                "proxy_layer",
            ],
        },
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _worker_result(capability: str, raw: dict, correlation: dict) -> dict:
    return {
        "contract_version": "v1",
        "correlation": correlation,
        "worker_id": f"local-python-{capability}",
        "status": "SUCCEEDED",
        "started_at": "2026-08-16T20:00:00Z",
        "completed_at": "2026-08-16T20:00:01Z",
        "raw_result": raw,
    }


def _phase64_handler(status_code: int = 403):
    def handler(request):
        cap = request.get("worker_capability")
        arguments = request.get("arguments") or {}
        correlation = dict(request.get("correlation") or {})
        if cap == "http.transaction":
            raw = {
                "authorized_origin": arguments.get("authorized_origin"),
                "method": arguments.get("method") or "GET",
                "path": arguments.get("path"),
                "status_code": status_code,
                "body_length": 0,
                "body_digest": "0" * 64,
                "request_fingerprint": "fp-mut",
                "elapsed_ms": 1,
            }
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=_worker_result(cap, raw, correlation),
                exit_code=0,
            )
        if cap == "http.raw_exchange":
            raw = {
                "authorized_origin": arguments.get("authorized_origin"),
                "path": arguments.get("path"),
                "framing_profile": arguments.get("framing_profile"),
                "lane": arguments.get("lane"),
                "control": arguments.get("control"),
                "status_code": 400,
                "write_count": 1,
                "bytes_written": 32,
                "body_length": 0,
                "body_digest": "0" * 64,
                "request_fingerprint": "fp-raw",
            }
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=_worker_result(cap, raw, correlation),
                exit_code=0,
            )
        message = ""
        if isinstance(arguments, dict):
            raw_msg = arguments.get("message", "")
            message = raw_msg if isinstance(raw_msg, str) else ""
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=STARTED_AT,
            completed_at=COMPLETED_AT,
            worker_result=_worker_result(
                "diagnostic.echo",
                {"echoed": message, "capability": "diagnostic.echo"},
                correlation,
            ),
            exit_code=0,
        )

    return handler


def _controller(store: _Store, *, status_code: int = 403):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store, handler=_phase64_handler(status_code))
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock()
    )
    return controller, port


class MutationProtocolProductionTests(unittest.TestCase):
    def test_m1_m2_m3_direct_source_variants_and_provenance(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        mutations = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.MUTATION_VARIANT.value
        ]
        self.assertTrue(mutations)
        bound = [
            item
            for item in store.audit_events.values()
            if item.event_type == "MUTATION_VARIANT_BOUND"
        ]
        self.assertTrue(bound)
        payload = bound[0].payload
        self.assertIn("baseline_ref", payload)
        self.assertIn("family_id", payload)
        self.assertIn("arguments", payload)
        self.assertEqual(len(mutations), len({item.structural_identity for item in mutations}))

    def test_m4_m5_m6_m7_m8_scheduler_compiler_worker_observation_eval(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, port = _controller(store, status_code=403)
        controller.start(_command())
        controller.step(_command())
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("selected_engine") == "MUTATION"
        ]
        self.assertTrue(compiled)
        self.assertEqual(compiled[-1].payload.get("compiled_capability"), "http.transaction")
        self.assertEqual(compiled[-1].payload.get("compiler"), "mutation_variant.v1")
        self.assertNotEqual(compiled[-1].payload.get("compiled_capability"), "diagnostic.echo")
        self.assertGreaterEqual(len(port.calls), 1)
        self.assertEqual(port.calls[0]["request"]["worker_capability"], "http.transaction")
        observations = [
            item
            for item in store.observations.values()
            if item.observation_kind == "HTTP_TRANSACTION"
            and item.observation_id.startswith("obs:")
        ]
        self.assertTrue(observations)
        assessments = list(store.hypothesis_assessments.values())
        self.assertTrue(assessments)
        self.assertEqual(assessments[-1].evaluation_strategy, "http.transaction.v1")

    def test_m9_negative_control_no_vuln_evidence(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, _ = _controller(store, status_code=403)
        controller.start(_command())
        controller.step(_command())
        mutation_evidence = [
            item
            for item in store.evidence.values()
            if "mutation" in (item.claim_scope or "").lower()
        ]
        self.assertFalse(mutation_evidence)
        pending = [
            item
            for item in store.audit_events.values()
            if item.event_type == "MUTATION_PROTOCOL_COVERAGE_UPDATED"
            and (item.payload or {}).get("evidence_pipeline") == "EVIDENCE_PIPELINE_PENDING"
        ]
        self.assertTrue(pending)

    def test_m10_positive_control_assessment_signal(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, _ = _controller(store, status_code=200)
        controller.start(_command())
        controller.step(_command())
        assessments = list(store.hypothesis_assessments.values())
        self.assertTrue(assessments)
        self.assertEqual(assessments[-1].assessment_outcome, "CONSISTENT_WITH_PREDICTION")

    def test_m11_m12_native_se_and_hard_ceiling(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, port = _controller(store)
        command = _command(bounds=_bounds(side_effect_ceiling=0))
        controller.start(command)
        controller.step(command)
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("selected_engine") == "MUTATION"
        ]
        if compiled:
            self.assertEqual(compiled[-1].payload.get("native_side_effect"), compiled[-1].payload.get("side_effect"))
            self.assertLessEqual(compiled[-1].payload.get("side_effect"), 0)
        blocked = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING"
            and (item.payload or {}).get("selected_engine") == "MUTATION"
        ]
        self.assertTrue(compiled or blocked)
        self.assertFalse(
            any(call["request"]["worker_capability"] == "diagnostic.echo" for call in port.calls)
        )

    def test_m13_p15_dedupe_hunter_and_direct_protocol(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-http-smuggling-desync:1"] = _protocol_family()
        _add_http_fact(
            store,
            fact_id="fact-proto",
            path="/proxy",
            extra_attributes={"protocol_surface_signals": ["reverse_proxy"]},
        )
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            "run-1"
        )
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        protocol = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.PROTOCOL_STEP.value
        ]
        identities = [item.structural_identity for item in protocol]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(protocol)

    def test_m14_p4_fairness_considers_mutation_and_protocol(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-sqli:1"] = _sql_family()
        store.hunter_families["hf-http-smuggling-desync:1"] = _protocol_family()
        _add_http_fact(
            store,
            fact_id="fact-sql",
            path="/echo",
            extra_attributes={"query_params": ["q"]},
        )
        _add_http_fact(
            store,
            fact_id="fact-proto",
            path="/proxy",
            extra_attributes={"protocol_surface_signals": ["reverse_proxy"]},
        )
        store.frontier_items["front-1"] = FrontierItemRecord(
            frontier_id="front-1",
            research_run_id="run-1",
            strategy_version="surface.discovery.v1",
            goal_kind="INSPECT_PATH",
            candidate_origin="http://127.0.0.1:9",
            candidate_path="/echo",
            identity_id="ANONYMOUS",
            proposed_capability="diagnostic.echo",
            proposed_action="echo",
            expected_side_effect=0,
            budget_class=0,
            structural_signature="sig-1",
            dedupe_identity="dedupe-1",
            created_at=CREATED_AT,
        )
        store.frontier_events["ev-1"] = FrontierEventRecord(
            event_id="ev-1",
            frontier_id="front-1",
            research_run_id="run-1",
            event_kind="DEFERRED_TO_RESEARCH",
            sequence=1,
            created_at=CREATED_AT,
            reason_code="HAND_OFF",
        )
        store.hypotheses["hyp-pre"] = HypothesisRecord(
            hypothesis_id="hyp-pre",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            created_at=CREATED_AT,
        )
        result = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        kinds = {item.opportunity.opportunity_kind for item in result.decisions}
        self.assertIn(OpportunityKind.MUTATION_VARIANT, kinds)
        self.assertIn(OpportunityKind.PROTOCOL_STEP, kinds)
        self.assertIn(OpportunityKind.HUNTER_COVERAGE_GAP, kinds)

    def test_m15_coverage_feedback_is_dimension_specific(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, _ = _controller(store)
        controller.start(_command())
        controller.step(_command())
        updated = [
            item
            for item in store.audit_events.values()
            if item.event_type == "MUTATION_PROTOCOL_COVERAGE_UPDATED"
        ]
        self.assertTrue(updated)
        self.assertFalse(updated[-1].payload["dimensions"]["endpoint_fully_mutation_covered"])

    def test_m16_recovery_no_duplicate_worker(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        controller, port = _controller(store)
        command = _command()
        controller.start(command)
        controller.step(command)
        first_calls = len(port.calls)
        first_bound = sum(
            1 for item in store.audit_events.values() if item.event_type == "MUTATION_VARIANT_BOUND"
        )
        restarted, port2 = _controller(store)
        restarted.step(command)
        mutation_experiments = {
            item.experiment_id
            for item in store.experiments.values()
            if any(
                event.event_type == "RESEARCH_WORK_COMPILED"
                and (event.payload or {}).get("selected_engine") == "MUTATION"
                and (event.payload or {}).get("variant_id")
                for event in store.audit_events.values()
            )
        }
        attempt_ids = [
            item.attempt_id
            for item in store.execution_attempts.values()
        ]
        self.assertEqual(len(attempt_ids), len(set(attempt_ids)))
        self.assertGreaterEqual(first_calls, 1)
        second_bound = sum(
            1 for item in store.audit_events.values() if item.event_type == "MUTATION_VARIANT_BOUND"
        )
        self.assertEqual(first_bound, second_bound)

    def test_p1_p2_p3_p5_p10_p11_protocol_source_and_se3_core_deny(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-http-smuggling-desync:1"] = _protocol_family()
        store.hunter_families["hf-cache-poison-deception:1"] = _cache_family()
        _add_http_fact(
            store,
            fact_id="fact-proto",
            path="/proxy",
            extra_attributes={"protocol_surface_signals": ["reverse_proxy", "cdn"]},
        )
        controller, port = _controller(store)
        command = _command(bounds=_bounds(side_effect_ceiling=3, max_cycles=8, max_experiments=8))
        controller.start(command)
        for _ in range(8):
            controller.step(command)
            compiled = [
                item
                for item in store.audit_events.values()
                if item.event_type == "RESEARCH_WORK_COMPILED"
                and (item.payload or {}).get("selected_engine") == "PROTOCOL"
            ]
            denied = [
                item
                for item in store.audit_events.values()
                if item.event_type == "RESEARCH_WORK_CORE_DENIED"
            ]
            if compiled or denied:
                break
        self.assertTrue(compiled)
        self.assertEqual(compiled[-1].payload.get("compiled_capability"), "http.raw_exchange")
        self.assertEqual(compiled[-1].payload.get("compiler"), "protocol_step.v1")
        self.assertEqual(compiled[-1].payload.get("native_side_effect"), 3)
        self.assertEqual(compiled[-1].payload.get("side_effect"), 3)
        denied = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_CORE_DENIED"
        ]
        self.assertTrue(denied)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(PROTOCOL_FAMILIES, frozenset(PROTOCOL_FAMILIES))

    def test_p6_no_authority_allowed_protocol_family_below_se3(self) -> None:
        self.assertEqual(
            PROTOCOL_FAMILIES,
            {"HTTP_REQUEST_SMUGGLING_DESYNC", "HTTP_CACHE_POISONING_DECEPTION"},
        )

    def test_p13_p14_protocol_not_dependency_pending_and_not_covered(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-http-smuggling-desync:1"] = _protocol_family()
        _add_http_fact(
            store,
            fact_id="fact-proto",
            path="/proxy",
            extra_attributes={"protocol_surface_signals": ["reverse_proxy"]},
        )
        controller, _ = _controller(store)
        command = _command()
        controller.start(command)
        for _ in range(8):
            controller.step(command)
            with FakeUnitOfWorkFactory(store).open() as uow:
                audit = global_research_work_audit(uow, "run-1")
                uow.rollback()
            if audit.protocol_authority_blocked or any(
                item.event_type == "RESEARCH_WORK_BLOCKED_SIDE_EFFECT_CEILING"
                for item in store.audit_events.values()
            ):
                break
        self.assertEqual(audit.engine_dependency_pending, 0)
        self.assertNotEqual(audit.protocol_pending, NOT_YET_CONNECTED)
        self.assertGreater(audit.protocol_authority_blocked, 0)
        covered = [
            item
            for item in store.audit_events.values()
            if item.event_type == "HUNT_CELL_COVERED"
        ]
        self.assertFalse(covered)

    def test_global_audit_mutation_protocol_are_ints(self) -> None:
        store = _Store()
        _seed_base(store)
        _add_http_fact(
            store,
            fact_id="fact-mut",
            path="/search",
            extra_attributes={"query_params": ["q"]},
        )
        SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SelectResearchOpportunitiesCommand(research_run_id="run-1"))
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.mutation_pending, int)
        self.assertIsInstance(audit.protocol_pending, int)
        self.assertGreater(audit.mutation_pending, 0)
        self.assertIsInstance(audit.oast_pending, int)

    def test_no_second_dispatcher(self) -> None:
        root = Path(__file__).resolve().parents[3]
        text = (root / "src/zest/application/autonomous_research_controller.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("RunResearchSelection(", text)
        self.assertNotIn("DispatchApprovedV3Queue", text)

    def test_matrix_families_remain_registered(self) -> None:
        self.assertIn("SQL_INJECTION", MUTATION_MATRIX_FAMILIES)
        self.assertEqual(len(MUTATION_MATRIX_FAMILIES), 9)
