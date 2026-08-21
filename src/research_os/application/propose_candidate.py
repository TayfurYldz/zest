"""Propose a Candidate from admitted Evidence. Application coordinates; Research decides.

Does not create Finding, Verification, or auto-promote Evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from research_os.application.errors import ApplicationError
from research_os.application.identity import new_opaque_id
from research_os.application.ports import Clock, SystemClock, UnitOfWorkFactory
from research_os.data.errors import PersistenceConflictError
from research_os.data.records import CandidateAdmissionRecord, CandidateRecord, EvidenceRecord
from research_os.data.uniqueness import (
    UQ_CANDIDATE_EVIDENCE_EVIDENCE_ID,
    is_uniqueness_conflict,
)
from research_os.research.candidate import (
    CANDIDATE_ADMISSION_POLICY_VERSION,
    CandidateAdmissionContext,
    CandidateAdmissionDecision,
    CandidateAdmissionOutcome,
    CandidateEvidenceRef,
    CandidateProposal,
    CandidateState,
    admit_candidate,
    propose_authorization_differential_candidate,
    propose_diagnostic_candidate,
    propose_state_transition_candidate,
)


@dataclass(frozen=True)
class ProposeCandidateFromEvidenceCommand:
    evidence_id: str
    proposal: CandidateProposal | None = None
    authoritative_out_of_scope: bool = False
    known_duplicate_candidate_id: str | None = None


@dataclass(frozen=True)
class ProposeCandidateFromEvidenceResult:
    outcome: CandidateAdmissionOutcome
    admission_record_id: str
    candidate_id: str | None
    reason_codes: tuple[str, ...]
    proposal_id: str
    state: CandidateState | None


class ProposeCandidateFromEvidence:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    def execute(
        self, command: ProposeCandidateFromEvidenceCommand
    ) -> ProposeCandidateFromEvidenceResult:
        with self._uow_factory.open() as uow:
            evidence = uow.evidence.get(command.evidence_id)
            if evidence is None:
                raise ApplicationError("evidence not found")
            existing = [
                item
                for item in uow.candidates.list_for_research_run(evidence.research_run_id)
                if evidence.evidence_id in item.evidence_ids
            ]
            if existing:
                record = sorted(existing, key=lambda item: item.created_at)[0]
                admission = uow.candidate_admissions.get(record.admission_record_id)
                uow.rollback()
                return ProposeCandidateFromEvidenceResult(
                    outcome=CandidateAdmissionOutcome.ADMITTED,
                    admission_record_id=record.admission_record_id,
                    candidate_id=record.candidate_id,
                    reason_codes=(
                        admission.reason_codes
                        if admission is not None
                        else ("CANDIDATE_ALREADY_ADMITTED",)
                    ),
                    proposal_id=(
                        admission.proposal_id
                        if admission is not None
                        else record.admission_record_id
                    ),
                    state=CandidateState(record.state),
                )
            context = _admission_context(
                evidence=evidence,
                requested_evidence_ids=(
                    command.proposal.evidence_ids if command.proposal is not None else ()
                ),
                authoritative_out_of_scope=command.authoritative_out_of_scope,
                known_duplicate_candidate_id=command.known_duplicate_candidate_id,
            )
            proposal = command.proposal
            if proposal is None:
                proposal = propose_diagnostic_candidate(
                    context, proposal_id=new_opaque_id()
                )
            if proposal is None:
                proposal = propose_authorization_differential_candidate(
                    context, proposal_id=new_opaque_id()
                )
            if proposal is None:
                proposal = propose_state_transition_candidate(
                    context, proposal_id=new_opaque_id()
                )
            if proposal is None:
                raise ApplicationError(
                    "no CandidateProposal can be built from this Evidence"
                )
            decision = admit_candidate(proposal, context)
            admission_record_id = new_opaque_id()
            candidate_id = new_opaque_id() if decision.creates_candidate else None
            if candidate_id is not None:
                assert decision.initial_state is CandidateState.OPEN
                try:
                    uow.candidates.insert(
                        CandidateRecord(
                            candidate_id=candidate_id,
                            research_run_id=proposal.research_run_id,
                            hypothesis_id=proposal.hypothesis_id,
                            claim=proposal.claim,
                            classification=proposal.classification,
                            state=decision.initial_state.value,
                            evidence_ids=proposal.evidence_ids,
                            created_at=self._clock.now(),
                            admission_record_id=admission_record_id,
                        )
                    )
                except PersistenceConflictError as exc:
                    if not is_uniqueness_conflict(
                        exc, UQ_CANDIDATE_EVIDENCE_EVIDENCE_ID
                    ):
                        raise
                    uow.rollback()
                    return self._reload_existing(command.evidence_id)
            uow.candidate_admissions.insert(
                _admission_record(
                    admission_record_id=admission_record_id,
                    candidate_id=candidate_id,
                    decision=decision,
                    created_at=self._clock.now(),
                )
            )
            uow.commit()
        return ProposeCandidateFromEvidenceResult(
            outcome=decision.outcome,
            admission_record_id=admission_record_id,
            candidate_id=candidate_id,
            reason_codes=decision.reason_codes,
            proposal_id=proposal.proposal_id,
            state=decision.initial_state,
        )


    def _reload_existing(self, evidence_id: str) -> ProposeCandidateFromEvidenceResult:
        with self._uow_factory.open() as uow:
            evidence = uow.evidence.get(evidence_id)
            if evidence is None:
                raise ApplicationError("evidence not found")
            existing = [
                item
                for item in uow.candidates.list_for_research_run(evidence.research_run_id)
                if evidence.evidence_id in item.evidence_ids
            ]
            if not existing:
                raise ApplicationError(
                    "candidate uniqueness conflict but no Candidate exists"
                )
            record = sorted(existing, key=lambda item: item.created_at)[0]
            admission = uow.candidate_admissions.get(record.admission_record_id)
            uow.rollback()
            return ProposeCandidateFromEvidenceResult(
                outcome=CandidateAdmissionOutcome.ADMITTED,
                admission_record_id=record.admission_record_id,
                candidate_id=record.candidate_id,
                reason_codes=(
                    admission.reason_codes
                    if admission is not None
                    else ("CANDIDATE_ALREADY_ADMITTED",)
                ),
                proposal_id=(
                    admission.proposal_id
                    if admission is not None
                    else record.admission_record_id
                ),
                state=CandidateState(record.state),
            )


def _admission_context(
    *,
    evidence: EvidenceRecord,
    requested_evidence_ids: tuple[str, ...],
    authoritative_out_of_scope: bool,
    known_duplicate_candidate_id: str | None,
) -> CandidateAdmissionContext:
    refs = (
        CandidateEvidenceRef(
            evidence_id=evidence.evidence_id,
            research_run_id=evidence.research_run_id,
            hypothesis_id=evidence.hypothesis_id,
            experiment_id=evidence.experiment_id,
            polarity=evidence.polarity,
            claim_scope=evidence.claim_scope,
        ),
    )
    present = {evidence.evidence_id}
    missing = tuple(item for item in requested_evidence_ids if item not in present)
    return CandidateAdmissionContext(
        research_run_id=evidence.research_run_id,
        hypothesis_id=evidence.hypothesis_id,
        evidence=refs,
        missing_evidence_ids=missing,
        authoritative_out_of_scope=authoritative_out_of_scope,
        known_duplicate_candidate_id=known_duplicate_candidate_id,
    )


def _admission_record(
    *,
    admission_record_id: str,
    candidate_id: str | None,
    decision: CandidateAdmissionDecision,
    created_at,
) -> CandidateAdmissionRecord:
    proposal = decision.proposal
    return CandidateAdmissionRecord(
        admission_record_id=admission_record_id,
        proposal_id=proposal.proposal_id,
        research_run_id=proposal.research_run_id,
        outcome=decision.outcome.value,
        reason_codes=decision.reason_codes,
        evidence_ids=proposal.evidence_ids,
        admission_policy_version=CANDIDATE_ADMISSION_POLICY_VERSION,
        created_at=created_at,
        admitted_candidate_id=candidate_id,
        claim=proposal.claim,
        classification=proposal.classification,
    )
