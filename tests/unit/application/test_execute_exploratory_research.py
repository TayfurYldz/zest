from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit
import unittest

import pathsetup  # noqa: F401

from zest.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from zest.application.execute_exploratory_research import (
    ExecuteExploratoryResearch,
    ExecuteExploratoryResearchCommand,
)
from zest.core.enums import ExecutionDecisionKind, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import ScopeRuleDefinition, compile_scope_rules
from zest.data.records import HunterFamilyRecord, IssuedBudgetRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.exploratory import ExploratorySignalKind
from zest.research.orchestration import OrchestrationBounds, OrchestrationState, StopReason
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import RecordingWorkerPort
from support.spine import CREATED_AT, seed_authorization_run


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
ORIGIN = "http://127.0.0.1:9"


class FixedClock:
    def now(self):
        return NOW


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-deny", ScopeRuleEffect.DENY, True, "scope-src"),),
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
                path_prefix="/",
                source_reference="scope-src",
            ),
        )
    )


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=2,
        max_experiments=4,
        max_model_calls=0,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=2,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )
    values.update(overrides)
    return OrchestrationBounds(**values)


def _seed(store: _Store) -> None:
    seed_authorization_run(store)
    store.issued_budgets["budget-1"] = IssuedBudgetRecord(
        budget_id="budget-1",
        research_run_id="run-1",
        max_requests=20,
        max_tool_calls=20,
        max_runtime_ms=10_000,
        max_concurrency=1,
        issued_at=CREATED_AT,
    )
    store.hunter_families["hf-existing:1"] = HunterFamilyRecord(
        family_id="hf-existing",
        name="Existing Boundary Drift",
        target_node_kinds=("OBJECT",),
        preconditions={},
        claim_template="existing template",
        evidence_requirements={"v1": "required"},
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _draft(store: _Store):
    return DraftExploratoryHypothesis(
        FakeUnitOfWorkFactory(store), clock=FixedClock()
    ).execute(
        DraftExploratoryHypothesisCommand(
            research_run_id="run-1",
            proposed_family_name="exploratory.cross_object.read.v1",
            proposed_family_rationale="Registry-external object-access anomaly.",
            signals=(
                ExploratorySignalInput(
                    signal_id="sig-1",
                    kind=ExploratorySignalKind.IDENTITY_ANOMALY.value,
                    description="Identity neighborhood around an object node drifted.",
                    source_refs=("change-1",),
                    target_node_kind="OBJECT",
                    attributes={"lab_fixture": "vulnerable"},
                ),
            ),
            correlation_id="corr-mr6",
        )
    )


def _authz_handler(mode: str):
    def handler(request):
        args = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        actor = str(args.get("actor") or "alice")
        own = str(args.get("own_object") or "alice")
        cross = str(args.get("cross_object") or "bob")
        origin = str(args.get("authorized_origin") or ORIGIN)
        if mode == "vulnerable":
            cross_status, cross_owner = 200, cross
        elif mode == "secure_only":
            cross_status, cross_owner = 403, None
        else:
            cross_status, cross_owner = 200, actor
        raw = {
            "mode": mode if mode in {"vulnerable", "secure_only", "redirect"} else "vulnerable",
            "authorized_origin": origin,
            "owner_request": {"status": 200, "object_owner": own},
            "cross_object_request": {"status": cross_status, "object_owner": cross_owner},
            "secure_control": {"status": 403},
            "unauthenticated_control": {"status": 401},
        }
        correlation = request.get("correlation") if isinstance(request.get("correlation"), dict) else {}
        return WorkerInvocationOutcome(
            invocation_status=InvocationStatus.COMPLETED,
            started_at=NOW,
            completed_at=NOW,
            worker_result={
                "contract_version": "v1",
                "correlation": dict(correlation),
                "worker_id": "local-python-http",
                "status": "SUCCEEDED",
                "started_at": "2026-08-22T00:00:00Z",
                "completed_at": "2026-08-22T00:00:01Z",
                "raw_result": raw,
            },
            exit_code=0,
        )

    return handler


def _execute(store: _Store, drafted, *, mode="vulnerable", scope=None, extra_args=None, compiled_scope="default"):
    worker = RecordingWorkerPort(store=store, handler=_authz_handler(mode))
    args = {
        "authorized_origin": ORIGIN,
        "actor": "alice",
        "own_object": "alice",
        "cross_object": "bob",
        "mode": "vulnerable" if mode == "deceptive" else mode,
    }
    if extra_args:
        args.update(extra_args)
    result = ExecuteExploratoryResearch(
        FakeUnitOfWorkFactory(store), worker, clock=FixedClock()
    ).execute(
        ExecuteExploratoryResearchCommand(
            research_run_id="run-1",
            hypothesis_id=drafted.hypothesis_id,
            budget_id="budget-1",
            target_reference="target-1",
            scope=scope or _allow_scope(),
            bounds=_bounds(),
            compile_arguments=args,
            compiled_scope=_compiled_scope(ORIGIN) if compiled_scope == "default" else compiled_scope,
            correlation_id="corr-mr6-exec",
        )
    )
    return result, worker


class ExecuteExploratoryResearchTests(unittest.TestCase):
    def test_vulnerable_fixture_reaches_evidence_candidate_verification(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)
        result, port = _execute(store, drafted, mode="vulnerable")
        self.assertEqual(result.compiler_outcome, "COMPILED")
        self.assertFalse(result.used_diagnostic_echo)
        self.assertGreaterEqual(len(port.calls), 1)
        request = port.calls[0]["request"]
        self.assertNotEqual(request.get("worker_capability"), "diagnostic.echo")
        self.assertEqual(set(store.hunter_families), families_before)
        self.assertTrue(store.research_opportunities)
        self.assertTrue(store.evidence)
        self.assertTrue(store.candidates)
        self.assertTrue(store.verifications)
        self.assertEqual(store.findings, {})
        self.assertEqual(len(store.human_reviews), 0)
        candidate = next(iter(store.candidates.values()))
        self.assertEqual(candidate.state, "VALIDATED")
        self.assertEqual(store.findings, {})
        self.assertEqual(len(store.human_reviews), 0)

    def test_secure_fixture_does_not_create_finding(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result, port = _execute(store, drafted, mode="secure_only")
        self.assertGreaterEqual(len(port.calls), 1)
        self.assertEqual(store.findings, {})
        self.assertEqual(len(store.finding_proposals), 0)
        if store.candidates:
            candidate = next(iter(store.candidates.values()))
            self.assertNotEqual(candidate.state, "VALIDATED")

    def test_deceptive_fixture_false_finding_is_zero(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        _execute(store, drafted, mode="deceptive")
        self.assertEqual(store.findings, {})
        self.assertEqual(len(store.finding_proposals), 0)

    def test_core_deny_invokes_zero_workers(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result, port = _execute(store, drafted, scope=_deny_scope(), compiled_scope=None)
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(result.core_decision, ExecutionDecisionKind.DENY.value)
        self.assertEqual(result.stop_reason, StopReason.CORE_BLOCKED.value)
        self.assertEqual(store.findings, {})
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.candidates, {})

    def test_model_payload_keys_cannot_bypass_compiler(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result, port = _execute(
            store,
            drafted,
            extra_args={"query": "1=1", "body": "attack", "headers": {"X": "y"}},
        )
        self.assertEqual(result.compiler_outcome, "COMPILED")
        self.assertGreaterEqual(len(port.calls), 1)
        args = port.calls[0]["request"]["arguments"]
        self.assertNotIn("query", args)
        self.assertNotIn("body", args)
        self.assertNotIn("headers", args)

    def test_missing_semantics_does_not_run_diagnostic_echo(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        worker = RecordingWorkerPort(store=store)
        result = ExecuteExploratoryResearch(
            FakeUnitOfWorkFactory(store), worker, clock=FixedClock()
        ).execute(
            ExecuteExploratoryResearchCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                budget_id="budget-1",
                target_reference="target-1",
                scope=_allow_scope(),
                bounds=_bounds(),
                compile_arguments={"query": "1=1"},
            )
        )
        self.assertEqual(result.compiler_outcome, "BLOCKED_MISSING_SEMANTICS")
        self.assertEqual(len(worker.calls), 0)
        self.assertEqual(store.findings, {})

    def test_no_permanent_registry_mutation_and_no_second_dispatcher(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)
        _execute(store, drafted)
        self.assertEqual(set(store.hunter_families), families_before)
        names = {record.name for record in store.hunter_families.values()}
        self.assertNotIn("exploratory.cross_object.read.v1", names)


if __name__ == "__main__":
    unittest.main()
