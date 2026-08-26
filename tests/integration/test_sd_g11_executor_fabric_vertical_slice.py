"""SD-G11 production executor fabric vertical slice.

This is attack-period SD-G11 evidence, not old infrastructure GATE 11 and not
G21 browser/application-state maturity.
"""

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

from e2e.lab.http_workflow_lab import (
    DECEPTIVE_200,
    REDIRECT_BOUNDARY,
    SECURE_ROLE_ENFORCEMENT,
    TRUE_ROLE_BYPASS,
    WorkflowLab,
)
from e2e.lab.https_transaction_lab import HttpsTransactionLab
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    truncate_spine,
)
from zest.application.executor_fabric_assessment import (
    AssessExecutorFabricExperiment,
    AssessExecutorFabricExperimentCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.data.records import (
    AuthorizationSourceRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from zest.research.http_transaction import plan_http_transaction_read
from zest.research.planning import HTTP_STATE_TRANSITION_CLAIM, plan_state_transition
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(origin: str, *, deny_control: bool = False):
    parsed = urlsplit(origin)
    rules = [
        ScopeRuleDefinition(
            rule_id="rule-allow",
            effect=ScopeRuleEffect.ALLOW,
            scheme=parsed.scheme or "http",
            host=parsed.hostname or "127.0.0.1",
            port=parsed.port,
            path_prefix=None,
            source_reference="scope-src",
        )
    ]
    if deny_control:
        rules.append(
            ScopeRuleDefinition(
                rule_id="rule-deny-control",
                effect=ScopeRuleEffect.DENY,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix="/control",
                source_reference="scope-src",
            )
        )
    return compile_scope_rules(tuple(rules))


def _local_worker() -> RecordingWorkerPort:
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                module=PACKAGED_WORKER_MODULE,
                default_timeout_ms=5_000,
            )
        )
    )


