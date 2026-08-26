from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pathsetup  # noqa: F401

from e2e.lab.http_auth_lab import (
    ALICE_PASSWORD,
    ALICE_USERNAME,
    BOB_PASSWORD,
    BOB_USERNAME,
    SESSION_COOKIE_NAME,
    Gate20AuthLab,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.session_lifecycle import revoke_session
from zest.application.transition_a.http_authentication import HTTP_AUTHENTICATION_OBSERVATION_KIND
from zest.core.enums import ReasonCode, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.records import ExperimentRecord, ResearchRunRecord, SessionContextRecord
from zest.platform.secrets import CompositeSecretPort, EnvSecretResolver, InMemorySecretStore
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.compiler import ExperimentCompileError, ExperimentIntent, compile_experiment_intent
from zest.research.http_authentication import plan_http_login
from zest.research.http_transaction import HttpRequestTemplate, plan_http_transaction
from zest.research.identity_session import (
    CredentialReference,
    HttpFormLoginProfile,
    Identity,
    SessionState,
    local_dev_credential,
)
from zest.research.types import ResearchInputError
from zest.worker_runtime.python.runtime import build_result, utc_now_rfc3339
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self) -> datetime:
        return CREATED_AT


PROFILE = HttpFormLoginProfile(
    profile_id="profile-form",
    path="/login",
    username_field="username",
    password_secret_name="login_password",
    session_cookie_name=SESSION_COOKIE_NAME,
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


def _secret_port() -> CompositeSecretPort:
    return CompositeSecretPort(
        InMemorySecretStore(),
        EnvSecretResolver({"ALICE_PASSWORD": ALICE_PASSWORD, "BOB_PASSWORD": BOB_PASSWORD}),
    )


def _add_experiment(store: _Store, experiment_id: str, *, research_run_id: str = "run-1") -> None:
    store.experiments[experiment_id] = ExperimentRecord(
        experiment_id=experiment_id,
        research_run_id=research_run_id,
        hypothesis_id="hyp-1",
        budget_id="budget-1",
        execution_state="PLANNED",
        created_at=CREATED_AT,
    )


def _in_process_worker(store: _Store) -> RecordingWorkerPort:
    def handler(request):
        result = build_result(request, utc_now_rfc3339())
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=CREATED_AT,
            completed_at=CREATED_AT,
            worker_result=result,
            exit_code=0,
        )

    return RecordingWorkerPort(store=store, handler=handler)


def _use_case(store: _Store | None = None, secret_port: CompositeSecretPort | None = None):
    store = store or _Store()
    seed_spine(store)
    factory = FakeUnitOfWorkFactory(store=store)
    worker = _in_process_worker(store)
    return (
        ExecutePlannedExperiment(
            factory, worker, clock=FixedClock(), secret_port=secret_port or _secret_port()
        ),
        factory,
        worker,
        store,
    )


def _login_plan(origin: str, identity: Identity, session_context_id: str, username: str):
    return plan_http_login(
        "hyp-1",
        budget_id="budget-1",
        target_reference="target-1",
        identity=identity,
        profile=PROFILE,
        username=username,
        authorized_origin=origin,
        session_context_id=session_context_id,
    )


def _authed_get(origin: str, session_context_id: str, *, target_reference: str = "target-1"):
    return plan_http_transaction(
        "hyp-1",
        budget_id="budget-1",
        target_reference=target_reference,
        template=HttpRequestTemplate(
            authorized_origin=origin,
            method="GET",
            path="/me",
            session_context_reference=session_context_id,
        ),
    )


def _secrets_in(blob: object, secrets: tuple[str, ...]) -> list[str]:
    text = json.dumps(blob, default=str)
    return [item for item in secrets if item in text]


class IdentitySessionModelTests(unittest.TestCase):
    def test_credential_must_be_secret_reference(self) -> None:
        with self.assertRaises(ResearchInputError):
            CredentialReference(scheme="raw", name="value")

    def test_unknown_login_method_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            HttpFormLoginProfile(
                profile_id="profile-form",
                path="/login",
                username_field="username",
                password_secret_name="login_password",
                session_cookie_name="sid",
                method="GET",
            )

    def test_malformed_profile_rejected(self) -> None:
        with self.assertRaises(ResearchInputError):
            HttpFormLoginProfile(
                profile_id="profile-form",
                path="login",
                username_field="username",
                password_secret_name="login_password",
                session_cookie_name="sid",
            )


