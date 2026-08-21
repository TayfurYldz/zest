"""Start Candidate verification. Application coordinates; Research owns the transition.

Does not run Workers. Does not create Finding. Does not invent VALIDATED.
"""

from __future__ import annotations

from dataclasses import dataclass

from research_os.application.errors import ApplicationError
from research_os.application.ports import UnitOfWorkFactory
from research_os.research.candidate import (
    HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION,
    HTTP_STATE_TRANSITION_CLASSIFICATION,
    CandidateState,
    start_candidate_verification,
)
from research_os.research.types import ResearchInputError
from research_os.research.verification import (
    VerificationPlan,
    plan_authorization_differential_verification,
    plan_diagnostic_verification,
    plan_state_transition_verification,
)


@dataclass(frozen=True)
class StartCandidateVerificationCommand:
    candidate_id: str


@dataclass(frozen=True)
class StartCandidateVerificationResult:
    candidate_id: str
    state: CandidateState
    plan: VerificationPlan


class StartCandidateVerification:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def execute(
        self, command: StartCandidateVerificationCommand
    ) -> StartCandidateVerificationResult:
        with self._uow_factory.open() as uow:
            candidate = uow.candidates.get(command.candidate_id)
            if candidate is None:
                raise ApplicationError("candidate not found")
            try:
                current = CandidateState(candidate.state)
            except ValueError as exc:
                raise ApplicationError("candidate state is not a CandidateState") from exc
            if current is CandidateState.VERIFYING:
                if candidate.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION:
                    plan = plan_authorization_differential_verification(
                        candidate.candidate_id, candidate.evidence_ids
                    )
                elif candidate.classification == HTTP_STATE_TRANSITION_CLASSIFICATION:
                    plan = plan_state_transition_verification(
                        candidate.candidate_id, candidate.evidence_ids
                    )
                else:
                    plan = plan_diagnostic_verification(
                        candidate.candidate_id, candidate.evidence_ids
                    )
                uow.rollback()
                return StartCandidateVerificationResult(
                    candidate_id=candidate.candidate_id,
                    state=current,
                    plan=plan,
                )
            try:
                next_state = start_candidate_verification(current)
            except ResearchInputError as exc:
                raise ApplicationError(str(exc)) from exc
            if candidate.classification == HTTP_AUTHORIZATION_DIFFERENTIAL_CLASSIFICATION:
                plan = plan_authorization_differential_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
            elif candidate.classification == HTTP_STATE_TRANSITION_CLASSIFICATION:
                plan = plan_state_transition_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
            else:
                plan = plan_diagnostic_verification(
                    candidate.candidate_id, candidate.evidence_ids
                )
            uow.candidates.set_state(candidate.candidate_id, next_state.value)
            uow.commit()
        return StartCandidateVerificationResult(
            candidate_id=candidate.candidate_id,
            state=next_state,
            plan=plan,
        )
