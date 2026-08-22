"""Slice 4B/4C mutation + protocol execution against real PostgreSQL."""

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
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from research_os.application.dispatch_approved_v3_queue import (
    DispatchApprovedV3Queue,
    DispatchApprovedV3QueueCommand,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from research_os.data.postgres.engine import create_sync_engine
from research_os.data.postgres.hunter_family_seed import SEED_FAMILIES
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import HuntV3QueueRecord
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.mutation.matrix import build_mutation_matrix
from research_os.research.protocol.parser_plan import build_protocol_parser_plan
from research_os.research.selection import HunterFamilyView
from research_os.worker_runtime.python.runtime import build_result, utc_now_rfc3339
from support.recording_worker import RecordingWorkerPort

TEST_URL = configured_test_url()


def _seed_family(family_id: str) -> HunterFamilyView:
    row = next(item for item in SEED_FAMILIES if item["family_id"] == family_id)
    return HunterFamilyView(
        family_id=str(row["family_id"]),
        name=str(row["name"]),
        target_node_kinds=tuple(str(item) for item in row["target_node_kinds"]),
        preconditions=dict(row["preconditions"]),
        claim_template=str(row["claim_template"]),
        evidence_requirements=dict(row["evidence_requirements"]),
        validation_tier=str(row["validation_tier"]),
        enabled=bool(row["enabled"]),
        version=int(row["version"]),
    )


def _in_process_worker():
    def handler(request):
        result = build_result(request, utc_now_rfc3339())
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=NOW,
            completed_at=NOW,
            worker_result=result,
            exit_code=0,
        )

    return RecordingWorkerPort(handler=handler)


