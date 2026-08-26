"""Data-layer errors. Distinct from Core policy DENY and from CoreInputError."""


class PersistenceError(Exception):
    """Persistence adapter failure (integrity, connectivity, append-only violation)."""


class DatabaseUnavailableError(PersistenceError):
    """PostgreSQL could not be reached. Not a research conclusion and not RAM fallback."""


class PersistenceConflictError(PersistenceError):
    """Unique/idempotency conflict. Not a policy decision and not duplicate Evidence.

    `constraint_name` is the PostgreSQL unique index/constraint that fired, when
    known. Callers must inspect it before treating the conflict as idempotent.
    """

    def __init__(self, *args: object, constraint_name: str | None = None) -> None:
        super().__init__(*args)
        self.constraint_name = constraint_name


class PersistenceInputError(PersistenceError):
    """Invalid record passed to a repository. Not a policy decision."""


class BudgetOverspendError(PersistenceError):
    """Append would exceed IssuedBudget. Not a research conclusion."""


class TerminalOrchestrationStateError(PersistenceError):
    """Write rejected because the persisted research_orchestration row is terminal.

    Terminal orchestration states (COMPLETED, BUDGET_EXHAUSTED,
    FAILED_OPERATIONAL) are immutable once persisted. No operator command and
    no internal transition may overwrite state or stop_reason on a terminal
    row; this is enforced at the repository boundary so it holds regardless
    of which caller attempts the write.
    """


class LeaseFencingError(PersistenceError):
    """Write rejected because the caller's remembered lease no longer holds.

    Raised when a fenced research_orchestration write's expected
    (owner_runtime_instance_id, lease_epoch) no longer matches the persisted
    row (another owner has since acquired a newer epoch), or when a
    reconciliation write required the row to be currently unowned/expired
    and it was not. This is the "0 rows affected = ownership lost" signal;
    the caller must stop producing new work for this run rather than retry.
    """
