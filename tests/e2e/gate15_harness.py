"""GATE 15 harness. Drives the existing pipeline. Hidden ground truth stays out."""

from __future__ import annotations

import sys

from e2e.lab.http_ground_truth_lab import GroundTruthLab
from integration.harness import NOW, FixedClock
from zest.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from zest.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
)
from zest.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from zest.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
)
from zest.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from zest.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from zest.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from zest.application.record_human_review import (
    RecordHumanReview,
    RecordHumanReviewCommand,
)
from zest.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from zest.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.core.enums import ActorType, ScopeRuleEffect
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, ScopeRuleDefinition, compile_scope_rules
from zest.data.records import (
    AuthorizationSourceRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from zest.research.finding_proposal import HumanReviewDecision
from zest.research.planning import plan_authorization_differential
from zest.security_benchmark.scenarios import SecurityGroundTruthScenario
from zest.security_benchmark.scorecard import ObservedScenarioResult
from zest.security_benchmark.types import ExpectedSecurityClass
from support.recording_worker import RecordingWorkerPort
from support.sd_g10_validator import seed_validator_pass

GATE15_HUMAN = "gate15-human-reviewer"
MODEL_MODULE_MARKERS = (
    "zest.integrations.models.cli_session",
    "openai",
    "anthropic",
)
STRIX_MODULE_MARKERS = ("zest.integrations.strix.adapter",)


def prefix_for(scenario_id: str) -> str:
    return scenario_id.split("_", 1)[0].lower()


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-deny", ScopeRuleEffect.OUT_OF_SCOPE, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _compiled_scope_for_origin(origin: str) -> CompiledScope:
    from urllib.parse import urlsplit

    parsed = urlsplit(origin)
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme=parsed.scheme or "http",
                host=parsed.hostname or "127.0.0.1",
                port=parsed.port,
                path_prefix=None,
                source_reference="scope-src",
            ),
        )
    )


def _deny_compiled_scope() -> CompiledScope:
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-deny",
                effect=ScopeRuleEffect.OUT_OF_SCOPE,
                scheme="http",
                host="127.0.0.1",
                port=None,
                path_prefix=None,
                source_reference="scope-src",
            ),
        )
    )


def _local_worker() -> RecordingWorkerPort:
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                module=PACKAGED_WORKER_MODULE,
                default_timeout_ms=5_000,
            )
        )
    )


def _plan(origin: str, scenario: SecurityGroundTruthScenario, *, verification: bool = False):
    harness = scenario.harness
    prefix = prefix_for(scenario.scenario_id)
    actor = harness.verification_actor if verification else harness.actor
    own = harness.verification_own_object if verification else harness.own_object
    cross = harness.verification_cross_object if verification else harness.cross_object
    assert actor is not None and own is not None and cross is not None
    return plan_authorization_differential(
        f"{prefix}-hyp",
        budget_id=f"{prefix}-budget",
        target_reference=origin,
        authorized_origin=origin,
        actor=actor,
        own_object=own,
        cross_object=cross,
    )


def seed_scenario(uow, scenario: SecurityGroundTruthScenario) -> None:
    prefix = prefix_for(scenario.scenario_id)
    uow.programs.insert(
        ProgramRecord(program_id=f"{prefix}-prog", created_at=NOW, name=f"gate15-{prefix}")
    )
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id=f"{prefix}-as",
            program_id=f"{prefix}-prog",
            state="ACTIVE",
            provenance_reference=f"written-local-lab-auth-{prefix}",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id=f"{prefix}-run",
            program_id=f"{prefix}-prog",
            authorization_source_id=f"{prefix}-as",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id=f"{prefix}-budget",
            research_run_id=f"{prefix}-run",
            max_requests=40,
            max_tool_calls=40,
            max_runtime_ms=60_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )
    uow.hypotheses.insert(
        HypothesisRecord(
            hypothesis_id=f"{prefix}-hyp",
            research_run_id=f"{prefix}-run",
            claim=(
                "Authenticated actor can read another actor's account object because object "
                "authorization is missing on the vulnerable endpoint."
            ),
            origin_reference="human-seed-gate15",
            created_at=NOW,
        )
    )