def _compiled_scope(origin: str):
    parsed = urlsplit(origin)
    return compile_scope_rules(
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


@unittest.skipUnless(
    TEST_URL,
    "RESEARCH_OS_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class Slice4B4CExecutionIntegrationTests(unittest.TestCase):
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
            uow.commit()
        self.lab = Gate19HttpLab()
        self.origin = self.lab.start()

    def tearDown(self) -> None:
        self.lab.stop()

    def test_sql_injection_cell_executes_through_postgres_and_records_observation(self) -> None:
        cell = build_mutation_matrix(_seed_family("hf-sqli")).cells[0]
        with PostgresUnitOfWork(self.engine) as uow:
            uow.hunt_v3_queue.insert(
                HuntV3QueueRecord(
                    queue_id="queue-sqli-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    family_id="hf-sqli",
                    node_canonical_key=f"origin:{self.origin}|path:/ok|method:GET",
                    identity_id=None,
                    capability="mutation.matrix",
                    action="plan",
                    arguments={
                        "family_name": "SQL_INJECTION",
                        "authorized_origin": self.origin,
                        "path": "/ok",
                        "cells": [
                            {
                                "cell_id": cell.cell_id,
                                "dimension_values": dict(cell.dimension_values),
                                "control": cell.control,
                            }
                        ],
                    },
                    side_effect_level=0,
                    state="APPROVED",
                    created_at=NOW,
                )
            )
            uow.commit()
        worker = _in_process_worker()
        result = DispatchApprovedV3Queue(PostgresUnitOfWorkFactory(self.engine), worker).execute(
            DispatchApprovedV3QueueCommand(
                research_run_id="run-1",
                queue_id="queue-sqli-1",
                budget_id="budget-1",
                target_reference="target-1",
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
                    ambiguous=False,
                ),
                compiled_scope=_compiled_scope(self.origin),
                selected_cell_id=cell.cell_id,
            )
        )
        self.assertEqual(result.reason_code, "DISPATCHED")
        self.assertTrue(result.worker_invoked)
        self.assertEqual(len(worker.calls), 1)
        with PostgresUnitOfWork(self.engine) as uow:
            attempts = uow.execution_attempts.list_for_experiment(result.experiment_id)
            observations = uow.observations.list_for_experiment(result.experiment_id)
            item = uow.hunt_v3_queue.get("queue-sqli-1")
            uow.rollback()
        self.assertEqual(len(attempts), 1)
        self.assertGreaterEqual(len(observations), 1)
        self.assertEqual(item.state, "APPROVED")

    def test_unknown_mutation_cell_is_blocked_with_zero_worker_and_no_attempt(self) -> None:
        cell = build_mutation_matrix(_seed_family("hf-sqli")).cells[0]
        with PostgresUnitOfWork(self.engine) as uow:
            uow.hunt_v3_queue.insert(
                HuntV3QueueRecord(
                    queue_id="queue-sqli-unknown",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    family_id="hf-sqli",
                    node_canonical_key=f"origin:{self.origin}|path:/ok|method:GET",
                    identity_id=None,
                    capability="mutation.matrix",
                    action="plan",
                    arguments={
                        "family_name": "SQL_INJECTION",
                        "family_id": "hf-sqli",
                        "authorized_origin": self.origin,
                        "path": "/ok",
                        "cells": [
                            {
                                "cell_id": cell.cell_id,
                                "dimension_values": dict(cell.dimension_values),
                                "control": cell.control,
                            }
                        ],
                    },
                    side_effect_level=0,
                    state="APPROVED",
                    created_at=NOW,
                )
            )
            uow.commit()
        worker = _in_process_worker()
        result = DispatchApprovedV3Queue(PostgresUnitOfWorkFactory(self.engine), worker).execute(
            DispatchApprovedV3QueueCommand(
                research_run_id="run-1",
                queue_id="queue-sqli-unknown",
                budget_id="budget-1",
                target_reference="target-1",
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
                    ambiguous=False,
                ),
                compiled_scope=_compiled_scope(self.origin),
                selected_cell_id="fabricated-cell-not-in-matrix",
                compile_arguments={
                    "dimension_values": dict(cell.dimension_values),
                    "control": cell.control,
                    "query": {"injected": "1"},
                    "body": "attacker",
                },
            )
        )
        self.assertEqual(result.outcome, "BLOCKED_UNKNOWN_CELL")
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")
        self.assertEqual(len(worker.calls), 0)
        self.assertFalse(result.coverage_recorded)
        self.assertFalse(result.worker_invoked)
        with PostgresUnitOfWork(self.engine) as uow:
            attempts = uow.execution_attempts.list_for_research_run("run-1")
            observations = uow.observations.list_for_research_run("run-1")
            item = uow.hunt_v3_queue.get("queue-sqli-unknown")
            uow.rollback()
        self.assertEqual(len(attempts), 0)
        self.assertEqual(len(observations), 0)
        self.assertEqual(item.state, "APPROVED")

    def test_protocol_step_fresh_core_deny_is_zero_worker(self) -> None:
        plan = build_protocol_parser_plan(_seed_family("hf-http-smuggling-desync"))
        step = plan.steps[0]
        with PostgresUnitOfWork(self.engine) as uow:
            uow.hunt_v3_queue.insert(
                HuntV3QueueRecord(
                    queue_id="queue-proto-1",
                    research_run_id="run-1",
                    hypothesis_id="hyp-1",
                    family_id="hf-http-smuggling-desync",
                    node_canonical_key=f"origin:{self.origin}|path:/ok|method:GET",
                    identity_id=None,
                    capability="protocol.parser",
                    action="plan",
                    arguments={
                        "family_name": "HTTP_REQUEST_SMUGGLING_DESYNC",
                        "protocol_lane": plan.lane,
                        "authorized_origin": self.origin,
                        "path": "/ok",
                        "steps": [
                            {
                                "step_id": step.step_id,
                                "dimension_values": dict(step.dimension_values),
                                "control": step.control,
                            }
                        ],
                    },
                    side_effect_level=3,
                    state="APPROVED",
                    created_at=NOW,
                )
            )
            uow.commit()
        worker = _in_process_worker()
        result = DispatchApprovedV3Queue(PostgresUnitOfWorkFactory(self.engine), worker).execute(
            DispatchApprovedV3QueueCommand(
                research_run_id="run-1",
                queue_id="queue-proto-1",
                budget_id="budget-1",
                target_reference="target-1",
                scope=ScopeEvaluationInput(
                    matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
                    ambiguous=False,
                ),
                compiled_scope=None,
                selected_step_id=step.step_id,
            )
        )
        self.assertEqual(result.outcome, "CORE_DENIED")
        self.assertEqual(len(worker.calls), 0)
