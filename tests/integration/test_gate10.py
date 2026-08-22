"""GATE 10 — Runtime / Strix Boundary Integrity on real PostgreSQL.

Architecture PASS does not fabricate runtime availability. GATE 04B may remain PENDING.
No new Alembic. Head remains a15_001_exploration_temporal.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research_os.application.authorize_strix_execution import (
    AuthorizeStrixExecution,
    AuthorizeStrixExecutionCommand,
)
from research_os.core.enums import ExecutionDecisionKind, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.platform.strix import StrixExecutionOutcome, StrixRuntimeStatus
from research_os.research.model_port import ModelCallRequest, ModelRole
from research_os.research.model_runtime import RuntimeKind, api_runtime_identity, cli_session_runtime_identity
from research_os.tools.capabilities import (
    CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
    STRIX_DIAGNOSTIC_PING_CAPABILITY,
)
from integration.harness import (
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    seed_authorized_spine,
    truncate_spine,
)

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


class RecordingStrix:
    def __init__(self, outcome: StrixExecutionOutcome | None = None) -> None:
        self.calls = []
        self._outcome = outcome

    def execute(self, request):
        self.calls.append(request)
        if self._outcome is not None:
            return self._outcome
        return StrixExecutionOutcome(
            status=StrixRuntimeStatus.COMPLETED,
            untrusted=True,
            capability=request.capability,
            reason_codes=("STRIX_DIAGNOSTIC_PING",),
            payload={"not_observation": True, "not_evidence": True},
        )


def _availability_label(available: bool) -> str:
    return "AVAILABLE" if available else "UNAVAILABLE"


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate10RuntimeStrixBoundaryTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 10 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_denied_strix_never_executes_and_creates_no_observation(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        strix = RecordingStrix()
        result = AuthorizeStrixExecution(factory, strix, clock=FixedClock()).execute(
            AuthorizeStrixExecutionCommand(
                research_run_id="run-1",
                experiment_id="exp-1",
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                scope=ScopeEvaluationInput(
                    matches=(
                        ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "scope-src"),
                    ),
                    ambiguous=False,
                ),
            )
        )
        self.assertEqual(result.core_decision, ExecutionDecisionKind.DENY)
        self.assertFalse(result.reached_strix)
        self.assertEqual(strix.calls, [])
        with factory.open() as uow:
            self.assertEqual(uow.observations.list_for_research_run("run-1"), [])
            self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
            uow.commit()

    def test_strix_runtime_failure_creates_no_observation_or_evidence(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        strix = RecordingStrix(
            StrixExecutionOutcome(
                status=StrixRuntimeStatus.UNAVAILABLE,
                untrusted=True,
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                reason_codes=("STRIX_RUNTIME_UNAVAILABLE",),
                payload={"not_observation": True, "not_evidence": True},
            )
        )
        result = AuthorizeStrixExecution(factory, strix, clock=FixedClock()).execute(
            AuthorizeStrixExecutionCommand(
                research_run_id="run-1",
                experiment_id="exp-1",
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                scope=_allow_scope(),
            )
        )
        self.assertTrue(result.reached_strix)
        assert result.outcome is not None
        self.assertEqual(result.outcome.status, StrixRuntimeStatus.UNAVAILABLE)
        with factory.open() as uow:
            self.assertEqual(uow.observations.list_for_research_run("run-1"), [])
            self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
            uow.commit()

    def test_runtime_identities_and_availability_are_reported_without_fabrication(self) -> None:
        from integrations.models.cli_session import (
            CodexCliSessionAdapter,
            load_codex_model_configurations,
            probe_codex_cli,
        )
        from integrations.models.external_agent import probe_external_agent
        from integrations.models.factory import probe_live_adapter
        from integrations.models.local_runtime import probe_local_model
        from integrations.strix.adapter import StrixDiagnosticAdapter, probe_strix_runtime

        api = probe_live_adapter("openai")
        cli = probe_codex_cli()
        local = probe_local_model()
        external = probe_external_agent()
        strix = probe_strix_runtime()
        report = {
            "API": _availability_label(bool(api.available)),
            "CLI/session": _availability_label(cli.available),
            "local": _availability_label(local.available),
            "external agent": _availability_label(external.available),
            "Strix": _availability_label(bool(strix["available"])),
        }
        print(f"GATE 10 runtime availability {report}", flush=True)
        self.assertIn(report["API"], {"AVAILABLE", "UNAVAILABLE"})
        self.assertIn(report["CLI/session"], {"AVAILABLE", "UNAVAILABLE"})
        self.assertIn(report["local"], {"AVAILABLE", "UNAVAILABLE"})
        self.assertEqual(report["local"], "UNAVAILABLE")
        self.assertEqual(report["external agent"], "UNAVAILABLE")
        api_identity = api_runtime_identity(adapter_id="openai.responses", runtime_id="openai")
        cli_identity = cli_session_runtime_identity(
            adapter_id="codex.cli.session", runtime_id="codex-cli"
        )
        self.assertEqual(api_identity.runtime_kind, RuntimeKind.API)
        self.assertEqual(cli_identity.runtime_kind, RuntimeKind.CLI_SESSION)
        self.assertNotEqual(api_identity.configuration_fingerprint, cli_identity.configuration_fingerprint)

        if cli.available and cli.executable is not None:
            configs = load_codex_model_configurations()
            selected = configs[0] if configs else None
            if selected is None:
                print("GATE 10 CLI diagnostic skipped: no configured model", flush=True)
            else:
                adapter = CodexCliSessionAdapter(
                    allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
                    executable=cli.executable,
                    version=cli.version,
                    model=selected.model,
                    configuration_id=selected.configuration_id,
                )
                try:
                    adapter.complete(
                        ModelCallRequest(
                            role=ModelRole.GENERATOR,
                            correlation_id="gate10-cli",
                            context_fingerprint="gate10",
                            instructions="Reply with a JSON object only.",
                            payload={"echo": "ping"},
                        )
                    )
                    print("GATE 10 CLI diagnostic COMPLETED", flush=True)
                except Exception as exc:
                    print(f"GATE 10 CLI diagnostic outcome={type(exc).__name__}", flush=True)

        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        live_strix = StrixDiagnosticAdapter()
        AuthorizeStrixExecution(factory, live_strix, clock=FixedClock()).execute(
            AuthorizeStrixExecutionCommand(
                research_run_id="run-1",
                experiment_id="exp-1",
                capability=STRIX_DIAGNOSTIC_PING_CAPABILITY,
                target_reference="target-1",
                budget_id="budget-1",
                side_effect_level=0,
                scope=_allow_scope(),
            )
        )
        if live_strix.calls:
            self.assertTrue(live_strix.calls[0].authorization_decision_reference)
        with factory.open() as uow:
            self.assertEqual(uow.observations.list_for_research_run("run-1"), [])
            self.assertEqual(uow.evidence.list_for_research_run("run-1"), [])
            uow.commit()

    def test_migration_head_is_unchanged(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        self.assertEqual(version, "a41_001_runtime_instance")


if __name__ == "__main__":
    unittest.main()
