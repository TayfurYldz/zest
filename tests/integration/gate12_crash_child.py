"""Child process for GATE 12 PostgreSQL crash/restart validation.

Commits the requested durable phase, then terminates without cleanup.
Not a production controller and not a mocked UnitOfWork.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import text

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_REPO / "tests") not in sys.path:
    sys.path.insert(0, str(_REPO / "tests"))

from zest.application.autonomous_research_controller import (  # noqa: E402
    AutonomousResearchController,
    StartAutonomousResearchCommand,
)
from zest.core.enums import ScopeRuleEffect  # noqa: E402
from zest.core.scope import ScopeEvaluationInput, ScopeRuleMatch  # noqa: E402
from zest.data.postgres.engine import (  # noqa: E402
    TEST_DATABASE_URL_ENV,
    create_sync_engine,
    validate_test_database_url,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork  # noqa: E402
from zest.research.orchestration import OrchestrationBounds  # noqa: E402
from integration.harness import FixedClock  # noqa: E402
from support.fake_model import ScriptedModelPort  # noqa: E402
from support.recording_worker import RecordingWorkerPort  # noqa: E402

CRASH_EXIT_CODE = 9


class CrashAfterPhaseFactory:
    def __init__(self, engine, until_phase: str) -> None:
        self._engine = engine
        self._until_phase = until_phase

    def open(self) -> "CrashAfterPhaseUnitOfWork":
        return CrashAfterPhaseUnitOfWork(
            PostgresUnitOfWork(self._engine), self._engine, self._until_phase
        )


class CrashAfterPhaseUnitOfWork:
    def __init__(self, inner: PostgresUnitOfWork, engine, until_phase: str) -> None:
        self._inner = inner
        self._engine = engine
        self._until_phase = until_phase

    def __enter__(self) -> "CrashAfterPhaseUnitOfWork":
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._inner.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def commit(self) -> None:
        self._inner.commit()
        with self._engine.connect() as connection:
            phase = connection.execute(
                text(
                    "SELECT current_phase FROM research_orchestration "
                    "WHERE research_run_id = 'run-1'"
                )
            ).scalar()
            attempt_state = connection.execute(
                text(
                    "SELECT state FROM execution_attempt "
                    "WHERE research_run_id = 'run-1' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            ).scalar()
        if self._until_phase == "DISPATCHING" and attempt_state == "DISPATCHING":
            print("CRASH_AFTER DISPATCHING", flush=True)
            os._exit(CRASH_EXIT_CODE)
        if phase == self._until_phase:
            print(f"CRASH_AFTER {phase}", flush=True)
            os._exit(CRASH_EXIT_CODE)


class TxGuardModel(ScriptedModelPort):
    def __init__(self, engine) -> None:
        super().__init__()
        self._engine = engine

    def complete(self, request):
        with self._engine.connect() as connection:
            idle = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'idle in transaction'"
                )
            ).scalar_one()
        if idle:
            print(f"OPEN_TRANSACTION_DURING_MODEL idle={idle}", flush=True)
            os._exit(11)
        return super().complete(request)


class TxGuardWorker(RecordingWorkerPort):
    def __init__(self, engine) -> None:
        super().__init__()
        self._engine = engine

    def invoke(self, request, *, timeout_ms=None):
        with self._engine.connect() as connection:
            idle = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'idle in transaction'"
                )
            ).scalar_one()
        if idle:
            print(f"OPEN_TRANSACTION_DURING_WORKER idle={idle}", flush=True)
            os._exit(11)
        return super().invoke(request, timeout_ms=timeout_ms)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gate12_crash_child.py PHASE", file=sys.stderr)
        return 2
    until = argv[1]
    raw = os.environ.get(TEST_DATABASE_URL_ENV)
    if not raw:
        print(f"{TEST_DATABASE_URL_ENV} required", file=sys.stderr)
        return 2
    url = validate_test_database_url(raw)
    engine = create_sync_engine(url)
    factory = CrashAfterPhaseFactory(engine, until)
    controller = AutonomousResearchController(
        factory,
        TxGuardWorker(engine),
        TxGuardModel(engine),
        clock=FixedClock(),
    )
    command = StartAutonomousResearchCommand(
        research_run_id="run-1",
        budget_id="budget-1",
        target_reference="target-1",
        scope=ScopeEvaluationInput(
            matches=(ScopeRuleMatch("rule-allow", ScopeRuleEffect.ALLOW, True, "scope-src"),),
            ambiguous=False,
        ),
        bounds=OrchestrationBounds(
            max_cycles=2,
            max_experiments=2,
            max_model_calls=20,
            max_worker_invocations=4,
            max_elapsed_ms=60_000,
            max_selected_opportunities=1,
            max_runtime_fallback=0,
            side_effect_ceiling=0,
            allow_repeated_control_experiments=True,
        ),
    )
    print(f"CHILD_STEP until={until}", flush=True)
    controller.step(command)
    print("PHASE_NOT_REACHED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
