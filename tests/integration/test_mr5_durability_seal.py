"""MR-5 durability seal: concurrent PostgreSQL uniqueness and ARC continuation."""

from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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
from zest.application.errors import ApplicationError
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from zest.application.promotion_pipeline import (
    AdvancePromotionCommand,
    PromotionOutcome,
    PromotionPipeline,
)
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.core.enums import ActorType, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.errors import PersistenceConflictError
from zest.data.postgres.engine import create_sync_engine
from zest.research.orchestration import OrchestrationBounds, OrchestrationState, StopReason
from zest.research.planning import plan_diagnostic_echo
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

TEST_URL = configured_test_url()
CONCURRENCY_REPETITIONS = 10


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _plan():
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message="ping",
    )


def _arc_bounds() -> OrchestrationBounds:
    return OrchestrationBounds(
        max_cycles=1,
        max_experiments=2,
        max_model_calls=20,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class Mr5DurabilitySealPostgresTests(unittest.TestCase):
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

    def _seed_assessment(self):
        factory = PostgresUnitOfWorkFactory(self.engine)
        ExecutePlannedExperiment(
            factory, RecordingWorkerPort(), clock=FixedClock()
        ).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        return factory, feedback

    def _counts(self, factory: PostgresUnitOfWorkFactory) -> dict[str, int]:
        with factory.open() as uow:
            evidence = uow.evidence.list_for_research_run("run-1")
            candidates = uow.candidates.list_for_research_run("run-1")
            bindings = 0
            for candidate in candidates:
                bindings += len(candidate.evidence_ids)
            counts = {
                "promotion_runs": len(uow.promotion_runs.list_for_research_run("run-1")),
                "evidence": len(evidence),
                "candidates": len(candidates),
                "verifications": len(uow.verifications.list_for_research_run("run-1")),
                "proposals": len(uow.finding_proposals.list_for_research_run("run-1")),
                "findings": len(uow.findings.list_for_research_run("run-1")),
                "experiments": len(uow.experiments.list_for_research_run("run-1")),
                "candidate_evidence_bindings": bindings,
                "candidate_states": tuple(item.state for item in candidates),
            }
            uow.rollback()
        return counts

    def _assert_single_authoritative_promotion(self, factory, *, verifying=False) -> None:
        counts = self._counts(factory)
        self.assertEqual(counts["promotion_runs"], 1)
        self.assertGreaterEqual(counts["evidence"], 1)
        self.assertEqual(counts["candidates"], 1)
        self.assertEqual(counts["candidate_evidence_bindings"], 1)
        if verifying:
            self.assertEqual(counts["candidate_states"], ("VERIFYING",))
        self.assertEqual(counts["findings"], 0)

    def test_concurrent_on_assessment_creates_one_candidate(self) -> None:
        for attempt in range(CONCURRENCY_REPETITIONS):
            with self.subTest(attempt=attempt):
                self.setUp()
                factory, feedback = self._seed_assessment()
                errors: list[BaseException] = []
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futs = [
                        pool.submit(PromotionPipeline(factory).on_assessment, feedback)
                        for _ in range(4)
                    ]
                    for fut in as_completed(futs):
                        try:
                            fut.result()
                        except Exception as exc:  # noqa: BLE001 — collect then assert
                            errors.append(exc)
                unexpected = [
                    exc
                    for exc in errors
                    if not isinstance(exc, PersistenceConflictError)
                ]
                self.assertEqual(unexpected, [])
                self._assert_single_authoritative_promotion(factory, verifying=True)
                self.assertEqual(self._counts(factory)["verifications"], 0)

    def test_concurrent_advance_creates_one_verification_and_proposal(self) -> None:
        for attempt in range(CONCURRENCY_REPETITIONS):
            with self.subTest(attempt=attempt):
                self.setUp()
                factory, feedback = self._seed_assessment()
                PromotionPipeline(factory, worker=RecordingWorkerPort()).on_assessment(
                    feedback
                )
                cmd = AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
                errors: list[BaseException] = []
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futs = [
                        pool.submit(
                            PromotionPipeline(
                                factory, worker=RecordingWorkerPort()
                            ).advance,
                            cmd,
                        )
                        for _ in range(3)
                    ]
                    for fut in as_completed(futs):
                        try:
                            fut.result()
                        except Exception as exc:  # noqa: BLE001
                            errors.append(exc)
                self.assertEqual(errors, [])
                counts = self._counts(factory)
                self.assertEqual(counts["candidates"], 1)
                self.assertEqual(counts["verifications"], 1)
                self.assertLessEqual(counts["proposals"], 1)
                self.assertEqual(counts["proposals"], 1)
                self.assertEqual(counts["findings"], 0)
                self.assertEqual(counts["candidate_states"], ("VALIDATED",))
                self.assertEqual(counts["experiments"], 2)

    def test_concurrent_finding_proposal_admission_is_idempotent(self) -> None:
        factory, feedback = self._seed_assessment()
        pipeline = PromotionPipeline(factory, worker=RecordingWorkerPort())
        pipeline.on_assessment(feedback)
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        with factory.open() as uow:
            candidate_id = uow.candidates.list_for_research_run("run-1")[0].candidate_id
            uow.rollback()
        errors: list[BaseException] = []
        results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [
                pool.submit(
                    SubmitFindingProposal(factory, clock=FixedClock()).execute,
                    SubmitFindingProposalCommand(candidate_id=candidate_id),
                )
                for _ in range(4)
            ]
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
        self.assertEqual(errors, [])
        proposal_ids = {item.proposal_id for item in results}
        self.assertEqual(len(proposal_ids), 1)
        self.assertEqual(self._counts(factory)["proposals"], 1)
        self.assertEqual(self._counts(factory)["findings"], 0)

    def test_restart_at_each_durable_boundary(self) -> None:
        factory, feedback = self._seed_assessment()
        PromotionPipeline(factory).on_assessment(feedback)
        self._assert_single_authoritative_promotion(factory, verifying=True)
        restarted = PromotionPipeline(factory, worker=RecordingWorkerPort())
        advanced = restarted.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(advanced[0].outcome, PromotionOutcome.FINDING_PROPOSAL_RECORDED)
        again = PromotionPipeline(factory, worker=RecordingWorkerPort()).advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(again, ())
        counts = self._counts(factory)
        self.assertEqual(counts["candidates"], 1)
        self.assertEqual(counts["verifications"], 1)
        self.assertEqual(counts["proposals"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["promotion_runs"], 1)

    def test_reproduction_assessment_does_not_create_second_promotion_tree(self) -> None:
        factory, feedback = self._seed_assessment()
        pipeline = PromotionPipeline(factory, worker=RecordingWorkerPort())
        pipeline.on_assessment(feedback)
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        with factory.open() as uow:
            promotions = uow.promotion_runs.list_for_research_run("run-1")
            assessments = uow.hypothesis_assessments.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(len(promotions), 1)
        self.assertEqual(len(assessments), 2)
        self.assertEqual(promotions[0].assessment_id, feedback.assessment_id)
        self.assertNotEqual(
            promotions[0].reproduction_experiment_id,
            promotions[0].original_experiment_id,
        )

    def test_arc_production_path_reaches_proposal_never_finding(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        controller = AutonomousResearchController(
            factory,
            RecordingWorkerPort(),
            ScriptedModelPort(),
            clock=FixedClock(),
        )
        result = controller.run_bounded(
            StartAutonomousResearchCommand(
                research_run_id="run-1",
                budget_id="budget-1",
                target_reference="target-1",
                scope=_allow_scope(),
                bounds=_arc_bounds(),
            )
        )
        self.assertEqual(result.state, OrchestrationState.COMPLETED.value)
        self.assertEqual(result.stop_reason, StopReason.MAX_CYCLES_REACHED.value)
        counts = self._counts(factory)
        self.assertEqual(counts["candidates"], 1)
        self.assertEqual(counts["verifications"], 1)
        self.assertEqual(counts["proposals"], 1)
        self.assertEqual(counts["findings"], 0)
        self.assertEqual(counts["promotion_runs"], 1)
        with factory.open() as uow:
            proposal_id = uow.finding_proposals.list_for_research_run("run-1")[0].proposal_id
            uow.rollback()
        with self.assertRaises(ApplicationError):
            FinalizeFinding(factory, clock=FixedClock()).execute(
                FinalizeFindingCommand(
                    proposal_id=proposal_id,
                    decided_by="operator-1",
                    actor_type=ActorType.HUMAN_OPERATOR,
                )
            )
        self.assertEqual(self._counts(factory)["findings"], 0)

    def test_promotion_run_cannot_point_at_missing_authoritative_objects(self) -> None:
        factory, feedback = self._seed_assessment()
        pipeline = PromotionPipeline(factory, worker=RecordingWorkerPort())
        pipeline.on_assessment(feedback)
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        with factory.open() as uow:
            run = uow.promotion_runs.list_for_research_run("run-1")[0]
            self.assertIsNotNone(uow.evidence.get(run.evidence_id))
            self.assertIsNotNone(uow.candidates.get(run.candidate_id))
            self.assertIsNotNone(uow.verifications.get(run.verification_id))
            self.assertIsNotNone(uow.finding_proposals.get(run.finding_proposal_id))
            uow.rollback()
