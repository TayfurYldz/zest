"""Phase 6.2 Hunter + Coverage production acceptance (H1–H16)."""

from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.global_research_work_audit import global_research_work_audit
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
from zest.research.discovery.types import SURFACE_DISCOVERY_STRATEGY_VERSION
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.orchestration import OrchestrationBounds, StopReason
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
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
        max_cycles=8,
        max_experiments=8,
        max_model_calls=50,
        max_worker_invocations=10,
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


def _sql_family() -> HunterFamilyRecord:
    return HunterFamilyRecord(
        family_id="hf-sqli",
        name="SQL_INJECTION",
        target_node_kinds=("HTTP_OPERATION", "FORM", "API_SPEC"),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template=(
            "Input-bearing surface {canonical_key} should receive bounded SQL "
            "parser-differential experiments before being marked covered."
        ),
        evidence_requirements={
            "required_observation_kinds": ["MUTATION_MATRIX_RESULT"],
            "required_controls": ["secure_fixture", "deceptive_fixture", "read_back"],
            "required_matrix_dimensions": ["input_vector", "encoding", "parser_delta"],
        },
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _protocol_family() -> HunterFamilyRecord:
    return HunterFamilyRecord(
        family_id="hf-http-smuggling-desync",
        name="HTTP_REQUEST_SMUGGLING_DESYNC",
        target_node_kinds=("HTTP_OPERATION", "SERVICE", "TECH"),
        preconditions={
            "scope_classification": "IN_SCOPE",
            "required_attribute_any": {
                "protocol_surface_signals": [
                    "reverse_proxy",
                    "http2",
                    "h2c_upgrade",
                    "connection_reuse",
                    "front_backend_split",
                ],
            },
        },
        claim_template=(
            "Protocol surface {canonical_key} has parser-boundary evidence "
            "supporting request-smuggling/desync specialist planning."
        ),
        evidence_requirements={
            "protocol_lane": "http_request_smuggling_desync",
            "required_surface_signals": [
                "reverse_proxy",
                "http2",
                "h2c_upgrade",
                "connection_reuse",
                "front_backend_split",
            ],
            "required_controls": [
                "single_parser_control",
                "connection_close_control",
                "deceptive_proxy_control",
            ],
            "required_protocol_dimensions": [
                "frontend_protocol",
                "backend_protocol",
                "normalization_boundary",
            ],
        },
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _authz_family() -> HunterFamilyRecord:
    return HunterFamilyRecord(
        family_id="hf-object-authz",
        name="OBJECT_AUTHORIZATION",
        target_node_kinds=("HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"),
        preconditions={"scope_classification": "IN_SCOPE"},
        claim_template=(
            "Object authorization boundary on {origin}{path} "
            "may allow cross-owner access to {resource_id}."
        ),
        evidence_requirements={"required_observation_kinds": ["HTTP_AUTHORIZATION_DIFFERENTIAL"]},
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _seed_base(store: _Store) -> None:
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


def _add_http_fact(
    store: _Store,
    *,
    fact_id: str,
    path: str,
    identity: str = "ANONYMOUS",
    extra_attributes: dict | None = None,
) -> None:
    store.observations[f"obs-{fact_id}"] = ObservationRecord(
        observation_id=f"obs-{fact_id}",
        worker_result_id="wr-seed",
        observation_kind="HTTP_TRANSACTION",
        payload={"path": path},
        normalization_version="http.transaction.v1",
        observed_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    store.discovery_facts[fact_id] = DiscoveryFactRecord(
        fact_id=fact_id,
        research_run_id="run-1",
        fact_kind="HTTP_OPERATION",
        canonical_key=f"GET http://127.0.0.1:9{path}",
        epistemic_status="OBSERVED",
        identity_id=identity,
        target_reference="target-1",
        created_at=CREATED_AT,
        normalized_origin="http://127.0.0.1:9",
        normalized_path=path,
        http_method="GET",
        attributes={
            "scope_classification": "IN_SCOPE",
            **(extra_attributes or {}),
        },
    )
    store.discovery_fact_sources[f"src-{fact_id}"] = DiscoveryFactSourceRecord(
        source_row_id=f"src-{fact_id}",
        research_run_id="run-1",
        fact_id=fact_id,
        created_at=CREATED_AT,
        observation_id=f"obs-{fact_id}",
    )


def _seed_sql_lab(store: _Store, *, extra_path: str | None = None) -> None:
    _seed_base(store)
    store.hunter_families["hf-sqli:1"] = _sql_family()
    _add_http_fact(store, fact_id="fact-sql-a", path="/echo")
    if extra_path is not None:
        _add_http_fact(store, fact_id="fact-sql-b", path=extra_path)


def _controller(store: _Store):
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store)
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock()
    )
    return controller, port


class HunterCoverageProductionTests(unittest.TestCase):
    def test_h1_live_coverage_refresh_from_sor(self) -> None:
        store = _Store()
        _seed_sql_lab(store, extra_path="/search")
        produced = ProduceHunterCoverageWork(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute("run-1")
        self.assertFalse(produced.skipped)
        self.assertIsNotNone(produced.snapshot_id)
        self.assertTrue(store.coverage_debt_snapshots)
        refreshed = [
            item
            for item in store.audit_events.values()
            if item.event_type == "LIVE_COVERAGE_DEBT_REFRESHED"
        ]
        self.assertTrue(refreshed)
        self.assertEqual(refreshed[-1].payload.get("research_run_id"), "run-1")
        self.assertGreaterEqual(refreshed[-1].payload.get("fact_count"), 2)

    def test_h2_hunt_scheduler_ranks_multiple_cells(self) -> None:
        store = _Store()
        _seed_sql_lab(store, extra_path="/search")
        produced = ProduceHunterCoverageWork(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute("run-1")
        self.assertGreaterEqual(produced.recommended_count, 2)
        scheduled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "HUNT_SCHEDULE_RECOMMENDED"
        ]
        self.assertTrue(scheduled)
        self.assertGreaterEqual(scheduled[0].payload["recommended_count"], 2)

    def test_h3_h4_h5_hypothesis_tiers_and_candidate(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            "run-1"
        )
        generated = [
            item
            for item in store.audit_events.values()
            if item.event_type == "HUNT_HYPOTHESIS_GENERATED"
        ]
        self.assertTrue(generated)
        claim = generated[0].payload.get("claim") or ""
        self.assertIn("/echo", claim)
        self.assertNotEqual(claim.lower(), "test the target")
        self.assertTrue(
            any(item.event_type == "HUNT_TIER_V1_PASSED" for item in store.audit_events.values())
        )
        self.assertTrue(
            any(item.event_type == "HUNT_TIER_V2_PASSED" for item in store.audit_events.values())
        )
        self.assertTrue(
            any(item.event_type == "HUNT_TIER_V3_QUEUED" for item in store.audit_events.values())
        )
        self.assertTrue(store.hunt_v3_queue)
        hunter = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.source_system == "HUNTER_COVERAGE"
        ]
        self.assertTrue(hunter)
        self.assertEqual(hunter[0].outcome, "PENDING")

    def test_h6_h7_h8_h9_planner_arc_worker_feedback(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        controller, port = _controller(store)
        command = _command()
        controller.start(command)
        result = controller.step(command)
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
        ]
        self.assertTrue(compiled)
        self.assertNotEqual(compiled[-1].payload.get("compiled_capability"), "diagnostic.echo")
        self.assertEqual(compiled[-1].payload.get("compiler"), "mutation_matrix_cell.v1")
        self.assertGreaterEqual(len(port.calls), 1)
        self.assertTrue(store.observations)
        self.assertTrue(
            any(item.event_type == "HUNTER_FEEDBACK_APPLIED" for item in store.audit_events.values())
        )
        self.assertNotEqual(result.stop_reason, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value)

    def test_h7_single_dispatch_owner_source(self) -> None:
        root = Path(__file__).resolve().parents[3] / "src" / "zest" / "application"
        for name in (
            "autonomous_research_controller.py",
            "select_research_opportunities.py",
            "produce_hunter_coverage_work.py",
            "research_work_planners.py",
        ):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("DispatchApprovedV3Queue(", text)
            self.assertNotIn("from zest.application.dispatch_approved_v3_queue", text)

    def test_h10_actionable_coverage_forbids_complete(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            "run-1"
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.coverage_actionable, 0)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("ACTIONABLE_COVERAGE_DEBT", audit.completion_block_reasons)

    def test_h11_coverage_exhaustion_after_feedback(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        controller, _ = _controller(store)
        command = _command()
        controller.start(command)
        controller.step(command)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreaterEqual(audit.coverage_resolved, 1)

    def test_h12_fairness_with_handoff_and_diagnostic(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        store.hypotheses["hyp-pre"] = HypothesisRecord(
            hypothesis_id="hyp-pre",
            research_run_id="run-1",
            claim="diagnostic runtime returns the provided echo value",
            created_at=CREATED_AT,
        )
        store.frontier_items["front-se2"] = FrontierItemRecord(
            frontier_id="front-se2",
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            goal_kind="CHARACTERIZE_HTTP_OPERATION",
            candidate_origin="http://127.0.0.1:9",
            candidate_path="/handoff",
            identity_id="ANONYMOUS",
            proposed_capability="http.transaction",
            proposed_action="read",
            expected_side_effect=2,
            budget_class=2,
            structural_signature="sig-front-se2",
            dedupe_identity="dedupe-front-se2",
            created_at=CREATED_AT,
            current_state="DEFERRED_TO_RESEARCH",
            state_version=3,
            attributes={"method": "GET"},
        )
        store.frontier_events["ev-1"] = FrontierEventRecord(
            event_id="ev-1",
            frontier_id="front-se2",
            research_run_id="run-1",
            event_kind="CREATED",
            sequence=1,
            created_at=CREATED_AT,
        )
        store.frontier_events["ev-2"] = FrontierEventRecord(
            event_id="ev-2",
            frontier_id="front-se2",
            research_run_id="run-1",
            event_kind="ELIGIBLE",
            sequence=2,
            created_at=CREATED_AT,
        )
        store.frontier_events["ev-3"] = FrontierEventRecord(
            event_id="ev-3",
            frontier_id="front-se2",
            research_run_id="run-1",
            event_kind="DEFERRED_TO_RESEARCH",
            sequence=3,
            created_at=CREATED_AT,
            reason_code="SIDE_EFFECT_NOT_DISCOVERY_EXECUTABLE",
        )
        result = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        engines = {item.opportunity.opportunity_kind for item in result.decisions}
        self.assertIn(OpportunityKind.HUNTER_COVERAGE_GAP, engines)
        self.assertIn(OpportunityKind.DISCOVERY_HANDOFF, engines)
        self.assertIn(OpportunityKind.HYPOTHESIS_FOLLOWUP, engines)

    def test_h13_dedupe_on_repeated_refresh(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        factory = FakeUnitOfWorkFactory(store)
        first = ProduceHunterCoverageWork(factory, clock=FixedClock()).execute("run-1")
        second = ProduceHunterCoverageWork(factory, clock=FixedClock()).execute("run-1")
        hunter = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.source_system == "HUNTER_COVERAGE"
        ]
        self.assertEqual(len(hunter), first.candidates_created)
        self.assertEqual(second.candidates_created, 0)

    def test_h14_recovery_does_not_duplicate_worker(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        controller, port = _controller(store)
        command = _command()
        controller.start(command)
        controller.step(command)
        hunter_hypotheses = {item.hypothesis_id for item in store.hunt_v3_queue.values()}
        hunter_experiments = {
            item.experiment_id
            for item in store.experiments.values()
            if item.hypothesis_id in hunter_hypotheses
        }
        hunter_attempts = [
            item.attempt_id
            for item in store.execution_attempts.values()
            if item.experiment_id in hunter_experiments
        ]
        restarted, _port2 = _controller(store)
        restarted.step(command)
        hunter_attempts_after = [
            item.attempt_id
            for item in store.execution_attempts.values()
            if item.experiment_id in hunter_experiments
        ]
        self.assertGreaterEqual(len(hunter_attempts), 1)
        self.assertEqual(sorted(hunter_attempts), sorted(hunter_attempts_after))

    def test_h15_native_side_effect_not_downgraded(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        controller, _ = _controller(store)
        controller.start(_command())
        controller.step(_command())
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
        ]
        self.assertTrue(compiled)
        self.assertEqual(
            compiled[-1].payload.get("native_side_effect"),
            compiled[-1].payload.get("side_effect"),
        )
        self.assertNotEqual(compiled[-1].payload.get("compiled_capability"), "diagnostic.echo")

    def test_h16_missing_precondition_not_weaker_experiment(self) -> None:
        store = _Store()
        _seed_base(store)
        store.hunter_families["hf-object-authz:1"] = _authz_family()
        _add_http_fact(store, fact_id="fact-authz", path="/accounts")
        controller, port = _controller(store)
        controller.start(_command())
        controller.step(_command())
        missing = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_MISSING_PRECONDITION"
        ]
        self.assertTrue(missing)
        self.assertEqual(len(port.calls), 0)
        echo = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("compiled_capability") == "diagnostic.echo"
        ]
        self.assertFalse(echo)

    def test_h11b_true_coverage_exhaustion_and_recovery(self) -> None:
        store = _Store()
        _seed_base(store)
        store.issued_budgets["budget-1"] = IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=80,
            max_tool_calls=80,
            max_runtime_ms=60_000,
            max_concurrency=1,
            issued_at=CREATED_AT,
        )
        store.hunter_families["hf-sqli:1"] = _sql_family()
        store.hunter_families["hf-http-smuggling-desync:1"] = _protocol_family()
        _add_http_fact(store, fact_id="fact-sql-a", path="/echo")
        _add_http_fact(store, fact_id="fact-sql-b", path="/search")
        _add_http_fact(store, fact_id="fact-sql-c", path="/items")
        _add_http_fact(
            store,
            fact_id="fact-proto",
            path="/proxy",
            extra_attributes={"protocol_surface_signals": ["reverse_proxy"]},
        )
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            "run-1"
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            initial = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(initial.coverage_actionable, 1)
        self.assertGreater(initial.hunter_pending, 0)
        self.assertFalse(initial.hunter_coverage_exhausted_for_connected_engines)

        command = _command(
            bounds=_bounds(
                max_cycles=48,
                max_experiments=48,
                max_worker_invocations=48,
                max_model_calls=80,
                max_selected_opportunities=1,
            )
        )
        controller, _ = _controller(store)
        controller.start(command)
        first = controller.step(command)
        self.assertNotEqual(first.stop_reason, StopReason.COMPLETED_NO_MORE_OPPORTUNITIES.value)
        with FakeUnitOfWorkFactory(store).open() as uow:
            mid = global_research_work_audit(uow, "run-1")
            uow.rollback()
        mid_resolved = mid.coverage_resolved
        mid_actionable = mid.coverage_actionable
        hunter_experiments = {
            item.experiment_id
            for item in store.experiments.values()
            if item.hypothesis_id in {row.hypothesis_id for row in store.hunt_v3_queue.values()}
        }
        hunter_attempts = [
            item.attempt_id
            for item in store.execution_attempts.values()
            if item.experiment_id in hunter_experiments
        ]

        restarted, _ = _controller(store)
        final_audit = mid
        for _ in range(48):
            restarted.step(command)
            with FakeUnitOfWorkFactory(store).open() as uow:
                final_audit = global_research_work_audit(uow, "run-1")
                uow.rollback()
            if (
                final_audit.hunter_coverage_exhausted_for_connected_engines
                and final_audit.coverage_actionable == 0
            ):
                break
        self.assertGreaterEqual(final_audit.coverage_resolved, mid_resolved)
        self.assertLessEqual(final_audit.coverage_actionable, mid_actionable)
        self.assertEqual(final_audit.coverage_actionable, 0)
        self.assertEqual(final_audit.hunter_pending, 0)
        self.assertEqual(final_audit.orphan_research_work, 0)
        self.assertTrue(final_audit.hunter_coverage_exhausted_for_connected_engines)
        self.assertEqual(final_audit.engine_dependency_pending, 0)
        self.assertNotIn("ENGINE_DEPENDENCY_PENDING", final_audit.completion_block_reasons)
        self.assertNotIn("ACTIONABLE_COVERAGE_DEBT", final_audit.completion_block_reasons)
        self.assertGreater(final_audit.protocol_authority_blocked, 0)
        protocol_covered = [
            item
            for item in store.audit_events.values()
            if item.event_type == "HUNT_CELL_COVERED"
            and (item.payload or {}).get("family_id") == "hf-http-smuggling-desync"
        ]
        self.assertFalse(protocol_covered)
        generated_at_exhaustion = sum(
            1
            for item in store.audit_events.values()
            if item.event_type == "HUNT_HYPOTHESIS_GENERATED"
        )
        hunter_attempts_after = [
            item.attempt_id
            for item in store.execution_attempts.values()
            if item.experiment_id in hunter_experiments
        ]
        self.assertEqual(sorted(hunter_attempts), sorted(hunter_attempts_after))
        reproduced = ProduceHunterCoverageWork(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute("run-1")
        self.assertEqual(reproduced.candidates_created, 0)
        generated_after_reproduce = sum(
            1
            for item in store.audit_events.values()
            if item.event_type == "HUNT_HYPOTHESIS_GENERATED"
        )
        self.assertEqual(generated_at_exhaustion, generated_after_reproduce)
        with FakeUnitOfWorkFactory(store).open() as uow:
            after_restart = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(after_restart.coverage_actionable, 0)
        self.assertTrue(after_restart.hunter_coverage_exhausted_for_connected_engines)
        self.assertEqual(after_restart.engine_dependency_pending, 0)

    def test_global_audit_hunter_coverage_are_real_counts(self) -> None:
        store = _Store()
        _seed_sql_lab(store)
        ProduceHunterCoverageWork(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            "run-1"
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertIsInstance(audit.hunter_pending, int)
        self.assertIsInstance(audit.coverage_actionable, int)
        self.assertGreater(audit.hunter_pending, 0)
        self.assertGreater(audit.coverage_actionable, 0)
