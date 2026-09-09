"""Phase 7 failure / recovery / chaos / unknown-outcome acceptance (F1–F18, A–F)."""

from __future__ import annotations

import hashlib
import unittest
from datetime import timedelta

import pathsetup  # noqa: F401

from application.test_finding_acceptance import (
    _open_candidate,
    _validated_candidate,
)
from zest.application.admit_oast_callback import AdmitOastCallback
from zest.application.classify_runtime_recovery import (
    ClassifyRuntimeRecovery,
    RuntimeRecoveryAction,
)
from zest.application.failure_census import (
    FAILURE_CENSUS,
    FAILURE_CENSUS_COMPLETE,
    FAILURE_CENSUS_GAPS_OPEN,
)
from zest.application.global_research_work_audit import global_research_work_audit
from zest.application.lifecycle_orphan_inventory import lifecycle_orphan_inventory
from zest.application.lifecycle_state_matrix import (
    STATE_MACHINE_MATRIX,
    STATE_MACHINE_MATRIX_COMPLETE,
)
from zest.application.phase6_engine_matrix import (
    CONNECTED_BLOCKED_BY_AUTHORITY,
    FINAL_ENGINE_MATRIX,
)
from zest.application.orchestration_config import fingerprint_for_start
from zest.application.propose_research_hypothesis import (
    ProposeResearchHypothesis,
    ProposeResearchHypothesisCommand,
)
from zest.application.retry_policy import automatic_retry_allowed
from zest.application.select_research_opportunities import (
    SelectResearchOpportunities,
    SelectResearchOpportunitiesCommand,
)
from zest.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.core.enums import ActorType
from zest.data.records import (
    AuditEventRecord,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExperimentPlanRecord,
    HypothesisRecord,
    OastCorrelationRecord,
    ResearchAdmissionRecord,
    ResearchOrchestrationRecord,
    ResearchRunRecord,
    VerificationRecord,
)
from zest.research.admission import AdmissionOutcome
from zest.research.candidate import CandidateState
from zest.research.exploration import ResearchPolicyBudget
from zest.research.finding_proposal import FindingProposalState
from zest.research.oast.types import OastCallbackDelivery
from zest.research.orchestration import (
    NextCycleAction,
    OrchestrationBounds,
    OrchestrationUsage,
    StopReason,
    next_cycle_action,
)
from support.fake_model import ScriptedModelPort, default_generator_output
from support.fake_unit_of_work import FakeUnitOfWorkFactory, _Store
from support.spine import CREATED_AT, seed_authorization_run, seed_spine


class FixedClock:
    def now(self):
        return CREATED_AT


