"""MR-6A: durable identity anomaly → ARC ResearchOpportunity → exploratory Hypothesis.

PostgreSQL required. SQLite is not a substitute. Does not use ExploratorySignalInput
or operator compile_arguments on the ARC-connected path.
"""

from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.application.promote_exploratory_family import (
    PromoteExploratoryFamily,
    PromoteExploratoryFamilyCommand,
)
from zest.core.enums import ActorType, ApprovalDecision, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.budget_ledger import ledger_totals
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.data.records import (
    BudgetConsumptionRecord,
    DiscoveryFactRecord,
    DiscoveryFactSourceRecord,
)
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.exploratory import ExploratorySignalKind
from zest.research.identity_anomaly import (
    IDENTITY_ANOMALY_CLAIM,
    is_exploratory_hypothesis_origin,
)
from zest.research.orchestration import OrchestrationBounds
from zest.research.planning import plan_authorization_differential
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from support.fake_model import ScriptedModelPort
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT

TEST_URL = configured_test_url()
ORIGIN = "http://127.0.0.1:9"
CONCURRENCY_REPETITIONS = 10
SEED_FAMILY_COUNT = len(SEED_FAMILIES)


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


def _compiled_scope(origin: str = ORIGIN):
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
        max_experiments=8,
        max_model_calls=20,
        max_worker_invocations=12,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _command(**overrides) -> StartAutonomousResearchCommand:
    values = dict(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
        selection_budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
        compiled_scope=_compiled_scope(),
        research_question="Is unexplained identity divergence an unmodelled access invariant?",
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _authz_handler(mode: str):
    def handler(request):
        args = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        actor = str(args.get("actor") or "alice")
        own = str(args.get("own_object") or "alice")
        cross = str(args.get("cross_object") or "bob")
        origin = str(args.get("authorized_origin") or ORIGIN)
        if mode == "vulnerable":
            cross_status, cross_owner, visibility = 200, cross, None
        elif mode == "secure_only":
            cross_status, cross_owner, visibility = 403, None, None
        else:
            cross_status, cross_owner, visibility = 200, actor, "PUBLIC"
        cross_object = {"status": cross_status}
        if cross_owner is not None:
            cross_object["object_owner"] = cross_owner
        if visibility is not None:
            cross_object["object_visibility"] = visibility
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
                    "mode": "vulnerable" if mode == "deceptive" else mode,
                    "authorized_origin": origin,
                    "owner_request": {"status": 200, "object_owner": own},
                    "cross_object_request": cross_object,
                    "secure_control": {"status": 403},
                    "unauthenticated_control": {"status": 401},
                },
            },
            exit_code=0,
        )

    return handler


def _produce_source(factory: PostgresUnitOfWorkFactory, *, mode: str) -> None:
    plan_mode = "secure_only" if mode == "secure_only" else "vulnerable"
    ExecutePlannedExperiment(
        factory, RecordingWorkerPort(handler=_authz_handler(mode)), clock=FixedClock()
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
                mode=plan_mode,
            ),
            scope=_allow_scope(),
            compiled_scope=_compiled_scope(),
        )
    )


def _controller(factory: PostgresUnitOfWorkFactory, *, mode: str, model=None):
    return AutonomousResearchController(
        factory,
        RecordingWorkerPort(handler=_authz_handler(mode)),
        model or NeverInvokedModel(),
        clock=FixedClock(),
    )


