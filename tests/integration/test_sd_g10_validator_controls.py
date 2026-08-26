"""SD-G10 validator, severity, and circuit-breaker integration tests.

PostgreSQL required. SQLite is not a substitute. Skipped when
ZEST_TEST_DATABASE_URL is absent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from integration.harness import (
    NOW,
    FixedClock,
    PostgresUnitOfWorkFactory,
    alembic_upgrade,
    configured_test_url,
    seed_authorized_spine,
    truncate_spine,
    warn_destructive,
)
from zest.application.evaluate_family_circuit_breaker import (
    EvaluateFamilyCircuitBreaker,
    EvaluateFamilyCircuitBreakerCommand,
)
from zest.application.impact.proof_resolver import UnitOfWorkProofResolver
from zest.application.impact.register_impact_chain import (
    RegisterImpactChain,
    RegisterImpactChainCommand,
)
from zest.application.score_finding_severity import (
    ScoreFindingSeverity,
    ScoreFindingSeverityCommand,
)
from zest.application.submit_finding_proposal import (
    SubmitFindingProposal,
    SubmitFindingProposalCommand,
)
from zest.data.postgres.engine import create_sync_engine
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuditEventRecord,
    CandidateAdmissionRecord,
    CandidateRecord,
    EvidenceAdmissionRecord,
    EvidenceRecord,
    FindingProposalRecord,
    HypothesisAssessmentRecord,
    ObservationRecord,
    VerificationRecord,
    WorkerResultRecord,
)
from zest.research.finding_proposal import (
    FindingProposalDraft,
    FindingProposalState,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
    ImpactClaim,
)
from zest.research.impact.chain import ImpactChain, ImpactNode, ImpactScopeRef
from zest.research.impact.types import ImpactKind
from zest.research.validation.circuit_breaker import CircuitBreakerAction
from zest.research.validation.severity import InternalSeverity

TEST_URL = configured_test_url()

_CLAIM_SCOPE = (
    "Authenticated actor can read another actor's account object because object "
    "authorization is missing on the vulnerable endpoint."
)


def _seed_candidate(uow: PostgresUnitOfWork) -> None:
    uow.worker_results.insert(
        WorkerResultRecord(
            worker_result_id="wr-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            request_id="req-1",
            correlation_id="corr-1",
            worker_capability="http.transaction",
            action="get",
            authorization_decision_reference="ad-1",
            budget_id="budget-1",
            side_effect_level=0,
            contract_version="http.transaction.v1",
            worker_id="worker-1",
            status="SUCCEEDED",
            received_at=NOW,
            started_at=NOW,
            completed_at=NOW,
        )
    )
    uow.observations.insert(
        ObservationRecord(
            observation_id="obs-1",
            worker_result_id="wr-1",
            observation_kind="HTTP_RESPONSE",
            payload={"status_code": 200, "body_digest": "abc"},
            normalization_version="http.response.v1",
            observed_at=NOW,
            created_at=NOW,
        )
    )
    uow.hypothesis_assessments.insert(
        HypothesisAssessmentRecord(
            assessment_id="ass-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            research_run_id="run-1",
            assessment_outcome="CONSISTENT_WITH_PREDICTION",
            observation_ids=("obs-1",),
            evaluator_kind="DETERMINISTIC",
            evaluator_version="deterministic.v1",
            rationale={"detail": "response matches expectation"},
            evaluation_strategy="expected_status_code",
            created_at=NOW,
        )
    )
    uow.evidence.insert(
        EvidenceRecord(
            evidence_id="ev-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            experiment_id="exp-1",
            admission_record_id="ea-1",
            polarity="SUPPORTING",
            claim_scope=_CLAIM_SCOPE,
            observation_ids=("obs-1",),
            assessment_ids=("ass-1",),
            created_at=NOW,
        )
    )
    uow.evidence_admissions.insert(
        EvidenceAdmissionRecord(
            admission_record_id="ea-1",
            proposal_id="ep-1",
            research_run_id="run-1",
            outcome="ADMITTED",
            reason_codes=("HTTP_AUTHORIZATION_DIFFERENTIAL_PROVENANCE_INTACT",),
            observation_ids=("obs-1",),
            assessment_ids=("ass-1",),
            admission_policy_version="evidence.admission.v1",
            evaluator_version="deterministic.v1",
            created_at=NOW,
            admitted_evidence_id="ev-1",
            claim_scope=_CLAIM_SCOPE,
            polarity="SUPPORTING",
        )
    )
    uow.candidates.insert(
        CandidateRecord(
            candidate_id="cand-1",
            research_run_id="run-1",
            hypothesis_id="hyp-1",
            claim="missing object authorization",
            classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
            state="VALIDATED",
            evidence_ids=("ev-1",),
            admission_record_id="ca-1",
            created_at=NOW,
        )
    )
    uow.candidate_admissions.insert(
        CandidateAdmissionRecord(
            admission_record_id="ca-1",
            proposal_id="cp-1",
            research_run_id="run-1",
            outcome="ADMITTED",
            reason_codes=("CANDIDATE_ADMITTED",),
            evidence_ids=("ev-1",),
            admission_policy_version="candidate.admission.v1",
            created_at=NOW,
            admitted_candidate_id="cand-1",
            claim="missing object authorization",
            classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
        )
    )
    uow.verifications.insert(
        VerificationRecord(
            verification_id="ver-1",
            candidate_id="cand-1",
            research_run_id="run-1",
            strategy="http.authorization.differential",
            outcome="VALIDATED",
            proposed_candidate_state="VALIDATED",
            original_evidence_ids=("ev-1",),
            reproduction_evidence_ids=("ev-1",),
            negative_control_evidence_ids=(),
            alternative_explanation_checks={},
            verifier_kind="DETERMINISTIC",
            verifier_identity="deterministic.authorization.differential.v1",
            created_at=NOW,
        )
    )


def _seed_tier_events(
    uow: PostgresUnitOfWork,
    outcomes: tuple[tuple[str, str], ...],
    *,
    family_id: str = "hf-object-authz",
    hypothesis_id: str = "hyp-1",
    scope_state: str | None = None,
) -> None:
    for tier, outcome in outcomes:
        payload = {
            "research_run_id": "run-1",
            "family_id": family_id,
            "tier": tier,
            "outcome": outcome,
            "reason_code": f"{tier}_{outcome}",
            "node_canonical_key": "origin:https://example.com|path:/api/accounts|method:GET",
        }
        if scope_state is not None:
            payload["scope_classification"] = scope_state
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=f"audit-{hypothesis_id}-{tier.lower()}-{outcome.lower()}",
                occurred_at=NOW,
                actor_id="control-plane:hunt-validation",
                actor_type="CONTROL_PLANE",
                event_type="HUNT_TIER_DECISION",
                subject_type="hypothesis",
                subject_id=hypothesis_id,
                correlation_id="run-1",
                payload=payload,
            )
        )


def _impact_chain(kind: ImpactKind = ImpactKind.DATA_READ) -> ImpactChain:
    return ImpactChain(
        chain_id="chain-1",
        nodes=(
            ImpactNode(
                node_id="impact-node-1",
                proof_refs=("ev-1",),
                impact_kind=kind,
                claim_text="attacker can read another user's object",
                scope_ref=ImpactScopeRef(
                    research_run_id="run-1",
                    program_id="prog-1",
                    target_reference="https://example.com",
                ),
                provenance={"source": "test"},
            ),
        ),
        edges=(),
    )


def _draft_with_chain() -> FindingProposalDraft:
    return FindingProposalDraft(
        proposal_id="fp-1",
        candidate_id="cand-1",
        research_run_id="run-1",
        title=HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
        claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
        evidence_ids=("ev-1",),
        verification_ids=("ver-1",),
        rationale={"reason_code": "AUTHORIZED_LOCAL_LAB_PROPOSAL", "not_a_finding": True},
        provenance={"source": "test"},
        impact_claims=(
            ImpactClaim(
                claim_text="cross-object read",
                impact_kind="DATA_READ",
                chain_id="chain-1",
            ),
        ),
    )


def _register_chain(uow_factory: PostgresUnitOfWorkFactory) -> None:
    with uow_factory.open() as uow:
        resolver = UnitOfWorkProofResolver(uow)
        RegisterImpactChain(uow_factory, clock=FixedClock()).execute(
            RegisterImpactChainCommand(
                chain_id="chain-1",
                research_run_id="run-1",
                program_id="prog-1",
                chain=_impact_chain(),
            ),
            resolver,
        )
        uow.rollback()


def _submit_proposal(uow_factory: PostgresUnitOfWorkFactory) -> str:
    result = SubmitFindingProposal(uow_factory, clock=FixedClock()).execute(
        SubmitFindingProposalCommand(candidate_id="cand-1", draft=_draft_with_chain())
    )
    assert result.proposal_id is not None
    return result.proposal_id


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG10ValidatorControlsIntegrationTests(unittest.TestCase):
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
            _seed_candidate(uow)
            uow.commit()

    def test_v1_v2_missing_blocks_finding_proposal_admission(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_tier_events(uow, (("V3", "PASSED"),), scope_state="IN_SCOPE")
            uow.commit()

        result = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id="cand-1")
        )

        self.assertIsNone(result.proposal_id)
        self.assertEqual(result.reason_codes, ("V1_MISSING", "V2_MISSING"))

    def test_v3_queued_is_not_validator_pass_postgres(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_tier_events(
                uow,
                (("V1", "PASSED"), ("V2", "PASSED"), ("V3", "QUEUED")),
                scope_state="IN_SCOPE",
            )
            uow.commit()

        result = SubmitFindingProposal(factory, clock=FixedClock()).execute(
            SubmitFindingProposalCommand(candidate_id="cand-1")
        )

        self.assertIsNone(result.proposal_id)
        self.assertEqual(result.reason_codes, ("V3_QUEUED",))

    def test_validated_in_scope_impact_receives_deterministic_severity(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_tier_events(
                uow,
                (("V1", "PASSED"), ("V2", "PASSED"), ("V3", "PASSED")),
                scope_state="IN_SCOPE",
            )
            uow.commit()
        _register_chain(factory)
        proposal_id = _submit_proposal(factory)

        scored = ScoreFindingSeverity(factory, clock=FixedClock()).execute(
            ScoreFindingSeverityCommand(proposal_id=proposal_id)
        )

        self.assertTrue(scored.scored)
        self.assertEqual(scored.severity, InternalSeverity.P2)
        with PostgresUnitOfWork(self.engine) as uow:
            proposal = uow.finding_proposals.get(proposal_id)
            events = uow.audit_events.list_for_subject_type("finding_proposal")
            uow.rollback()
        assert proposal is not None
        self.assertFalse(hasattr(proposal, "severity"))
        event = next(item for item in events if item.audit_event_id == scored.audit_event_id)
        self.assertEqual(event.event_type, "FINDING_SEVERITY_SCORED")
        self.assertEqual(event.payload["severity"], "P2")
        self.assertEqual(event.payload["platform_mapping"]["bugcrowd_priority"], "P3")

    def test_caller_admin_scope_cannot_escalate_data_read_to_p0(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_tier_events(
                uow,
                (("V1", "PASSED"), ("V2", "PASSED"), ("V3", "PASSED")),
                scope_state="IN_SCOPE",
            )
            uow.commit()
        _register_chain(factory)
        proposal_id = _submit_proposal(factory)
        scored = ScoreFindingSeverity(factory, clock=FixedClock()).execute(
            ScoreFindingSeverityCommand(
                proposal_id=proposal_id,
                data_sensitivity="BULK_SENSITIVE",
                affected_scope="ADMIN",
            )
        )
        self.assertTrue(scored.scored)
        self.assertEqual(scored.severity, InternalSeverity.P2)
        self.assertIn("CALLER_SEVERITY_CLAMPED", scored.reason_codes)

    def test_out_of_scope_or_validation_missing_severity_is_not_scored(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            _seed_tier_events(
                uow,
                (("V1", "PASSED"), ("V2", "PASSED"), ("V3", "PASSED")),
                scope_state="OUT_OF_SCOPE",
            )
            uow.commit()
        _register_chain(factory)
        proposal_id = _submit_proposal(factory)

        out_of_scope = ScoreFindingSeverity(factory, clock=FixedClock()).execute(
            ScoreFindingSeverityCommand(proposal_id=proposal_id)
        )
        self.assertFalse(out_of_scope.scored)
        self.assertEqual(out_of_scope.reason_codes, ("SEVERITY_REJECTED_NOT_IN_SCOPE",))

        truncate_spine(self.engine)
        with PostgresUnitOfWork(self.engine) as uow:
            seed_authorized_spine(uow)
            _seed_candidate(uow)
            uow.finding_proposals.insert(
                FindingProposalRecord(
                    proposal_id="fp-legacy",
                    candidate_id="cand-1",
                    research_run_id="run-1",
                    title=HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
                    claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
                    classification="HTTP_AUTHORIZATION_DIFFERENTIAL",
                    state=FindingProposalState.PROPOSED.value,
                    evidence_ids=("ev-1",),
                    verification_ids=("ver-1",),
                    content_fingerprint="legacy-proposal-fingerprint",
                    created_at=NOW,
                )
            )
            uow.commit()

        missing_validation = ScoreFindingSeverity(factory, clock=FixedClock()).execute(
            ScoreFindingSeverityCommand(proposal_id="fp-legacy")
        )
        self.assertFalse(missing_validation.scored)
        self.assertEqual(
            missing_validation.reason_codes,
            ("SEVERITY_REJECTED_VALIDATION_NOT_PASSED",),
        )

    def test_family_circuit_breaker_throttles_noisy_family_without_disabling(self) -> None:
        factory = PostgresUnitOfWorkFactory(self.engine)
        with factory.open() as uow:
            for index, outcome in enumerate(
                (
                    "PASSED",
                    "PASSED",
                    "PASSED",
                    "REJECTED",
                    "REJECTED",
                    "REJECTED",
                    "REJECTED",
                    "INCONCLUSIVE",
                    "INCONCLUSIVE",
                    "INCONCLUSIVE",
                )
            ):
                _seed_tier_events(
                    uow,
                    (("V3", outcome),),
                    hypothesis_id=f"hyp-cb-{index}",
                    scope_state="IN_SCOPE",
                )
            uow.commit()

        decision = EvaluateFamilyCircuitBreaker(factory, clock=FixedClock()).execute(
            EvaluateFamilyCircuitBreakerCommand(
                research_run_id="run-1",
                family_id="hf-object-authz",
            )
        )

        self.assertEqual(decision.action, CircuitBreakerAction.THROTTLE)
        self.assertTrue(decision.throttle)
        self.assertFalse(decision.disable_family)
        self.assertEqual(decision.supported_count, 3)
        self.assertEqual(decision.rejected_count, 4)
        self.assertEqual(decision.inconclusive_count, 3)
        with PostgresUnitOfWork(self.engine) as uow:
            family = uow.hunter_families.get("hf-object-authz", 1)
            events = uow.audit_events.list_for_subject_type("hunter_family")
            uow.rollback()
        assert family is not None
        self.assertTrue(family.enabled)
        event = next(item for item in events if item.audit_event_id == decision.audit_event_id)
        self.assertEqual(event.payload["action"], "THROTTLE")
        self.assertFalse(event.payload["disable_family"])
        self.assertIn("FAMILY_BAD_OUTCOME_RATE_THROTTLE", event.payload["reason_codes"])
        self.assertEqual(event.payload["bad_outcome_rate"], 0.7)


if __name__ == "__main__":
    unittest.main()
