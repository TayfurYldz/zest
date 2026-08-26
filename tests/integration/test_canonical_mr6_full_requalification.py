"""Independent Canonical MR-6 Full requalification lab.

PostgreSQL required. Does not use ExploratorySignalInput or operator
compile_arguments on the ARC-connected path. Source production is a fixture
Worker action; ARC.step() owns exploratory selection and follow-up.
"""

from __future__ import annotations

import sys
import unittest
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
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.data.records import DiscoveryFactRecord, DiscoveryFactSourceRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.identity_anomaly import (
    IDENTITY_ANOMALY_CLAIM,
    is_exploratory_hypothesis_origin,
)
from zest.research.orchestration import OrchestrationBounds
from zest.research.planning import (
    HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION,
    HTTP_AUTHORIZATION_EXPECTED_OBSERVATION,
    plan_authorization_differential,
)
from zest.tools.capabilities import HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY
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
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.spine import CREATED_AT

TEST_URL = configured_test_url()
ORIGIN = "http://127.0.0.1:9"
SEED_FAMILY_COUNT = len(SEED_FAMILIES)


class NeverInvokedModel:
    def complete(self, request):
        raise AssertionError("ModelPort must not be invoked for registry-external admission")


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


def _controller(factory: PostgresUnitOfWorkFactory, *, mode: str, worker=None, model=None):
    return AutonomousResearchController(
        factory,
        worker or RecordingWorkerPort(handler=_authz_handler(mode)),
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
            "exploratory_opportunities": len(
                [
                    item
                    for item in opportunities
                    if item.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
                ]
            ),
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
        }
        uow.rollback()
    return counts


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class CanonicalMr6FullRequalificationPostgresTests(unittest.TestCase):
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

    def test_vulnerable_lab_provenance_chain_and_discriminating_plan(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        controller = _controller(factory, mode="vulnerable")
        command = _command()
        controller.start(command)
        result = controller.step(command)
        self.assertIsNotNone(result.hypothesis_id)
        self.assertIsNotNone(result.experiment_id)
        with factory.open() as uow:
            authz_obs = [
                item
                for item in uow.observations.list_for_research_run("run-1")
                if item.observation_kind == "HTTP_AUTHORIZATION_DIFFERENTIAL"
            ]
            self.assertGreaterEqual(len(authz_obs), 1)
            candidates = [
                item
                for item in uow.opportunity_selection_candidates.list_for_research_run("run-1")
                if item.source_system == "REGISTRY_EXTERNAL_ANOMALY"
            ]
            self.assertEqual(len(candidates), 1)
            source_id = candidates[0].source_refs[0]
            source = uow.observations.get(source_id)
            self.assertIsNotNone(source)
            self.assertEqual(source.observation_kind, "HTTP_AUTHORIZATION_DIFFERENTIAL")
            worker_result = uow.worker_results.get(source.worker_result_id)
            self.assertIsNotNone(worker_result)
            self.assertEqual(worker_result.research_run_id, "run-1")
            opportunities = [
                item
                for item in uow.research_opportunities.list_for_research_run("run-1")
                if item.opportunity_kind == OpportunityKind.REGISTRY_EXTERNAL_EXPLORATORY.value
            ]
            self.assertEqual(len(opportunities), 1)
            self.assertEqual(opportunities[0].source_refs, (source.observation_id,))
            hypothesis = uow.hypotheses.get(result.hypothesis_id)
            self.assertTrue(is_exploratory_hypothesis_origin(hypothesis.origin_reference))
            self.assertEqual(hypothesis.claim, IDENTITY_ANOMALY_CLAIM)
            self.assertEqual(hypothesis.research_run_id, "run-1")
            plan = uow.experiment_plans.get(result.experiment_id)
            self.assertEqual(plan.required_capability, HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY)
            self.assertEqual(plan.expected_observation, HTTP_AUTHORIZATION_EXPECTED_OBSERVATION)
            self.assertEqual(
                plan.disconfirming_observation, HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION
            )
            self.assertEqual(plan.arguments["actor"], "alice")
            self.assertEqual(plan.arguments["cross_object"], "bob")
            self.assertNotIn("body", plan.arguments)
            self.assertNotIn("headers", plan.arguments)
            self.assertNotIn("query", plan.arguments)
            assessments = [
                item
                for item in uow.hypothesis_assessments.list_for_research_run("run-1")
                if item.hypothesis_id == result.hypothesis_id
            ]
            self.assertGreaterEqual(len(assessments), 1)
            self.assertIn(
                AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value,
                {item.assessment_outcome for item in assessments},
            )
            self.assertIn("benign representation", opportunities[0].unresolved_question)
            self.assertEqual(
                plan.disconfirming_observation, HTTP_AUTHORIZATION_DISCONFIRMING_OBSERVATION
            )
            evidence = uow.evidence.list_for_research_run("run-1")
            candidates_out = uow.candidates.list_for_research_run("run-1")
            verifications = uow.verifications.list_for_research_run("run-1")
            proposals = uow.finding_proposals.list_for_research_run("run-1")
            findings = uow.findings.list_for_research_run("run-1")
            self.assertGreaterEqual(len(evidence), 1)
            self.assertEqual(len([item for item in candidates_out if item.state == "VALIDATED"]), 1)
            self.assertEqual(len(verifications), 1)
            self.assertEqual(len(proposals), 1)
            self.assertEqual(len(findings), 0)
            self.assertEqual(proposals[0].research_run_id, "run-1")
            self.assertEqual(candidates_out[0].research_run_id, "run-1")
            uow.rollback()
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["validated_candidates"], 1)
        self.assertEqual(counts["proposals"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["families"], SEED_FAMILY_COUNT)

    def test_secure_and_deceptive_false_positive_ladder(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="secure_only")
        secure_command = _command()
        _controller(factory, mode="secure_only", model=ScriptedModelPort()).start(secure_command)
        _controller(factory, mode="secure_only", model=ScriptedModelPort()).step(secure_command)
        secure = _counts(factory)
        self.assertEqual(secure["exploratory_opportunities"], 0)
        self.assertEqual(secure["exploratory_hypotheses"], 0)
        self.assertEqual(secure["validated_candidates"], 0)
        self.assertEqual(secure["proposals"], 0)
        self.assertEqual(secure["findings"], 0)

        truncate_spine(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        _produce_source(factory, mode="deceptive")
        _controller(factory, mode="deceptive").start(_command())
        _controller(factory, mode="deceptive").step(_command())
        deceptive = _counts(factory)
        self.assertEqual(deceptive["exploratory_opportunities"], 1)
        self.assertEqual(deceptive["exploratory_hypotheses"], 1)
        self.assertEqual(deceptive["validated_candidates"], 0)
        self.assertEqual(deceptive["proposals"], 0)
        self.assertEqual(deceptive["findings"], 0)

    def test_known_family_appearing_later_does_not_mint_a_second_exploratory(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        command = _command(bounds=_bounds(max_cycles=4, max_model_calls=20))
        first = _controller(factory, mode="vulnerable")
        first.start(command)
        first.step(command)
        with factory.open() as uow:
            observations = uow.observations.list_for_research_run("run-1")
            uow.discovery_facts.insert(
                DiscoveryFactRecord(
                    fact_id="fact-http-later",
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
                    source_row_id="fact-src-later",
                    research_run_id="run-1",
                    fact_id="fact-http-later",
                    created_at=CREATED_AT,
                    observation_id=observations[0].observation_id,
                )
            )
            uow.commit()
        second = _controller(factory, mode="vulnerable", model=ScriptedModelPort())
        second.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["findings"], 0)

    def test_follow_up_timeout_is_operational_not_disconfirmation(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        worker = RecordingWorkerPort(outcome=invocation_outcome(InvocationStatus.TIMED_OUT))
        controller = _controller(factory, mode="vulnerable", worker=worker)
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
                    AssessmentOutcome.EXECUTION_UNUSABLE.value,
                    AssessmentOutcome.INCONCLUSIVE.value,
                    AssessmentOutcome.NEEDS_MORE_CONTEXT.value,
                }
            )
        )

    def test_unknown_outcome_does_not_blind_retry(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        worker = RecordingWorkerPort(handler=_authz_handler("vulnerable"))
        controller = _controller(factory, mode="vulnerable", worker=worker)
        command = _command(bounds=_bounds(max_cycles=4, max_model_calls=20))
        controller.start(command)
        first = controller.step(command)
        self.assertIsNotNone(first.experiment_id)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE execution_attempt SET state = 'UNKNOWN_OUTCOME' "
                    "WHERE research_run_id = 'run-1'"
                )
            )
        before = len(worker.calls)
        resumed = AutonomousResearchController(
            factory,
            worker,
            NeverInvokedModel(),
            clock=FixedClock(),
        ).step(command)
        self.assertEqual(len(worker.calls), before)
        self.assertEqual(resumed.stop_reason, "OPERATIONAL_FAILURE")
        counts = _counts(factory)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["validated_candidates"], 1)

    def test_restart_after_assessment_does_not_create_finding(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        _produce_source(factory, mode="vulnerable")
        command = _command()
        first = _controller(factory, mode="vulnerable")
        first.start(command)
        first.step(command)
        second = _controller(factory, mode="vulnerable")
        second.start(command)
        second.step(command)
        counts = _counts(factory)
        self.assertEqual(counts["exploratory_opportunities"], 1)
        self.assertEqual(counts["exploratory_hypotheses"], 1)
        self.assertEqual(counts["proposals"], 1)
        self.assertEqual(counts["findings"], 0)


if __name__ == "__main__":
    unittest.main()
