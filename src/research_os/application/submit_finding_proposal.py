"""Submit a FindingProposal from a VALIDATED Candidate. Application coordinates.

Does not create Finding, Approval, or Human Review. Does not auto-approve.
"""

from __future__ import annotations

from dataclasses import dataclass

from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.impact.proof_resolver import (
    UnitOfWorkProofResolver,
    rebuild_impact_chain,
)
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.application.validation_audit import read_validation_audit_view
from research_os.core.enums import ActorType
from research_os.data.errors import PersistenceConflictError
from research_os.data.records import AuditEventRecord, FindingProposalRecord
from research_os.data.uniqueness import UQ_FINDING_PROPOSAL_CANDIDATE, is_uniqueness_conflict
from research_os.data.unit_of_work import UnitOfWork
from research_os.research.candidate import (
    DIAGNOSTIC_CANDIDATE_CLASSIFICATION,
    CandidateState,
)
from research_os.research.impact.capability_map import validate_chain_impact_scope
from research_os.research.impact.validator import validate_chain
from research_os.research.finding_proposal import (
    FindingProposalAdmissionContext,
    FindingProposalAdmissionOutcome,
    FindingProposalDraft,
    FindingProposalState,
    admit_finding_proposal,
    propose_authorization_differential_finding_proposal,
    propose_diagnostic_finding_proposal,
    propose_state_transition_finding_proposal,
)


FINDING_VALIDATION_REJECTED = "FINDING_PROPOSAL_VALIDATION_REJECTED"


@dataclass(frozen=True)
class SubmitFindingProposalCommand:
    candidate_id: str
    draft: FindingProposalDraft | None = None


@dataclass(frozen=True)
class SubmitFindingProposalResult:
    outcome: FindingProposalAdmissionOutcome
    proposal_id: str | None
    state: FindingProposalState | None
    reason_codes: tuple[str, ...]