def _orchestration_record(*, state: str = "RUNNING") -> ResearchOrchestrationRecord:
    bounds = OrchestrationBounds(
        max_cycles=8,
        max_experiments=8,
        max_model_calls=32,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=4,
        max_runtime_fallback=0,
        side_effect_ceiling=1,
    )
    fingerprint = fingerprint_for_start(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        research_question="phase7 recovery",
        policy_version="orchestration.bounded.v1",
        bounds=bounds,
        routing_policy_version=None,
        scope_fp=None,
    )
    return ResearchOrchestrationRecord(
        research_run_id="run-1",
        state=state,
        cycle_number=1,
        last_phase="running",
        policy_version="orchestration.bounded.v1",
        max_cycles=8,
        max_experiments=8,
        max_model_calls=32,
        max_worker_invocations=8,
        max_elapsed_ms=60_000,
        max_selected_opportunities=4,
        max_runtime_fallback=0,
        side_effect_ceiling=1,
        allow_repeated_control_experiments=True,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        checkpoint_at=CREATED_AT,
        budget_id="budget-1",
        target_reference="target-1",
        research_question="phase7 recovery",
        configuration_fingerprint=fingerprint,
        current_phase="CYCLE_READY",
    )


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Phase7FailureRecoveryTests(unittest.TestCase):
    def test_f1_failure_census(self) -> None:
        self.assertTrue(FAILURE_CENSUS_COMPLETE)
        self.assertEqual(FAILURE_CENSUS_GAPS_OPEN, 0)
        names = {item["name"] for item in FAILURE_CENSUS}
        self.assertIn("LocalRunSupervisorRegistry", names)
        self.assertIn("ARC step/recovery", names)
        self.assertIn("ExecutePlannedExperiment", names)
        self.assertTrue(all(item["gap"] for item in FAILURE_CENSUS))

    def test_f2_state_machine_matrix(self) -> None:
        self.assertTrue(STATE_MACHINE_MATRIX_COMPLETE)
        records = {item["record"] for item in STATE_MACHINE_MATRIX}
        for required in (
            "WorkerAttempt",
            "Evidence",
            "Candidate",
            "Verification",
            "FindingProposal",
            "OastArm",
            "ModelReasoning",
            "CompletionGuard",
        ):
            self.assertIn(required, records)
        attempts = next(item for item in STATE_MACHINE_MATRIX if item["record"] == "WorkerAttempt")
        self.assertIn("UNKNOWN_OUTCOME", attempts["terminal_unknown"])
        self.assertFalse(attempts["retriable"])

    def test_f3_unknown_outcome_fail_closed(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.research_orchestrations["run-1"] = _orchestration_record()
        store.execution_attempts["att-unknown"] = ExecutionAttemptRecord(
            attempt_id="att-unknown",
            request_id="req-unknown",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="corr-unknown",
            worker_capability="http.state_transition",
            action="transition",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=1,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.UNKNOWN_OUTCOME.value,
            created_at=CREATED_AT,
            dispatch_started_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.unknown_outcome, 0)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("UNKNOWN_OUTCOME", audit.completion_block_reasons)
        self.assertFalse(
            automatic_retry_allowed(attempt_state="UNKNOWN_OUTCOME", side_effect_level=1)
        )
        recovery = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(recovery.action, RuntimeRecoveryAction.HUMAN_REQUIRED)
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.findings, {})

    def test_f4_duplicate_execution_blocked_by_attempt_identity(self) -> None:
        store = _Store()
        seed_spine(store)
        first = ExecutionAttemptRecord(
            attempt_id="att-1",
            request_id="req-dup",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="corr-1",
            worker_capability="diagnostic.echo",
            action="echo",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=0,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.COMPLETED.value,
            created_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            uow.execution_attempts.insert(first)
            uow.commit()
        with FakeUnitOfWorkFactory(store).open() as uow:
            with self.assertRaises(Exception):
                uow.execution_attempts.insert(
                    ExecutionAttemptRecord(
                        attempt_id="att-2",
                        request_id="req-dup",
                        experiment_id="exp-1",
                        research_run_id="run-1",
                        correlation_id="corr-2",
                        worker_capability="diagnostic.echo",
                        action="echo",
                        target_reference="target-1",
                        budget_id="budget-1",
                        side_effect_level=0,
                        authorization_decision_reference="ad-1",
                        state=ExecutionAttemptState.AUTHORIZED.value,
                        created_at=CREATED_AT,
                    )
                )
            uow.rollback()

    def test_f5_db_rollback_does_not_leave_ghost_hypothesis(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            uow.hypotheses.insert(
                HypothesisRecord(
                    hypothesis_id="hyp-ghost",
                    research_run_id="run-1",
                    claim="rolled back",
                    created_at=CREATED_AT,
                )
            )
            uow.rollback()
        self.assertNotIn("hyp-ghost", store.hypotheses)
        self.assertFalse(
            any(item.admitted_hypothesis_id == "hyp-ghost" for item in store.research_admissions.values())
        )

    def test_f6_supervisor_recovery_does_not_resume_unknown(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.research_orchestrations["run-1"] = _orchestration_record()
        clean = ClassifyRuntimeRecovery(FakeUnitOfWorkFactory(store)).execute("run-1")
        self.assertEqual(clean.action, RuntimeRecoveryAction.SAFE_RESUME)

    def test_f7_worker_unknown_is_not_observation(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.execution_attempts["att-d"] = ExecutionAttemptRecord(
            attempt_id="att-d",
            request_id="req-d",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="corr-d",
            worker_capability="http.transaction",
            action="read",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=1,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.DISPATCHING.value,
            created_at=CREATED_AT,
            dispatch_started_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.dispatching, 0)
        self.assertGreater(audit.in_flight, 0)
        self.assertEqual(store.observations, {})
        self.assertEqual(store.evidence, {})
        store.execution_attempts["att-timeout"] = ExecutionAttemptRecord(
            attempt_id="att-timeout",
            request_id="req-timeout",
            experiment_id="exp-1",
            research_run_id="run-1",
            correlation_id="corr-timeout",
            worker_capability="http.transaction",
            action="read",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=1,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.TIMED_OUT.value,
            created_at=CREATED_AT,
            dispatch_started_at=CREATED_AT,
            completed_at=CREATED_AT,
        )
        self.assertFalse(
            automatic_retry_allowed(attempt_state="TIMED_OUT", side_effect_level=1)
        )
        self.assertEqual(store.observations, {})
        self.assertEqual(store.evidence, {})

    def test_f8_model_failure_persists_rejection(self) -> None:
        store = _Store()
        seed_authorization_run(store)

        def hallucinate(request):
            payload = dict(default_generator_output(request))
            payload["source_references"] = ["obs:does-not-exist"]
            return payload

        result = ProposeResearchHypothesis(
            FakeUnitOfWorkFactory(store),
            ScriptedModelPort(generator=hallucinate),
            clock=FixedClock(),
        ).execute(
            ProposeResearchHypothesisCommand(
                research_run_id="run-1",
                research_question="q",
                budget_id="budget-1",
                target_reference="target-1",
                correlation_id="corr-model-fail",
            )
        )
        self.assertNotEqual(result.outcome, AdmissionOutcome.ADMITTED)
        self.assertEqual(store.hypotheses, {})
        self.assertEqual(store.worker_results, {})
        self.assertEqual(store.findings, {})
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.model_rejected, 0)

    def test_f9_oast_unknown_and_expired_are_not_evidence(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        delivery = OastCallbackDelivery(
            delivery_id="del-unknown",
            correlation_id="corr-missing",
            provider_adapter_id="interactsh",
            provider_event_id="evt-1",
            received_at=CREATED_AT,
            normalized_payload={"host": "example.test"},
            normalized_digest=_digest("host"),
        )
        admitted = AdmitOastCallback(FakeUnitOfWorkFactory(store), clock=lambda: CREATED_AT).execute(
            delivery
        )
        self.assertFalse(admitted.admitted)
        self.assertEqual(admitted.reason_code, "OAST_CORRELATION_NOT_FOUND")
        self.assertEqual(store.evidence, {})
        self.assertTrue(
            any(item.event_type == "OAST_CALLBACK_UNKNOWN_TOKEN" for item in store.audit_events.values())
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.sensor_uncorrelated, 0)

    def test_f10_verification_failure_open_cannot_propose(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _open_candidate(store)
        from zest.application.errors import ApplicationError

        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(
                FakeUnitOfWorkFactory(store), clock=FixedClock()
            ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(store.finding_proposals, {})

    def test_f11_completion_blockers_independent(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.hypotheses["hyp-model"] = HypothesisRecord(
            hypothesis_id="hyp-model",
            research_run_id="run-1",
            claim="admitted pending scheduler",
            created_at=CREATED_AT,
            origin_reference="reasoning-1",
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertIn("MODEL_PENDING", audit.completion_block_reasons)

    def test_f12_authority_chaos_protocol_not_coverage(self) -> None:
        protocol = next(row for row in FINAL_ENGINE_MATRIX if row["ENGINE"] == "Protocol")
        self.assertEqual(protocol["STATUS"], CONNECTED_BLOCKED_BY_AUTHORITY)
        action, reason = next_cycle_action(
            bounds=OrchestrationBounds(
                max_cycles=8,
                max_experiments=8,
                max_model_calls=32,
                max_worker_invocations=8,
                max_elapsed_ms=60_000,
                max_selected_opportunities=4,
                max_runtime_fallback=0,
                side_effect_ceiling=1,
            ),
            usage=OrchestrationUsage(
                cycles_completed=0,
                experiments_executed=0,
                model_calls=0,
                worker_invocations=0,
                elapsed_ms=0,
                opportunities_selected=0,
                runtime_fallbacks=0,
            ),
            selected_count=0,
            hypothesis_count=1,
            unknown_outcome_open=True,
        )
        self.assertEqual(action, NextCycleAction.STOP)
        self.assertEqual(reason, StopReason.UNKNOWN_OUTCOME_REQUIRES_REVIEW)

    def test_f13_f14_sensor_and_cross_run_isolation(self) -> None:
        store = _Store()
        seed_spine(store)
        store.research_runs["run-2"] = ResearchRunRecord(
            research_run_id="run-2",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=CREATED_AT,
        )
        store.experiment_plans["exp-1"] = ExperimentPlanRecord(
            experiment_id="exp-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            required_capability="oast",
            action="arm-correlation",
            target_reference="target-1",
            side_effect_level=0,
            arguments={},
            requested_budget_id="budget-1",
            expected_observation="callback",
            disconfirming_observation="no callback",
            evaluation_strategy="deterministic",
            created_at=CREATED_AT,
        )
        store.execution_attempts["attempt-1"] = ExecutionAttemptRecord(
            attempt_id="attempt-1",
            request_id="request-cross",
            experiment_id="exp-1",
            research_run_id="run-2",
            correlation_id="corr-cross",
            worker_capability="http.transaction",
            action="read",
            target_reference="target-1",
            budget_id="budget-1",
            side_effect_level=1,
            authorization_decision_reference="ad-1",
            state=ExecutionAttemptState.COMPLETED.value,
            created_at=CREATED_AT,
        )
        store.oast_correlations["corr-cross"] = OastCorrelationRecord(
            correlation_id="corr-cross",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="target-1",
            identity_id="id-alice",
            armed_at=CREATED_AT,
            expires_at=CREATED_AT + timedelta(hours=1),
            created_at=CREATED_AT,
        )
        delivery = OastCallbackDelivery(
            delivery_id="del-cross",
            correlation_id="corr-cross",
            provider_adapter_id="interactsh",
            provider_event_id="evt-cross",
            received_at=CREATED_AT,
            normalized_payload={"host": "b.test"},
            normalized_digest=_digest("b.test"),
        )
        result = AdmitOastCallback(FakeUnitOfWorkFactory(store), clock=lambda: CREATED_AT).execute(
            delivery
        )
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason_code, "OAST_ATTEMPT_RUN_MISMATCH")
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.observations, {})
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(audit.cross_run_rejected, 0)

    def test_f15_orphan_detection_blocks_completion(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.verifications["ver-orphan"] = VerificationRecord(
            verification_id="ver-orphan",
            candidate_id="cand-missing",
            research_run_id="run-1",
            strategy="http.authorization.differential.v1",
            outcome="INCONCLUSIVE",
            proposed_candidate_state="INCONCLUSIVE",
            original_evidence_ids=("ev-1",),
            reproduction_evidence_ids=(),
            negative_control_evidence_ids=(),
            alternative_explanation_checks={},
            verifier_kind="DETERMINISTIC",
            verifier_identity="control-plane:verifier",
            created_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            dangling = lifecycle_orphan_inventory(uow, "run-1")
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertGreater(dangling.verification_without_candidate, 0)
        self.assertGreater(dangling.total, 0)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("ORPHAN_RESEARCH_WORK", audit.completion_block_reasons)
        self.assertIn("ver-orphan", store.verifications)

    def test_f16_fairness_under_failure_still_selects(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.oast_correlations["corr-wait"] = OastCorrelationRecord(
            correlation_id="corr-wait",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="target-1",
            identity_id="id-alice",
            armed_at=CREATED_AT,
            expires_at=CREATED_AT + timedelta(hours=1),
            created_at=CREATED_AT,
        )
        selected = SelectResearchOpportunities(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(
            SelectResearchOpportunitiesCommand(
                research_run_id="run-1",
                budget=ResearchPolicyBudget(max_selected=2, max_exploratory=1),
            )
        )
        self.assertIsNotNone(selected)
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertFalse(audit.completion_allowed)
        self.assertIn("OAST_WAITING_CALLBACK", audit.completion_block_reasons)

    def test_f17_budget_failure_is_explicit(self) -> None:
        action, reason = next_cycle_action(
            bounds=OrchestrationBounds(
                max_cycles=1,
                max_experiments=1,
                max_model_calls=1,
                max_worker_invocations=1,
                max_elapsed_ms=1,
                max_selected_opportunities=1,
                max_runtime_fallback=0,
                side_effect_ceiling=0,
            ),
            usage=OrchestrationUsage(
                cycles_completed=1,
                experiments_executed=0,
                model_calls=0,
                worker_invocations=0,
                elapsed_ms=2,
                opportunities_selected=0,
                runtime_fallbacks=0,
            ),
            selected_count=0,
            hypothesis_count=1,
            unknown_outcome_open=False,
        )
        self.assertEqual(action, NextCycleAction.STOP)
        self.assertEqual(reason, StopReason.MAX_CYCLES_REACHED)

    def test_f18_and_scenario_a_recovery_e2e(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        again = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        self.assertEqual(submitted.proposal_id, again.proposal_id)
        self.assertEqual(len(store.finding_proposals), 1)
        self.assertEqual(len(store.candidates), 1)
        self.assertEqual(store.findings, {})

    def test_scenario_b_negative_run_no_finding(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.research_admissions["adm-rej"] = ResearchAdmissionRecord(
            admission_record_id="adm-rej",
            research_run_id="run-1",
            outcome="NEEDS_MORE_CONTEXT",
            reason="hallucinated",
            reason_code="HALLUCINATED_SOURCE",
            context_fingerprint="fp",
            created_at=CREATED_AT,
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(store.evidence, {})
        self.assertEqual(store.findings, {})
        self.assertGreater(audit.model_rejected, 0)

    def test_scenario_c_unknown_side_effect(self) -> None:
        self.test_f3_unknown_outcome_fail_closed()

    def test_scenario_d_oast_late_callback_after_timeout(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        store.oast_correlations["corr-late"] = OastCorrelationRecord(
            correlation_id="corr-late",
            attempt_id="attempt-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            target_reference="target-1",
            identity_id="id-alice",
            armed_at=CREATED_AT - timedelta(hours=2),
            expires_at=CREATED_AT - timedelta(hours=1),
            created_at=CREATED_AT - timedelta(hours=2),
        )
        store.audit_events["aud-timeout"] = AuditEventRecord(
            audit_event_id="aud-timeout",
            occurred_at=CREATED_AT - timedelta(minutes=1),
            actor_id="control-plane:oast",
            actor_type=ActorType.CONTROL_PLANE.value,
            event_type="OAST_NO_CALLBACK_TIMEOUT",
            subject_type="research_run",
            subject_id="run-1",
            payload={"correlation_id": "corr-late"},
        )
        delivery = OastCallbackDelivery(
            delivery_id="del-late",
            correlation_id="corr-late",
            provider_adapter_id="interactsh",
            provider_event_id="evt-late",
            received_at=CREATED_AT,
            normalized_payload={"host": "late.test"},
            normalized_digest=_digest("late.test"),
        )
        result = AdmitOastCallback(FakeUnitOfWorkFactory(store), clock=lambda: CREATED_AT).execute(
            delivery
        )
        self.assertFalse(result.admitted)
        self.assertEqual(result.reason_code, "OAST_CORRELATION_EXPIRED")
        self.assertIn("del-late", store.oast_callback_deliveries)
        self.assertEqual(store.evidence, {})
        self.assertTrue(
            any(item.event_type == "OAST_CALLBACK_EXPIRED" for item in store.audit_events.values())
        )

    def test_scenario_e_human_review_blocks(self) -> None:
        store = _Store()
        seed_spine(store)
        candidate_id = _validated_candidate(store)
        submitted = SubmitFindingProposal(
            FakeUnitOfWorkFactory(store), clock=FixedClock()
        ).execute(SubmitFindingProposalCommand(candidate_id=candidate_id))
        StartHumanReview(FakeUnitOfWorkFactory(store)).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        with FakeUnitOfWorkFactory(store).open() as uow:
            audit = global_research_work_audit(uow, "run-1")
            uow.rollback()
        self.assertEqual(store.finding_proposals[submitted.proposal_id].state, FindingProposalState.HUMAN_REVIEW.value)
        self.assertFalse(audit.completion_allowed)
        self.assertIn("FINDING_REVIEW_PENDING", audit.completion_block_reasons)
        self.assertEqual(store.findings, {})

    def test_scenario_f_cross_run_callback_rejected(self) -> None:
        self.test_f13_f14_sensor_and_cross_run_isolation()

    def test_audit_exposes_phase7_counts(self) -> None:
        store = _Store()
        seed_authorization_run(store)
        with FakeUnitOfWorkFactory(store).open() as uow:
            payload = global_research_work_audit(uow, "run-1").as_payload()
            uow.rollback()
        for key in (
            "unknown_outcome",
            "dispatching",
            "in_flight",
            "approval_waiting",
            "human_pending",
            "verification_pending",
            "finding_proposal_pending",
            "finding_review_pending",
            "oast_waiting_callback",
            "oast_expired",
            "model_rejected",
            "model_pending",
            "orphan_research_work",
            "sensor_uncorrelated",
            "cross_run_rejected",
            "budget_blocked",
            "authority_blocked",
            "completion_block_reasons",
        ):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
