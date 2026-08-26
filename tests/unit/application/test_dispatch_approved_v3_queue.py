"""MR-4: APPROVED V3 items compile through the registry and re-authorize at Core."""

from __future__ import annotations

import unittest
from dataclasses import replace

import pathsetup  # noqa: F401

from zest.application.dispatch_approved_v3_queue import (
    DispatchApprovedV3Queue,
    DispatchApprovedV3QueueCommand,
    HuntV3DispatchError,
)
from zest.application.hunt_v3_queue_approval import (
    ApproveHuntV3Queue,
    ApproveHuntV3QueueCommand,
    approval_subject_for_queue,
)
from zest.core.enums import ActorType, ApprovalDecision, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.records import ApprovalRecord, HuntV3QueueRecord, IssuedBudgetRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.compiler_registry import (
    COMPILER_AUTHORIZATION_DIFFERENTIAL,
    COMPILER_MUTATION_MATRIX_CELL,
    COMPILER_PROTOCOL_STEP,
)
from zest.research.mutation.matrix import build_mutation_matrix
from zest.research.protocol.parser_plan import build_protocol_parser_plan
from zest.data.postgres.hunter_family_seed import SEED_FAMILIES
from zest.research.selection import HunterFamilyView
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort, STARTED_AT, COMPLETED_AT
from support.spine import CREATED_AT, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _queue(**overrides) -> HuntV3QueueRecord:
    values = dict(
        queue_id="queue-1",
        research_run_id="run-1",
        hypothesis_id="hyp-1",
        family_id="hf-object-authz",
        node_canonical_key="origin:http://127.0.0.1:8094|path:/accounts|method:GET",
        identity_id=None,
        capability="http.authorization_differential",
        action="probe",
        arguments={
            "claim": "object authorization",
            "node_id": "op-1",
            "family_name": "OBJECT_AUTHORIZATION",
            "family_id": "hf-object-authz",
            "identity_id": "ANONYMOUS",
            "authorized_origin": "http://127.0.0.1:8094",
            "actor": "alice",
            "own_object": "1",
            "cross_object": "2",
            "mode": "vulnerable",
        },
        side_effect_level=0,
        state="APPROVED",
        created_at=CREATED_AT,
    )
    values.update(overrides)
    return HuntV3QueueRecord(**values)


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


def _sqli_cell():
    matrix = build_mutation_matrix(_seed_family("hf-sqli"))
    return matrix.cells[0]


def _smuggling_step():
    plan = build_protocol_parser_plan(_seed_family("hf-http-smuggling-desync"))
    return plan.lane, plan.steps[0]


