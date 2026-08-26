from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import unittest

import pathsetup  # noqa: F401

from zest.application.draft_exploratory_hypothesis import (
    DraftExploratoryHypothesis,
    DraftExploratoryHypothesisCommand,
    ExploratorySignalInput,
)
from zest.application.errors import ApplicationError
from zest.application.execute_exploratory_hypothesis import (
    ExecuteExploratoryHypothesis,
    ExecuteExploratoryHypothesisCommand,
)
from zest.application.promote_exploratory_family import (
    PromoteExploratoryFamily,
    PromoteExploratoryFamilyCommand,
)
from zest.core.enums import ActorType, ApprovalDecision, ExecutionDecisionKind, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.data.records import HunterFamilyRecord, IssuedBudgetRecord
from zest.platform.worker import InvocationStatus, WorkerInvocationOutcome
from zest.research.assessment import AssessmentOutcome
from zest.research.exploratory import ExploratorySignalKind
from zest.research.orchestration import OrchestrationBounds, OrchestrationState, StopReason
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.recording_worker import (
    RecordingWorkerPort,
    completed_diagnostic_outcome,
    invocation_outcome,
)
from support.spine import CREATED_AT, seed_authorization_run


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
EXECUTE_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "zest"
    / "application"
    / "execute_exploratory_hypothesis.py"
).read_text(encoding="utf-8")
PROMOTE_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "zest"
    / "application"
    / "promote_exploratory_family.py"
).read_text(encoding="utf-8")


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


def _bounds(**overrides) -> OrchestrationBounds:
    values = dict(
        max_cycles=2,
        max_experiments=2,
        max_model_calls=0,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
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
        target_node_kinds=("ACTION",),
        preconditions={},
        claim_template="existing template",
        evidence_requirements={"v1": "required"},
        validation_tier="V3",
        enabled=True,
        version=1,
        created_at=CREATED_AT,
    )


def _draft_command(**overrides) -> DraftExploratoryHypothesisCommand:
    values = dict(
        research_run_id="run-1",
        proposed_family_name="Unmapped Response Shape Coupling",
        proposed_family_rationale="Lab-only response-shape coupling is not a registered family.",
        signals=(
            ExploratorySignalInput(
                signal_id="sig-1",
                kind=ExploratorySignalKind.LAB_ZERO_DAY_STYLE_ANOMALY.value,
                description="A lab-only zero-day-style behavior changed the response shape.",
                source_refs=("change-1",),
                target_node_kind="ACTION",
                attributes={"lab_fixture": "zero_day_style"},
            ),
        ),
        correlation_id="corr-slice7",
    )
    values.update(overrides)
    return DraftExploratoryHypothesisCommand(**values)


def _draft(store: _Store):
    return DraftExploratoryHypothesis(
        FakeUnitOfWorkFactory(store), clock=FixedClock()
    ).execute(_draft_command())


def _execute_command(hypothesis_id: str, **overrides) -> ExecuteExploratoryHypothesisCommand:
    values = dict(
        research_run_id="run-1",
        hypothesis_id=hypothesis_id,
        budget_id="budget-1",
        target_reference="target-1",
        scope=_allow_scope(),
        bounds=_bounds(),
        correlation_id="corr-slice7-exec",
    )
    values.update(overrides)
    return ExecuteExploratoryHypothesisCommand(**values)


def _execute(store: _Store, command: ExecuteExploratoryHypothesisCommand, *, worker=None):
    port = worker or RecordingWorkerPort(store=store)
    service = ExecuteExploratoryHypothesis(
        FakeUnitOfWorkFactory(store), port, clock=FixedClock()
    )
    return service.execute(command), port