def probe(factory, experiment_id: str, run_id: str, plan, worker, scope, compiled_scope) -> None:
    PreparePlannedExperiment(factory, clock=FixedClock()).execute(
        PreparePlannedExperimentCommand(
            experiment_id=experiment_id,
            research_run_id=run_id,
            plan=plan,
        )
    )
    ExecutePlannedExperiment(factory, worker, clock=FixedClock()).execute(
        ExecutePlannedExperimentCommand(
            experiment_id=experiment_id,
            plan=plan,
            scope=scope,
            compiled_scope=compiled_scope,
        )
    )


def assess_and_admit(factory, experiment_id: str):
    EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
        EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
    )
    return AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
        AdmitDiagnosticEvidenceCommand(experiment_id=experiment_id)
    )


def run_scenario(factory, scenario: SecurityGroundTruthScenario) -> ObservedScenarioResult:
    prefix = prefix_for(scenario.scenario_id)
    harness = scenario.harness
    run_id = f"{prefix}-run"
    with factory.open() as uow:
        seed_scenario(uow, scenario)
        uow.commit()
    lab = None
    origin = harness.target_reference or "http://127.0.0.1:9"
    if harness.in_scope:
        lab = GroundTruthLab(harness.fixture_kind)
        origin = lab.start()
        host, _port = lab._server.server_address[:2]
        if host != "127.0.0.1":
            lab.stop()
            raise AssertionError(f"lab bound {host!r}, not 127.0.0.1")
    worker = _local_worker()
    finding_before_approval = False
    human_approved = False
    try:
        scope = _allow_scope() if harness.in_scope else _deny_scope()
        compiled_scope = (
            _compiled_scope_for_origin(origin)
            if harness.in_scope
            else _deny_compiled_scope()
        )
        probe(factory, f"{prefix}-exp", run_id, _plan(origin, scenario), worker, scope, compiled_scope)
        expected = scenario.hidden_evaluation.expected_class
        if expected in {
            ExpectedSecurityClass.SCOPE_DENIED,
            ExpectedSecurityClass.CONTROLLED_STOP,
        }:
            return _snapshot(
                factory, scenario, prefix, worker, lab, False, False
            )
        admitted = assess_and_admit(factory, f"{prefix}-exp")
        needs_candidate = expected in {
            ExpectedSecurityClass.VULNERABLE,
            ExpectedSecurityClass.CONTRADICTION_REJECTED,
            ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE,
        }
        if not needs_candidate or admitted.evidence_id is None:
            return _snapshot(
                factory, scenario, prefix, worker, lab, False, False
            )
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=admitted.evidence_id)
        )
        assert proposed.candidate_id is not None
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=proposed.candidate_id)
        )
        probe(
            factory,
            f"{prefix}-repro",
            run_id,
            _plan(origin, scenario, verification=True),
            worker,
            _allow_scope(),
            _compiled_scope_for_origin(origin),
        )
        if expected is not ExpectedSecurityClass.OPERATIONAL_INCONCLUSIVE:
            assess_and_admit(factory, f"{prefix}-repro")
        CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=proposed.candidate_id,
                reproduction_experiment_id=f"{prefix}-repro",
            )
        )
        if not harness.attempt_finding:
            return _snapshot(
                factory, scenario, prefix, worker, lab, False, False
            )
        with factory.open() as uow:
            candidate = uow.candidates.get(proposed.candidate_id)
            assert candidate is not None
            seed_validator_pass(
                uow,
                research_run_id=candidate.research_run_id,
                hypothesis_id=candidate.hypothesis_id,
                created_at=NOW,
                marker=proposed.candidate_id,
            )
            uow.commit()
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=proposed.candidate_id)
        )
        assert submitted.proposal_id is not None
        with factory.open() as uow:
            finding_before_approval = bool(uow.findings.list_for_research_run(run_id))
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=GATE15_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
                note="gate15 explicit human review",
            )
        )
        with factory.open() as uow:
            finding_before_approval = finding_before_approval or bool(
                uow.findings.list_for_research_run(run_id)
            )
        FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=GATE15_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        human_approved = True
        return _snapshot(
            factory,
            scenario,
            prefix,
            worker,
            lab,
            finding_before_approval,
            human_approved,
        )
    finally:
        if lab is not None:
            lab.stop()


