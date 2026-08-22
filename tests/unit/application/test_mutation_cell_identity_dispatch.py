"""Dispatch-level mutation cell identity: unknown cells never invoke Worker or coverage."""

from __future__ import annotations

import unittest

import pathsetup  # noqa: F401

from research_os.application.dispatch_approved_v3_queue import (
    DispatchApprovedV3Queue,
    DispatchApprovedV3QueueCommand,
)
from research_os.core.enums import ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from research_os.data.records import HuntV3QueueRecord, IssuedBudgetRecord
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.compiler_registry import COMPILER_MUTATION_MATRIX_CELL, CompilerOutcome
from research_os.research.mutation.identity import rebuild_authoritative_mutation_matrix
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    COMPLETED_AT,
    STARTED_AT,
    RecordingWorkerPort,
    invocation_outcome,
)
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _compiled_allow(host: str = "127.0.0.1", port: int = 8094):
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme="http",
                host=host,
                port=port,
                path_prefix=None,
                source_reference="scope-src",
            ),
        )
    )


def _http_success_handler(request):
    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
    raw = {
        "authorized_origin": arguments.get("authorized_origin"),
        "method": arguments.get("method") or "GET",
        "path": arguments.get("path") or "/",
        "status_code": 200,
        "body_length": 2,
        "body_digest": "aa",
        "framing_profile": arguments.get("framing_profile"),
        "lane": arguments.get("lane"),
        "control": arguments.get("control"),
        "write_count": 1,
        "bytes_written": 16,
        "request_fingerprint": "ab",
        "redirect": False,
    }
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
        worker_result={
            "contract_version": "v1",
            "correlation": dict(request["correlation"]),
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-16T20:00:00Z",
            "completed_at": "2026-08-16T20:00:01Z",
            "raw_result": raw,
        },
        exit_code=0,
    )


def _queue(**overrides) -> HuntV3QueueRecord:
    values = dict(
        queue_id="queue-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        family_id="hf-sqli",
        node_canonical_key="origin:http://127.0.0.1:8094|path:/ok|method:GET",
        identity_id=None,
        capability="mutation.matrix",
        action="plan",
        arguments={},
        side_effect_level=0,
        state="APPROVED",
        created_at=CREATED_AT,
    )
    values.update(overrides)
    return HuntV3QueueRecord(**values)


def _dispatch(store: _Store, worker=None, **command_overrides):
    factory = FakeUnitOfWorkFactory(store)
    port = worker or RecordingWorkerPort(store=store)
    use_case = DispatchApprovedV3Queue(factory, port, clock=FixedClock())
    values = dict(
        research_run_id="run-1",
        queue_id="queue-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        compiled_scope=_compiled_allow(),
    )
    values.update(command_overrides)
    return use_case.execute(DispatchApprovedV3QueueCommand(**values)), port


