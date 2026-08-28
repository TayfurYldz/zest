"""GATE 22 autonomous surface discovery lab.

Skipped when PostgreSQL or Chromium is unavailable. Not a formal PASS.
Does not set SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY.
"""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from e2e.lab.surface_discovery_lab import (
    ALICE_COOKIE,
    BOB_COOKIE,
    HIDDEN_TRUTH,
    SESSION_COOKIE_NAME,
    Gate22SurfaceLab,
)
from integration.harness import PostgresUnitOfWorkFactory, alembic_upgrade, configured_test_url, truncate_spine
from zest.application.autonomous_research_controller import (
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.application.discovery.runner import SurfaceDiscoveryStart
from zest.application.program_research_context import ProgramPolicyView
from zest.application.session_binding import session_material_reference
from zest.core.enums import ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.postgres.engine import TEST_DATABASE_URL_ENV, create_sync_engine
from zest.data.records import (
    AuthorizationSourceRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
    SessionContextRecord,
)
from zest.maturity import (
    GATE_04B_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from zest.platform.secrets import CompositeSecretPort, InMemorySecretStore
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.discovery.config import DiscoveryBounds, DiscoveryRunConfig
from zest.research.discovery.context_pack import pack_surface_discovery_context
from zest.research.discovery.facts import DiscoveryFact, DiscoveryFactSourceView
from zest.research.discovery.frontier import FrontierItem
from zest.research.discovery.graph import rebuild_attack_surface_graph
from zest.research.discovery.inference import DiscoveryInference
from zest.research.discovery.types import (
    ANONYMOUS_IDENTITY_ID,
    SURFACE_DISCOVERY_STRATEGY_VERSION,
    AttackSurfaceEdgeKind,
    DiscoveryFactKind,
    DiscoveryGoalKind,
    DiscoveryInferenceKind,
    DiscoverySourcePlane,
)
from zest.research.exploration import EXPLORATION_STRATEGY_VERSION
from zest.research.identity_session import Identity, local_dev_credential
from zest.research.orchestration import OrchestrationBounds
from zest.research.target_model import TARGET_MODEL_STRATEGY_VERSION, TargetEpistemicStatus
from zest.worker_runtime.python.browser_engine import BrowserEngineUnavailable
from zest.worker_runtime.python.browser_page import execute_browser_page
from zest.worker_runtime.python.http_transaction import execute_http_transaction
from support.fake_model import ScriptedModelPort

TEST_URL = configured_test_url()
CHROMIUM_REASON = "Chromium/Playwright is not installed for GATE 22 real-browser tests"
PG_REASON = f"{TEST_DATABASE_URL_ENV} not set; GATE 22 PostgreSQL E2E skipped"


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _chromium_engine():
    try:
        from zest.worker_runtime.python.playwright_chromium_engine import (
            PlaywrightChromiumEngine,
        )

        engine = PlaywrightChromiumEngine()
        engine.start()
        return engine
    except (BrowserEngineUnavailable, ImportError, OSError):
        return None


class LabWorkerPort:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.calls: list[dict] = []

    def invoke(self, request, *, timeout_ms=None):
        del timeout_ms
        self.calls.append(dict(request))
        cap = request.get("worker_capability")
        started = datetime.now(timezone.utc)
        if cap == "browser.page":
            status, raw, diagnostics = execute_browser_page(request, engine=self.engine)
        elif cap == "http.transaction":
            status, raw, diagnostics = execute_http_transaction(request)
        else:
            status, raw, diagnostics = "EXECUTION_FAILED", {}, {"error": "unsupported capability"}
        completed = datetime.now(timezone.utc)
        result = {
            "contract_version": "v1",
            "correlation": request.get("correlation"),
            "worker_id": "g22-lab-worker",
            "status": status,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "completed_at": completed.isoformat().replace("+00:00", "Z"),
            "raw_result": raw,
        }
        if diagnostics is not None:
            result["diagnostics"] = diagnostics
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=started,
            completed_at=completed,
            worker_result=result,
            exit_code=0,
        )


def _bounds() -> DiscoveryBounds:
    return DiscoveryBounds(
        max_discovery_cycles=64,
        max_frontier_items=128,
        max_new_facts_per_cycle=32,
        max_browser_actions=64,
        max_http_transactions=16,
        max_per_route_revisit=16,
        max_identity_variants=2,
        max_transition_depth=4,
        max_graph_depth_from_seed=8,
        max_template_inference_fanout=4,
        max_duplicate_observations=8,
    )


class Gate22MaturityTests(unittest.TestCase):
    def test_maturity_and_prior_strategy_identities(self) -> None:
        self.assertEqual(GATE_04B_STATUS, "PASS")
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)
        self.assertEqual(EXPLORATION_STRATEGY_VERSION, "exploration.diagnostic.echo.v1")
        self.assertEqual(TARGET_MODEL_STRATEGY_VERSION, "target.model.diagnostic.echo.v1")
        self.assertEqual(SURFACE_DISCOVERY_STRATEGY_VERSION, "surface.discovery.v1")
        self.assertNotEqual(SURFACE_DISCOVERY_STRATEGY_VERSION, EXPLORATION_STRATEGY_VERSION)