class DispatchApprovedV3QueueTests(unittest.TestCase):
    def _store(self) -> _Store:
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
        return store

    def test_object_authorization_compiles_and_creates_exactly_one_attempt(self) -> None:
        store = self._store()
        store.hunt_v3_queue["queue-1"] = _queue()
        result, port = _dispatch(store)
        self.assertEqual(result.state, "RUN")
        self.assertEqual(result.compiler_id, COMPILER_AUTHORIZATION_DIFFERENTIAL)
        self.assertEqual(result.reason_code, "DISPATCHED")
        self.assertEqual(len(port.calls), 1)
        attempts = list(store.execution_attempts.values())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "RUN")
        self.assertTrue(result.worker_invoked)

    def test_second_dispatch_is_idempotent_and_does_not_create_another_attempt(self) -> None:
        store = self._store()
        store.hunt_v3_queue["queue-1"] = _queue()
        first, port = _dispatch(store)
        self.assertEqual(first.state, "RUN")
        second, _ = _dispatch(store, worker=port)
        self.assertEqual(second.outcome, "ALREADY_DISPATCHED")
        self.assertEqual(second.reason_code, "ALREADY_RUN")
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(len(store.execution_attempts), 1)

    def test_stale_approval_does_not_bypass_fresh_core_deny(self) -> None:
        store = self._store()
        store.hunt_v3_queue["queue-1"] = _queue()
        store.approvals["approval-1"] = ApprovalRecord(
            approval_id="approval-1",
            subject_reference=approval_subject_for_queue("queue-1"),
            decision=ApprovalDecision.APPROVE.value,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            recorded=True,
            created_at=CREATED_AT,
            research_run_id="run-1",
            proposal_id="proposal-placeholder",
            human_review_id="human-review-placeholder",
        )
        # Historical V3 approval plus a naive ScopeEvaluationInput is still not
        # execution authorization: HTTP capabilities require a compiled Core
        # scope, re-checked at dispatch time.
        result, port = _dispatch(store, compiled_scope=None)
        self.assertEqual(result.outcome, "CORE_DENIED")
        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "BLOCKED")
        self.assertEqual(store.approvals["approval-1"].decision, ApprovalDecision.APPROVE.value)

    def test_protocol_step_compiles_and_reauthorizes_without_granting_next_step(self) -> None:
        store = self._store()
        lane, step = _smuggling_step()
        store.hunt_v3_queue["queue-1"] = _queue(
            family_id="hf-http-smuggling-desync",
            capability="protocol.parser",
            action="plan",
            side_effect_level=3,
            arguments={
                "family_name": "HTTP_REQUEST_SMUGGLING_DESYNC",
                "protocol_lane": lane,
                "step_count": 8,
                "authorized_origin": "http://127.0.0.1:8094",
                "path": "/ok",
                "steps": [
                    {
                        "step_id": step.step_id,
                        "dimension_values": dict(step.dimension_values),
                        "control": step.control,
                    }
                ],
                "approval_required": "SE3",
                "worker_dispatch": "forbidden_until_se3_approval",
            },
        )
        worker = RecordingWorkerPort(store=store, handler=_http_success_handler)
        result, port = _dispatch(
            store, worker=worker, selected_step_id=step.step_id
        )
        self.assertEqual(result.state, "APPROVED")
        self.assertEqual(result.compiler_id, COMPILER_PROTOCOL_STEP)
        self.assertEqual(result.reason_code, "DISPATCHED")
        self.assertEqual(len(port.calls), 1)
        self.assertTrue(result.worker_invoked)
        self.assertTrue(result.coverage_recorded)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")
        second, _ = _dispatch(store, worker=port, selected_step_id=step.step_id)
        self.assertEqual(second.outcome, "ALREADY_DISPATCHED")
        self.assertEqual(second.reason_code, "UNIT_ALREADY_ATTEMPTED")
        self.assertEqual(len(port.calls), 1)

    def test_mutation_matrix_cell_executes_and_does_not_count_compile_as_coverage(self) -> None:
        store = self._store()
        cell = _sqli_cell()
        store.hunt_v3_queue["queue-1"] = _queue(
            family_id="hf-sqli",
            capability="mutation.matrix",
            action="plan",
            arguments={
                "family_name": "SQL_INJECTION",
                "cell_count": 1,
                "authorized_origin": "http://127.0.0.1:8094",
                "path": "/ok",
                "cells": [
                    {
                        "cell_id": cell.cell_id,
                        "dimension_values": dict(cell.dimension_values),
                        "control": cell.control,
                    }
                ],
                "worker_dispatch": "forbidden_until_operator_approval",
            },
        )
        worker = RecordingWorkerPort(store=store, handler=_http_success_handler)
        result, port = _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        self.assertEqual(result.state, "APPROVED")
        self.assertEqual(result.compiler_id, COMPILER_MUTATION_MATRIX_CELL)
        self.assertEqual(result.reason_code, "DISPATCHED")
        self.assertEqual(len(port.calls), 1)
        self.assertTrue(result.coverage_recorded)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")
        second, _ = _dispatch(store, worker=port, selected_cell_id=cell.cell_id)
        self.assertEqual(second.outcome, "ALREADY_DISPATCHED")
        self.assertEqual(len(port.calls), 1)

    def test_core_deny_does_not_invoke_worker_for_mutation_cell(self) -> None:
        store = self._store()
        cell = _sqli_cell()
        store.hunt_v3_queue["queue-1"] = _queue(
            family_id="hf-sqli",
            capability="mutation.matrix",
            action="plan",
            arguments={
                "family_name": "SQL_INJECTION",
                "authorized_origin": "http://127.0.0.1:8094",
                "path": "/ok",
                "cells": [
                    {
                        "cell_id": cell.cell_id,
                        "dimension_values": dict(cell.dimension_values),
                        "control": cell.control,
                    }
                ],
            },
        )
        result, port = _dispatch(store, compiled_scope=None, selected_cell_id=cell.cell_id)
        self.assertEqual(result.outcome, "CORE_DENIED")
        self.assertEqual(result.state, "BLOCKED")
        self.assertEqual(len(port.calls), 0)
        self.assertFalse(result.coverage_recorded)

    def test_crash_after_dispatch_does_not_retry_same_cell(self) -> None:
        store = self._store()
        cell = _sqli_cell()
        store.hunt_v3_queue["queue-1"] = _queue(
            family_id="hf-sqli",
            capability="mutation.matrix",
            action="plan",
            arguments={
                "family_name": "SQL_INJECTION",
                "authorized_origin": "http://127.0.0.1:8094",
                "path": "/ok",
                "cells": [
                    {
                        "cell_id": cell.cell_id,
                        "dimension_values": dict(cell.dimension_values),
                        "control": cell.control,
                    }
                ],
            },
        )

        def boom(request):
            raise RuntimeError("crash after dispatch")

        worker = RecordingWorkerPort(store=store, handler=boom)
        with self.assertRaises(RuntimeError):
            _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        self.assertEqual(len(worker.calls), 1)
        second, _ = _dispatch(store, worker=worker, selected_cell_id=cell.cell_id)
        self.assertEqual(second.outcome, "ALREADY_DISPATCHED")
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")

    def test_pending_item_is_rejected(self) -> None:
        store = self._store()
        store.hunt_v3_queue["queue-1"] = replace(_queue(), state="PENDING")
        with self.assertRaises(HuntV3DispatchError):
            _dispatch(store)

    def test_approval_use_case_still_does_not_dispatch(self) -> None:
        store = self._store()
        store.hunt_v3_queue["queue-1"] = replace(_queue(), state="PENDING")
        store.approvals["approval-1"] = ApprovalRecord(
            approval_id="approval-1",
            subject_reference=approval_subject_for_queue("queue-1"),
            decision=ApprovalDecision.APPROVE.value,
            decided_by="operator-1",
            actor_type=ActorType.HUMAN_OPERATOR.value,
            recorded=True,
            created_at=CREATED_AT,
            research_run_id="run-1",
            proposal_id="proposal-placeholder",
            human_review_id="human-review-placeholder",
        )
        approved = ApproveHuntV3Queue(FakeUnitOfWorkFactory(store), clock=FixedClock()).execute(
            ApproveHuntV3QueueCommand(research_run_id="run-1", queue_id="queue-1")
        )
        self.assertTrue(approved.approved)
        self.assertEqual(store.hunt_v3_queue["queue-1"].state, "APPROVED")
        self.assertEqual(len(store.execution_attempts), 0)


if __name__ == "__main__":
    unittest.main()
