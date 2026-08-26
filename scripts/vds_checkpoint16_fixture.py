"""Controlled VDS qualification fixtures for Checkpoint 16 crash/reboot cases.

Uses existing Application recovery classification and PostgreSQL SoR rows.
Does not change research authority. Does not auto-retry DISPATCHING or
UNKNOWN_OUTCOME. Does not print secrets.

seed --truncate requires ZEST_ALLOW_SPINE_TRUNCATE=YES and wipes the
connected database spine. Use only on a dedicated empty staging database.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from zest.application.classify_runtime_recovery import ClassifyRuntimeRecovery
from zest.application.orchestration_config import fingerprint_for_start
from zest.application.osd_settings import LINUX_ENV_FILE, load_osd_settings
from zest.core.enums import ActorType
from zest.data.postgres.engine import create_sync_engine, redacted_database_url
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.data.records import (
    AuditEventRecord,
    ExecutionAttemptRecord,
    ResearchOrchestrationRecord,
)
from zest.qualification.staging_spine import (
    TRUNCATE_GUARD,
    StagingTruncateDenied,
    require_explicit_spine_truncate,
    seed_authorized_spine,
    truncate_spine,
)
from zest.qualification.j11_fencing import (
    J11QualificationError,
    cleanup_j11_owner,
    prepare_j11_run,
    run_stale_epoch_proof,
    run_two_process_owner_race,
)
from zest.research.orchestration import (
    ORCHESTRATION_POLICY_VERSION,
    OrchestrationBounds,
    OrchestrationPhase,
    OrchestrationState,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
TARGET = "https://example.com/"
RUN_ID = "run-1"
ATTEMPT_ID = "ea-cp16"
REQUEST_ID = "req-cp16"
AUDIT_ID = "ae-cp16"


def _load_optional_env_file(path: Path | None) -> None:
    candidate = path or Path(LINUX_ENV_FILE)
    if not candidate.is_file():
        if path is not None:
            raise SystemExit(f"env file missing: {candidate}")
        return
    for raw in candidate.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value)


def _engine():
    settings = load_osd_settings(dict(os.environ))
    print(f"database={redacted_database_url(settings.database_url)}")
    return create_sync_engine(settings.database_url)


def _bounds() -> OrchestrationBounds:
    return OrchestrationBounds(
        max_cycles=1,
        max_experiments=1,
        max_model_calls=4,
        max_worker_invocations=4,
        max_elapsed_ms=60_000,
        max_selected_opportunities=1,
        max_runtime_fallback=0,
        side_effect_ceiling=0,
        allow_repeated_control_experiments=True,
    )


def _seed(engine, *, truncate: bool) -> None:
    require_explicit_spine_truncate(truncate=truncate, env=os.environ)
    truncate_spine(engine)
    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        seed_authorized_spine(uow)
        uow.audit_events.insert(
            AuditEventRecord(
                audit_event_id=AUDIT_ID,
                occurred_at=NOW,
                actor_id="operator-1",
                actor_type=ActorType.HUMAN_OPERATOR.value,
                event_type="CHECKPOINT16_FIXTURE_AUTHORIZATION",
                subject_type="research_run",
                subject_id=RUN_ID,
                payload={"fixture": True, "authorizes_start": False},
            )
        )
        uow.commit()
    _ensure_orchestration(engine)
    print(f"seeded={RUN_ID}")


def _orchestration_record(*, state: str) -> ResearchOrchestrationRecord:
    """Same insert shape AutonomousResearchController.start() persists.

    Qualification may then checkpoint the row to RUNNING. This does not
    authorize research or dispatch a Worker.
    """

    bounds = _bounds()
    fingerprint = fingerprint_for_start(
        research_run_id=RUN_ID,
        budget_id="budget-1",
        target_reference=TARGET,
        research_question="checkpoint-16 fixture",
        policy_version=ORCHESTRATION_POLICY_VERSION,
        bounds=bounds,
        routing_policy_version=None,
        scope_fp=None,
    )
    return ResearchOrchestrationRecord(
        research_run_id=RUN_ID,
        state=state,
        cycle_number=0,
        last_phase="start",
        policy_version=ORCHESTRATION_POLICY_VERSION,
        max_cycles=bounds.max_cycles,
        max_experiments=bounds.max_experiments,
        max_model_calls=bounds.max_model_calls,
        max_worker_invocations=bounds.max_worker_invocations,
        max_elapsed_ms=bounds.max_elapsed_ms,
        max_selected_opportunities=bounds.max_selected_opportunities,
        max_runtime_fallback=bounds.max_runtime_fallback,
        side_effect_ceiling=bounds.side_effect_ceiling,
        allow_repeated_control_experiments=bounds.allow_repeated_control_experiments,
        created_at=NOW,
        updated_at=NOW,
        checkpoint_at=NOW,
        budget_id="budget-1",
        target_reference=TARGET,
        research_question="checkpoint-16 fixture",
        configuration_fingerprint=fingerprint,
        current_phase=OrchestrationPhase.CYCLE_READY.value,
        owner_runtime_instance_id=None,
        lease_epoch=0,
        lease_expires_at=None,
    )


def _ensure_orchestration(engine, *, state: str = OrchestrationState.RUNNING.value) -> None:
    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        existing = uow.research_orchestrations.get(RUN_ID)
        if existing is None:
            created = _orchestration_record(state=state)
            uow.research_orchestrations.insert(created)
        elif existing.state != state or existing.pause_reason is not None:
            uow.research_orchestrations.save(
                replace(
                    existing,
                    state=state,
                    pause_reason=None,
                    updated_at=NOW,
                    checkpoint_at=NOW,
                )
            )
        uow.commit()


def _ensure_audit(engine) -> None:
    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        existing = uow.audit_events.get(AUDIT_ID)
        if existing is None:
            uow.audit_events.insert(
                AuditEventRecord(
                    audit_event_id=AUDIT_ID,
                    occurred_at=NOW,
                    actor_id="operator-1",
                    actor_type=ActorType.HUMAN_OPERATOR.value,
                    event_type="CHECKPOINT16_FIXTURE_AUTHORIZATION",
                    subject_type="research_run",
                    subject_id=RUN_ID,
                    payload={"fixture": True, "authorizes_start": False},
                )
            )
        uow.commit()


def _set_attempt(engine, *, state: str, side_effect_level: int) -> None:
    _ensure_audit(engine)
    factory = PostgresUnitOfWork(engine)
    with factory.open() as uow:
        current = uow.execution_attempts.get(ATTEMPT_ID)
        if current is None:
            uow.execution_attempts.insert(
                ExecutionAttemptRecord(
                    attempt_id=ATTEMPT_ID,
                    request_id=REQUEST_ID,
                    experiment_id="exp-1",
                    research_run_id=RUN_ID,
                    correlation_id="corr-cp16",
                    worker_capability="diagnostic.echo",
                    action="echo",
                    target_reference=TARGET,
                    budget_id="budget-1",
                    side_effect_level=side_effect_level,
                    authorization_decision_reference=AUDIT_ID,
                    state=state,
                    created_at=NOW,
                    authorized_at=NOW,
                )
            )
            uow.commit()
        else:
            uow.rollback()
    if current is not None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE execution_attempt SET state = :state, "
                    "side_effect_level = :level WHERE attempt_id = :attempt_id"
                ),
                {
                    "state": state,
                    "level": side_effect_level,
                    "attempt_id": ATTEMPT_ID,
                },
            )
    print(f"attempt={ATTEMPT_ID} state={state} side_effect_level={side_effect_level}")


def _classify(engine) -> None:
    decision = ClassifyRuntimeRecovery(PostgresUnitOfWork(engine)).execute(RUN_ID)
    print(f"research_run_id={decision.research_run_id}")
    print(f"recovery_action={decision.action.value}")
    print(f"recovery_reason={decision.reason}")


def _show_runtime(engine) -> None:
    with engine.connect() as connection:
        rows = list(
            connection.execute(
                text(
                    "SELECT runtime_instance_id, status, process_id, started_at "
                    "FROM runtime_instance ORDER BY started_at"
                )
            )
        )
        orch = connection.execute(
            text(
                "SELECT research_run_id, state, pause_reason, last_phase, "
                "owner_runtime_instance_id, lease_epoch, lease_expires_at "
                "FROM research_orchestration WHERE research_run_id = :run_id"
            ),
            {"run_id": RUN_ID},
        ).mappings().first()
        attempts = list(
            connection.execute(
                text(
                    "SELECT attempt_id, state, side_effect_level "
                    "FROM execution_attempt WHERE research_run_id = :run_id "
                    "ORDER BY created_at"
                ),
                {"run_id": RUN_ID},
            )
        )
        version_rows = list(connection.execute(text("SELECT version_num FROM alembic_version")))
    print(f"runtime_instance_count={len(rows)}")
    for row in rows:
        print(
            "runtime_instance "
            f"id={row.runtime_instance_id} status={row.status} pid={row.process_id}"
        )
    if orch is None:
        print("orchestration=missing")
    else:
        print(
            "orchestration "
            f"state={orch['state']} pause_reason={orch['pause_reason']} "
            f"last_phase={orch['last_phase']} "
            f"owner={orch['owner_runtime_instance_id']} "
            f"lease_epoch={orch['lease_epoch']}"
        )
    for row in attempts:
        print(
            "execution_attempt "
            f"id={row.attempt_id} state={row.state} "
            f"side_effect_level={row.side_effect_level}"
        )
    print(f"alembic_version_rows={len(version_rows)}")
    if version_rows:
        print(f"alembic_version={version_rows[0][0]}")


def _j11_prepare(engine) -> None:
    result = prepare_j11_run(engine)
    print(
        "j11_prepare=ok "
        f"state={result.state} owner={result.owner_runtime_instance_id} "
        f"lease_epoch={result.lease_epoch} "
        f"side_effect_ceiling={result.side_effect_ceiling}"
    )


def _j11_race(engine, *, iterations: int) -> None:
    engine.dispose()
    database_url = load_osd_settings(dict(os.environ)).database_url
    result = run_two_process_owner_race(database_url, iterations=iterations)
    print(
        "j11_race=PASS "
        f"iterations={len(result.iterations)} "
        f"final_owner={result.final_owner_runtime_instance_id} "
        f"final_lease_epoch={result.final_lease_epoch} "
        f"final_owner_count={result.final_owner_count}"
    )
    for item in result.iterations:
        print(
            "j11_race_iteration "
            f"n={item.iteration} winner={item.winner_runtime_instance_id} "
            f"winner_pid={item.winner_process_id} "
            f"loser={item.loser_runtime_instance_id} "
            f"loser_pid={item.loser_process_id} "
            f"lease_epoch={item.lease_epoch} "
            f"loser_worker_blocked={item.loser_worker_blocked} "
            f"loser_inner_dispatch_count={item.loser_inner_dispatch_count} "
            f"released_after_iteration={item.released_after_iteration}"
        )


def _j11_stale_proof(engine) -> None:
    engine.dispose()
    database_url = load_osd_settings(dict(os.environ)).database_url
    result = run_stale_epoch_proof(database_url)
    print(
        "j11_stale=PASS "
        f"stale_owner={result.stale_owner_runtime_instance_id} "
        f"stale_pid={result.stale_process_id} "
        f"stale_epoch={result.stale_epoch} "
        f"current_owner={result.current_owner_runtime_instance_id} "
        f"current_pid={result.current_process_id} "
        f"current_epoch={result.current_epoch} "
        f"stale_save_blocked={result.stale_save_blocked} "
        f"stale_worker_blocked={result.stale_worker_blocked} "
        f"stale_inner_dispatch_count={result.stale_inner_dispatch_count} "
        f"stopped_runtime_count={result.stopped_runtime_count}"
    )


def _j11_cleanup(
    engine,
    *,
    owner_runtime_instance_id: str | None,
    expected_lease_epoch: int | None,
) -> None:
    result = cleanup_j11_owner(
        engine,
        owner_runtime_instance_id=owner_runtime_instance_id,
        expected_lease_epoch=expected_lease_epoch,
    )
    print(
        "j11_cleanup=PASS "
        f"cleanup={result.cleanup} "
        f"owner={result.owner_runtime_instance_id} "
        f"expected_lease_epoch={result.expected_lease_epoch} "
        f"lease_epoch_after={result.lease_epoch_after} "
        f"owner_after={result.owner_after} "
        f"lease_expires_at_after={result.lease_expires_at_after} "
        f"derived_owner={result.derived_owner} "
        f"owner_status={result.owner_status} "
        f"owner_expired={result.owner_expired} "
        f"release_cas_applied={result.release_cas_applied}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vds_checkpoint16_fixture")
    parser.add_argument(
        "action",
        choices=(
            "seed",
            "authorized-not-dispatched",
            "dispatching",
            "unknown-outcome",
            "classify",
            "show",
            "j11-prepare",
            "j11-race",
            "j11-stale-proof",
            "j11-cleanup",
        ),
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help=(
            "required for seed; destroys spine rows on the connected database; "
            f"also requires {TRUNCATE_GUARD}=YES"
        ),
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--owner-runtime-instance-id")
    parser.add_argument("--expected-lease-epoch", type=int)
    args = parser.parse_args(argv)
    _load_optional_env_file(args.env_file)
    if args.action == "seed":
        try:
            require_explicit_spine_truncate(truncate=args.truncate, env=os.environ)
        except StagingTruncateDenied as exc:
            raise SystemExit(str(exc)) from exc
    engine = _engine()
    try:
        if args.action == "seed":
            _seed(engine, truncate=args.truncate)
        elif args.action == "authorized-not-dispatched":
            _ensure_orchestration(engine, state="RUNNING")
            _set_attempt(engine, state="AUTHORIZED", side_effect_level=0)
        elif args.action == "dispatching":
            _ensure_orchestration(engine, state="RUNNING")
            _set_attempt(engine, state="DISPATCHING", side_effect_level=1)
        elif args.action == "unknown-outcome":
            _ensure_orchestration(engine, state="RUNNING")
            _set_attempt(engine, state="UNKNOWN_OUTCOME", side_effect_level=2)
        elif args.action == "classify":
            _classify(engine)
        elif args.action == "show":
            _show_runtime(engine)
        elif args.action == "j11-prepare":
            _j11_prepare(engine)
        elif args.action == "j11-race":
            _j11_race(engine, iterations=args.iterations)
            engine = None
        elif args.action == "j11-stale-proof":
            _j11_stale_proof(engine)
            engine = None
        elif args.action == "j11-cleanup":
            _j11_cleanup(
                engine,
                owner_runtime_instance_id=args.owner_runtime_instance_id,
                expected_lease_epoch=args.expected_lease_epoch,
            )
    except J11QualificationError as exc:
        raise SystemExit(f"J11_QUALIFICATION_ERROR: {exc}") from exc
    finally:
        if engine is not None:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