@unittest.skipUnless(TEST_URL, PG_REASON)
@unittest.skipUnless(_playwright_installed(), CHROMIUM_REASON)
class Gate22SurfaceDiscoveryE2ETests(unittest.TestCase):
    engine = None
    browser = None
    lab = None
    origin = ""

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        cls.browser = _chromium_engine()
        if cls.browser is None:
            raise unittest.SkipTest(CHROMIUM_REASON)
        alembic_upgrade(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        cls.lab = Gate22SurfaceLab()
        cls.origin = cls.lab.start()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.browser is not None:
            cls.browser.stop()
        if cls.lab is not None:
            cls.lab.stop()
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)
        if self.browser is not None:
            self.browser.close_all()

    def test_autonomous_surface_discovery_from_visible_seed(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        now = datetime.now(timezone.utc)
        parsed = urlsplit(self.origin)
        compiled = compile_scope_rules(
            (
                ScopeRuleDefinition(
                    rule_id="rule-allow",
                    effect=ScopeRuleEffect.ALLOW,
                    scheme="http",
                    host=parsed.hostname or "127.0.0.1",
                    port=parsed.port,
                    path_prefix=None,
                    source_reference="scope-src",
                ),
            )
        )
        alice = Identity(
            identity_id="id-alice",
            actor_reference="actor-alice",
            target_reference="target-1",
            credential_reference=local_dev_credential("ALICE_PASSWORD"),
            authentication_profile_reference="profile-form",
        )
        bob = Identity(
            identity_id="id-bob",
            actor_reference="actor-bob",
            target_reference="target-1",
            credential_reference=local_dev_credential("BOB_PASSWORD"),
            authentication_profile_reference="profile-form",
        )
        secrets = CompositeSecretPort(InMemorySecretStore())
        with factory.open() as uow:
            uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=now, name="g22-lab"))
            uow.authorization_sources.insert(
                AuthorizationSourceRecord(
                    authorization_source_id="as-1",
                    program_id="prog-1",
                    state="ACTIVE",
                    provenance_reference="written-auth-1",
                    created_at=now,
                )
            )
            uow.research_runs.insert(
                ResearchRunRecord(
                    research_run_id="run-1",
                    program_id="prog-1",
                    authorization_source_id="as-1",
                    initiated_by_actor_id="operator-1",
                    initiated_by_actor_type="HUMAN_OPERATOR",
                    started_at=now,
                )
            )
            uow.issued_budgets.insert(
                IssuedBudgetRecord(
                    budget_id="budget-1",
                    research_run_id="run-1",
                    max_requests=512,
                    max_tool_calls=128,
                    max_runtime_ms=15_000,
                    max_concurrency=1,
                    issued_at=now,
                )
            )
            for session_id, identity_id, actor in (
                ("session-alice", "id-alice", "actor-alice"),
                ("session-bob", "id-bob", "actor-bob"),
            ):
                uow.session_contexts.insert(
                    SessionContextRecord(
                        session_context_id=session_id,
                        research_run_id="run-1",
                        identity_id=identity_id,
                        actor_reference=actor,
                        origin=self.origin,
                        authentication_profile_reference="profile-form",
                        authentication_method="HTTP_FORM_LOGIN",
                        secret_scheme="SESSION_MATERIAL",
                        secret_name=f"session:{session_id}",
                        state="ACTIVE",
                        created_at=now,
                        updated_at=now,
                        established_at=now,
                        session_cookie_name=SESSION_COOKIE_NAME,
                    )
                )
            uow.commit()
        secrets.put_session(
            session_material_reference("session-alice"),
            f"{SESSION_COOKIE_NAME}={ALICE_COOKIE}",
        )
        secrets.put_session(
            session_material_reference("session-bob"),
            f"{SESSION_COOKIE_NAME}={BOB_COOKIE}",
        )
        worker = LabWorkerPort(self.browser)
        controller = AutonomousResearchController(
            factory, worker, ScriptedModelPort(), secret_port=secrets
        )
        config = DiscoveryRunConfig(
            research_run_id="run-1",
            seed_target_reference=self.origin + "/",
            normalized_origin=self.origin,
            normalized_path="/",
            bounds=_bounds(),
        )
        start = SurfaceDiscoveryStart(
            config=config,
            compiled_scope=compiled,
            identities=(ANONYMOUS_IDENTITY_ID, "id-alice", "id-bob"),
            session_context_by_identity={"id-alice": "session-alice", "id-bob": "session-bob"},
            identity_by_id={"id-alice": alice, "id-bob": bob},
        )
        command = StartAutonomousResearchCommand(
            research_run_id="run-1",
            budget_id="budget-1",
            target_reference="target-1",
            scope=ScopeEvaluationInput(
                matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
                ambiguous=False,
            ),
            bounds=OrchestrationBounds(
                max_cycles=128,
                max_experiments=64,
                max_model_calls=0,
                max_worker_invocations=128,
                max_elapsed_ms=180_000,
                max_selected_opportunities=16,
                max_runtime_fallback=0,
                side_effect_ceiling=1,
                allow_repeated_control_experiments=True,
            ),
            surface_discovery=start,
            program_policy=ProgramPolicyView(
                loopback_fixture=True,
                max_response_bytes=4096,
                timeout_ms=2000,
                action_policy={},
            ),
        )
        result = controller.run_bounded(command)
        self.assertIsNotNone(result.stop_reason)
        with factory.open() as uow:
            facts = uow.discovery_facts.list_for_research_run("run-1")
            inferences = uow.discovery_inferences.list_for_research_run("run-1")
            frontiers = uow.frontier_items.list_for_research_run("run-1")
            control_events = uow.control_events.list_for_research_run("run-1")
            attempts = uow.execution_attempts.list_for_research_run("run-1")
            persisted = uow.discovery_run_configs.get("run-1")
            observations = uow.observations.list_for_research_run("run-1")
            domain_facts = tuple(
                item for item in (_fact_from_record(uow, row) for row in facts) if item is not None
            )
            domain_inferences = tuple(_inference_from_record(item) for item in inferences)
            uow.rollback()
        paths = {
            item.normalized_path
            for item in facts
            if item.fact_kind == DiscoveryFactKind.EXACT_PATH.value and item.normalized_path
        }
        methods = {
            (item.http_method, item.normalized_path)
            for item in facts
            if item.fact_kind == DiscoveryFactKind.HTTP_OPERATION.value
        }
        self.assertTrue(any(item.normalized_path == "/" for item in facts))
        self.assertIn("/api/browser-only", paths)
        self.assertIn("/api/orders/101", paths)
        self.assertIn("/api/orders/202", paths)
        self.assertIn("/api/orders/303", paths)
        self.assertTrue(any(item[0] == "GET" for item in methods))
        identities = {item.identity_id for item in facts}
        self.assertIn(ANONYMOUS_IDENTITY_ID, identities)
        self.assertIn("id-alice", identities)
        self.assertIn("id-bob", identities)
        graph = rebuild_attack_surface_graph(
            research_run_id="run-1",
            strategy_version=SURFACE_DISCOVERY_STRATEGY_VERSION,
            facts=domain_facts,
            inferences=domain_inferences,
        )
        self.assertFalse(graph.grants_scope())
        self.assertFalse(graph.binds_session())
        self.assertFalse(graph.mints_budget())
        self.assertFalse(graph.mints_capability())
        self.assertTrue(
            any(item.kind is AttackSurfaceEdgeKind.OBSERVED_REQUEST_TO for item in graph.edges)
        )
        self.assertFalse(any(item.inference_kind == "OBJECT_INSTANCE" for item in inferences))
        templates = [item for item in inferences if item.inference_kind == "ROUTE_TEMPLATE"]
        self.assertTrue(templates)
        self.assertTrue(all(item.epistemic_status == "INFERRED" for item in templates))
        characterize = [
            item
            for item in frontiers
            if item.goal_kind == DiscoveryGoalKind.CHARACTERIZE_HTTP_OPERATION.value
        ]
        self.assertTrue(characterize)
        self.assertTrue(all((item.attributes or {}).get("auto_replay") is False for item in characterize))
        browser_calls = [call for call in worker.calls if call.get("worker_capability") == "browser.page"]
        http_calls = [call for call in worker.calls if call.get("worker_capability") == "http.transaction"]
        self.assertTrue(browser_calls)
        first_http_index = next(
            (index for index, call in enumerate(worker.calls) if call.get("worker_capability") == "http.transaction"),
            None,
        )
        if first_http_index is not None:
            self.assertTrue(
                any(
                    call.get("worker_capability") == "browser.page"
                    for call in worker.calls[:first_http_index]
                )
            )
        origins = {
            str((call.get("arguments") or {}).get("authorized_origin") or "").rstrip("/")
            for call in worker.calls
        }
        self.assertNotIn("http://example.com", origins)
        self.assertTrue(all(not item.structural_signature.startswith("el-") for item in frontiers))
        self.assertFalse(
            any(
                item.epistemic_status == "OBSERVED" and item.fact_kind == "SCOPE_BOUNDARY_CANDIDATE"
                for item in facts
            )
        )
        unknown = [item for item in attempts if item.state == "UNKNOWN_OUTCOME"]
        self.assertEqual(unknown, [])
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.configuration_fingerprint, config.fingerprint())
        self.assertGreaterEqual(len(observations), 1)
        self.assertTrue(any(obs.observation_kind == "BROWSER_PAGE_STATE" for obs in observations))
        context = pack_surface_discovery_context(
            graph,
            research_run_id="run-1",
            research_question="Observe authorized in-scope target surface under configured identities.",
            unexplored_frontier=tuple(_frontier_from_record(item) for item in frontiers),
        )
        blob = json.dumps(asdict(context), default=str).lower()
        self.assertNotIn("hidden_route_map", blob)
        self.assertNotIn("ground_truth", blob)
        self.assertNotIn(HIDDEN_TRUTH["leakage_canary"].lower(), blob)
        self.assertNotIn("vulnerability", blob)
        self.assertNotIn("exploit", blob)
        controller._discovery.clear_live_pages()
        self.assertEqual(controller._discovery._live_pages, {})
        if http_calls:
            self.assertTrue(all(call.get("action") in {"read", "mutate"} for call in http_calls))
        self.assertIn("/hidden", paths)
        self.assertTrue(any(item[0] == "POST" for item in methods) or any(
            (item.attributes or {}).get("method") == "POST" for item in characterize
        ))
        transitions = [item for item in facts if item.fact_kind == "WORKFLOW_TRANSITION"]
        self.assertTrue(
            transitions
            or any(item.kind is AttackSurfaceEdgeKind.TRANSITIONS_TO for item in graph.edges)
        )
        self.assertTrue(
            control_events
            or any(item.fact_kind == "SCOPE_BOUNDARY_CANDIDATE" for item in facts)
        )
        del result