def _counts(factory: PostgresUnitOfWorkFactory) -> dict[str, object]:
    with factory.open() as uow:
        hypotheses = uow.hypotheses.list_for_research_run("run-1")
        opportunities = uow.research_opportunities.list_for_research_run("run-1")
        candidates = uow.candidates.list_for_research_run("run-1")
        counts = {
            "observations": len(uow.observations.list_for_research_run("run-1")),
            "opportunities": len(opportunities),
            "exploratory_opportunities": len(
                [
                    item
                    for item in opportunities
                    if item.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
                ]
            ),
            "hypotheses": len(hypotheses),
            "exploratory_hypotheses": len(
                [
                    item
                    for item in hypotheses
                    if is_exploratory_hypothesis_origin(item.origin_reference)
                ]
            ),
            "experiments": len(uow.experiments.list_for_research_run("run-1")),
            "assessments": len(uow.hypothesis_assessments.list_for_research_run("run-1")),
            "evidence": len(uow.evidence.list_for_research_run("run-1")),
            "candidates": len(candidates),
            "validated_candidates": len([item for item in candidates if item.state == "VALIDATED"]),
            "verifications": len(uow.verifications.list_for_research_run("run-1")),
            "proposals": len(uow.finding_proposals.list_for_research_run("run-1")),
            "findings": len(uow.findings.list_for_research_run("run-1")),
            "families": len(uow.hunter_families.list_enabled()),
            "exploratory_claims": tuple(
                item.claim
                for item in hypotheses
                if is_exploratory_hypothesis_origin(item.origin_reference)
            ),
            "candidate_states": tuple(item.state for item in candidates),
        }
        uow.rollback()
    return counts


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class Mr6aArcAnomalyAdmissionPostgresTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()

    def test_vulnerable_lab_reaches_finding_proposal_without_finding(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        controller = _controller(factory, mode="vulnerable")
        command = _command()
        controller.start(command)
        result = controller.step(command)
        self.assertIsNotNone(result.hypothesis_id)
        counts = _counts(factory)
        self.assertGreaterEqual(counts["observations"], 1)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["exploratory_claims"], (IDENTITY_ANOMALY_CLAIM,))
        self.assertGreaterEqual(counts["evidence"], 1)
        self.assertEqual(counts["validated_candidates"], 1)
        self.assertEqual(counts["proposals"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["families"], SEED_FAMILY_COUNT)

    def test_secure_lab_does_not_emit_exploratory_or_false_promote(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="secure_only")
        controller = _controller(factory, mode="secure_only", model=ScriptedModelPort())
        command = _command(bounds=_bounds(max_cycles=1, max_model_calls=20))
        controller.start(command)
        controller.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 0)
        self.assertEqual(counts["exploratory_hypotheses"], 0)
        self.assertEqual(counts["validated_candidates"], 0)
        self.assertEqual(counts["proposals"], 0)
        self.assertEqual(counts["findings"], 0)

    def test_deceptive_lab_may_hypothesize_but_must_not_false_promote(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="deceptive")
        controller = _controller(factory, mode="deceptive")
        command = _command()
        controller.start(command)
        controller.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["validated_candidates"], 0)
        self.assertEqual(counts["proposals"], 0)
        self.assertEqual(counts["findings"], 0)
        with factory.open() as uow:
            assessments = uow.hypothesis_assessments.list_for_research_run("run-1")
            exploratory = [
                item
                for item in uow.hypotheses.list_for_research_run("run-1")
                if is_exploratory_hypothesis_origin(item.origin_reference)
            ]
            outcomes = [
                item.assessment_outcome
                for item in assessments
                if exploratory and item.hypothesis_id == exploratory[0].hypothesis_id
            ]
            uow.rollback()
        self.assertTrue(
            set(outcomes).issubset(
                {
                    AssessmentOutcome.CONTRADICTS_PREDICTION.value,
                    AssessmentOutcome.INCONCLUSIVE.value,
                    AssessmentOutcome.NEEDS_MORE_CONTEXT.value,
                }
            )
        )

    def test_known_family_compatible_anomaly_is_not_exploratory(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        with factory.open() as uow:
            observations = uow.observations.list_for_research_run("run-1")
            observation_id = observations[0].observation_id
            uow.discovery_facts.insert(
                DiscoveryFactRecord(
                    fact_id="fact-http-1",
                    research_run_id="run-1",
                    fact_kind="HTTP_OPERATION",
                    canonical_key="GET http://127.0.0.1:9/accounts",
                    epistemic_status="OBSERVED",
                    identity_id="alice",
                    target_reference="target-1",
                    created_at=CREATED_AT,
                    normalized_origin="http://127.0.0.1:9",
                    normalized_path="/accounts",
                    http_method="GET",
                    attributes={"scope_classification": "IN_SCOPE"},
                )
            )
            uow.discovery_fact_sources.insert(
                DiscoveryFactSourceRecord(
                    source_row_id="fact-src-1",
                    research_run_id="run-1",
                    fact_id="fact-http-1",
                    created_at=CREATED_AT,
                    observation_id=observation_id,
                )
            )
            uow.commit()
        controller = _controller(factory, mode="vulnerable", model=ScriptedModelPort())
        command = _command(bounds=_bounds(max_cycles=1, max_model_calls=20))
        controller.start(command)
        controller.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 0)
        self.assertEqual(counts["exploratory_hypotheses"], 0)

    def test_two_arc_steps_do_not_duplicate_exploratory_identity(self) -> None:
        for attempt in range(CONCURRENCY_REPETITIONS):
            with self.subTest(attempt=attempt):
                self.setUp()
                factory = PostgresUnitOfWorkFactory(self.engine)
                _produce_source(factory, mode="vulnerable")
                command = _command(bounds=_bounds(max_cycles=4, max_model_calls=20))
                starter = _controller(factory, mode="vulnerable", model=ScriptedModelPort())
                starter.start(command)
                errors: list[BaseException] = []
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futs = [
                        pool.submit(
                            _controller(
                                factory, mode="vulnerable", model=ScriptedModelPort()
                            ).step,
                            command,
                        )
                        for _ in range(2)
                    ]
                    for fut in as_completed(futs):
                        try:
                            fut.result()
                        except Exception as exc:  # noqa: BLE001
                            errors.append(exc)
                self.assertEqual(errors, [])
                counts = _counts(factory)
                self.assertEqual(counts["exploratory_opportunities"], 1)
                self.assertEqual(counts["exploratory_hypotheses"], 1)

    def test_restart_after_source_does_not_duplicate(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        command = _command()
        first = _controller(factory, mode="vulnerable")
        first.start(command)
        first.step(command)
        second = _controller(factory, mode="vulnerable", model=ScriptedModelPort())
        second.start(command)
        second.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["findings"], 0)

    def test_expired_authorization_yields_worker_zero_after_anomaly(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE authorization_source SET state = 'EXPIRED' "
                    "WHERE authorization_source_id = 'as-1'"
                )
            )
        worker = RecordingWorkerPort(handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            factory, worker, NeverInvokedModel(), clock=FixedClock()
        )
        command = _command()
        controller.start(command)
        controller.step(command)
        self.assertEqual(len(worker.calls), 0)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["validated_candidates"], 0)

    def test_budget_exhausted_between_source_and_dispatch_is_worker_zero(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        with factory.open() as uow:
            issued = uow.issued_budgets.get("budget-1")
            assert issued is not None
            used = ledger_totals(uow.budget_consumptions.list_for_budget("budget-1"))
            remaining = issued.max_requests - used.worker_requests
            if remaining > 0:
                uow.budget_consumptions.insert_within_allowance(
                    BudgetConsumptionRecord(
                        consumption_id="cons-exhaust-after-source",
                        budget_id="budget-1",
                        research_run_id="run-1",
                        resource_type="REQUEST",
                        amount=remaining,
                        unit="count",
                        occurred_at=CREATED_AT,
                        provenance="mr6a-exhaust-between-source-and-dispatch",
                    ),
                    issued,
                )
            uow.commit()
        worker = RecordingWorkerPort(handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            factory, worker, NeverInvokedModel(), clock=FixedClock()
        )
        command = _command()
        controller.start(command)
        controller.step(command)
        self.assertEqual(len(worker.calls), 0)

    def test_permanent_family_promotion_remains_human_only(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        drafted = DraftExploratoryHypothesis(factory, clock=FixedClock()).execute(
            DraftExploratoryHypothesisCommand(
                research_run_id="run-1",
                proposed_family_name="Unmapped Identity Differential Coupling",
                proposed_family_rationale="Registry-external identity anomaly family candidate.",
                signals=(
                    ExploratorySignalInput(
                        signal_id="sig-1",
                        kind=ExploratorySignalKind.IDENTITY_ANOMALY.value,
                        description="Identity neighborhood around an object node drifted.",
                        source_refs=("change-1",),
                        target_node_kind="OBJECT",
                    ),
                ),
                correlation_id="corr-mr6a-family",
            )
        )
        families_before = SEED_FAMILY_COUNT
        denied = PromoteExploratoryFamily(factory, clock=FixedClock()).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="control-plane",
                actor_type=ActorType.CONTROL_PLANE,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertFalse(denied.promoted)
        promoted = PromoteExploratoryFamily(factory, clock=FixedClock()).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertTrue(promoted.promoted)
        errors: list[BaseException] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [
                pool.submit(
                    PromoteExploratoryFamily(factory, clock=FixedClock()).execute,
                    PromoteExploratoryFamilyCommand(
                        research_run_id="run-1",
                        hypothesis_id=drafted.hypothesis_id,
                        reviewer_id="operator-1",
                        actor_type=ActorType.HUMAN_OPERATOR,
                        decision=ApprovalDecision.APPROVE,
                    ),
                )
                for _ in range(10)
            ]
            for fut in as_completed(futs):
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
        self.assertEqual(errors, [])
        with factory.open() as uow:
            names = [
                record.name
                for record in uow.hunter_families.list_enabled()
                if record.name == "Unmapped Identity Differential Coupling"
            ]
            uow.rollback()
        self.assertEqual(len(names), 1)
        collision = PromoteExploratoryFamily(factory, clock=FixedClock()).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertIn(collision.reason_code, {"ALREADY_PROMOTED", "FAMILY_NAME_ALREADY_REGISTERED"})
        with factory.open() as uow:
            enabled = len(uow.hunter_families.list_enabled())
            uow.rollback()
        self.assertEqual(enabled, families_before + 1)

    def test_out_of_scope_compiled_scope_does_not_dispatch_worker(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        worker = RecordingWorkerPort(handler=_authz_handler("vulnerable"))
        controller = AutonomousResearchController(
            factory, worker, NeverInvokedModel(), clock=FixedClock()
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
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["validated_candidates"], 0)


if __name__ == "__main__":
    unittest.main()
