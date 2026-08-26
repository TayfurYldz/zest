"""PostgreSQL GATE 19 authorized HTTP substrate tests. SQLite is not a substitute.

Does not mark GATE 19 PASS. Formal PASS requires later Kali validation.
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

from e2e.lab.http_transaction_lab import Gate19HttpLab
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
)
from zest.application.executor_replay_manifest import (
    BuildExecutorReplayManifest,
    BuildExecutorReplayManifestCommand,
)
from zest.application.executor_replay_bundle import (
    BuildExecutorReplayBundle,
    BuildExecutorReplayBundleCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.plan_records import experiment_plan_from_record
from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from zest.research.http_transaction import (
    plan_http_transaction_read,
    replay_http_transaction_plan,
)
from zest.tools.registry import load_capability_registry
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_scope(origin: str, path_prefix: str | None = None):
    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix=path_prefix,
                source_reference="scope-src",
            ),
        )
    )


def _local_worker():
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                module=PACKAGED_WORKER_MODULE,
                default_timeout_ms=5_000,
            )
        )
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate19HttpTransactionTests(unittest.TestCase):
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
        self.lab = Gate19HttpLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_authorized_get_and_replay_binding(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        plan = plan_http_transaction_read(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=self.origin,
            path="/ok",
        )
        use_case = ExecutePlannedExperiment(factory, _local_worker(), clock=FixedClock())
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan,
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertTrue(outcome.observation_ids)
        with factory.open() as uow:
            stored = uow.experiment_plans.get("exp-1")
            observations = uow.observations.list_for_experiment("exp-1")
            worker_results = uow.worker_results.list_for_experiment("exp-1")
            uow.rollback()
        assert stored is not None
        loaded = experiment_plan_from_record(stored)
        replayed = replay_http_transaction_plan(loaded)
        capability = load_capability_registry().get("http.transaction")
        assert capability is not None
        self.assertEqual(stored.capability_version, capability.version)
        self.assertEqual(stored.capability_definition_fingerprint, capability.definition_fingerprint)
        self.assertEqual(replayed.capability_definition_fingerprint, loaded.capability_definition_fingerprint)
        blob = str(stored.arguments).lower()
        self.assertNotIn("password", blob)
        self.assertNotIn("cookie", blob)
        self.assertNotIn("bearer", blob)
        self.assertTrue(observations)
        for item in observations:
            payload = str(item.payload).lower()
            self.assertNotIn("password", payload)
            self.assertNotIn("cookie", payload)
        self.assertTrue(worker_results)
        for item in worker_results:
            self.assertNotIn("password", str(item.raw_result).lower())
            self.assertNotIn("cookie", str(item.diagnostics).lower())

        manifest = BuildExecutorReplayManifest(factory).execute(
            BuildExecutorReplayManifestCommand("exp-1")
        )
        repeated = BuildExecutorReplayManifest(factory).execute(
            BuildExecutorReplayManifestCommand("exp-1")
        )
        self.assertEqual(manifest.manifest_hash, repeated.manifest_hash)
        self.assertEqual(manifest.replay_class, "DETERMINISTIC_REPLAY")
        self.assertEqual(manifest.reason_codes, ("REPLAY_MANIFEST_READY",))
        self.assertEqual(manifest.manifest["attempts"][0]["worker_result"]["status"], "SUCCEEDED")
        self.assertNotIn("cookie", str(manifest.manifest).lower())
        self.assertNotIn("password", str(manifest.manifest).lower())

        bundle = BuildExecutorReplayBundle(factory).execute(
            BuildExecutorReplayBundleCommand("exp-1")
        )
        repeated_bundle = BuildExecutorReplayBundle(factory).execute(
            BuildExecutorReplayBundleCommand("exp-1")
        )
        self.assertEqual(bundle.bundle_hash, repeated_bundle.bundle_hash)
        self.assertEqual(bundle.manifest_hash, manifest.manifest_hash)
        self.assertEqual(bundle.replay_class, "DETERMINISTIC_REPLAY")
        self.assertEqual(bundle.bundle["request_template"]["template_state"], "PLAN_BOUND")
        self.assertEqual(
            bundle.bundle["request_template"]["capability_definition_fingerprint"],
            capability.definition_fingerprint,
        )
        self.assertFalse(bundle.bundle["replay_controls"]["auto_redispatch_allowed"])
        self.assertTrue(bundle.bundle["replay_controls"]["requires_core_authorization"])
        self.assertTrue(bundle.bundle["replay_controls"]["requires_redirect_reauthorization"])
        self.assertNotIn("cookie", str(bundle.bundle).lower())
        self.assertNotIn("password", str(bundle.bundle).lower())

    def test_out_of_scope_origin_denied(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        plan = plan_http_transaction_read(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            authorized_origin=self.origin,
            path="/ok",
        )
        worker = _local_worker()
        use_case = ExecutePlannedExperiment(factory, worker, clock=FixedClock())
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan,
                scope=_allow_scope(),
                compiled_scope=_compiled_scope("http://127.0.0.1:9"),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(len(worker.calls), 0)


if __name__ == "__main__":
    unittest.main()