def _fact_from_record(uow, record) -> DiscoveryFact | None:
    sources = tuple(
        DiscoveryFactSourceView(
            source_plane=(
                DiscoverySourcePlane.OBSERVATION
                if item.observation_id
                else DiscoverySourcePlane.CONTROL_EVENT
                if item.control_event_id
                else None
            ),
            observation_id=item.observation_id,
            control_event_id=item.control_event_id,
            source_fact_id=item.source_fact_id,
            source_inference_id=item.source_inference_id,
        )
        for item in uow.discovery_fact_sources.list_for_fact(record.fact_id)
    )
    if not sources:
        return None
    return DiscoveryFact(
        fact_id=record.fact_id,
        research_run_id=record.research_run_id,
        fact_kind=DiscoveryFactKind(record.fact_kind),
        canonical_key=record.canonical_key,
        epistemic_status=TargetEpistemicStatus(record.epistemic_status),
        identity_id=record.identity_id,
        target_reference=record.target_reference,
        sources=sources,
        session_context_id=record.session_context_id,
        normalized_origin=record.normalized_origin,
        normalized_path=record.normalized_path,
        http_method=record.http_method,
        attributes=record.attributes,
    )


def _inference_from_record(record) -> DiscoveryInference:
    return DiscoveryInference(
        inference_id=record.inference_id,
        research_run_id=record.research_run_id,
        inference_kind=DiscoveryInferenceKind(record.inference_kind),
        canonical_key=record.canonical_key,
        epistemic_status=TargetEpistemicStatus(record.epistemic_status),
        identity_id=record.identity_id,
        source_fact_ids=(),
        source_inference_ids=(),
        source_observation_ids=(),
        attributes=record.attributes,
    )


def _frontier_from_record(record) -> FrontierItem:
    return FrontierItem(
        frontier_id=record.frontier_id,
        research_run_id=record.research_run_id,
        goal_kind=DiscoveryGoalKind(record.goal_kind),
        candidate_origin=record.candidate_origin,
        candidate_path=record.candidate_path,
        identity_id=record.identity_id,
        proposed_capability=record.proposed_capability,
        proposed_action=record.proposed_action,
        expected_side_effect=record.expected_side_effect,
        budget_class=record.budget_class,
        structural_signature=record.structural_signature,
        dedupe_identity=record.dedupe_identity,
        strategy_version=record.strategy_version,
        session_context_id=record.session_context_id,
        scope_hint=record.scope_hint,
        attributes=record.attributes,
    )
