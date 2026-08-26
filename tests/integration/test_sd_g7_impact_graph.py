"""SD-G7 ImpactGraph integration.

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
from zest.application.errors import ApplicationError
from zest.application.impact.proof_resolver import UnitOfWorkProofResolver
from sqlalchemy import text
from zest.application.impact.register_impact_chain import (
    RegisterImpactChain,
    RegisterImpactChainCommand,
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
    HypothesisAssessmentRecord,
    ObservationRecord,
    VerificationRecord,
    WorkerResultRecord,
)
from zest.research.finding_proposal import (
    FindingProposalDraft,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
    HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
    ImpactClaim,
)
from zest.research.impact.chain import ImpactChain, ImpactNode, ImpactScopeRef
from zest.research.impact.types import ImpactKind

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


def _seed_validator_pass(uow: PostgresUnitOfWork) -> None:
    for tier in ("V1", "V2", "V3"):
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=f"audit-{tier.lower()}-pass",
                occurred_at=NOW,
                actor_id="control-plane:hunt-validation",
                actor_type="CONTROL_PLANE",
                event_type="HUNT_TIER_DECISION",
                subject_type="hypothesis",
                subject_id="hyp-1",
                correlation_id="run-1",
                payload={
                    "research_run_id": "run-1",
                    "family_id": "hf-object-authz",
                    "tier": tier,
                    "outcome": "PASSED",
                    "reason_code": f"{tier}_PASSED",
                    "node_canonical_key": "origin:https://example.com|path:/api/accounts|method:GET",
                },
            )
        )


def _impact_node(proof_refs: tuple[str, ...], kind: ImpactKind) -> ImpactNode:
    return ImpactNode(
        node_id="n1",
        proof_refs=proof_refs,
        impact_kind=kind,
        claim_text="attacker can read another user's object",
        scope_ref=ImpactScopeRef(
            research_run_id="run-1",
            program_id="prog-1",
            target_reference="https://example.com",
        ),
        provenance={"source": "test"},
    )


def _impact_chain(kind: ImpactKind = ImpactKind.DATA_READ) -> ImpactChain:
    return ImpactChain(
        chain_id="chain-1",
        nodes=(_impact_node(("ev-1",), kind),),
        edges=(),
    )


def _draft_with_chain(research_run_id: str = "run-1") -> FindingProposalDraft:
    return FindingProposalDraft(
        proposal_id="fp-1",
        candidate_id="cand-1",
        research_run_id=research_run_id,
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


@unittest.skipUnless(
    TEST_URL,
    "ZEST_TEST_DATABASE_URL is not configured; PostgreSQL integration tests skipped",
)
class SDG7ImpactGraphIntegrationTests(unittest.TestCase):
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

    def test_register_impact_chain_persists_nodes_and_edges(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        register = RegisterImpactChain(uow_factory, clock=FixedClock())
        chain = _impact_chain()
        with uow_factory.open() as uow:
            _seed_validator_pass(uow)
            resolver = UnitOfWorkProofResolver(uow)
            register.execute(
                RegisterImpactChainCommand(
                    chain_id=chain.chain_id,
                    research_run_id="run-1",
                    program_id="prog-1",
                    chain=chain,
                ),
                resolver,
            )
            uow.commit()

        with PostgresUnitOfWork(self.engine) as uow:
            record = uow.impact_chains.get("chain-1")
            nodes = uow.impact_chains.get_nodes("chain-1")
            edges = uow.impact_chains.get_edges("chain-1")
            uow.rollback()

        self.assertIsNotNone(record)
        self.assertEqual(record.research_run_id, "run-1")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].impact_kind, "DATA_READ")
        self.assertEqual(len(edges), 0)

    def test_submit_finding_proposal_with_valid_chain(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        register = RegisterImpactChain(uow_factory, clock=FixedClock())
        submit = SubmitFindingProposal(uow_factory, clock=FixedClock())

        with uow_factory.open() as uow:
            _seed_validator_pass(uow)
            resolver = UnitOfWorkProofResolver(uow)
            register.execute(
                RegisterImpactChainCommand(
                    chain_id="chain-1",
                    research_run_id="run-1",
                    program_id="prog-1",
                    chain=_impact_chain(),
                ),
                resolver,
            )
            uow.commit()

        result = submit.execute(
            SubmitFindingProposalCommand(
                candidate_id="cand-1",
                draft=_draft_with_chain(),
            )
        )
        self.assertIsNotNone(result.proposal_id)

        with PostgresUnitOfWork(self.engine) as uow:
            proposal = uow.finding_proposals.get(result.proposal_id)
            uow.rollback()
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.impact_chain_ids, ("chain-1",))

    def test_submit_finding_proposal_rejects_missing_validator_pass(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        submit = SubmitFindingProposal(uow_factory, clock=FixedClock())

        result = submit.execute(
            SubmitFindingProposalCommand(
                candidate_id="cand-1",
                draft=FindingProposalDraft(
                    proposal_id="fp-no-validator",
                    candidate_id="cand-1",
                    research_run_id="run-1",
                    title=HTTP_AUTHORIZATION_DIFFERENTIAL_FINDING_TITLE,
                    claim=HTTP_AUTHORIZATION_DIFFERENTIAL_CANDIDATE_CLAIM,
                    evidence_ids=("ev-1",),
                    verification_ids=("ver-1",),
                    rationale={
                        "reason_code": "AUTHORIZED_LOCAL_LAB_PROPOSAL",
                        "not_a_finding": True,
                    },
                    provenance={"source": "test"},
                ),
            )
        )

        self.assertEqual(result.proposal_id, None)
        self.assertEqual(result.reason_codes, ("V1_MISSING", "V2_MISSING", "V3_MISSING"))
        with PostgresUnitOfWork(self.engine) as uow:
            proposals = uow.finding_proposals.list_for_research_run("run-1")
            events = uow.audit_events.list_for_subject_type("candidate")
            uow.rollback()
        self.assertEqual(proposals, [])
        self.assertTrue(
            any(event.event_type == "FINDING_PROPOSAL_VALIDATION_REJECTED" for event in events)
        )

    def test_submit_finding_proposal_rejects_missing_chain(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        submit = SubmitFindingProposal(uow_factory, clock=FixedClock())

        with uow_factory.open() as uow:
            _seed_validator_pass(uow)
            uow.commit()

        with self.assertRaises(ApplicationError) as ctx:
            submit.execute(
                SubmitFindingProposalCommand(
                    candidate_id="cand-1",
                    draft=_draft_with_chain(),
                )
            )
        self.assertIn("impact chain not found", str(ctx.exception))

    def test_submit_finding_proposal_rejects_exaggerated_impact(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        register = RegisterImpactChain(uow_factory, clock=FixedClock())
        submit = SubmitFindingProposal(uow_factory, clock=FixedClock())

        chain = _impact_chain(ImpactKind.DATA_WRITE)
        with uow_factory.open() as uow:
            _seed_validator_pass(uow)
            resolver = UnitOfWorkProofResolver(uow)
            register.execute(
                RegisterImpactChainCommand(
                    chain_id=chain.chain_id,
                    research_run_id="run-1",
                    program_id="prog-1",
                    chain=chain,
                ),
                resolver,
            )
            uow.commit()

        with self.assertRaises(ApplicationError) as ctx:
            submit.execute(
                SubmitFindingProposalCommand(
                    candidate_id="cand-1",
                    draft=_draft_with_chain(),
                )
            )
        self.assertIn("impact scope validation failed", str(ctx.exception))

    def test_register_impact_chain_rejects_cross_run_proof(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        register = RegisterImpactChain(uow_factory, clock=FixedClock())

        with self.assertRaises(ApplicationError) as ctx:
            with uow_factory.open() as uow:
                resolver = UnitOfWorkProofResolver(uow)
                register.execute(
                    RegisterImpactChainCommand(
                        chain_id="chain-1",
                        research_run_id="run-2",
                        program_id="prog-1",
                        chain=_impact_chain(),
                    ),
                    resolver,
                )
        self.assertIn("CROSS_RUN_PROOF", str(ctx.exception))

    def test_submit_finding_proposal_rejects_cross_run_chain(self) -> None:
        uow_factory = PostgresUnitOfWorkFactory(self.engine)
        register = RegisterImpactChain(uow_factory, clock=FixedClock())
        submit = SubmitFindingProposal(uow_factory, clock=FixedClock())

        with uow_factory.open() as uow:
            _seed_validator_pass(uow)
            resolver = UnitOfWorkProofResolver(uow)
            register.execute(
                RegisterImpactChainCommand(
                    chain_id="chain-1",
                    research_run_id="run-1",
                    program_id="prog-1",
                    chain=_impact_chain(),
                ),
                resolver,
            )
            uow.commit()

        # Simulate a chain record that was persisted under a different run.
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO research_run (research_run_id, program_id, authorization_source_id, "
                    "initiated_by_actor_id, initiated_by_actor_type, started_at) "
                    "VALUES ('run-2', 'prog-1', 'as-1', 'operator-1', 'HUMAN_OPERATOR', :started_at)"
                ),
                {"started_at": NOW},
            )
            connection.execute(
                text(
                    "UPDATE impact_chain SET research_run_id = 'run-2' WHERE chain_id = 'chain-1'"
                )
            )

        with self.assertRaises(ApplicationError) as ctx:
            submit.execute(
                SubmitFindingProposalCommand(
                    candidate_id="cand-1",
                    draft=_draft_with_chain(),
                )
            )
        self.assertIn("impact chain cross-run", str(ctx.exception))
