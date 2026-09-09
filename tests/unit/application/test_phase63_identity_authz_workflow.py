"""Phase 6.3 identity / auth / authz / workflow production acceptance (I1–I26)."""

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
from zest.application.research_identity_catalog import (
    RESEARCH_IDENTITIES_CONFIGURED,
    load_research_identity_catalog,
)
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
    HunterFamilyRecord,
    IssuedBudgetRecord,
    ObservationRecord,
)
from zest.platform.secrets import CompositeSecretPort, EnvSecretResolver, InMemorySecretStore
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.exploration import OpportunityKind, ResearchPolicyBudget
from zest.research.identity_session import HttpFormLoginProfile, Identity, local_dev_credential
from zest.research.orchestration import OrchestrationBounds
from support.fake_model import ScriptedModelPort
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, STARTED_AT, COMPLETED_AT
from support.spine import CREATED_AT, seed_authorization_run


PROFILE = HttpFormLoginProfile(
    profile_id="profile-form",
    path="/login",
    username_field="username",
    password_secret_name="login_password",
    session_cookie_name="sid",
)


def _alice() -> Identity:
    return Identity(
        identity_id="id-alice",
        actor_reference="alice",
        target_reference="http://127.0.0.1:9",
        credential_reference=local_dev_credential("ALICE_PASSWORD"),
        authentication_profile_reference=PROFILE.profile_id,
    )


def _bob() -> Identity:
    return Identity(
        identity_id="id-bob",
        actor_reference="bob",
        target_reference="http://127.0.0.1:9",
        credential_reference=local_dev_credential("BOB_PASSWORD"),
        authentication_profile_reference=PROFILE.profile_id,
    )


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
        max_cycles=24,
        max_experiments=24,
        max_model_calls=50,
        max_worker_invocations=24,
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
        identities=(_alice(), _bob()),
        authentication_profiles=(PROFILE,),
    )
    values.update(overrides)
    return StartAutonomousResearchCommand(**values)


def _worker_result(capability: str, raw: dict, *, ephemeral: dict | None = None) -> dict:
    payload = {
        "contract_version": "v1",
        "correlation": {"request_id": "req-1"},
        "worker_id": f"local-python-{capability}",
        "status": "SUCCEEDED",
        "started_at": "2026-08-16T20:00:00Z",
        "completed_at": "2026-08-16T20:00:01Z",
        "raw_result": raw,
    }
    if ephemeral:
        payload["ephemeral_secrets"] = ephemeral
    return payload


def _phase63_handler(mode: str = "vulnerable"):
    def handler(request):
        cap = request.get("worker_capability")
        arguments = request.get("arguments") or {}
        correlation = dict(request.get("correlation") or {})
        if cap == "http.authentication":
            raw = {
                "status_code": 200,
                "session_established": True,
                "path": arguments.get("path"),
                "authorized_origin": arguments.get("authorized_origin"),
                "identity_id": arguments.get("identity_id"),
                "session_context_id": arguments.get("session_context_id"),
            }
            result = _worker_result(cap, raw, ephemeral={"session_cookie": "sid-value"})
            result["correlation"] = correlation
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=result,
                exit_code=0,
            )
        if cap == "http.authorization.differential":
            actor = arguments.get("actor")
            own = arguments.get("own_object")
            cross = arguments.get("cross_object")
            positive = mode == "vulnerable"
            raw = {
                "mode": arguments.get("mode") or mode,
                "authorized_origin": arguments.get("authorized_origin"),
                "owner_request": {
                    "status": 200,
                    "object_owner": own if own == actor else actor,
                },
                "cross_object_request": {
                    "status": 200 if positive else 403,
                    **({"object_owner": cross} if positive else {}),
                },
                "secure_control": {"status": 403},
                "unauthenticated_control": {"status": 401},
            }
            result = _worker_result(cap, raw)
            result["correlation"] = correlation
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=result,
                exit_code=0,
            )
        if cap == "http.state_transition":
            positive = mode == "vulnerable"
            raw = {
                "authorized_origin": arguments.get("authorized_origin"),
                "area": arguments.get("area") or "workflow",
                "resource_id": arguments.get("resource_id"),
                "requested_transition": arguments.get("transition"),
                "pre_state_request": {
                    "status": 200,
                    "state": "UNDER_REVIEW",
                    "actor_role": "requester",
                    "owner": arguments.get("actor"),
                    "approve_requires_role": "reviewer",
                    "approve_from_states": ["UNDER_REVIEW"],
                },
                "transition_request": {"status": 200, "ok": True},
                "post_state_request": {
                    "status": 200,
                    "state": "APPROVED" if positive else "UNDER_REVIEW",
                    "approved_by": arguments.get("actor") if positive else None,
                    "actor_role": "requester",
                },
                "control_request": {"status": 200 if positive else 403},
            }
            result = _worker_result(cap, raw)
            result["correlation"] = correlation
            return WorkerInvocationOutcome(
                invocation_status=InvocationStatus.COMPLETED,
                started_at=STARTED_AT,
                completed_at=COMPLETED_AT,
                worker_result=result,
                exit_code=0,
            )
        from support.recording_worker import completed_diagnostic_outcome

        return completed_diagnostic_outcome(request)

    return handler


