"""Canonical MR-5/MR-6 durable promotion + exploratory research on real PostgreSQL."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research_os.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_exploratory_research import (
    ExecuteExploratoryResearch,
    ExecuteExploratoryResearchCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from research_os.application.promotion_pipeline import (
    AdvancePromotionCommand,
    PromotionOutcome,
    PromotionPipeline,
)
from research_os.core.enums import ExecutionDecisionKind, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from research_os.data.postgres.engine import create_sync_engine
from research_os.data.records import IssuedBudgetRecord
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.assessment import AssessmentOutcome
from research_os.research.exploratory import ExploratorySignalKind
from research_os.research.orchestration import OrchestrationBounds, StopReason
from research_os.research.planning import plan_diagnostic_echo
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT

TEST_URL = configured_test_url()
ORIGIN = "http://127.0.0.1:9"


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


def _plan():
    return plan_diagnostic_echo(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        message="ping",
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


def _bounds() -> OrchestrationBounds:
    return OrchestrationBounds(
        max_cycles=2,
        max_experiments=4,
        max_model_calls=0,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=2,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )


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
        raw = {
            "mode": mode if mode in {"vulnerable", "secure_only", "redirect"} else "vulnerable",
            "authorized_origin": origin,
            "owner_request": {"status": 200, "object_owner": own},
            "cross_object_request": {"status": cross_status, "object_owner": cross_owner},
            "secure_control": {"status": 403},
            "unauthenticated_control": {"status": 401},
        }
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
                "raw_result": raw,
            },
            exit_code=0,
        )

    return handler


@unittest.skipUnless(
    TEST_URL,
    "RESEARCH_OS_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class CanonicalPromotionPostgresTests(unittest.TestCase):
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

    def test_supported_path_is_restart_safe_and_does_not_create_finding(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        worker = RecordingWorkerPort()
        ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_plan(),
                scope=_allow_scope(),
            )
        )
        feedback = EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id="exp-1")
        )
        self.assertEqual(feedback.assessment_outcome, AssessmentOutcome.CONSISTENT_WITH_PREDICTION)
        pipeline = PromotionPipeline(factory, clock=FixedClock(), worker=RecordingWorkerPort())
        started = pipeline.on_assessment(feedback)
        self.assertEqual(started.outcome, PromotionOutcome.VERIFICATION_STARTED)
        pipeline.on_assessment(feedback)
        advanced = pipeline.advance(
            AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope())
        )
        self.assertEqual(advanced[0].outcome, PromotionOutcome.FINDING_PROPOSAL_RECORDED)
        with factory.open() as uow:
            candidates = uow.candidates.list_for_research_run("run-1")
            verifications = uow.verifications.list_for_research_run("run-1")
            proposals = uow.finding_proposals.list_for_research_run("run-1")
            findings = uow.findings.list_for_research_run("run-1")
            promotions = uow.promotion_runs.list_for_research_run("run-1")
            uow.rollback()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].state, "VALIDATED")
        self.assertEqual(len(verifications), 1)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(findings), 0)
        self.assertEqual(len(promotions), 1)
        pipeline.advance(AdvancePromotionCommand(research_run_id="run-1", scope=_allow_scope()))
        with factory.open() as uow:
            self.assertEqual(len(uow.verifications.list_for_research_run("run-1")), 1)
            self.assertEqual(len(uow.finding_proposals.list_for_research_run("run-1")), 1)
            self.assertEqual(len(uow.findings.list_for_research_run("run-1")), 0)
            self.assertEqual(len(uow.promotion_runs.list_for_research_run("run-1")), 1)
            uow.rollback()


@unittest.skipUnless(
    TEST_URL,
    "RESEARCH_OS_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class CanonicalExploratoryPostgresTests(unittest.TestCase):
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
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id="budget-exp",
                    research_run_id="run-1",
                    max_requests=40,
                    max_tool_calls=40,
                    max_runtime_ms=10_000,
                    max_concurrency=1,
                    issued_at=CREATED_AT,
                )
            )
            uow.commit()

    def _draft(self):
        factory = PostgresUnitOfWorkFactory(self.engine)
        return DraftExploratoryHypothesis(factory, clock=FixedClock()).execute(
            DraftExploratoryHypothesisCommand(
                research_run_id="run-1",
                proposed_family_name="exploratory.cross_object.read.v1",
                proposed_family_rationale="Registry-external object-access anomaly.",
                signals=(
                    ExploratorySignalInput(
                        signal_id="sig-1",
                        kind=ExploratorySignalKind.IDENTITY_ANOMALY.value,
                        description="Identity neighborhood around an object node drifted.",
                        source_refs=("change-1",),
                        target_node_kind="OBJECT",
                    ),
                ),
                correlation_id="corr-mr6-pg",
            )
        )

    def _execute(self, drafted, *, mode="vulnerable", scope=None, compiled_scope="default"):
        worker = RecordingWorkerPort(handler=_authz_handler(mode))
        args = {
            "authorized_origin": ORIGIN,
            "actor": "alice",
            "own_object": "alice",
            "cross_object": "bob",
            "mode": "vulnerable" if mode == "deceptive" else mode,
        }
        result = ExecuteExploratoryResearch(
            PostgresUnitOfWorkFactory(self.engine), worker, clock=FixedClock()
        ).execute(
            ExecuteExploratoryResearchCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                budget_id="budget-exp",
                target_reference="target-1",
                scope=scope or _allow_scope(),
                bounds=_bounds(),
                compile_arguments=args,
                compiled_scope=_compiled_scope(ORIGIN) if compiled_scope == "default" else compiled_scope,
                correlation_id="corr-mr6-pg-exec",
            )
        )
        return result, worker

    def test_vulnerable_fixture_reaches_evidence_candidate_verification(self) -> None:
        drafted = self._draft()
        result, port = self._execute(drafted, mode="vulnerable")
        self.assertEqual(result.compiler_outcome, "COMPILED")
        self.assertFalse(result.used_diagnostic_echo)
        self.assertGreaterEqual(len(port.calls), 1)
        self.assertNotEqual(port.calls[0]["request"].get("worker_capability"), "diagnostic.echo")
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            families = {record.name for record in uow.hunter_families.list_enabled()}
            evidence = uow.evidence.list_for_research_run("run-1")
            candidates = uow.candidates.list_for_research_run("run-1")
            verifications = uow.verifications.list_for_research_run("run-1")
            findings = uow.findings.list_for_research_run("run-1")
            uow.rollback()
        self.assertNotIn("exploratory.cross_object.read.v1", families)
        self.assertTrue(evidence)
        self.assertTrue(candidates)
        self.assertTrue(verifications)
        self.assertEqual(candidates[0].state, "VALIDATED")
        self.assertEqual(findings, [])

    def test_secure_and_deceptive_fixtures_false_finding_zero(self) -> None:
        for mode in ("secure_only", "deceptive"):
            with self.subTest(mode=mode):
                truncate_spine(self.engine)
                factory = PostgresUnitOfWorkFactory(self.engine)
                with factory.open() as uow:
                    seed_authorized_spine(uow)
                    uow.issued_budgets.insert(
                        IssuedBudgetRecord(
                            budget_id="budget-exp",
                            research_run_id="run-1",
                            max_requests=40,
                            max_tool_calls=40,
                            max_runtime_ms=10_000,
                            max_concurrency=1,
                            issued_at=CREATED_AT,
                        )
                    )
                    uow.commit()
                drafted = self._draft()
                self._execute(drafted, mode=mode)
                with factory.open() as uow:
                    findings = uow.findings.list_for_research_run("run-1")
                    proposals = uow.finding_proposals.list_for_research_run("run-1")
                    uow.rollback()
                self.assertEqual(findings, [])
                self.assertEqual(proposals, [])

    def test_core_deny_invokes_zero_workers(self) -> None:
        drafted = self._draft()
        result, port = self._execute(
            drafted, scope=_deny_scope(), compiled_scope=None
        )
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(result.core_decision, ExecutionDecisionKind.DENY.value)
        self.assertEqual(result.stop_reason, StopReason.CORE_BLOCKED.value)
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
            self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            uow.rollback()


if __name__ == "__main__":
    unittest.main()