def _snapshot(
    factory,
    scenario: SecurityGroundTruthScenario,
    prefix: str,
    worker: RecordingWorkerPort,
    lab: GroundTruthLab | None,
    finding_before_approval: bool,
    human_approved: bool,
) -> ObservedScenarioResult:
    run_id = f"{prefix}-run"
    with factory.open() as uow:
        observations = uow.observations.list_for_experiment(f"{prefix}-exp")
        evidence = [
            item
            for item in uow.evidence.list_for_research_run(run_id)
            if item.polarity == "SUPPORTING"
        ]
        candidates = uow.candidates.list_for_research_run(run_id)
        findings = uow.findings.list_for_research_run(run_id)
        attempts = uow.execution_attempts.list_for_experiment(f"{prefix}-exp")
        repro_attempts = uow.execution_attempts.list_for_experiment(f"{prefix}-repro")
        results = uow.worker_results.list_for_experiment(f"{prefix}-exp")
        repro_results = uow.worker_results.list_for_experiment(f"{prefix}-repro")
        admissions = uow.evidence_admissions.list_for_research_run(run_id)
        assessments = uow.hypothesis_assessments.list_for_experiment(f"{prefix}-exp")
        candidate = candidates[0] if candidates else None
        verification_outcome = None
        if candidate is not None:
            stored = uow.verifications.list_for_candidate(candidate.candidate_id)
            if stored:
                verification_outcome = stored[-1].outcome
        observation_payload = observations[0].payload if observations else None
        evidence_rationale = None
        if admissions:
            evidence_rationale = {"reason_codes": list(admissions[0].reason_codes)}
        assessment_reason = None
        if assessments:
            assessment_reason = str(
                (assessments[-1].rationale or {}).get("reason_code") or ""
            ) or None
        original_request = attempts[0].request_id if attempts else None
        repro_request = repro_attempts[0].request_id if repro_attempts else None
        worker_status = results[0].status if results else None
        core_reason = "SCOPE_DENIED" if not results and not attempts else None
        redirect_followed = False
        for item in [*results, *repro_results]:
            if (item.diagnostics or {}).get("followed"):
                redirect_followed = True
    if lab is not None and lab.followed_external():
        redirect_followed = True
    return ObservedScenarioResult(
        scenario_id=scenario.scenario_id,
        version=scenario.version,
        observation_count=len(observations),
        evidence_admitted=bool(evidence),
        candidate_state=None if candidate is None else candidate.state,
        verification_outcome=verification_outcome,
        finding_count=len(findings),
        finding_before_human_approval=finding_before_approval,
        human_approved=human_approved,
        worker_invocation_count=len(worker.calls),
        http_request_count=0 if lab is None else lab.http_request_count(),
        redirect_followed=redirect_followed,
        original_experiment_id=f"{prefix}-exp" if attempts else None,
        reproduction_experiment_id=f"{prefix}-repro" if repro_attempts else None,
        original_request_id=original_request,
        reproduction_request_id=repro_request,
        worker_out_of_process=(
            isinstance(worker._inner, LocalProcessWorkerAdapter)
            and worker._inner._config.module == PACKAGED_WORKER_MODULE
        ),
        worker_request=worker.calls[0]["request"] if worker.calls else None,
        observation_payload=observation_payload,
        evidence_rationale=evidence_rationale,
        assessment_reason_code=assessment_reason,
        worker_result_status=worker_status,
        core_reason_code=core_reason,
        model_modules_loaded=tuple(
            name for name in MODEL_MODULE_MARKERS if name in sys.modules
        ),
        strix_modules_loaded=tuple(
            name for name in STRIX_MODULE_MARKERS if name in sys.modules
        ),
    )