class ExecuteExploratoryHypothesisTests(unittest.TestCase):
    def test_registry_external_hypothesis_enters_compiler_core_worker_path(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)

        result, port = _execute(store, _execute_command(drafted.hypothesis_id))

        self.assertEqual(result.compiler_outcome, "COMPILED")
        self.assertIsNotNone(result.experiment_id)
        self.assertIsNotNone(result.assessment_id)
        self.assertIsNotNone(result.observation_id)
        self.assertEqual(result.core_decision, ExecutionDecisionKind.ALLOW.value)
        self.assertFalse(result.may_write_hunter_registry)
        self.assertFalse(result.registry_written)
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(set(store.hunter_families), families_before)
        self.assertEqual(store.findings, {})
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(store.finding_proposals, {})
        assessment = store.hypothesis_assessments[result.assessment_id]
        self.assertEqual(
            assessment.assessment_outcome,
            AssessmentOutcome.CONSISTENT_WITH_PREDICTION.value,
        )
        self.assertEqual(len(store.research_orchestrations), 1)

    def test_second_instance_does_not_promote_exploratory_state(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)
        _execute(store, _execute_command(drafted.hypothesis_id))

        reloaded, port = _execute(store, _execute_command(drafted.hypothesis_id))

        self.assertEqual(reloaded.compiler_reason, "ALREADY_ASSESSED")
        self.assertEqual(len(port.calls), 0)
        self.assertEqual(set(store.hunter_families), families_before)
        names = {record.name for record in store.hunter_families.values()}
        self.assertNotIn("Unmapped Response Shape Coupling", names)

    def test_core_deny_does_not_invoke_worker_or_write_registry(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)

        result, port = _execute(
            store, _execute_command(drafted.hypothesis_id, scope=_deny_scope())
        )

        self.assertEqual(len(port.calls), 0)
        self.assertEqual(result.core_decision, ExecutionDecisionKind.DENY.value)
        self.assertEqual(result.stop_reason, StopReason.CORE_BLOCKED.value)
        self.assertEqual(result.orchestration_state, OrchestrationState.BLOCKED.value)
        self.assertEqual(set(store.hunter_families), families_before)
        self.assertEqual(store.findings, {})

    def test_negative_fixture_does_not_create_finding_or_registry_row(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)

        def _mismatch(request):
            outcome = completed_diagnostic_outcome(request)
            payload = dict(outcome.worker_result)
            raw = dict(payload["raw_result"])
            raw["echoed"] = "not-the-submitted-value"
            payload["raw_result"] = raw
            return WorkerInvocationOutcome(
                invocation_status=outcome.invocation_status,
                started_at=outcome.started_at,
                completed_at=outcome.completed_at,
                worker_result=payload,
                exit_code=outcome.exit_code,
            )

        result, port = _execute(
            store,
            _execute_command(drafted.hypothesis_id),
            worker=RecordingWorkerPort(store=store, handler=_mismatch),
        )

        self.assertEqual(len(port.calls), 1)
        assessment = store.hypothesis_assessments[result.assessment_id]
        self.assertEqual(
            assessment.assessment_outcome,
            AssessmentOutcome.CONTRADICTS_PREDICTION.value,
        )
        self.assertEqual(store.findings, {})
        self.assertEqual(store.evidence, {})
        self.assertNotIn(
            "Unmapped Response Shape Coupling",
            {record.name for record in store.hunter_families.values()},
        )

    def test_operational_failure_is_not_falsified_hypothesis(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result, port = _execute(
            store,
            _execute_command(drafted.hypothesis_id),
            worker=RecordingWorkerPort(
                store=store,
                outcome=invocation_outcome(InvocationStatus.PROCESS_FAILED, reason="crash"),
            ),
        )
        self.assertEqual(len(port.calls), 1)
        self.assertEqual(store.hypothesis_assessments, {})
        self.assertEqual(store.findings, {})
        self.assertFalse(
            any(
                record.assessment_outcome == AssessmentOutcome.CONTRADICTS_PREDICTION.value
                for record in store.hypothesis_assessments.values()
            )
        )
        self.assertEqual(result.assessment_id, None)

    def test_tampered_may_write_flag_hard_fails(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        audit = store.audit_events[drafted.audit_event_id]
        store.audit_events[drafted.audit_event_id] = replace(
            audit, payload={**dict(audit.payload), "may_write_hunter_registry": True}
        )
        families_before = set(store.hunter_families)
        with self.assertRaisesRegex(ApplicationError, "cannot authorize hunter registry write"):
            _execute(store, _execute_command(drafted.hypothesis_id))
        self.assertEqual(set(store.hunter_families), families_before)

    def test_non_exploratory_hypothesis_is_rejected(self) -> None:
        store = _Store()
        _seed(store)
        from zest.data.records import HypothesisRecord

        store.hypotheses["hyp-known"] = HypothesisRecord(
            hypothesis_id="hyp-known",
            research_run_id="run-1",
            claim="known family",
            created_at=CREATED_AT,
            origin_reference=None,
        )
        with self.assertRaisesRegex(ApplicationError, "not an exploratory draft"):
            _execute(store, _execute_command("hyp-known"))

    def test_does_not_construct_a_second_worker_dispatcher(self) -> None:
        self.assertIn("run_managed_cycle", EXECUTE_SOURCE)
        self.assertNotIn("PreparePlannedExperiment(", EXECUTE_SOURCE)
        self.assertNotIn("ExecutePlannedExperiment(", EXECUTE_SOURCE)
        self.assertNotIn("hunter_families.insert", EXECUTE_SOURCE)
        self.assertNotIn("EvaluateExperimentFeedback(", EXECUTE_SOURCE)


class PromoteExploratoryFamilyTests(unittest.TestCase):
    def test_human_approval_is_required_to_write_registry(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        families_before = set(store.hunter_families)
        result = PromoteExploratoryFamily(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="control-plane",
                actor_type=ActorType.CONTROL_PLANE,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason_code, "APPROVAL_INVALID_ACTOR")
        self.assertEqual(set(store.hunter_families), families_before)

    def test_approve_writes_permanent_family_once(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        service = PromoteExploratoryFamily(FakeUnitOfWorkFactory(store), clock=FixedClock())
        first = service.execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertTrue(first.promoted)
        self.assertEqual(first.reason_code, "ALLOWED")
        self.assertIsNotNone(first.family_id)
        names = {record.name for record in store.hunter_families.values()}
        self.assertIn("Unmapped Response Shape Coupling", names)
        second = service.execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertEqual(second.reason_code, "ALREADY_PROMOTED")
        self.assertEqual(second.family_id, first.family_id)
        promoted_rows = [
            record
            for record in store.hunter_families.values()
            if record.name == "Unmapped Response Shape Coupling"
        ]
        self.assertEqual(len(promoted_rows), 1)
        self.assertEqual(store.findings, {})
        self.assertEqual(store.finding_proposals, {})
        self.assertEqual(store.human_reviews, {})

    def test_reject_does_not_write_registry(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result = PromoteExploratoryFamily(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=ApprovalDecision.REJECT,
            )
        )
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason_code, "APPROVAL_REJECTED")
        self.assertNotIn(
            "Unmapped Response Shape Coupling",
            {record.name for record in store.hunter_families.values()},
        )

    def test_control_plane_actor_cannot_promote(self) -> None:
        store = _Store()
        _seed(store)
        drafted = _draft(store)
        result = PromoteExploratoryFamily(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            PromoteExploratoryFamilyCommand(
                research_run_id="run-1",
                hypothesis_id=drafted.hypothesis_id,
                reviewer_id="control-plane",
                actor_type=ActorType.CONTROL_PLANE,
                decision=ApprovalDecision.APPROVE,
            )
        )
        self.assertFalse(result.promoted)
        self.assertEqual(result.reason_code, "APPROVAL_INVALID_ACTOR")

    def test_promotion_does_not_dispatch_or_share_finding_review(self) -> None:
        self.assertIn("hunter_families.insert", PROMOTE_SOURCE)
        self.assertNotIn("run_managed_cycle", PROMOTE_SOURCE)
        self.assertNotIn("WorkerPort", PROMOTE_SOURCE)
        self.assertNotIn("finding_proposals", PROMOTE_SOURCE)
        self.assertNotIn("StartHumanReview", PROMOTE_SOURCE)
        self.assertNotIn("uow.approvals", PROMOTE_SOURCE)


if __name__ == "__main__":
    unittest.main()
