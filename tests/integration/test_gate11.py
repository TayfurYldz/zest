"""GATE 11 — Runtime Routing Integrity on real PostgreSQL.

GATE 11 can PASS while GATE 04B remains PENDING. Availability is not fabricated.
No new Alembic. Head remains a15_001_exploration_temporal.
"""

from __future__ import annotations

import json
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

from research_os.application.select_research_runtime import (
    SelectResearchRuntime,
    SelectResearchRuntimeCommand,
)
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.research.model_port import ModelRole
from research_os.research.model_runtime import api_runtime_identity, cli_session_runtime_identity
from research_os.research.routing import (
    CandidateLocality,
    RoutingBudget,
    RoutingOutcome,
    RoutingRequest,
    RuntimeCandidate,
    RuntimeQualityObservation,
    select_runtime,
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


def _api(adapter_id: str, **kwargs) -> RuntimeCandidate:
    values = dict(
        identity=api_runtime_identity(adapter_id=adapter_id, runtime_id=adapter_id),
        available=True,
        authenticated=True,
        structured_output_compatible=True,
        locality=CandidateLocality.REMOTE,
    )
    values.update(kwargs)
    return RuntimeCandidate(**values)


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; PostgreSQL integration tests skipped "
    "(SQLite is not a substitute)",
)
class Gate11RuntimeRoutingTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 11 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def test_routing_decision_is_audited_and_does_not_authorize(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            seed_authorized_spine(uow)
            uow.commit()
        request = RoutingRequest(
            role=ModelRole.GENERATOR,
            candidates=(
                _api(
                    "unsafe.fast",
                    quality=RuntimeQualityObservation(grounding_safety_hard_failures=2, latency_ms=1),
                ),
                _api(
                    "safer.slow",
                    quality=RuntimeQualityObservation(grounding_safety_hard_failures=0, latency_ms=8_000),
                ),
                RuntimeCandidate(
                    identity=cli_session_runtime_identity(
                        adapter_id="codex.cli.session", runtime_id="codex-cli"
                    ),
                    available=True,
                    authenticated=True,
                    structured_output_compatible=True,
                    allowed_capabilities=("codex.diagnostic.structured_output",),
                ),
            ),
            budget=RoutingBudget(max_runtime_attempts=2, max_fallback_attempts=1),
            require_operator_on_tie=False,
        )
        result = SelectResearchRuntime(factory, clock=FixedClock()).execute(
            SelectResearchRuntimeCommand(research_run_id="run-1", request=request)
        )
        self.assertEqual(result.decision.outcome, RoutingOutcome.SELECT)
        assert result.decision.selected_identity is not None
        self.assertEqual(result.decision.selected_identity.adapter_id, "safer.slow")
        with factory.open() as uow:
            event = uow.audit_events.get(result.audit_event_id)
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual(event.event_type, "RUNTIME_ROUTING_DECISION")
            payload = event.payload
            self.assertTrue(payload["not_authorization"])
            self.assertTrue(payload["no_aggregate_model_score"])
            self.assertTrue(payload["no_automatic_winner"])
            self.assertNotIn("WINNER", payload)
            self.assertNotIn("winner", payload)
            uow.commit()

    def test_discovery_matrix_and_gate_04b_are_reported_without_fabrication(self) -> None:
        from integrations.models.discovery import discover_configured_runtimes, gate_04b_status

        report = discover_configured_runtimes()
        print(f"GATE 11 runtime matrix {report.kind_matrix}", flush=True)
        print(
            f"GATE 11 available model configurations {list(report.available_model_configurations)}",
            flush=True,
        )
        status = gate_04b_status(
            available_model_configurations=report.available_model_configurations,
            executed_live_configurations=(),
            comparable=False,
            harness_invariant_failed=False,
            runs_per_scenario=3,
            development_suite=True,
        )
        print(f"GATE 04B {status['status']}: {status['reason']}", flush=True)
        self.assertIn(status["status"], {"PASS", "PENDING", "NEEDS_REVIEW"})
        if len(report.available_model_configurations) < 2:
            self.assertEqual(status["status"], "PENDING")
        strix = next(item for item in report.entries if item.runtime_kind == "STRIX")
        self.assertFalse(strix.counts_as_model_runtime)
        serialized = json.dumps(report.to_mapping())
        self.assertNotIn("sk-", serialized)

    def test_routing_is_deterministic_for_the_same_observations(self) -> None:
        request = RoutingRequest(
            role=ModelRole.FALSIFIER,
            candidates=(
                _api("alpha", quality=RuntimeQualityObservation(falsifier_quality_failures=2)),
                _api("beta", quality=RuntimeQualityObservation(falsifier_quality_failures=0)),
            ),
            budget=RoutingBudget(max_runtime_attempts=1, max_fallback_attempts=0),
            require_operator_on_tie=False,
        )
        first = select_runtime(request)
        second = select_runtime(request)
        self.assertEqual(first.to_mapping(), second.to_mapping())

    def test_migration_head_is_unchanged(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        self.assertEqual(version, "a42_001_preflight_report")


if __name__ == "__main__":
    unittest.main()
