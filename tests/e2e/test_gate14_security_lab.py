"""GATE 14 — Authorized local security-research pipeline E2E.

Skipped when RESEARCH_OS_TEST_DATABASE_URL is absent (PENDING, not PASS).
Does not set SECURITY_RESEARCH_VALIDATED or PRODUCTION_READY.
GATE 14 may be PASS while GATE 04B remains PENDING. No Codex/LLM/Strix/internet target.
"""

from __future__ import annotations

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

from e2e.lab.http_idor_lab import Gate14Lab
from integration.harness import (
    FixedClock,
    NOW,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    truncate_spine,
)
from research_os.application.admit_diagnostic_evidence import (
    AdmitDiagnosticEvidence,
    AdmitDiagnosticEvidenceCommand,
)
from research_os.application.complete_candidate_verification import (
    CompleteCandidateVerification,
    CompleteCandidateVerificationCommand,
)
from research_os.application.errors import ApplicationError
from research_os.application.evaluate_experiment_feedback import (
    EvaluateExperimentFeedback,
    EvaluateExperimentFeedbackCommand,
)
from research_os.application.execute_planned_experiment import (
    ExecutePlannedExperiment,
    ExecutePlannedExperimentCommand,
    ResearchLoopStatus,
)
from research_os.application.finalize_finding import FinalizeFinding, FinalizeFindingCommand
from research_os.application.prepare_planned_experiment import (
    PreparePlannedExperiment,
    PreparePlannedExperimentCommand,
)
from research_os.application.propose_candidate import (
    ProposeCandidateFromEvidence,
    ProposeCandidateFromEvidenceCommand,
)
from research_os.application.record_human_review import (
    RecordHumanReview,
    RecordHumanReviewCommand,
)
from research_os.application.start_candidate_verification import (
    StartCandidateVerification,
    StartCandidateVerificationCommand,
)
from research_os.application.start_human_review import StartHumanReview, StartHumanReviewCommand
from research_os.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from research_os.core.enums import ActorType, ReasonCode, ScopeRuleEffect
from research_os.core.scope import ScopeEvaluationInput, ScopeRuleMatch
from research_os.core.scope_compiler import CompiledScope, ScopeRuleDefinition, compile_scope_rules
from research_os.data.postgres.engine import (
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    redacted_database_url,
    validate_test_database_url,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.data.records import (
    AuthorizationSourceRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ProgramRecord,
    ResearchRunRecord,
)
from research_os.maturity import (
    GATE_04B_STATUS,
    GATE_14_STATUS,
    LIVE_MODEL_VALIDATED,
    PRODUCTION_READY,
    SECURITY_RESEARCH_VALIDATED,
)
from research_os.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
    PACKAGED_WORKER_MODULE,
)
from research_os.platform.worker import InvocationStatus, WorkerInvocationOutcome
from research_os.research.candidate import CandidateState
from research_os.research.evidence import HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM
from research_os.research.finding_proposal import (
    FindingCreationOutcome,
    FindingProposalState,
    HumanReviewDecision,
)
from research_os.research.planning import plan_authorization_differential
from research_os.research.verification import VerificationOutcome
from support.recording_worker import RecordingWorkerPort, invocation_outcome
from support.sd_g10_validator import seed_validator_pass

TEST_URL = os.environ.get(TEST_DATABASE_URL_ENV)
if TEST_URL:
    TEST_URL = validate_test_database_url(
        TEST_URL, application_url=os.environ.get("RESEARCH_OS_DATABASE_URL")
    )

GATE14_HUMAN = "gate14-human-reviewer"
HTTP_CLAIM = HTTP_AUTHORIZATION_DIFFERENTIAL_CLAIM


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


