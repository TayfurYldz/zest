"""Canonical PromotionPipeline: Assessment → Evidence → Candidate → Verification → FindingProposal.

Composes existing use-cases. Does not rewrite Evidence/Candidate/Verification/
FindingProposal domain models. Does not create Finding. Does not auto-approve
Human Review. Does not bypass Core Approval.

ARC may trigger `on_assessment` after an assessment. ARC is not Candidate or
Finding authority. Independent verification Worker execution is `advance()` and
may continue after the ResearchRun is terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
    AdmitDiagnosticEvidenceResult,
)
from zest.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
)
from zest.application.errors import ApplicationError
from zest.data.errors import PersistenceConflictError
from zest.data.uniqueness import (
    UQ_EVIDENCE_EXPERIMENT_SUPPORTING,
    UQ_FINDING_PROPOSAL_CANDIDATE,
    UQ_VERIFICATION_CANDIDATE,
)
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from zest.application.identity import new_opaque_id
from zest.application.plan_records import experiment_plan_from_record
from zest.application.ports import Clock, SystemClock, UnitOfWorkFactory
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from zest.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.core.approval import ApprovalView
from zest.core.scope import ScopeEvaluationInput
from zest.core.scope_compiler import CompiledScope
from zest.data.records import CandidateRecord, PromotionRunRecord
from zest.platform.secrets import CompositeSecretPort
from zest.platform.worker import WorkerPort
from zest.research.assessment import AssessmentOutcome, ResearchFeedback
from zest.research.candidate import CandidateAdmissionOutcome, CandidateState
from zest.research.evidence import EvidenceAdmissionOutcome, SUPPORTED_EVIDENCE_STRATEGIES
from zest.research.finding_proposal import FindingProposalAdmissionOutcome
from zest.research.verification import VerificationOutcome


class PromotionOutcome(Enum):
    ADMITTED = "ADMITTED"
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    CANDIDATE_ADMITTED = "CANDIDATE_ADMITTED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    FINDING_PROPOSAL_RECORDED = "FINDING_PROPOSAL_RECORDED"
    SKIPPED_NOT_EVIDENCE_ELIGIBLE = "SKIPPED_NOT_EVIDENCE_ELIGIBLE"
    SKIPPED_UNSUPPORTED_STRATEGY = "SKIPPED_UNSUPPORTED_STRATEGY"
    SKIPPED_ALREADY_ATTEMPTED = "SKIPPED_ALREADY_ATTEMPTED"
    SKIPPED_MISSING_ASSESSMENT = "SKIPPED_MISSING_ASSESSMENT"
    SKIPPED_REPRODUCTION_EXPERIMENT = "SKIPPED_REPRODUCTION_EXPERIMENT"
    SKIPPED_NO_WORKER = "SKIPPED_NO_WORKER"
    PENDING_INDEPENDENT_VERIFICATION = "PENDING_INDEPENDENT_VERIFICATION"


EVIDENCE_ELIGIBLE_ASSESSMENT_OUTCOMES = frozenset({AssessmentOutcome.CONSISTENT_WITH_PREDICTION})
PENDING_VERIFICATION_STAGES = frozenset({"VERIFYING", "REPRODUCTION_EXECUTED"})
TERMINAL_PROMOTION_STAGES = frozenset(
    {
        "EVIDENCE_REJECTED",
        "CANDIDATE_REJECTED",
        "VERIFIED",
        "PROPOSAL_RECORDED",
        "STOPPED",
    }
)
EXPECTED_ADVANCE_CONFLICTS = frozenset(
    {
        UQ_VERIFICATION_CANDIDATE,
        UQ_FINDING_PROPOSAL_CANDIDATE,
        UQ_EVIDENCE_EXPERIMENT_SUPPORTING,
        "uq_experiment_id_run",
        "experiment_pkey",
        "uq_experiment_plan_id_run",
        "experiment_plan_pkey",
        "hypothesis_assessment_pkey",
    }
)
NON_SCIENTIFIC_REPRODUCTION_STATUSES = frozenset(
    {
        ResearchLoopStatus.UNKNOWN_OUTCOME,
        ResearchLoopStatus.INVOCATION_FAILED,
        ResearchLoopStatus.DISPATCH_DENIED,
        ResearchLoopStatus.HUMAN_REVIEW_REQUIRED,
        ResearchLoopStatus.AUTHORIZED_NOT_DISPATCHED,
        ResearchLoopStatus.INPUT_REJECTED,
        ResearchLoopStatus.REAUTHORIZATION_REQUIRED,
    }
)


@dataclass(frozen=True)
class PromotionResult:
    outcome: PromotionOutcome
    assessment_id: str | None
    experiment_id: str
    admission_record_id: str | None = None
    evidence_id: str | None = None
    candidate_id: str | None = None
    verification_id: str | None = None
    finding_proposal_id: str | None = None
    promotion_run_id: str | None = None
    stage: str | None = None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdvancePromotionCommand:
    research_run_id: str
    scope: ScopeEvaluationInput
    compiled_scope: CompiledScope | None = None
    approval: ApprovalView | None = None
    assessment_id: str | None = None
    promotion_run_id: str | None = None


class PromotionPipeline:
    """Durable Assessment→FindingProposal composition. Not Finding authority."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        admit: AdmitDiagnosticEvidence | None = None,
        worker: WorkerPort | None = None,
        actor_id: str = "control-plane:promotion-pipeline",
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._admit = admit or AdmitDiagnosticEvidence(uow_factory, clock=self._clock)
        self._propose = ProposeCandidateFromEvidence(uow_factory, clock=self._clock)
        self._start_verification = StartCandidateVerification(uow_factory)
        self._complete_verification = CompleteCandidateVerification(
            uow_factory, clock=self._clock
        )
        self._submit_proposal = SubmitFindingProposal(
            uow_factory, clock=self._clock, actor_id=actor_id
        )
        self._prepare = PreparePlannedExperiment(uow_factory, clock=self._clock)
        self._evaluate = EvaluateExperimentFeedback(uow_factory, clock=self._clock)
        self._worker = worker
        self._actor_id = actor_id
        self._execute = (
            ExecutePlannedExperiment(
                uow_factory,
                worker,
                clock=self._clock,
                actor_id=actor_id,
                secret_port=secret_port,
            )
            if worker is not None
            else None
        )

    def on_assessment(self, feedback: ResearchFeedback) -> PromotionResult:
        if not feedback.assessment_id:
            return PromotionResult(
                outcome=PromotionOutcome.SKIPPED_MISSING_ASSESSMENT,
                assessment_id=None,
                experiment_id=feedback.experiment_id,
            )
        if self._is_reproduction_experiment(feedback.experiment_id):
            return PromotionResult(
                outcome=PromotionOutcome.SKIPPED_REPRODUCTION_EXPERIMENT,
                assessment_id=feedback.assessment_id,
                experiment_id=feedback.experiment_id,
            )
        if feedback.assessment_outcome not in EVIDENCE_ELIGIBLE_ASSESSMENT_OUTCOMES:
            return PromotionResult(
                outcome=PromotionOutcome.SKIPPED_NOT_EVIDENCE_ELIGIBLE,
                assessment_id=feedback.assessment_id,
                experiment_id=feedback.experiment_id,
            )
        if feedback.evaluation_strategy not in SUPPORTED_EVIDENCE_STRATEGIES:
            return PromotionResult(
                outcome=PromotionOutcome.SKIPPED_UNSUPPORTED_STRATEGY,
                assessment_id=feedback.assessment_id,
                experiment_id=feedback.experiment_id,
            )
        existing = self._get_by_assessment(feedback.assessment_id)
        admitted = self._admit.execute(
            AdmitDiagnosticEvidenceCommand(
                experiment_id=feedback.experiment_id,
                assessment_id=feedback.assessment_id,
            )
        )
        run = self._ensure_promotion_run(feedback, admitted, existing)
        if admitted.outcome is not EvidenceAdmissionOutcome.ADMITTED or admitted.evidence_id is None:
            if run.stage != "EVIDENCE_REJECTED":
                run = self._save(
                    replace(
                        run,
                        stage="EVIDENCE_REJECTED",
                        evidence_id=None,
                        stop_reason="EVIDENCE_ADMISSION_REJECTED",
                        updated_at=self._clock.now(),
                    )
                )
            return _result_from_run(
                PromotionOutcome.ADMISSION_REJECTED,
                feedback,
                run,
                admitted.reason_codes,
            )
        if existing is not None and existing.stage not in {
            "EVIDENCE_ADMITTED",
            "CANDIDATE_OPEN",
        }:
            return _result_from_run(
                PromotionOutcome.SKIPPED_ALREADY_ATTEMPTED,
                feedback,
                existing,
                admitted.reason_codes,
            )
        if run.stage in {"EVIDENCE_REJECTED", "EVIDENCE_ADMITTED"} or existing is None:
            run = self._save(
                replace(
                    run,
                    stage="EVIDENCE_ADMITTED",
                    evidence_id=admitted.evidence_id,
                    updated_at=self._clock.now(),
                )
            )
        return self._promote_candidate(feedback, run, admitted)

    def advance(self, command: AdvancePromotionCommand) -> tuple[PromotionResult, ...]:
        targets = self._pending_runs(command)
        if not targets:
            return ()
        if self._worker is None or self._execute is None:
            return tuple(
                PromotionResult(
                    outcome=PromotionOutcome.SKIPPED_NO_WORKER,
                    assessment_id=item.assessment_id,
                    experiment_id=item.original_experiment_id,
                    promotion_run_id=item.promotion_run_id,
                    evidence_id=item.evidence_id,
                    candidate_id=item.candidate_id,
                    stage=item.stage,
                )
                for item in targets
            )
        return tuple(self._advance_one(item, command) for item in targets)

    def _promote_candidate(
        self,
        feedback: ResearchFeedback,
        run: PromotionRunRecord,
        admitted: AdmitDiagnosticEvidenceResult,
    ) -> PromotionResult:
        assert admitted.evidence_id is not None
        existing_candidate = self._candidate_for_evidence(admitted.evidence_id)
        if existing_candidate is None:
            proposed = self._propose.execute(
                ProposeCandidateFromEvidenceCommand(evidence_id=admitted.evidence_id)
            )
            if (
                proposed.outcome is not CandidateAdmissionOutcome.ADMITTED
                or proposed.candidate_id is None
            ):
                run = self._save(
                    replace(
                        run,
                        stage="CANDIDATE_REJECTED",
                        stop_reason="CANDIDATE_NOT_ADMITTED",
                        updated_at=self._clock.now(),
                    )
                )
                return _result_from_run(
                    PromotionOutcome.CANDIDATE_REJECTED,
                    feedback,
                    run,
                    proposed.reason_codes,
                )
            candidate_id = proposed.candidate_id
        else:
            candidate_id = existing_candidate.candidate_id
        run = self._save(
            replace(
                run,
                stage="CANDIDATE_OPEN",
                candidate_id=candidate_id,
                updated_at=self._clock.now(),
            )
        )
        candidate = self._get_candidate(candidate_id)
        if candidate is not None and candidate.state == CandidateState.OPEN.value:
            started = self._start_verification.execute(
                StartCandidateVerificationCommand(candidate_id=candidate_id)
            )
            if started.state is not CandidateState.VERIFYING:
                run = self._save(
                    replace(
                        run,
                        stage="STOPPED",
                        stop_reason="VERIFICATION_START_REJECTED",
                        updated_at=self._clock.now(),
                    )
                )
                return _result_from_run(
                    PromotionOutcome.CANDIDATE_ADMITTED,
                    feedback,
                    run,
                    ("VERIFICATION_START_REJECTED",),
                )
        run = self._save(
            replace(
                run,
                stage="VERIFYING",
                candidate_id=candidate_id,
                updated_at=self._clock.now(),
            )
        )
        return _result_from_run(
            PromotionOutcome.VERIFICATION_STARTED,
            feedback,
            run,
            admitted.reason_codes,
        )

    def _advance_one(
        self, run: PromotionRunRecord, command: AdvancePromotionCommand
    ) -> PromotionResult:
        try:
            return self._advance_one_body(run, command)
        except PersistenceConflictError as exc:
            if (
                exc.constraint_name is not None
                and exc.constraint_name not in EXPECTED_ADVANCE_CONFLICTS
            ):
                raise
            reloaded = self._get(run.promotion_run_id)
            if reloaded is None:
                raise
            if run.candidate_id is not None:
                existing = self._verifications_for(run.candidate_id)
                if existing:
                    return self._advance_one_body(reloaded, command)
            return _result_from_run(
                PromotionOutcome.PENDING_INDEPENDENT_VERIFICATION,
                _feedback_stub(reloaded),
                reloaded,
                ("CONCURRENT_PROMOTION_CONFLICT",),
            )

    def _advance_one_body(
        self, run: PromotionRunRecord, command: AdvancePromotionCommand
    ) -> PromotionResult:
        assert self._execute is not None
        if run.candidate_id is None:
            return _result_from_run(
                PromotionOutcome.CANDIDATE_REJECTED,
                _feedback_stub(run),
                run,
                ("MISSING_CANDIDATE",),
            )
        existing_verifications = self._verifications_for(run.candidate_id)
        if existing_verifications:
            latest = existing_verifications[-1]
            run = self._save(
                replace(
                    run,
                    stage="VERIFIED" if run.finding_proposal_id is None else run.stage,
                    verification_id=latest.verification_id,
                    updated_at=self._clock.now(),
                )
            )
            if latest.outcome == VerificationOutcome.VALIDATED.value:
                return self._submit_if_validated(run, latest.verification_id)
            return _result_from_run(
                PromotionOutcome.VERIFICATION_COMPLETED,
                _feedback_stub(run),
                run,
                (latest.outcome,),
            )
        if run.stage == "VERIFYING":
            reserved_at_entry = run.reproduction_experiment_id
            if reserved_at_entry is None:
                experiment_id = new_opaque_id()
                claimed = self._claim_reproduction(
                    run.promotion_run_id, experiment_id
                )
                reloaded = self._get(run.promotion_run_id)
                if reloaded is not None:
                    run = reloaded
                if not claimed:
                    existing_verifications = self._verifications_for(run.candidate_id)
                    if existing_verifications:
                        return self._advance_one(run, command)
                    return _result_from_run(
                        PromotionOutcome.PENDING_INDEPENDENT_VERIFICATION,
                        _feedback_stub(run),
                        run,
                        ("REPRODUCTION_CLAIMED_BY_CONCURRENT_ADVANCE",),
                    )
            status = self._execute_independent_reproduction(run, command)
            reloaded = self._get(run.promotion_run_id)
            if reloaded is not None:
                run = reloaded
            if status is None:
                run = self._save(
                    replace(
                        run,
                        stage="STOPPED",
                        stop_reason="INDEPENDENT_REPRODUCTION_NOT_EXECUTED",
                        updated_at=self._clock.now(),
                    )
                )
                return _result_from_run(
                    PromotionOutcome.VERIFICATION_COMPLETED,
                    _feedback_stub(run),
                    run,
                    ("INDEPENDENT_REPRODUCTION_NOT_EXECUTED",),
                )
            if status in NON_SCIENTIFIC_REPRODUCTION_STATUSES:
                run = self._save(
                    replace(
                        run,
                        stage="STOPPED",
                        stop_reason=status.value,
                        updated_at=self._clock.now(),
                    )
                )
                return _result_from_run(
                    PromotionOutcome.VERIFICATION_COMPLETED,
                    _feedback_stub(run),
                    run,
                    (status.value,),
                )
            run = self._save(
                replace(
                    run,
                    stage="REPRODUCTION_EXECUTED",
                    updated_at=self._clock.now(),
                )
            )
        reproduction_id = run.reproduction_experiment_id
        if reproduction_id is None:
            run = self._save(
                replace(
                    run,
                    stage="STOPPED",
                    stop_reason="INDEPENDENT_REPRODUCTION_NOT_EXECUTED",
                    updated_at=self._clock.now(),
                )
            )
            return _result_from_run(
                PromotionOutcome.VERIFICATION_COMPLETED,
                _feedback_stub(run),
                run,
                ("INDEPENDENT_REPRODUCTION_NOT_EXECUTED",),
            )
        self._admit_reproduction_evidence(reproduction_id)
        try:
            completed = self._complete_verification.execute(
                CompleteCandidateVerificationCommand(
                    candidate_id=run.candidate_id,
                    reproduction_experiment_id=reproduction_id,
                )
            )
        except ApplicationError:
            existing_verifications = self._verifications_for(run.candidate_id)
            if not existing_verifications:
                raise
            latest = existing_verifications[-1]
            run = self._save(
                replace(
                    run,
                    stage="VERIFIED" if run.finding_proposal_id is None else run.stage,
                    verification_id=latest.verification_id,
                    updated_at=self._clock.now(),
                )
            )
            if latest.outcome == VerificationOutcome.VALIDATED.value:
                return self._submit_if_validated(run, latest.verification_id)
            return _result_from_run(
                PromotionOutcome.VERIFICATION_COMPLETED,
                _feedback_stub(run),
                run,
                (latest.outcome,),
            )
        run = self._save(
            replace(
                run,
                stage="VERIFIED",
                verification_id=completed.verification_id,
                updated_at=self._clock.now(),
            )
        )
        if completed.outcome is VerificationOutcome.VALIDATED:
            return self._submit_if_validated(run, completed.verification_id)
        return _result_from_run(
            PromotionOutcome.VERIFICATION_COMPLETED,
            _feedback_stub(run),
            run,
            completed.reason_codes,
        )

    def _submit_if_validated(
        self, run: PromotionRunRecord, verification_id: str
    ) -> PromotionResult:
        assert run.candidate_id is not None
        existing = self._proposals_for(run.candidate_id)
        if existing:
            proposal_id = existing[-1].proposal_id
            run = self._save(
                replace(
                    run,
                    stage="PROPOSAL_RECORDED",
                    finding_proposal_id=proposal_id,
                    verification_id=verification_id,
                    updated_at=self._clock.now(),
                )
            )
            return _result_from_run(
                PromotionOutcome.SKIPPED_ALREADY_ATTEMPTED,
                _feedback_stub(run),
                run,
                ("FINDING_PROPOSAL_ALREADY_RECORDED",),
            )
        submitted = self._submit_proposal.execute(
            SubmitFindingProposalCommand(candidate_id=run.candidate_id)
        )
        if (
            submitted.outcome is FindingProposalAdmissionOutcome.ADMITTED
            and submitted.proposal_id is not None
        ):
            run = self._save(
                replace(
                    run,
                    stage="PROPOSAL_RECORDED",
                    finding_proposal_id=submitted.proposal_id,
                    verification_id=verification_id,
                    updated_at=self._clock.now(),
                )
            )
            return _result_from_run(
                PromotionOutcome.FINDING_PROPOSAL_RECORDED,
                _feedback_stub(run),
                run,
                submitted.reason_codes,
            )
        run = self._save(
            replace(
                run,
                stage="VERIFIED",
                verification_id=verification_id,
                stop_reason="FINDING_PROPOSAL_NOT_ADMITTED",
                updated_at=self._clock.now(),
            )
        )
        return _result_from_run(
            PromotionOutcome.VERIFICATION_COMPLETED,
            _feedback_stub(run),
            run,
            submitted.reason_codes,
        )

    def _execute_independent_reproduction(
        self, run: PromotionRunRecord, command: AdvancePromotionCommand
    ) -> ResearchLoopStatus | None:
        assert self._execute is not None
        experiment_id = run.reproduction_experiment_id
        if experiment_id is None:
            return None
        with self._uow_factory.open() as uow:
            attempts = uow.execution_attempts.list_for_experiment(experiment_id)
            uow.rollback()
        if attempts:
            return ResearchLoopStatus.ALREADY_TERMINAL
        with self._uow_factory.open() as uow:
            original = uow.experiment_plans.get(run.original_experiment_id)
            uow.rollback()
        if original is None:
            return None
        plan = experiment_plan_from_record(original)
        try:
            self._prepare.execute(
                PreparePlannedExperimentCommand(
                    experiment_id=experiment_id,
                    research_run_id=run.research_run_id,
                    plan=plan,
                )
            )
        except ApplicationError:
            return None
        executed = self._execute.execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=command.scope,
                approval=command.approval,
                compiled_scope=command.compiled_scope,
            )
        )
        if executed.status in {
            ResearchLoopStatus.OBSERVATION_PRODUCED,
            ResearchLoopStatus.ALREADY_TERMINAL,
            ResearchLoopStatus.NO_OBSERVATION,
        }:
            try:
                self._evaluate.execute(
                    EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
                )
            except ApplicationError:
                if executed.status is not ResearchLoopStatus.ALREADY_TERMINAL:
                    raise
        return executed.status

    def _claim_reproduction(
        self, promotion_run_id: str, reproduction_experiment_id: str
    ) -> bool:
        with self._uow_factory.open() as uow:
            claimed = uow.promotion_runs.claim_reproduction(
                promotion_run_id, reproduction_experiment_id
            )
            if claimed:
                uow.commit()
            else:
                uow.rollback()
            return claimed

    def _admit_reproduction_evidence(self, experiment_id: str) -> None:
        try:
            self._admit.execute(AdmitDiagnosticEvidenceCommand(experiment_id=experiment_id))
        except ApplicationError:
            return

    def _ensure_promotion_run(
        self,
        feedback: ResearchFeedback,
        admitted: AdmitDiagnosticEvidenceResult,
        existing: PromotionRunRecord | None,
    ) -> PromotionRunRecord:
        if existing is not None:
            return existing
        now = self._clock.now()
        record = PromotionRunRecord(
            promotion_run_id=new_opaque_id(),
            research_run_id=feedback.research_run_id,
            assessment_id=feedback.assessment_id or new_opaque_id(),
            original_experiment_id=feedback.experiment_id,
            stage="EVIDENCE_ADMITTED"
            if admitted.outcome is EvidenceAdmissionOutcome.ADMITTED
            else "EVIDENCE_REJECTED",
            created_at=now,
            updated_at=now,
            evidence_id=admitted.evidence_id,
        )
        with self._uow_factory.open() as uow:
            collision = uow.promotion_runs.get_by_assessment_id(record.assessment_id)
            if collision is not None:
                uow.rollback()
                return collision
            uow.promotion_runs.insert(record)
            uow.commit()
        return record

    def _save(self, record: PromotionRunRecord) -> PromotionRunRecord:
        with self._uow_factory.open() as uow:
            uow.promotion_runs.save(record)
            uow.commit()
        return record

    def _get(self, promotion_run_id: str) -> PromotionRunRecord | None:
        with self._uow_factory.open() as uow:
            record = uow.promotion_runs.get(promotion_run_id)
            uow.rollback()
        return record

    def _get_by_assessment(self, assessment_id: str) -> PromotionRunRecord | None:
        with self._uow_factory.open() as uow:
            record = uow.promotion_runs.get_by_assessment_id(assessment_id)
            uow.rollback()
        return record

    def _is_reproduction_experiment(self, experiment_id: str) -> bool:
        with self._uow_factory.open() as uow:
            experiment = uow.experiments.get(experiment_id)
            if experiment is None:
                uow.rollback()
                return False
            all_runs = uow.promotion_runs.list_for_research_run(experiment.research_run_id)
            uow.rollback()
        return any(item.reproduction_experiment_id == experiment_id for item in all_runs)

    def _candidate_for_evidence(self, evidence_id: str) -> CandidateRecord | None:
        with self._uow_factory.open() as uow:
            evidence = uow.evidence.get(evidence_id)
            if evidence is None:
                uow.rollback()
                return None
            candidates = uow.candidates.list_for_research_run(evidence.research_run_id)
            uow.rollback()
        matches = [item for item in candidates if evidence_id in item.evidence_ids]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.created_at)[0]

    def _get_candidate(self, candidate_id: str) -> CandidateRecord | None:
        with self._uow_factory.open() as uow:
            candidate = uow.candidates.get(candidate_id)
            uow.rollback()
        return candidate

    def _verifications_for(self, candidate_id: str):
        with self._uow_factory.open() as uow:
            items = uow.verifications.list_for_candidate(candidate_id)
            uow.rollback()
        return items

    def _proposals_for(self, candidate_id: str):
        with self._uow_factory.open() as uow:
            items = uow.finding_proposals.list_for_candidate(candidate_id)
            uow.rollback()
        return items

    def _pending_runs(self, command: AdvancePromotionCommand) -> tuple[PromotionRunRecord, ...]:
        with self._uow_factory.open() as uow:
            if command.promotion_run_id is not None:
                item = uow.promotion_runs.get(command.promotion_run_id)
                uow.rollback()
                return (item,) if item is not None else ()
            if command.assessment_id is not None:
                item = uow.promotion_runs.get_by_assessment_id(command.assessment_id)
                uow.rollback()
                return (item,) if item is not None else ()
            items = uow.promotion_runs.list_for_research_run(command.research_run_id)
            uow.rollback()
        pending = tuple(
            item for item in items if item.stage in PENDING_VERIFICATION_STAGES
        )
        return pending