def _seed_spine(factory: PostgresUnitOfWorkFactory, prefix: str) -> None:
    with factory.open() as uow:
        uow.programs.insert(
            ProgramRecord(
                program_id=f"{prefix}-prog",
                created_at=FixedClock().now(),
                name=prefix,
            )
        )
        uow.authorization_sources.insert(
            AuthorizationSourceRecord(
                authorization_source_id=f"{prefix}-as",
                program_id=f"{prefix}-prog",
                state="ACTIVE",
                provenance_reference=f"written-local-lab-auth-{prefix}",
                created_at=FixedClock().now(),
            )
        )
        uow.research_runs.insert(
            ResearchRunRecord(
                research_run_id=f"{prefix}-run",
                program_id=f"{prefix}-prog",
                authorization_source_id=f"{prefix}-as",
                initiated_by_actor_id="operator-1",
                initiated_by_actor_type="HUMAN_OPERATOR",
                started_at=FixedClock().now(),
            )
        )
        uow.issued_budgets.insert(
            IssuedBudgetRecord(
                budget_id=f"{prefix}-budget",
                research_run_id=f"{prefix}-run",
                max_requests=40,
                max_tool_calls=40,
                max_runtime_ms=60_000,
                max_concurrency=1,
                issued_at=FixedClock().now(),
            )
        )
        uow.hypotheses.insert(
            HypothesisRecord(
                hypothesis_id=f"{prefix}-hyp",
                research_run_id=f"{prefix}-run",
                claim=HTTP_STATE_TRANSITION_CLAIM,
                origin_reference=f"human-seed-{prefix}",
                created_at=FixedClock().now(),
            )
        )
        uow.commit()


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; SD-G11 executor fabric integration skipped "
    "(SQLite is not a substitute)",
)
class SDG11ExecutorFabricVerticalSliceTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def _run_fixture(
        self,
        fixture_kind: str,
        prefix: str,
        *,
        area: str = "workflow",
        deny_control: bool = False,
    ):
        factory = PostgresUnitOfWorkFactory(self.engine)
        _seed_spine(factory, prefix)
        worker = _local_worker()
        lab = WorkflowLab(fixture_kind)
        origin = lab.start()
        try:
            plan = plan_state_transition(
                f"{prefix}-hyp",
                budget_id=f"{prefix}-budget",
                target_reference=origin,
                authorized_origin=origin,
                actor="alice",
                resource_id="R1",
                transition="approve",
                area=area,
            )
            PreparePlannedExperiment(factory, clock=FixedClock()).execute(
                PreparePlannedExperimentCommand(
                    experiment_id=f"{prefix}-exp",
                    research_run_id=f"{prefix}-run",
                    plan=plan,
                )
            )
            outcome = ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
                ExecutePlannedExperimentCommand(
                    experiment_id=f"{prefix}-exp",
                    plan=plan,
                    scope=_allow_scope(),
                    compiled_scope=_compiled_scope(origin, deny_control=deny_control),
                )
            )
            assessment = AssessExecutorFabricExperiment(factory).execute(
                AssessExecutorFabricExperimentCommand(f"{prefix}-exp")
            )
            with factory.open() as uow:
                worker_results = uow.worker_results.list_for_experiment(f"{prefix}-exp")
                uow.rollback()
            return outcome, assessment, lab, worker_results
        finally:
            lab.stop()

    def test_vulnerable_secure_and_deceptive_fixtures_assess_without_dilution(self) -> None:
        for fixture_kind, prefix in (
            (TRUE_ROLE_BYPASS, "sdg11vuln"),
            (SECURE_ROLE_ENFORCEMENT, "sdg11secure"),
            (DECEPTIVE_200, "sdg11deceptive"),
        ):
            outcome, assessment, lab, worker_results = self._run_fixture(fixture_kind, prefix)
            self.assertIn(
                outcome.status,
                {ResearchLoopStatus.OBSERVATION_PRODUCED, ResearchLoopStatus.NO_OBSERVATION},
            )
            self.assertEqual(assessment.assessment_status, "PASS")
            self.assertEqual(assessment.assessment["replay_class"], "ENVIRONMENT_SENSITIVE")
            self.assertEqual(
                assessment.assessment["capability_surface"],
                ("http.state_transition",),
            )
            self.assertEqual(len(worker_results), 1)
            self.assertEqual(worker_results[0].status, "SUCCEEDED")
            self.assertGreater(lab.http_request_count(), 0)
            self.assertFalse(lab.followed_external())

    def test_https_api_transaction_is_manifested_and_assessed(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        prefix = "sdg11https"
        _seed_spine(factory, prefix)
        worker = _local_worker()
        lab = HttpsTransactionLab()
        origin = lab.start()
        try:
            plan = plan_http_transaction_read(
                f"{prefix}-hyp",
                budget_id=f"{prefix}-budget",
                target_reference=origin,
                authorized_origin=origin,
                path="/ok",
            )
            PreparePlannedExperiment(factory, clock=FixedClock()).execute(
                PreparePlannedExperimentCommand(
                    experiment_id=f"{prefix}-exp",
                    research_run_id=f"{prefix}-run",
                    plan=plan,
                )
            )
            outcome = ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
                ExecutePlannedExperimentCommand(
                    experiment_id=f"{prefix}-exp",
                    plan=plan,
                    scope=_allow_scope(),
                    compiled_scope=_compiled_scope(origin),
                )
            )
            assessment = AssessExecutorFabricExperiment(factory).execute(
                AssessExecutorFabricExperimentCommand(f"{prefix}-exp")
            )
            with factory.open() as uow:
                worker_results = uow.worker_results.list_for_experiment(f"{prefix}-exp")
                uow.rollback()
        finally:
            lab.stop()

        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(assessment.assessment_status, "PASS")
        self.assertEqual(assessment.assessment["replay_class"], "DETERMINISTIC_REPLAY")
        self.assertEqual(assessment.assessment["capability_surface"], ("http.transaction",))
        self.assertEqual(worker_results[0].status, "SUCCEEDED")
        self.assertTrue(str(worker_results[0].raw_result.get("url")).startswith("https://"))

    def test_scope_escape_fixture_is_blocked_by_core_issued_envelope(self) -> None:
        outcome, assessment, lab, worker_results = self._run_fixture(
            TRUE_ROLE_BYPASS,
            "sdg11scope",
            deny_control=True,
        )

        self.assertEqual(outcome.status, ResearchLoopStatus.NO_OBSERVATION)
        self.assertEqual(assessment.assessment_status, "PASS")
        self.assertIn("SCOPE_ESCAPE_BLOCKED_BY_ENVELOPE", assessment.reason_codes)
        self.assertEqual(
            assessment.assessment["invariants"]["contacted_outside_envelope_count"],
            0,
        )
        self.assertEqual(worker_results[0].status, "EXECUTION_FAILED")
        self.assertTrue(all(not item.path.startswith("/control") for item in lab.ledger))

    def test_redirect_fixture_requires_reauthorization_and_is_not_followed(self) -> None:
        outcome, assessment, lab, worker_results = self._run_fixture(
            REDIRECT_BOUNDARY,
            "sdg11redirect",
            area="redirect",
        )

        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        self.assertEqual(assessment.assessment_status, "PASS")
        self.assertIn("REDIRECT_REAUTHORIZATION_REQUIRED", assessment.reason_codes)
        self.assertEqual(worker_results[0].status, "REAUTHORIZATION_REQUIRED")
        self.assertFalse(lab.followed_external())


if __name__ == "__main__":
    unittest.main()
