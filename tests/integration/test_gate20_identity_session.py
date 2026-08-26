"""PostgreSQL GATE 20 identity/session tests. SQLite is not a substitute.

Does not mark GATE 20 PASS. Formal PASS requires later Kali validation.
"""

from __future__ import annotations

import json
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

from e2e.lab.http_auth_lab import (
    ALICE_PASSWORD,
    ALICE_USERNAME,
    BOB_PASSWORD,
    BOB_USERNAME,
    SESSION_COOKIE_NAME,
    Gate20AuthLab,
)
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.data.records import ExperimentRecord
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from zest.platform.secrets import CompositeSecretPort, EnvSecretResolver, InMemorySecretStore
from zest.research.http_authentication import plan_http_login
from zest.research.http_transaction import HttpRequestTemplate, plan_http_transaction
from zest.research.identity_session import HttpFormLoginProfile, Identity, SessionState, local_dev_credential
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()
NOW = FixedClock().now()
PROFILE = HttpFormLoginProfile(
    profile_id="profile-form",
    path="/login",
    username_field="username",
    password_secret_name="login_password",
    session_cookie_name=SESSION_COOKIE_NAME,
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
                path_prefix=None,
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


def _alice() -> Identity:
    return Identity(
        identity_id="id-alice",
        actor_reference="actor-alice",
        target_reference="target-1",
        credential_reference=local_dev_credential("ALICE_PASSWORD"),
        authentication_profile_reference=PROFILE.profile_id,
    )


def _bob() -> Identity:
    return Identity(
        identity_id="id-bob",
        actor_reference="actor-bob",
        target_reference="target-1",
        credential_reference=local_dev_credential("BOB_PASSWORD"),
        authentication_profile_reference=PROFILE.profile_id,
    )


def _secrets() -> CompositeSecretPort:
    return CompositeSecretPort(
        InMemorySecretStore(),
        EnvSecretResolver({"ALICE_PASSWORD": ALICE_PASSWORD, "BOB_PASSWORD": BOB_PASSWORD}),
    )


def _add_experiment(factory: PostgresUnitOfWorkFactory, experiment_id: str) -> None:
    with factory.open() as uow:
        uow.experiments.insert(
            ExperimentRecord(
                experiment_id=experiment_id,
                research_run_id="run-1",
                hypothesis_id="hyp-1",
                budget_id="budget-1",
                execution_state="PLANNED",
                created_at=NOW,
            )
        )
        uow.commit()


def _persisted_blob(factory: PostgresUnitOfWorkFactory) -> str:
    with factory.open() as uow:
        payload = {
            "plans": [dict(item.arguments) for item in [uow.experiment_plans.get("exp-1")] if item],
            "sessions": [],
            "results": [],
            "observations": [],
            "audit": [],
        }
        alice = uow.session_contexts.get("session-alice")
        if alice is not None:
            payload["sessions"].append(
                {
                    "state": alice.state,
                    "secret_name": alice.secret_name,
                    "origin": alice.origin,
                }
            )
        for experiment_id in ("exp-1", "exp-2"):
            for item in uow.worker_results.list_for_experiment(experiment_id):
                payload["results"].append({"raw": item.raw_result, "diag": item.diagnostics})
            for item in uow.observations.list_for_experiment(experiment_id):
                payload["observations"].append(item.payload)
        uow.rollback()
    return json.dumps(payload, default=str)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate20IdentitySessionTests(unittest.TestCase):
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
        self.lab = Gate20AuthLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_login_and_bound_request_without_secret_persistence(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        secrets = _secrets()
        use_case = ExecutePlannedExperiment(
            factory, _local_worker(), clock=FixedClock(), secret_port=secrets
        )
        login = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan_http_login(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    identity=_alice(),
                    profile=PROFILE,
                    username=ALICE_USERNAME,
                    authorized_origin=self.origin,
                    session_context_id="session-alice",
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(login.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        _add_experiment(factory, "exp-2")
        authed = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=plan_http_transaction(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    template=HttpRequestTemplate(
                        authorized_origin=self.origin,
                        method="GET",
                        path="/me",
                        session_context_reference="session-alice",
                    ),
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(authed.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        with factory.open() as uow:
            session = uow.session_contexts.get("session-alice")
            uow.rollback()
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.state, SessionState.ACTIVE.value)
        blob = _persisted_blob(factory)
        self.assertNotIn(ALICE_PASSWORD, blob)
        self.assertNotIn(BOB_PASSWORD, blob)
        self.assertNotIn("sid=", blob)
        _add_experiment(factory, "exp-3")
        denied = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-3",
                plan=plan_http_transaction(
                    "hyp-1",
                    budget_id="budget-1",
                    target_reference="target-1",
                    template=HttpRequestTemplate(
                        authorized_origin=self.origin,
                        method="GET",
                        path="/me",
                        session_context_reference="session-alice",
                    ),
                ),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-bob",
                identity=_bob(),
            )
        )
        self.assertEqual(denied.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(denied.core_reason_code, ReasonCode.SCHEMA_MISMATCH)


if __name__ == "__main__":
    unittest.main()
