"""SD-G6 mutation engine + OAST core integration.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.admit_oast_callback import (
    AdmitOastCallback,
    OastCallbackAdmissionResult,
)
from zest.application.record_mutation_variants import RecordMutationVariants
from zest.core.enums import ScopeClassification
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    OastTokenRecord,
    ProgramPolicyRecord,
)
from zest.research.discovery.graph import AttackSurfaceGraph, AttackSurfaceNode
from zest.research.discovery.types import AttackSurfaceNodeKind
from zest.research.target_model import TargetEpistemicStatus
from fixtures.oast import LoopbackOastPort

TEST_URL = configured_test_url()


def _http_node() -> AttackSurfaceNode:
    return AttackSurfaceNode(
        node_id="op-1",
        kind=AttackSurfaceNodeKind.HTTP_OPERATION,
        canonical_key="origin:http://example.com|path:/api/users|method:GET",
        epistemic_status=TargetEpistemicStatus.UNTRUSTED_EXTERNAL,
        identity_ids=(),
        provenance_refs=("sensor_observation:so-1",),
        scope_classification=ScopeClassification.IN_SCOPE,
        attributes={
            "origin": "http://example.com",
            "path": "/api/users",
            "method": "GET",
            "query_params": ["id"],
        },
    )


def _graph() -> AttackSurfaceGraph:
    return AttackSurfaceGraph(
        research_run_id="run-1",
        strategy_version="surface.discovery.v1",
        nodes=(_http_node(),),
        edges=(),
    )


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG6MutationOastIntegrationTests(unittest.TestCase):
    engine = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        warn_destructive(TEST_URL)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)

    def setUp(self) -> None:
        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            uow.program_policies.insert(
                ProgramPolicyRecord(
                    program_id="prog-1",
                    loopback_fixture=False,
                    max_response_bytes=4096,
                    timeout_ms=2000,
                    created_at=NOW,
                    updated_at=NOW,
                    action_policy={},
                )
            )
            uow.commit()

    def test_mutation_variants_are_recorded_in_ledger(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        use_case = RecordMutationVariants(uow_factory, clock=lambda: NOW)
        result = use_case.execute(_http_node(), _graph(), research_run_id="run-1")
        self.assertGreater(result.variant_count, 0)

        with PostgresUnitOfWork(self.engine) as uow:
            events = uow.audit_events.list_for_subject_type("ATTACK_SURFACE_NODE")
            uow.rollback()
        self.assertEqual(len(events), result.variant_count)
        for event in events:
            self.assertEqual(event.event_type, "MUTATION_VARIANT_PLANNED")
            payload = event.payload
            self.assertIn("variant_id", payload)
            self.assertIn("arguments", payload)
            # No secrets in audit payload.
            self.assertNotIn("token", payload["arguments"])
            self.assertNotIn("secret", payload["arguments"])

    def test_legacy_oast_token_callback_is_not_authoritative(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            uow.oast_tokens.insert(
                OastTokenRecord(
                    token_id="tok-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    target_reference="http://example.com",
                    expires_at=NOW + timedelta(hours=1),
                    created_at=NOW,
                )
            )
            uow.commit()

        oast = LoopbackOastPort()
        oast.mint_token(
            token_id="tok-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=NOW + timedelta(hours=1),
        )
        callback = oast.register_callback(
            "tok-1",
            source_address="192.0.2.1",
            request_summary={"path": "/cb", "method": "GET"},
            received_at=NOW,
        )

        admitter = AdmitOastCallback(uow_factory, clock=lambda: NOW)
        result = admitter.execute(
            callback,
            scope_classification=ScopeClassification.IN_SCOPE.value,
        )
        self.assertIsInstance(result, OastCallbackAdmissionResult)
        self.assertFalse(result.admitted)
        self.assertIsNone(result.fact_id)
        self.assertEqual(result.observation_id, "")
        self.assertEqual(result.reason_code, "OAST_LEGACY_TOKEN_NOT_AUTHORITATIVE")

        with PostgresUnitOfWork(self.engine) as uow:
            self.assertEqual(uow.sensor_observations.list_for_research_run("run-1"), [])
            self.assertEqual(uow.discovery_facts.list_for_research_run("run-1"), [])
            uow.rollback()

    def test_expired_oast_callback_is_rejected(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            uow.oast_tokens.insert(
                OastTokenRecord(
                    token_id="tok-expired",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    target_reference="http://example.com",
                    expires_at=NOW - timedelta(minutes=1),
                    created_at=NOW - timedelta(hours=1),
                )
            )
            uow.commit()

        oast = LoopbackOastPort()
        oast.mint_token(
            token_id="tok-expired",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            target_reference="http://example.com",
            expires_at=NOW - timedelta(minutes=1),
        )
        callback = oast.register_callback(
            "tok-expired",
            source_address="192.0.2.1",
            request_summary={"path": "/cb", "method": "GET"},
            received_at=NOW,
        )

        admitter = AdmitOastCallback(uow_factory, clock=lambda: NOW)
        result = admitter.execute(
            callback,
            scope_classification=ScopeClassification.IN_SCOPE.value,
        )
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason_code, "OAST_TOKEN_EXPIRED")


if __name__ == "__main__":
    unittest.main()