class PromoteOnAssessment:
    """ARC evaluate wrapper. EvaluateExperimentFeedback itself still creates no Evidence."""

    def __init__(
        self,
        evaluate: object,
        promotion: PromotionPipeline,
    ) -> None:
        self._evaluate = evaluate
        self._promotion = promotion

    def execute(self, command):
        feedback = self._evaluate.execute(command)
        self._promotion.on_assessment(feedback)
        return feedback


class AdvancePromotionPipeline:
    """Resume pending independent verification after assessment or after a terminal run.

    Does not own ARC. Does not create Finding. Does not auto-approve Human Review.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        worker: WorkerPort,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane:promotion-pipeline",
        secret_port: CompositeSecretPort | None = None,
    ) -> None:
        self._pipeline = PromotionPipeline(
            uow_factory,
            clock=clock,
            worker=worker,
            actor_id=actor_id,
            secret_port=secret_port,
        )

    def execute(self, command: AdvancePromotionCommand) -> tuple[PromotionResult, ...]:
        return self._pipeline.advance(command)


def _from_admission(
    feedback: ResearchFeedback, admitted: AdmitDiagnosticEvidenceResult
) -> PromotionResult:
    if admitted.outcome is EvidenceAdmissionOutcome.ADMITTED:
        outcome = PromotionOutcome.ADMITTED
    else:
        outcome = PromotionOutcome.ADMISSION_REJECTED
    return PromotionResult(
        outcome=outcome,
        assessment_id=feedback.assessment_id,
        experiment_id=feedback.experiment_id,
        admission_record_id=admitted.admission_record_id,
        evidence_id=admitted.evidence_id,
        reason_codes=admitted.reason_codes,
    )


def _result_from_run(
    outcome: PromotionOutcome,
    feedback: ResearchFeedback,
    run: PromotionRunRecord,
    reason_codes: tuple[str, ...],
) -> PromotionResult:
    return PromotionResult(
        outcome=outcome,
        assessment_id=feedback.assessment_id,
        experiment_id=feedback.experiment_id,
        evidence_id=run.evidence_id,
        candidate_id=run.candidate_id,
        verification_id=run.verification_id,
        finding_proposal_id=run.finding_proposal_id,
        promotion_run_id=run.promotion_run_id,
        stage=run.stage,
        reason_codes=reason_codes,
    )


def _feedback_stub(run: PromotionRunRecord) -> ResearchFeedback:
    return ResearchFeedback(
        hypothesis_id="promotion-advance",
        experiment_id=run.original_experiment_id,
        assessment_id=run.assessment_id,
        assessment_outcome=AssessmentOutcome.CONSISTENT_WITH_PREDICTION,
        observation_ids=(),
        execution_usable=True,
        evaluation_strategy="diagnostic.echo.v1",
        research_run_id=run.research_run_id,
    )