class SubmitFindingProposal:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        actor_id: str = "control-plane",
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._actor_id = actor_id

    def execute(self, command: SubmitFindingProposalCommand) -> SubmitFindingProposalResult:
        with self._uow_factory.open() as uow:
            candidate = uow.candidates.get(command.candidate_id)
            if candidate is None:
                raise ApplicationError("candidate not found")
            try:
                state = CandidateState(candidate.state)
            except ValueError as exc:
                raise ApplicationError("candidate state is not a CandidateState") from exc
            existing_proposals = uow.finding_proposals.list_for_candidate(
                candidate.candidate_id
            )
            if existing_proposals:
                record = existing_proposals[0]
                uow.rollback()
                return SubmitFindingProposalResult(
                    outcome=FindingProposalAdmissionOutcome.ADMITTED,
                    proposal_id=record.proposal_id,
                    state=FindingProposalState(record.state),
                    reason_codes=("FINDING_PROPOSAL_ALREADY_RECORDED",),
                )
            verifications = uow.verifications.list_for_candidate(candidate.candidate_id)
            context = FindingProposalAdmissionContext(
                candidate_id=candidate.candidate_id,
                candidate_state=state,
                research_run_id=candidate.research_run_id,
                evidence_ids=candidate.evidence_ids,
                verification_ids=tuple(item.verification_id for item in verifications),
                classification=candidate.classification,
            )
            draft = command.draft
            if draft is None:
                draft = propose_diagnostic_finding_proposal(
                    context, proposal_id=new_opaque_id()
                )
            if draft is None:
                draft = propose_authorization_differential_finding_proposal(
                    context, proposal_id=new_opaque_id()
                )
            if draft is None:
                draft = propose_state_transition_finding_proposal(
                    context, proposal_id=new_opaque_id()
                )
            if draft is None:
                raise ApplicationError(
                    "no FindingProposal can be built from this Candidate"
                )
            decision = admit_finding_proposal(draft, context)
            proposal_id = draft.proposal_id if decision.creates_proposal else None
            if proposal_id is not None:
                assert decision.initial_state is FindingProposalState.PROPOSED
                validation = _validate_security_candidate_tiers(uow, candidate)
                if validation is not None and not validation.admitted:
                    uow.audit_events.insert(
                        AuditEventRecord(
                            audit_event_id=new_opaque_id(),
                            occurred_at=self._clock.now(),
                            actor_id=self._actor_id,
                            actor_type=ActorType.CONTROL_PLANE.value,
                            event_type=FINDING_VALIDATION_REJECTED,
                            subject_type="candidate",
                            subject_id=candidate.candidate_id,
                            correlation_id=candidate.research_run_id,
                            payload={
                                "hypothesis_id": candidate.hypothesis_id,
                                "classification": candidate.classification,
                                "outcome": validation.outcome.value,
                                "reason_codes": list(validation.reason_codes),
                                "required_tiers": [
                                    item.value for item in validation.required_tiers
                                ],
                                "not_a_finding": True,
                            },
                        )
                    )
                    uow.commit()
                    return SubmitFindingProposalResult(
                        outcome=FindingProposalAdmissionOutcome.REJECTED_VALIDATION_NOT_PASSED,
                        proposal_id=None,
                        state=None,
                        reason_codes=validation.reason_codes,
                    )
                impact_chain_ids = _validate_impact_claims(
                    uow, draft, draft.research_run_id
                )
                try:
                    uow.finding_proposals.insert(
                        FindingProposalRecord(
                            proposal_id=proposal_id,
                            candidate_id=draft.candidate_id,
                            research_run_id=draft.research_run_id,
                            title=draft.title,
                            claim=draft.claim,
                            classification=candidate.classification,
                            state=decision.initial_state.value,
                            evidence_ids=draft.evidence_ids,
                            verification_ids=draft.verification_ids,
                            content_fingerprint=draft.content_fingerprint,
                            impact_chain_ids=impact_chain_ids,
                            created_at=self._clock.now(),
                        )
                    )
                except PersistenceConflictError as exc:
                    if not is_uniqueness_conflict(
                        exc, UQ_FINDING_PROPOSAL_CANDIDATE
                    ):
                        raise
                    uow.rollback()
                    return self._reload_existing(command.candidate_id)
                uow.audit_events.insert(
                    AuditEventRecord(
                        audit_event_id=new_opaque_id(),
                        occurred_at=self._clock.now(),
                        actor_id=self._actor_id,
                        actor_type=ActorType.CONTROL_PLANE.value,
                        event_type="FINDING_PROPOSAL_CREATED",
                        subject_type="finding_proposal",
                        subject_id=proposal_id,
                        payload={
                            "candidate_id": candidate.candidate_id,
                            "not_a_vulnerability": True,
                        },
                    )
                )
            uow.commit()
        return SubmitFindingProposalResult(
            outcome=decision.outcome,
            proposal_id=proposal_id,
            state=decision.initial_state,
            reason_codes=decision.reason_codes,
        )

    def _reload_existing(self, candidate_id: str) -> SubmitFindingProposalResult:
        with self._uow_factory.open() as uow:
            existing = uow.finding_proposals.list_for_candidate(candidate_id)
            uow.rollback()
        if not existing:
            raise ApplicationError(
                "finding proposal uniqueness conflict but no FindingProposal exists"
            )
        record = existing[0]
        return SubmitFindingProposalResult(
            outcome=FindingProposalAdmissionOutcome.ADMITTED,
            proposal_id=record.proposal_id,
            state=FindingProposalState(record.state),
            reason_codes=("FINDING_PROPOSAL_ALREADY_RECORDED",),
        )


def _validate_impact_claims(
    uow: UnitOfWork,
    draft: FindingProposalDraft,
    expected_run_id: str,
) -> tuple[str, ...]:
    if not draft.impact_claims:
        return ()
    resolver = UnitOfWorkProofResolver(uow)
    chain_ids: list[str] = []
    for claim in draft.impact_claims:
        record = uow.impact_chains.get(claim.chain_id)
        if record is None:
            raise ApplicationError(f"impact chain not found: {claim.chain_id}")
        if record.research_run_id != expected_run_id:
            raise ApplicationError(
                f"impact chain cross-run: {claim.chain_id}"
            )
        chain = rebuild_impact_chain(uow, record)
        structural = validate_chain(chain, resolver, expected_run_id)
        if not structural.valid:
            raise ApplicationError(
                f"impact chain validation failed for {claim.chain_id}: {structural.reason_codes}"
            )
        scope = validate_chain_impact_scope(chain, resolver, expected_run_id)
        if not scope.valid:
            raise ApplicationError(
                f"impact scope validation failed for {claim.chain_id}: {scope.reason_codes}"
            )
        chain_ids.append(claim.chain_id)
    return tuple(chain_ids)


def _validate_security_candidate_tiers(uow: UnitOfWork, candidate):
    if candidate.classification == DIAGNOSTIC_CANDIDATE_CLASSIFICATION:
        return None
    return read_validation_audit_view(uow, candidate).decision