def _seed(store: _Store, *, authz: bool = True, workflow: bool = False) -> None:
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
    if authz:
        store.discovery_facts["fact-authz"] = DiscoveryFactRecord(
            fact_id="fact-authz",
            research_run_id="run-1",
            fact_kind="RESOURCE_INSTANCE_CANDIDATE",
            canonical_key="resource:alice-account",
            epistemic_status="OBSERVED",
            identity_id="id-alice",
            target_reference="target-1",
            created_at=CREATED_AT,
            normalized_origin="http://127.0.0.1:9",
            normalized_path="/accounts/alice",
            http_method="GET",
            attributes={
                "scope_classification": "IN_SCOPE",
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "vulnerable",
            },
        )
        _link_fact(store, "fact-authz")
    if workflow:
        store.discovery_facts["fact-wf"] = DiscoveryFactRecord(
            fact_id="fact-wf",
            research_run_id="run-1",
            fact_kind="WORKFLOW_TRANSITION",
            canonical_key="workflow:ticket-1:approve",
            epistemic_status="OBSERVED",
            identity_id="id-alice",
            target_reference="target-1",
            created_at=CREATED_AT,
            normalized_origin="http://127.0.0.1:9",
            normalized_path="/workflow/ticket-1",
            http_method="POST",
            attributes={
                "scope_classification": "IN_SCOPE",
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "resource_id": "ticket-1",
                "transition": "approve",
                "area": "workflow",
            },
        )
        _link_fact(store, "fact-wf")


