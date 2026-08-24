"""Data: authoritative PostgreSQL persistence spine (Decision 020).

Python records/ports here are not language-neutral contracts.
SQLAlchemy/psycopg live only in `research_os.data.postgres`.
"""

from research_os.data.errors import (
    DatabaseUnavailableError,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
)
from research_os.data.records import (
    AuditEventRecord,
    AuthorizationSourceRecord,
    ExecutionAttemptRecord,
    ExecutionAttemptState,
    ExperimentExecutionState,
    ExperimentPlanRecord,
    ExperimentRecord,
    HypothesisAssessmentRecord,
    HypothesisRecord,
    IssuedBudgetRecord,
    ObservationRecord,
    ProgramRecord,
    ResearchAdmissionRecord,
    ResearchReasoningRecord,
    ResearchRunRecord,
    WorkerResultRecord,
    WorkerResultStatus,
)
from research_os.data.unit_of_work import UnitOfWork

__all__ = [
    "AuditEventRecord",
    "AuthorizationSourceRecord",
    "ExecutionAttemptRecord",
    "ExecutionAttemptState",
    "ExperimentExecutionState",
    "ExperimentPlanRecord",
    "ExperimentRecord",
    "HypothesisAssessmentRecord",
    "HypothesisRecord",
    "IssuedBudgetRecord",
    "ObservationRecord",
    "DatabaseUnavailableError",
    "PersistenceConflictError",
    "PersistenceError",
    "PersistenceInputError",
    "ProgramRecord",
    "ResearchAdmissionRecord",
    "ResearchReasoningRecord",
    "ResearchRunRecord",
    "UnitOfWork",
    "WorkerResultRecord",
    "WorkerResultStatus",
]