def _loopback_compiled_scope() -> CompiledScope:
    return compile_scope_rules(
        (
            ScopeRuleDefinition(
                rule_id="rule-allow",
                effect=ScopeRuleEffect.ALLOW,
                scheme="http",
                host="127.0.0.1",
                port=None,
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


def _allow_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _deny_scope() -> ScopeEvaluationInput:
    return ScopeEvaluationInput(
        matches=(
            ScopeRuleMatch("rule-deny", ScopeRuleEffect.OUT_OF_SCOPE, True, "scope-src"),
        ),
        ambiguous=False,
    )


def _plan(origin: str, *, actor: str, own: str, cross: str, mode: str = "vulnerable"):
    return plan_authorization_differential(
        "hyp-1",
        budget_id="budget-1",
        target_reference=origin,
        authorized_origin=origin,
        actor=actor,
        own_object=own,
        cross_object=cross,
        mode=mode,
    )


def _local_worker():
    return RecordingWorkerPort(
        inner=LocalProcessWorkerAdapter(
            LocalProcessWorkerConfig(
                module=PACKAGED_WORKER_MODULE,
                default_timeout_ms=5_000,
            )
        )
    )


def _seed_run(uow: PostgresUnitOfWork) -> None:
    uow.programs.insert(ProgramRecord(program_id="prog-1", created_at=NOW, name="gate14-lab"))
    uow.authorization_sources.insert(
        AuthorizationSourceRecord(
            authorization_source_id="as-1",
            program_id="prog-1",
            state="ACTIVE",
            provenance_reference="written-local-lab-auth-1",
            created_at=NOW,
        )
    )
    uow.research_runs.insert(
        ResearchRunRecord(
            research_run_id="run-1",
            program_id="prog-1",
            authorization_source_id="as-1",
            initiated_by_actor_id="operator-1",
            initiated_by_actor_type="HUMAN_OPERATOR",
            started_at=NOW,
        )
    )
    uow.issued_budgets.insert(
        IssuedBudgetRecord(
            budget_id="budget-1",
            research_run_id="run-1",
            max_requests=40,
            max_tool_calls=40,
            max_runtime_ms=60_000,
            max_concurrency=1,
            issued_at=NOW,
        )
    )
    uow.hypotheses.insert(
        HypothesisRecord(
            hypothesis_id="hyp-1",
            research_run_id="run-1",
            claim=HTTP_CLAIM,
            origin_reference="human-seed-gate14",
            created_at=NOW,
        )
    )


def _status_only_handler(request):
    arguments = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
    return WorkerInvocationOutcome(
        invocation_status=InvocationStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        worker_result={
            "contract_version": "v1",
            "correlation": dict(request["correlation"]),
            "worker_id": "local-python-diagnostic",
            "status": "SUCCEEDED",
            "started_at": "2026-08-17T12:00:00Z",
            "completed_at": "2026-08-17T12:00:01Z",
            "raw_result": {
                "mode": arguments.get("mode", "vulnerable"),
                "authorized_origin": arguments.get("authorized_origin"),
                "owner_request": {"status": 200, "object_owner": "alice"},
                "cross_object_request": {"status": 200},
                "secure_control": {"status": 403},
                "unauthenticated_control": {"status": 401},
            },
        },
        exit_code=0,
    )


@unittest.skipUnless(
    TEST_URL,
    f"{TEST_DATABASE_URL_ENV} not set; GATE 14 PostgreSQL E2E skipped "
    "(PENDING, not PASS; SQLite is not a substitute)",
)
class Gate14SecurityLabE2ETests(unittest.TestCase):
    engine = None
    lab: Gate14Lab | None = None

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_URL is not None
        print(f"GATE 14 PostgreSQL target={redacted_database_url(TEST_URL)}", flush=True)
        cls.engine = create_sync_engine(TEST_URL)
        alembic_upgrade(TEST_URL)
        cls.lab = Gate14Lab()
        cls.lab.start()
        host, port = cls.lab._server.server_address[:2]
        if host != "127.0.0.1":
            raise AssertionError(f"lab bound {host!r}, not 127.0.0.1")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.lab is not None:
            cls.lab.stop()
        if cls.engine is not None:
            cls.engine.dispose()

    def setUp(self) -> None:
        assert self.engine is not None
        truncate_spine(self.engine)

    def _factory(self) -> PostgresUnitOfWorkFactory:
        assert self.engine is not None
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_run(uow)
            uow.commit()
        return factory

    def _origin(self) -> str:
        assert self.lab is not None
        return self.lab.origin

    def _run_probe(
        self,
        factory: PostgresUnitOfWorkFactory,
        experiment_id: str,
        plan,
        *,
        worker=None,
        scope=None,
        compiled_scope=None,
    ):
        PreparePlannedExperiment(factory, clock=FixedClock()).execute(
            PreparePlannedExperimentCommand(
                experiment_id=experiment_id,
                research_run_id="run-1",
                plan=plan,
            )
        )
        port = worker or _local_worker()
        if compiled_scope is None:
            compiled_scope = _compiled_scope_for_origin(plan.arguments["authorized_origin"])
        outcome = ExecutePlannedExperiment(factory, port, clock=FixedClock()).execute(
            ExecutePlannedExperimentCommand(
                experiment_id=experiment_id,
                plan=plan,
                scope=scope or _allow_scope(),
                compiled_scope=compiled_scope,
            )
        )
        return outcome, port

    def _assess_and_admit(self, factory, experiment_id: str):
        EvaluateExperimentFeedback(factory, clock=FixedClock()).execute(
            EvaluateExperimentFeedbackCommand(experiment_id=experiment_id)
        )
        return AdmitDiagnosticEvidence(factory, clock=FixedClock()).execute(
            AdmitDiagnosticEvidenceCommand(experiment_id=experiment_id)
        )

    def _vulnerable_candidate(self, factory) -> str:
        origin = self._origin()
        plan = _plan(origin, actor="alice", own="alice", cross="bob")
        outcome, port = self._run_probe(factory, "exp-1", plan)
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertEqual(port.calls[0]["request"]["worker_capability"], "http.authorization.differential")
        self.assertEqual(port._inner._config.module, PACKAGED_WORKER_MODULE)
        admitted = self._assess_and_admit(factory, "exp-1")
        self.assertIsNotNone(admitted.evidence_id)
        proposed = ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
            ProposeCandidateFromEvidenceCommand(evidence_id=admitted.evidence_id)
        )
        assert proposed.candidate_id is not None
        self.assertEqual(proposed.state, CandidateState.OPEN)
        return proposed.candidate_id

    def _validate(self, factory, candidate_id: str) -> str:
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        origin = self._origin()
        repro = _plan(origin, actor="bob", own="bob", cross="alice")
        outcome, _ = self._run_probe(factory, "exp-repro", repro)
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        admitted = self._assess_and_admit(factory, "exp-repro")
        self.assertIsNotNone(admitted.evidence_id)
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=candidate_id,
                reproduction_experiment_id="exp-repro",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.VALIDATED)
        self.assertEqual(completed.state, CandidateState.VALIDATED)
        with factory.open() as uow:
            candidate = uow.candidates.get(candidate_id)
            assert candidate is not None
            seed_validator_pass(
                uow,
                research_run_id=candidate.research_run_id,
                hypothesis_id=candidate.hypothesis_id,
                created_at=NOW,
            )
            uow.commit()
        return completed.verification_id

    def test_01_vulnerable_lab_completes_http_security_probe(self) -> None:
        factory = self._factory()
        origin = self._origin()
        outcome, port = self._run_probe(
            factory, "exp-1", _plan(origin, actor="alice", own="alice", cross="bob")
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertTrue(outcome.observation_ids)
        request = port.calls[0]["request"]
        self.assertEqual(request["worker_capability"], "http.authorization.differential")
        self.assertEqual(request["action"], "probe")
        self.assertEqual(request["arguments"]["authorized_origin"], origin)
        with factory.open() as uow:
            results = uow.worker_results.list_for_experiment("exp-1")
            observations = uow.observations.list_for_experiment("exp-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "SUCCEEDED")
        self.assertEqual(results[0].request_id, request["correlation"]["request_id"])
        self.assertEqual(observations[0].observation_kind, "HTTP_AUTHORIZATION_DIFFERENTIAL")
        payload = observations[0].payload
        self.assertEqual(payload["cross_object_request_object_owner"], "bob")
        self.assertNotIn("vulnerability", payload)
        self.assertNotIn("finding", payload)

    def test_02_worker_is_out_of_process_packaged_runtime(self) -> None:
        factory = self._factory()
        outcome, port = self._run_probe(
            factory,
            "exp-1",
            _plan(self._origin(), actor="alice", own="alice", cross="bob"),
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        self.assertIsInstance(port._inner, LocalProcessWorkerAdapter)
        self.assertEqual(port._inner._config.module, PACKAGED_WORKER_MODULE)
        self.assertEqual(port.calls[0]["request"]["worker_capability"], "http.authorization.differential")

    def test_03_worker_cannot_contact_non_loopback_target(self) -> None:
        factory = self._factory()
        plan = _plan("http://8.8.8.8:80", actor="alice", own="alice", cross="bob")
        worker = _local_worker()
        outcome, _ = self._run_probe(
            factory,
            "exp-blocked",
            plan,
            worker=worker,
            compiled_scope=_loopback_compiled_scope(),
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(len(worker.calls), 0)
        with factory.open() as uow:
            results = uow.worker_results.list_for_experiment("exp-blocked")
            observations = uow.observations.list_for_experiment("exp-blocked")
        self.assertEqual(results, [])
        self.assertEqual(observations, [])

    def test_04_redirects_are_not_silently_followed(self) -> None:
        factory = self._factory()
        plan = _plan(self._origin(), actor="alice", own="alice", cross="bob", mode="redirect")
        outcome, _ = self._run_probe(factory, "exp-redirect", plan)
        self.assertEqual(outcome.status, ResearchLoopStatus.REAUTHORIZATION_REQUIRED)
        with factory.open() as uow:
            results = uow.worker_results.list_for_experiment("exp-redirect")
            observations = uow.observations.list_for_experiment("exp-redirect")
        self.assertEqual(results[0].status, "REAUTHORIZATION_REQUIRED")
        diagnostics = results[0].diagnostics or {}
        self.assertFalse(diagnostics.get("followed"))
        self.assertTrue(diagnostics.get("requires_core_re_evaluation"))
        self.assertEqual(observations, [])

    def test_05_worker_result_becomes_observation_only_through_transition_a(self) -> None:
        factory = self._factory()
        self._run_probe(
            factory, "exp-1", _plan(self._origin(), actor="alice", own="alice", cross="bob")
        )
        with factory.open() as uow:
            result = uow.worker_results.list_for_experiment("exp-1")[0]
            observation = uow.observations.list_for_experiment("exp-1")[0]
        self.assertEqual(observation.worker_result_id, result.worker_result_id)
        self.assertEqual(observation.normalization_version, "http.authorization.differential.v1")
        self.assertNotEqual(result.raw_result, observation.payload)

    def test_06_http_200_alone_does_not_admit_evidence(self) -> None:
        factory = self._factory()
        plan = _plan(self._origin(), actor="alice", own="alice", cross="bob")
        worker = RecordingWorkerPort(handler=_status_only_handler)
        outcome, _ = self._run_probe(factory, "exp-status", plan, worker=worker)
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        admitted = self._assess_and_admit(factory, "exp-status")
        self.assertIsNone(admitted.evidence_id)
        with factory.open() as uow:
            self.assertEqual(uow.evidence.list_for_experiment("exp-status"), [])
            self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])

    def test_07_full_vulnerable_differential_admits_evidence(self) -> None:
        factory = self._factory()
        self._run_probe(
            factory, "exp-1", _plan(self._origin(), actor="alice", own="alice", cross="bob")
        )
        admitted = self._assess_and_admit(factory, "exp-1")
        self.assertIsNotNone(admitted.evidence_id)
        with factory.open() as uow:
            evidence = uow.evidence.get(admitted.evidence_id)
            admissions = uow.evidence_admissions.list_for_research_run("run-1")
        assert evidence is not None
        self.assertEqual(evidence.claim_scope, HTTP_CLAIM)
        self.assertEqual(evidence.polarity, "SUPPORTING")
        self.assertEqual(admissions[0].outcome, "ADMITTED")

    def test_08_secure_control_admits_no_security_evidence(self) -> None:
        factory = self._factory()
        plan = _plan(
            self._origin(), actor="alice", own="alice", cross="bob", mode="secure_only"
        )
        outcome, _ = self._run_probe(factory, "exp-secure", plan)
        self.assertEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        admitted = self._assess_and_admit(factory, "exp-secure")
        self.assertIsNone(admitted.evidence_id)
        with factory.open() as uow:
            self.assertEqual(uow.evidence.list_for_experiment("exp-secure"), [])
            self.assertEqual(uow.candidates.list_for_research_run("run-1"), [])
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            observations = uow.observations.list_for_experiment("exp-secure")
        self.assertEqual(len(observations), 1)

    def test_09_candidate_created_only_from_admitted_evidence(self) -> None:
        factory = self._factory()
        self._run_probe(
            factory,
            "exp-secure",
            _plan(self._origin(), actor="alice", own="alice", cross="bob", mode="secure_only"),
        )
        self._assess_and_admit(factory, "exp-secure")
        with self.assertRaises(ApplicationError):
            ProposeCandidateFromEvidence(factory, clock=FixedClock()).execute(
                ProposeCandidateFromEvidenceCommand(evidence_id="ev-missing")
            )
        candidate_id = self._vulnerable_candidate(factory)
        with factory.open() as uow:
            candidate = uow.candidates.get(candidate_id)
        assert candidate is not None
        self.assertEqual(candidate.claim, HTTP_CLAIM)
        self.assertEqual(candidate.classification, "HTTP_AUTHORIZATION_DIFFERENTIAL")
        self.assertEqual(candidate.state, "OPEN")

    def test_10_and_11_verification_uses_fresh_ids_and_validates(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        self._validate(factory, candidate_id)
        with factory.open() as uow:
            original = uow.experiments.get("exp-1")
            repro = uow.experiments.get("exp-repro")
            original_attempts = uow.execution_attempts.list_for_experiment("exp-1")
            repro_attempts = uow.execution_attempts.list_for_experiment("exp-repro")
            original_obs = uow.observations.list_for_experiment("exp-1")
            repro_obs = uow.observations.list_for_experiment("exp-repro")
            candidate = uow.candidates.get(candidate_id)
        assert original is not None and repro is not None and candidate is not None
        self.assertNotEqual(original.experiment_id, repro.experiment_id)
        self.assertNotEqual(original_attempts[0].request_id, repro_attempts[0].request_id)
        self.assertNotEqual(original_obs[0].observation_id, repro_obs[0].observation_id)
        self.assertEqual(candidate.state, "VALIDATED")
        self.assertEqual(repro_obs[0].payload["actor"], "bob")
        self.assertEqual(repro_obs[0].payload["cross_object"], "alice")

    def test_12_timeout_verification_is_inconclusive(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        timeout_worker = RecordingWorkerPort(
            outcome=invocation_outcome(InvocationStatus.TIMED_OUT, reason="injected-timeout")
        )
        outcome, _ = self._run_probe(
            factory,
            "exp-timeout",
            _plan(self._origin(), actor="bob", own="bob", cross="alice"),
            worker=timeout_worker,
        )
        self.assertNotEqual(outcome.status, ResearchLoopStatus.OBSERVATION_PRODUCED)
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=candidate_id,
                reproduction_experiment_id="exp-timeout",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.INCONCLUSIVE)
        self.assertEqual(completed.state, CandidateState.INCONCLUSIVE)
        self.assertIn("FAILURE_TO_VERIFY_IS_NOT_REJECTION", completed.reason_codes)

    def test_13_secure_control_cannot_become_validated_vulnerability(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        StartCandidateVerification(factory).execute(
            StartCandidateVerificationCommand(candidate_id=candidate_id)
        )
        self._run_probe(
            factory,
            "exp-secure-repro",
            _plan(
                self._origin(),
                actor="bob",
                own="bob",
                cross="alice",
                mode="secure_only",
            ),
        )
        admitted = self._assess_and_admit(factory, "exp-secure-repro")
        self.assertIsNone(admitted.evidence_id)
        completed = CompleteCandidateVerification(factory, clock=FixedClock()).execute(
            CompleteCandidateVerificationCommand(
                candidate_id=candidate_id,
                reproduction_experiment_id="exp-secure-repro",
            )
        )
        self.assertEqual(completed.outcome, VerificationOutcome.REJECTED)
        self.assertEqual(completed.state, CandidateState.REJECTED)

    def test_14_finding_proposal_only_after_validated(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        with self.assertRaises(ApplicationError):
            SubmitFindingProposal(factory, clock=FixedClock()).execute(
                SubmitFindingProposalCommand(candidate_id=candidate_id)
            )
        self._validate(factory, candidate_id)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        self.assertIsNotNone(submitted.proposal_id)
        self.assertEqual(submitted.state, FindingProposalState.PROPOSED)

    def test_15_and_16_human_approval_creates_exactly_one_finding(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        self._validate(factory, candidate_id)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
                note="gate14 explicit human review",
            )
        )
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
        finalized = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(finalized.outcome, FindingCreationOutcome.CREATED)
        self.assertIsNotNone(finalized.finding_id)
        with factory.open() as uow:
            findings = uow.findings.list_for_research_run("run-1")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].classification, "HTTP_AUTHORIZATION_DIFFERENTIAL")
        self.assertEqual(findings[0].claim, HTTP_CLAIM)

    def test_17_human_rejection_creates_zero_finding(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        self._validate(factory, candidate_id)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.REJECT,
                note="gate14 explicit rejection",
            )
        )
        finalized = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        self.assertEqual(finalized.outcome, FindingCreationOutcome.REJECTED_PROPOSAL)
        self.assertIsNone(finalized.finding_id)
        with factory.open() as uow:
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])
            candidate = uow.candidates.get(candidate_id)
        assert candidate is not None
        self.assertEqual(candidate.state, "VALIDATED")

    def test_18_out_of_scope_target_never_reaches_worker(self) -> None:
        factory = self._factory()
        worker = _local_worker()
        outcome, _ = self._run_probe(
            factory,
            "exp-oos",
            _plan("http://example.com", actor="alice", own="alice", cross="bob"),
            worker=worker,
            scope=_deny_scope(),
            compiled_scope=_deny_compiled_scope(),
        )
        self.assertEqual(outcome.status, ResearchLoopStatus.DISPATCH_DENIED)
        self.assertEqual(outcome.core_reason_code, ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED)
        self.assertEqual(len(worker.calls), 0)
        with factory.open() as uow:
            self.assertEqual(uow.execution_attempts.list_for_experiment("exp-oos"), [])
            self.assertEqual(uow.worker_results.list_for_experiment("exp-oos"), [])
            self.assertEqual(uow.observations.list_for_experiment("exp-oos"), [])
            self.assertEqual(uow.evidence.list_for_experiment("exp-oos"), [])
            self.assertEqual(uow.findings.list_for_research_run("run-1"), [])

    def test_19_restart_reload_preserves_provenance(self) -> None:
        factory = self._factory()
        candidate_id = self._vulnerable_candidate(factory)
        verification_id = self._validate(factory, candidate_id)
        submitted = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id=candidate_id)
        )
        assert submitted.proposal_id is not None
        StartHumanReview(factory).execute(
            StartHumanReviewCommand(proposal_id=submitted.proposal_id)
        )
        RecordHumanReview(factory, clock=FixedClock()).execute(
            RecordHumanReviewCommand(
                proposal_id=submitted.proposal_id,
                reviewer_id=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
                decision=HumanReviewDecision.APPROVE,
            )
        )
        finalized = FinalizeFinding(factory, clock=FixedClock()).execute(
            FinalizeFindingCommand(
                proposal_id=submitted.proposal_id,
                decided_by=GATE14_HUMAN,
                actor_type=ActorType.HUMAN_OPERATOR,
            )
        )
        assert finalized.finding_id is not None
        reloaded = create_sync_engine(TEST_URL)
        try:
            with PostgresUnitOfWork(reloaded) as uow:
                run = uow.research_runs.get("run-1")
                experiment = uow.experiments.get("exp-1")
                repro = uow.experiments.get("exp-repro")
                attempts = uow.execution_attempts.list_for_experiment("exp-1")
                results = uow.worker_results.list_for_experiment("exp-1")
                observations = uow.observations.list_for_experiment("exp-1")
                evidence = uow.evidence.list_for_research_run("run-1")
                candidate = uow.candidates.get(candidate_id)
                verification = uow.verifications.get(verification_id)
                proposal = uow.finding_proposals.get(submitted.proposal_id)
                review = uow.human_reviews.get_for_proposal(submitted.proposal_id)
                finding = uow.findings.get(finalized.finding_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.authorization_source_id, "as-1")
            self.assertIsNotNone(experiment)
            self.assertIsNotNone(repro)
            self.assertNotEqual(experiment.experiment_id, repro.experiment_id)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(results[0].request_id, attempts[0].request_id)
            self.assertEqual(observations[0].observation_kind, "HTTP_AUTHORIZATION_DIFFERENTIAL")
            self.assertGreaterEqual(len(evidence), 2)
            assert candidate is not None
            self.assertEqual(candidate.state, "VALIDATED")
            assert verification is not None
            self.assertEqual(verification.outcome, "VALIDATED")
            assert proposal is not None
            self.assertEqual(proposal.state, "APPROVED")
            assert review is not None
            self.assertEqual(review.reviewer_id, GATE14_HUMAN)
            self.assertEqual(review.actor_type, ActorType.HUMAN_OPERATOR.value)
            assert finding is not None
            self.assertEqual(finding.claim, HTTP_CLAIM)
            self.assertEqual(finding.approval_id, finalized.approval_id)
        finally:
            reloaded.dispose()

    def test_20_no_codex_or_model_runtime_and_maturity_unchanged(self) -> None:
        self.assertEqual(GATE_14_STATUS, "PASS")
        self.assertEqual(GATE_04B_STATUS, "PASS")
        self.assertTrue(LIVE_MODEL_VALIDATED)
        self.assertFalse(SECURITY_RESEARCH_VALIDATED)
        self.assertFalse(PRODUCTION_READY)
        factory = self._factory()
        self._run_probe(
            factory, "exp-1", _plan(self._origin(), actor="alice", own="alice", cross="bob")
        )
        self.assertNotIn("research_os.integrations.models.cli_session", sys.modules)
        self.assertNotIn("openai", sys.modules)

    def test_alembic_head_includes_http_classification(self) -> None:
        assert self.engine is not None
        with self.engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        self.assertEqual(version, "a44_001_oast_correlation")


if __name__ == "__main__":
    unittest.main()