class Gate20IdentitySessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lab = Gate20AuthLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_alice_login_establishes_session_without_persisting_secrets(self) -> None:
        use_case, factory, _, store = _use_case()
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        session = store.session_contexts["session-alice"]
        self.assertEqual(session.state, SessionState.ACTIVE.value)
        self.assertEqual(session.identity_id, "id-alice")
        self.assertNotIn("password", json.dumps(session.__dict__, default=str).lower())
        self.assertNotIn(ALICE_PASSWORD, json.dumps(session.__dict__, default=str))
        observations = list(store.observations.values())
        self.assertEqual(observations[0].observation_kind, HTTP_AUTHENTICATION_OBSERVATION_KIND)
        self.assertTrue(observations[0].payload["session_established"])
        leaked = _secrets_in(
            {
                "plans": [item.arguments for item in store.experiment_plans.values()],
                "results": [item.raw_result for item in store.worker_results.values()],
                "diagnostics": [item.diagnostics for item in store.worker_results.values()],
                "observations": [item.payload for item in observations],
                "audit": [item.payload for item in store.audit_events.values()],
                "sessions": [item.__dict__ for item in store.session_contexts.values()],
            },
            (ALICE_PASSWORD, BOB_PASSWORD),
        )
        self.assertEqual(leaked, [])
        for result in store.worker_results.values():
            self.assertIsNone(getattr(result, "ephemeral_secrets", None))
            raw = json.dumps(result.raw_result, default=str)
            self.assertNotIn("_ephemeral_session_cookie", raw)
            self.assertNotIn("sid=", raw)

    def test_alice_session_cannot_be_used_as_bob(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        login = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(login.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        _add_experiment(store, "exp-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-bob",
                identity=_bob(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCHEMA_MISMATCH)

    def test_bob_session_cannot_be_used_as_alice(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        login = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _bob(), "session-bob", BOB_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-bob",
                identity=_bob(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(login.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        _add_experiment(store, "exp-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-bob"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)

    def test_session_origin_cannot_be_substituted(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        _add_experiment(store, "exp-2")
        other = "http://127.0.0.1:9"
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(other, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(other),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)

    def test_session_id_alone_is_not_authority(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        _add_experiment(store, "exp-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)

    def test_expired_and_revoked_sessions_cannot_dispatch(self) -> None:
        secrets = _secret_port()
        use_case, factory, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        current = store.session_contexts["session-alice"]
        store.session_contexts["session-alice"] = SessionContextRecord(
            session_context_id=current.session_context_id,
            research_run_id=current.research_run_id,
            identity_id=current.identity_id,
            actor_reference=current.actor_reference,
            origin=current.origin,
            authentication_profile_reference=current.authentication_profile_reference,
            authentication_method=current.authentication_method,
            secret_scheme=current.secret_scheme,
            secret_name=current.secret_name,
            state=SessionState.ACTIVE.value,
            created_at=current.created_at,
            updated_at=current.updated_at,
            established_at=current.established_at,
            expires_at=CREATED_AT - timedelta(seconds=1),
            session_cookie_name=current.session_cookie_name,
        )
        _add_experiment(store, "exp-2")
        expired = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(expired.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(expired.core_reason_code, ReasonCode.AUTHORIZATION_INACTIVE)
        with factory.open() as uow:
            revoke_session(uow, "session-alice", secrets, CREATED_AT)
            uow.commit()
        _add_experiment(store, "exp-3")
        revoked = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-3",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(revoked.status, ResearchLoopStatus.DISPATCH_DENIED)

    def test_invalid_credential_does_not_create_active_session(self) -> None:
        secrets = CompositeSecretPort(
            InMemorySecretStore(),
            EnvSecretResolver({"ALICE_PASSWORD": "wrong-password"}),
        )
        use_case, _, _, store = _use_case(secret_port=secrets)
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(store.session_contexts["session-alice"].state, SessionState.FAILED.value)
        self.assertFalse(list(store.observations.values())[0].payload["session_established"])

    def test_missing_secret_reference_fails_closed(self) -> None:
        secrets = CompositeSecretPort(InMemorySecretStore(), EnvSecretResolver({}))
        use_case, _, worker, _ = _use_case(secret_port=secrets)
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(len(worker.calls), 0)

    def test_restart_does_not_fabricate_session_material(self) -> None:
        first_store = InMemorySecretStore()
        secrets = CompositeSecretPort(
            first_store,
            EnvSecretResolver({"ALICE_PASSWORD": ALICE_PASSWORD}),
        )
        store = _Store()
        use_case, _, _, store = _use_case(store=store, secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(store.session_contexts["session-alice"].state, SessionState.ACTIVE.value)
        restarted = CompositeSecretPort(
            InMemorySecretStore(),
            EnvSecretResolver({"ALICE_PASSWORD": ALICE_PASSWORD}),
        )
        later = ExecutePlannedExperiment(
            FakeUnitOfWorkFactory(store=store),
            _in_process_worker(store),
            clock=FixedClock(),
            secret_port=restarted,
        )
        _add_experiment(store, "exp-2")
        outcome = later.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(store.session_contexts["session-alice"].state, SessionState.ACTIVE.value)

    def test_authenticated_get_uses_bound_session(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        _add_experiment(store, "exp-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        payload = list(store.observations.values())[-1].payload
        self.assertEqual(payload["status_code"], 200)
        self.assertNotIn("cookie", json.dumps(payload).lower())

    def test_session_from_run_a_cannot_execute_experiment_in_run_b(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        store.research_runs["run-2"] = ResearchRunRecord(
            research_run_id="run-2",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=CREATED_AT,
        )
        _add_experiment(store, "exp-2", research_run_id="run-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCHEMA_MISMATCH)

    def test_same_identity_origin_wrong_target_is_rejected(self) -> None:
        secrets = _secret_port()
        use_case, _, _, store = _use_case(secret_port=secrets)
        use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(self.origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        _add_experiment(store, "exp-2")
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-2",
                plan=_authed_get(self.origin, "session-alice", target_reference="target-2"),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.INPUT_REJECTED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCHEMA_MISMATCH)

    def test_redirect_during_login_does_not_follow(self) -> None:
        use_case, _, worker, store = _use_case()
        plan = plan_http_login(
            "hyp-1",
            budget_id="budget-1",
            target_reference="target-1",
            identity=_alice(),
            profile=HttpFormLoginProfile(
                profile_id="profile-form",
                path="/login-redirect",
                username_field="username",
                password_secret_name="login_password",
                session_cookie_name=SESSION_COOKIE_NAME,
            ),
            username=ALICE_USERNAME,
            authorized_origin=self.origin,
            session_context_id="session-alice",
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=plan,
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(self.origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=HttpFormLoginProfile(
                    profile_id="profile-form",
                    path="/login-redirect",
                    username_field="username",
                    password_secret_name="login_password",
                    session_cookie_name=SESSION_COOKIE_NAME,
                ),
            )
        )
        results = list(store.worker_results.values())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "REAUTHORIZATION_REQUIRED")
        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        self.assertEqual(outcome.experiment_execution_state, "AUTHORIZATION_CHECK")
        self.assertIsNotNone(outcome.reauthorization_request)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(store.session_contexts["session-alice"].state, SessionState.AUTHENTICATING.value)
        self.assertEqual(list(store.observations.values()), [])

    def test_caller_cannot_inject_cookie_or_authorization_headers(self) -> None:
        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="http.transaction",
                    action="read",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": self.origin,
                        "method": "GET",
                        "path": "/me",
                        "headers": {"Cookie": "sid=stolen"},
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="http.transaction.v1",
                )
            )
        with self.assertRaises(ExperimentCompileError):
            compile_experiment_intent(
                ExperimentIntent(
                    hypothesis_id="hyp-1",
                    capability_id="http.authentication",
                    action="login",
                    target_reference="target-1",
                    arguments={
                        "authorized_origin": self.origin,
                        "path": "/login",
                        "username": "alice",
                        "username_field": "username",
                        "password_secret_name": "login_password",
                        "session_cookie_name": "sid",
                        "session_context_id": "session-alice",
                        "headers": {"Authorization": "Bearer stolen"},
                    },
                    requested_budget_id="budget-1",
                    expected_observation="x",
                    disconfirming_observation="y",
                    evaluation_strategy="http.authentication.v1",
                )
            )


class ReauthorizationControlLoopTests(unittest.TestCase):
    def test_reauthorization_required_is_not_classified_as_no_observation(self) -> None:
        origin = "http://127.0.0.1:9"
        store = _Store()
        seed_spine(store)
        factory = FakeUnitOfWorkFactory(store=store)

        def handler(request):
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=CREATED_AT,
                completed_at=CREATED_AT,
                worker_result={
                    "contract_version": "v1",
                    "correlation": dict(request["correlation"]),
                    "worker_id": "local-python-diagnostic",
                    "status": "REAUTHORIZATION_REQUIRED",
                    "started_at": "2026-08-16T21:00:00Z",
                    "completed_at": "2026-08-16T21:00:01Z",
                    "raw_result": {
                        "stopped": True,
                        "reason": "redirect_or_new_origin",
                        "status": 302,
                        "method": "POST",
                        "path": "/login",
                    },
                    "diagnostics": {
                        "redirect": True,
                        "raw_location": "/next",
                        "response_url": f"{origin}/login",
                        "location": f"{origin}/next",
                        "followed": False,
                        "self_authorized": False,
                        "requires_core_re_evaluation": True,
                    },
                },
                exit_code=0,
            )

        worker = RecordingWorkerPort(store=store, handler=handler)
        use_case = ExecutePlannedExperiment(
            factory, worker, clock=FixedClock(), secret_port=_secret_port()
        )
        outcome = use_case.execute(
            ExecutePlannedExperimentCommand(
                experiment_id="exp-1",
                plan=_login_plan(origin, _alice(), "session-alice", ALICE_USERNAME),
                scope=_allow_scope(),
                compiled_scope=_compiled_scope(origin),
                identity_id="id-alice",
                identity=_alice(),
                authentication_profile=PROFILE,
            )
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        self.assertEqual(outcome.experiment_execution_state, "AUTHORIZATION_CHECK")
        self.assertIsNotNone(outcome.reauthorization_request)
        self.assertEqual(outcome.reauthorization_request["reason"], "redirect")
        self.assertFalse(outcome.reauthorization_request["discovery_context"]["followed"])
        self.assertEqual(list(store.observations.values()), [])
        self.assertEqual(list(store.worker_results.values())[0].status, "REAUTHORIZATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