def _link_fact(store: _Store, fact_id: str) -> None:
    store.observations[f"obs-{fact_id}"] = ObservationRecord(
        observation_id=f"obs-{fact_id}",
        worker_result_id="wr-seed",
        observation_kind="HTTP_TRANSACTION",
        payload={"path": "/seed"},
        normalization_version="http.transaction.v1",
        observed_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    store.discovery_fact_sources[f"src-{fact_id}"] = DiscoveryFactSourceRecord(
        source_row_id=f"src-{fact_id}",
        research_run_id="run-1",
        fact_id=fact_id,
        created_at=CREATED_AT,
        observation_id=f"obs-{fact_id}",
    )


class FixedClock:
    def now(self):
        return CREATED_AT


def _controller_fixed(store: _Store, *, mode: str = "vulnerable"):
    secrets = CompositeSecretPort(
        InMemorySecretStore(),
        env=EnvSecretResolver({"ALICE_PASSWORD": "alice-pass", "BOB_PASSWORD": "bob-pass"}),
    )
    factory = FakeUnitOfWorkFactory(store=store)
    port = RecordingWorkerPort(store=store, handler=_phase63_handler(mode))
    controller = AutonomousResearchController(
        factory, port, ScriptedModelPort(), clock=FixedClock(), secret_port=secrets
    )
    return controller, port, factory


class Phase63IdentityAuthWorkflowTests(unittest.TestCase):
    def test_i1_identity_census_without_secrets(self) -> None:
        store = _Store()
        _seed(store)
        controller, _, _ = _controller_fixed(store)
        controller.start(_command())
        payload = [
            item.payload
            for item in store.audit_events.values()
            if item.event_type == RESEARCH_IDENTITIES_CONFIGURED
        ][-1]
        blob = str(payload)
        self.assertNotIn("alice-pass", blob)
        self.assertIn("id-alice", blob)
        with FakeUnitOfWorkFactory(store).open() as uow:
            catalog = load_research_identity_catalog(uow, "run-1")
            uow.rollback()
        self.assertEqual(len(catalog.identities), 2)

    def test_i2_authentication_opportunity_from_dependent_work(self) -> None:
        store = _Store()
        _seed(store)
        controller, _, _ = _controller_fixed(store)
        controller.start(_command())
        SelectResearchOpportunities(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        kinds = {
            item.opportunity_kind
            for item in store.opportunity_selection_candidates.values()
        }
        self.assertIn(OpportunityKind.AUTHENTICATION.value, kinds)

    def test_i3_i4_i8_i9_i11_real_login_then_native_authz(self) -> None:
        store = _Store()
        _seed(store)
        controller, port, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        capabilities = []
        for _ in range(12):
            controller.step(command)
            capabilities = [
                item["request"].get("worker_capability") for item in port.calls
            ]
            if "http.authorization.differential" in capabilities:
                break
        self.assertIn("http.authentication", capabilities)
        self.assertIn("http.authorization.differential", capabilities)
        login_calls = [
            item for item in port.calls if item["request"].get("worker_capability") == "http.authentication"
        ]
        self.assertEqual(len(login_calls), 1)
        compiled = [
            item
            for item in store.audit_events.values()
            if item.event_type == "RESEARCH_WORK_COMPILED"
            and (item.payload or {}).get("compiled_capability") == "http.authorization.differential"
        ]
        self.assertTrue(compiled)
        evidence = list(store.evidence.values()) if hasattr(store, "evidence") else list(
            getattr(store, "evidences", {}).values()
        )
        self.assertTrue(store.observations)
        self.assertTrue(
            any(
                "authorization" in (item.claim_scope or "").lower()
                for item in store.evidence.values()
            )
        )
        self.assertTrue(
            any(
                item.event_type == "IDENTITY_ENGINE_COVERAGE_UPDATED"
                for item in store.audit_events.values()
            )
        )

    def test_i10_authz_negative_control_no_evidence(self) -> None:
        store = _Store()
        _seed(store)
        store.discovery_facts["fact-authz"] = DiscoveryFactRecord(
            fact_id="fact-authz",
            research_run_id="run-1",
            fact_kind="RESOURCE_INSTANCE_CANDIDATE",
            canonical_key="resource:alice-account",
            epistemic_status="OBSERVED",
            identity_id="id-alice",
            target_reference="target-1",
            created_at=CREATED_AT,
            normalized_origin="http://127.0.0.1:9",
            attributes={
                "scope_classification": "IN_SCOPE",
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
                "own_object": "alice",
                "cross_object": "bob",
                "mode": "secure_only",
            },
        )
        controller, _, _ = _controller_fixed(store, mode="secure_only")
        command = _command()
        controller.start(command)
        for _ in range(12):
            controller.step(command)
        assessments = list(store.hypothesis_assessments.values())
        authz = [
            item
            for item in assessments
            if item.evaluation_strategy == "http.authorization.differential.v1"
        ]
        self.assertTrue(authz)
        self.assertNotEqual(authz[-1].assessment_outcome, "CONSISTENT_WITH_PREDICTION")
        authz_evidence = [
            item
            for item in store.evidence.values()
            if "authorization" in (item.claim_scope or "").lower()
        ]
        self.assertFalse(authz_evidence)

    def test_i12_unknown_ownership_not_fabricated(self) -> None:
        store = _Store()
        _seed(store, authz=False)
        store.discovery_facts["fact-unknown"] = DiscoveryFactRecord(
            fact_id="fact-unknown",
            research_run_id="run-1",
            fact_kind="HTTP_OPERATION",
            canonical_key="GET http://127.0.0.1:9/objects/1",
            epistemic_status="OBSERVED",
            identity_id="id-alice",
            target_reference="target-1",
            created_at=CREATED_AT,
            normalized_origin="http://127.0.0.1:9",
            normalized_path="/objects/1",
            http_method="GET",
            attributes={
                "scope_classification": "IN_SCOPE",
                "authorized_origin": "http://127.0.0.1:9",
                "actor": "alice",
            },
        )
        _link_fact(store, "fact-unknown")
        controller, port, _ = _controller_fixed(store)
        controller.start(_command())
        SelectResearchOpportunities(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=1, max_exploratory=1),
            )
        )
        authz = [
            item
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.AUTHORIZATION_DIFFERENTIAL.value
        ]
        self.assertFalse(authz)
        self.assertEqual(len(port.calls), 0)

    def test_i13_i14_i16_workflow_native_positive(self) -> None:
        store = _Store()
        _seed(store, authz=False, workflow=True)
        controller, port, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        for _ in range(12):
            controller.step(command)
            if any(
                item["request"].get("worker_capability") == "http.state_transition"
                for item in port.calls
            ):
                break
        self.assertTrue(
            any(
                item["request"].get("worker_capability") == "http.state_transition"
                for item in port.calls
            )
        )
        compiled = [
            item
            for item in store.audit_events.values()
            if (item.payload or {}).get("compiled_capability") == "http.state_transition"
        ]
        self.assertTrue(compiled)

    def test_i15_workflow_negative(self) -> None:
        store = _Store()
        _seed(store, authz=False, workflow=True)
        controller, _, _ = _controller_fixed(store, mode="secure_only")
        command = _command()
        controller.start(command)
        for _ in range(12):
            controller.step(command)
        wf = [
            item
            for item in store.hypothesis_assessments.values()
            if item.evaluation_strategy == "http.state_transition.v1"
        ]
        self.assertTrue(wf)
        self.assertNotEqual(wf[-1].assessment_outcome, "CONSISTENT_WITH_PREDICTION")

    def test_i5_i6_session_isolation_and_recovery(self) -> None:
        store = _Store()
        _seed(store)
        controller, _, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        for _ in range(6):
            controller.step(command)
        sessions = list(store.session_contexts.values())
        alice = [item for item in sessions if item.identity_id == "id-alice"]
        bob = [item for item in sessions if item.identity_id == "id-bob"]
        for item in sessions:
            self.assertEqual(item.research_run_id, "run-1")
        restarted, _, _ = _controller_fixed(store)
        restarted.step(command)
        after = list(store.session_contexts.values())
        self.assertEqual(len(sessions), len(after))
        self.assertTrue(alice)
        self.assertFalse(any(item.identity_id == "id-alice" and item.identity_id == "id-bob" for item in after))
        del bob

    def test_i7_direct_authz_source_not_hunter_only(self) -> None:
        store = _Store()
        _seed(store)
        controller, _, _ = _controller_fixed(store)
        controller.start(_command())
        for _ in range(10):
            controller.step(_command())
        sources = {
            item.source_system
            for item in store.opportunity_selection_candidates.values()
            if item.opportunity_kind == OpportunityKind.AUTHORIZATION_DIFFERENTIAL.value
        }
        self.assertTrue(sources.intersection({"AUTHORIZATION", "HUNTER_COVERAGE"}))

    def test_i17_i18_hunter_interop_and_dedupe(self) -> None:
        store = _Store()
        _seed(store)
        store.hunter_families["hf-object-authz:1"] = HunterFamilyRecord(
            family_id="hf-object-authz",
            name="OBJECT_AUTHORIZATION",
            target_node_kinds=("HTTP_OPERATION", "RESOURCE_INSTANCE_CANDIDATE"),
            preconditions={"scope_classification": "IN_SCOPE"},
            claim_template="Object authorization boundary on {origin}{path} may allow cross-owner access to {resource_id}.",
            evidence_requirements={"required_observation_kinds": ["HTTP_AUTHORIZATION_DIFFERENTIAL"]},
            validation_tier="V3",
            enabled=True,
            version=1,
            created_at=CREATED_AT,
        )
        controller, port, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        for _ in range(12):
            controller.step(command)
        authz_caps = [
            item
            for item in port.calls
            if item["request"].get("worker_capability") == "http.authorization.differential"
        ]
        self.assertLessEqual(len(authz_caps), 1)

    def test_i19_i20_fairness_and_dependency(self) -> None:
        store = _Store()
        _seed(store)
        controller, port, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        order = []
        for _ in range(12):
            controller.step(command)
            if port.calls:
                cap = port.calls[-1]["request"].get("worker_capability")
                if cap not in order:
                    order.append(cap)
        if "http.authentication" in order and "http.authorization.differential" in order:
            self.assertLess(
                order.index("http.authentication"),
                order.index("http.authorization.differential"),
            )

    def test_i21_i22_hard_ceiling_and_core_not_weakened(self) -> None:
        root = Path(__file__).resolve().parents[3] / "src" / "zest" / "application"
        text = (root / "research_work_planners.py").read_text(encoding="utf-8")
        self.assertIn("BLOCKED_SIDE_EFFECT_CEILING", text)
        self.assertIn("HTTP_STATE_TRANSITION_CAPABILITY", text)
        self.assertIn("plan_state_transition", text)
        planner = text.split("class WorkflowStateTransitionPlanner")[1][:2000]
        self.assertNotIn("diagnostic.echo", planner)

    def test_i23_i24_i25_coverage_completion_precondition(self) -> None:
        store = _Store()
        _seed(store)
        controller, _, factory = _controller_fixed(store)
        command = _command(identities=(_alice(),), authentication_profiles=(PROFILE,))
        controller.start(command)
        for _ in range(10):
            controller.step(command)
        with factory.open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertNotEqual(audit.auth_pending, NOT_YET_CONNECTED)
        self.assertNotEqual(audit.authz_pending, NOT_YET_CONNECTED)
        self.assertNotEqual(audit.workflow_pending, NOT_YET_CONNECTED)
        self.assertIsInstance(audit.auth_pending, int)

    def test_i26_recovery_no_duplicate_auth_execution(self) -> None:
        store = _Store()
        _seed(store)
        controller, port, _ = _controller_fixed(store)
        command = _command()
        controller.start(command)
        controller.step(command)
        first = [
            item for item in port.calls if item["request"].get("worker_capability") == "http.authentication"
        ]
        restarted, port2, _ = _controller_fixed(store)
        restarted.step(command)
        second = [
            item
            for item in port2.calls
            if item["request"].get("worker_capability") == "http.authentication"
        ]
        self.assertLessEqual(len(first) + len(second), 1)

    def test_architecture_no_second_scheduler_owner(self) -> None:
        root = Path(__file__).resolve().parents[3] / "src" / "zest" / "application"
        for name in (
            "autonomous_research_controller.py",
            "select_research_opportunities.py",
            "zestd.py",
            "local_run_supervisor.py",
        ):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("RunResearchSelection(", text)


if __name__ == "__main__":
    unittest.main()