def _sqli_store() -> tuple[_Store, object]:
    matrix = rebuild_authoritative_mutation_matrix(family_name="SQL_INJECTION")
    cell = matrix.cells[0]
    store = _Store()
    seed_spine(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=20,
        max_tool_calls=20,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    store.hunt_v3_queue["queue-1"] = _queue(
        arguments={
            "family_name": "SQL_INJECTION",
            "family_id": "hf-sqli",
            "matrix_hash": matrix.matrix_hash,
            "authorized_origin": "http://127.0.0.1:8094",
            "path": "/ok",
            "cells": [
                {
                    "cell_id": cell.cell_id,
                    "dimension_values": dict(cell.dimension_values),
                    "control": cell.control,
                }
            ],
        }
    )
    return store, cell


class MutationCellIdentityDispatchTests(unittest.TestCase):
    def test_unknown_cell_is_blocked_unknown_with_zero_worker_and_no_coverage(self) -> None:
        store, cell = _sqli_store()
        result, port = _dispatch(
            store,
            worker=RecordingWorkerPort(store=store, handler=_http_success_handler),
            selected_cell_id="fabricated-cell-not-in-matrix",
            compile_arguments={
                "dimension_values": dict(cell.dimension_values),
                "control": cell.control,
                "query": {"injected": "1"},
                "body": "attacker",
                "headers": {"X-Attack": "1"},
            },
        )
        self.assertEqual(result.outcome, CompilerOutcome.BLOCKED_UNKNOWN_CELL.value)
        self.assertEqual(result.reason_code, "MUTATION_MATRIX_UNKNOWN_CELL")
        self.assertEqual(result.compiler_id, COMPILER_MUTATION_MATRIX_CELL)
        self.assertEqual(len(port.calls), 0)
        self.assertFalse(result.worker_invoked)
        self.assertFalse(result.coverage_recorded)
        self.assertEqual(len(store.execution_attempts), 0)
        self.assertEqual(len(store.observations), 0)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")

    def test_unknown_cell_arbitrary_query_body_headers_do_not_dispatch(self) -> None:
        for payload in (
            {"query": {"injected": "1=1"}},
            {"body": '{"drop":true}'},
            {"headers": {"X-Attack": "1"}},
        ):
            with self.subTest(payload=tuple(payload)):
                store, cell = _sqli_store()
                result, port = _dispatch(
                    store,
                    worker=RecordingWorkerPort(store=store, handler=_http_success_handler),
                    selected_cell_id="unknown-payload-cell",
                    compile_arguments={
                        "dimension_values": dict(cell.dimension_values),
                        "control": cell.control,
                        **payload,
                    },
                )
                self.assertEqual(result.outcome, "BLOCKED_UNKNOWN_CELL")
                self.assertEqual(len(port.calls), 0)
                self.assertFalse(result.coverage_recorded)
                self.assertEqual(len(store.execution_attempts), 0)

    def test_core_deny_known_cell_records_no_coverage(self) -> None:
        store, cell = _sqli_store()
        result, port = _dispatch(store, compiled_scope=None, selected_cell_id=cell.cell_id)
        self.assertEqual(result.outcome, "CORE_DENIED")
        self.assertEqual(len(port.calls), 0)
        self.assertFalse(result.coverage_recorded)

    def test_worker_timeout_does_not_record_coverage_or_falsify(self) -> None:
        store, cell = _sqli_store()
        worker = RecordingWorkerPort(
            store=store, outcome=invocation_outcome(InvocationStatus.TIMED_OUT)
        )
        result, port = _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        self.assertEqual(result.outcome, "INVOCATION_FAILED")
        self.assertEqual(len(port.calls), 1)
        self.assertFalse(result.coverage_recorded)
        self.assertEqual(len(store.observations), 0)
        consistent = [
            item
            for item in store.hypothesis_assessments.values()
            if item.assessment_outcome == "CONSISTENT_WITH_PREDICTION"
        ]
        self.assertEqual(consistent, [])

    def test_successful_observation_coverage_uses_exact_authoritative_cell(self) -> None:
        store, cell = _sqli_store()
        worker = RecordingWorkerPort(store=store, handler=_http_success_handler)
        result, port = _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        self.assertTrue(result.coverage_recorded)
        self.assertEqual(len(port.calls), 1)
        unit_events = [
            event
            for event in store.audit_events.values()
            if event.event_type == "HUNT_V3_UNIT_OUTCOME"
            and event.payload.get("coverage_eligible") is True
        ]
        self.assertEqual(len(unit_events), 1)
        self.assertEqual(unit_events[0].payload.get("unit_id"), cell.cell_id)

    def test_duplicate_valid_cell_keeps_unit_idempotency(self) -> None:
        store, cell = _sqli_store()
        worker = RecordingWorkerPort(store=store, handler=_http_success_handler)
        first, port = _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        second, _ = _dispatch(store, worker=port, selected_cell_id=cell.cell_id)
        self.assertTrue(first.coverage_recorded)
        self.assertEqual(second.outcome, "ALREADY_DISPATCHED")
        self.assertEqual(second.reason_code, "UNIT_ALREADY_ATTEMPTED")
        self.assertEqual(len(port.calls), 1)

    def test_compile_only_does_not_create_attempts_or_coverage(self) -> None:
        store, cell = _sqli_store()
        factory = FakeUnitOfWorkFactory(store)
        compiled = DispatchApprovedV3Queue(
            factory, RecordingWorkerPort(store=store), clock=FixedClock()
        )._compile(
            DispatchApprovedV3QueueCommand(
                research_run_id="run-1",
                queue_id="queue-1",
                budget_id="budget-1",
                target_reference="target-1",
                scope=_allow_scope(),
                compiled_scope=_compiled_allow(),
                selected_cell_id=cell.cell_id,
            ),
            store.hunt_v3_queue["queue-1"],
        )
        self.assertTrue(compiled.compiled)
        self.assertEqual(len(store.execution_attempts), 0)
        self.assertEqual(len(store.observations), 0)


if __name__ == "__main__":
    unittest.main()
