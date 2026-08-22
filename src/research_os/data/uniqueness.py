"""Named PostgreSQL uniqueness constraints used for durable idempotency.

Callers may catch PersistenceConflictError only when constraint_name matches
one of these. Other unique conflicts remain failures.
"""

UQ_EVIDENCE_EXPERIMENT_SUPPORTING = "uq_evidence_experiment_supporting"
UQ_CANDIDATE_EVIDENCE_EVIDENCE_ID = "uq_candidate_evidence_evidence_id"
UQ_VERIFICATION_CANDIDATE = "uq_verification_candidate"
UQ_FINDING_PROPOSAL_CANDIDATE = "uq_finding_proposal_candidate"
UQ_PROMOTION_RUN_REPRODUCTION_EXPERIMENT = "uq_promotion_run_reproduction_experiment"
UQ_PROMOTION_RUN_ASSESSMENT = "uq_promotion_run_assessment"
UQ_RESEARCH_CYCLE_RUN_NUMBER = "uq_research_cycle_run_number"


def is_uniqueness_conflict(exc: BaseException, constraint_name: str) -> bool:
    from research_os.data.errors import PersistenceConflictError

    return (
        isinstance(exc, PersistenceConflictError)
        and exc.constraint_name == constraint_name
    )
