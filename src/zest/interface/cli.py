"""Operator CLI. Composition root. Does not print secrets."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Mapping

from zest.application.operator_status import OperatorStatusSnapshot, render_operator_status
from zest.maturity import GATE_01_STATUS, GATE_04B_STATUS, GATE_14_STATUS, GATE_15_STATUS, GATE_16_STATUS, GATE_17_STATUS, GATE_18_STATUS, GATE_19_STATUS, GATE_20_STATUS, GATE_21_STATUS, GATE_22_STATUS, SUBSCRIPTION_OAUTH_STATUS
from zest.platform.health import ComponentHealth


def _health_from_readiness(readiness: str, *, auth_hint: str = "") -> str:
    if readiness == "AVAILABLE":
        return ComponentHealth.HEALTHY.value
    if "auth" in auth_hint.lower() or "credential" in auth_hint.lower() or "unauthenticated" in auth_hint.lower():
        return ComponentHealth.AUTH_REQUIRED.value
    if readiness == "CONFIGURED_NOT_READY":
        return ComponentHealth.DEGRADED.value
    return ComponentHealth.UNAVAILABLE.value


def _probe_application_db(url: str | None) -> tuple[str, str, str]:
    from zest.data.postgres.engine import create_sync_engine, ping_database, redacted_database_url

    if not url:
        return ComponentHealth.UNAVAILABLE.value, "unset", "no database"
    try:
        dsn = redacted_database_url(url)
    except Exception:
        dsn = "unparseable"
    try:
        engine = create_sync_engine(url)
        ping_database(engine)
        health = ComponentHealth.HEALTHY.value
        orchestrator = "no active run"
        budget = "unknown"
        with engine.connect() as connection:
            from sqlalchemy import text

            row = connection.execute(
                text("SELECT state, COUNT(*) FROM research_orchestration GROUP BY state")
            ).fetchall()
            if row:
                orchestrator = ", ".join(f"{state}={count}" for state, count in row)
            consumed = connection.execute(text("SELECT COUNT(*) FROM budget_consumption")).scalar_one()
            budget = f"append-only rows={consumed}"
        engine.dispose()
        return health, dsn, f"{orchestrator}; {budget}"
    except Exception as exc:
        return ComponentHealth.UNAVAILABLE.value, dsn, f"unavailable ({exc.__class__.__name__})"


def _probe_test_db(url: str | None) -> tuple[str, str]:
    from zest.data.postgres.engine import create_sync_engine, ping_database, redacted_database_url

    if not url:
        return "not configured", "unset"
    try:
        dsn = redacted_database_url(url)
    except Exception:
        dsn = "unparseable"
    try:
        engine = create_sync_engine(url)
        ping_database(engine)
        engine.dispose()
        return ComponentHealth.HEALTHY.value, dsn
    except Exception as exc:
        return f"{ComponentHealth.UNAVAILABLE.value} ({exc.__class__.__name__})", dsn


def build_status_snapshot(*, env: Mapping[str, str] | None = None, argv_runner=None) -> OperatorStatusSnapshot:
    from zest.data.postgres.engine import DATABASE_URL_ENV, TEST_DATABASE_URL_ENV
    from zest.integrations.models.discovery import ProbeMode, discover_configured_runtimes
    from zest.platform.worker_health import probe_local_python_worker

    source = dict(os.environ if env is None else env)
    postgres, app_dsn, db_detail = _probe_application_db(source.get(DATABASE_URL_ENV))
    test_postgres, test_dsn = _probe_test_db(source.get(TEST_DATABASE_URL_ENV))
    orchestrator = "no database"
    budget = "unknown"
    if postgres == ComponentHealth.HEALTHY.value:
        parts = db_detail.split("; ", 1)
        orchestrator = parts[0]
        budget = parts[1] if len(parts) > 1 else "unknown"
    elif postgres == ComponentHealth.UNAVAILABLE.value:
        orchestrator = "unavailable"
        budget = db_detail

    report = discover_configured_runtimes(
        env=source,
        argv_runner=argv_runner,
        probe_mode=ProbeMode.PASSIVE,
    )
    kind = report.kind_matrix
    model_runtimes = {
        "API": _health_from_readiness(kind.get("API", "UNAVAILABLE")),
        "SUBSCRIPTION_OAUTH": SUBSCRIPTION_OAUTH_STATUS,
        "CLI_SESSION": _health_from_readiness(
            kind.get("CLI_SESSION", "UNAVAILABLE"),
            auth_hint=" ".join(
                item.reason for item in report.entries if item.runtime_kind == "CLI_SESSION"
            ),
        ),
        "LOCAL_MODEL": _health_from_readiness(kind.get("LOCAL_MODEL", "UNAVAILABLE")),
        "EXTERNAL_AGENT": _health_from_readiness(kind.get("EXTERNAL_AGENT", "UNAVAILABLE")),
    }
    strix_entry = next((item for item in report.entries if item.runtime_kind == "STRIX"), None)
    strix = (
        _health_from_readiness(strix_entry.readiness.value)
        if strix_entry is not None
        else ComponentHealth.UNAVAILABLE.value
    )
    worker_check = probe_local_python_worker()
    return OperatorStatusSnapshot(
        postgresql=postgres,
        test_postgresql=test_postgres,
        application_dsn=app_dsn,
        test_dsn=test_dsn,
        worker={"local-python": worker_check.health.value},
        model_runtimes=model_runtimes,
        strix=strix,
        auth="runtime-owned sessions only; tokens are not scraped",
        orchestrator=orchestrator,
        budget_ledger=budget,
        reconciliation="classifier available; no side-effect guessing",
        observability="structured events; not AuditEvent; not Evidence",
        gate_01=GATE_01_STATUS,
        gate_04b=GATE_04B_STATUS,
        gate_14=GATE_14_STATUS,
        gate_15=GATE_15_STATUS,
        gate_16=GATE_16_STATUS,
        gate_17=GATE_17_STATUS,
        gate_18=GATE_18_STATUS,
        gate_19=GATE_19_STATUS,
        gate_20=GATE_20_STATUS,
        gate_21=GATE_21_STATUS,
        gate_22=GATE_22_STATUS,
    )


def _cmd_census(rest: list[str]) -> int:
    from zest.application.program_research_context import (
        load_program_research_context,
    )
    from zest.application.sensor.runner import SensorAcquisitionRunner
    from zest.data.postgres.engine import create_sync_engine
    from zest.data.postgres.unit_of_work import PostgresUnitOfWork
    from zest.platform.url_normalize import normalize_url
    from zest.core.scope_compiler import evaluate_scope_candidate
    from zest.research.sensor import (
        CTLogSensor,
        CertificateMetaSensor,
        DNSSensor,
        TechnologyFingerprintSensor,
        WaybackArchiveSensor,
    )
    from zest.research.sensor.types import FixtureLoader, ScopeCensusView

    census_parser = argparse.ArgumentParser(prog="zest census")
    census_parser.add_argument("--research-run-id", required=True)
    census_parser.add_argument("--target", required=True)
    census_parser.add_argument("--fixture-dir", default=None)
    census_args = census_parser.parse_args(rest)

    db_url = os.environ.get("ZEST_DATABASE_URL")
    if not db_url:
        sys.stderr.write("ZEST_DATABASE_URL is not set\n")
        return 1

    engine = create_sync_engine(db_url)
    uow_factory = PostgresUnitOfWork(engine)

    fixture_loader: FixtureLoader | None = None
    if census_args.fixture_dir:
        from pathlib import Path
        from zest.research.sensor.fixture_loader import FileFixtureLoader
        fixture_loader = FileFixtureLoader(Path(census_args.fixture_dir))

    sensors = [
        DNSSensor(fixture_loader),
        CTLogSensor(fixture_loader),
        WaybackArchiveSensor(fixture_loader),
        CertificateMetaSensor(fixture_loader),
        TechnologyFingerprintSensor(fixture_loader),
    ]

    with uow_factory.open() as uow:
        run = uow.research_runs.get(census_args.research_run_id)
        if run is None:
            sys.stderr.write(f"research run not found: {census_args.research_run_id}\n")
            return 1
        context = load_program_research_context(uow, run.program_id)
        if context is None:
            sys.stderr.write(f"program not found: {run.program_id}\n")
            return 1
        scope_check = evaluate_scope_candidate(
            normalize_url(census_args.target),
            context.compiled_scope,
        )
        uow.rollback()

    scope_view = ScopeCensusView(
        classification=scope_check.classification,
        reason_code=scope_check.reason_code,
        matched_rule_ids=scope_check.matched_rule_ids,
    )
    if not scope_view.allows_census():
        sys.stderr.write(
            f"census denied: {scope_view.reason_code.value} "
            f"({scope_view.classification.value})\n"
        )
        return 1

    runner = SensorAcquisitionRunner(uow_factory, sensors)
    result = runner.run(
        census_args.research_run_id,
        census_args.target,
        scope_view,
    )
    sys.stdout.write(
        f"census completed: {len(result.observations)} observations, "
        f"{len(result.errors)} errors, {result.budget_units_consumed} budget units\n"
    )
    return 0


def _microdollars_to_usd_display(microdollars: int) -> str:
    return f"{microdollars / 1_000_000:.6f} USD"


def _cmd_budget(rest: list[str]) -> int:
    from datetime import date, timezone

    from zest.application.program_daily_budget import ProgramDailyBudgetUsage
    from zest.data.postgres.engine import create_sync_engine
    from zest.data.postgres.unit_of_work import PostgresUnitOfWork

    budget_parser = argparse.ArgumentParser(prog="zest budget")
    budget_parser.add_argument("--program-id", required=True)
    budget_parser.add_argument("--date", default=date.today(timezone.utc).isoformat())
    budget_args = budget_parser.parse_args(rest)

    db_url = os.environ.get("ZEST_DATABASE_URL")
    if not db_url:
        sys.stderr.write("ZEST_DATABASE_URL is not set\n")
        return 1

    engine = create_sync_engine(db_url)
    uow_factory = PostgresUnitOfWork(engine)
    usage = ProgramDailyBudgetUsage(uow_factory)
    try:
        view = usage.execute(budget_args.program_id, budget_args.date)
    except Exception as exc:
        sys.stderr.write(f"budget query failed: {exc}\n")
        return 1

    class_counts: dict[str, int] = {}
    for call in view.last_calls:
        model_class = "unknown"
        if call.resource_type == "MODEL_CALL":
            model_class = "call"
        elif call.resource_type in {"MODEL_TOKENS_IN", "MODEL_TOKENS_OUT"}:
            model_class = call.resource_type.split("_")[-1].lower()
        class_counts[model_class] = class_counts.get(model_class, 0) + 1

    lines = [
        f"program_id: {view.program_id}",
        f"date: {view.budget_date}",
        f"limit: {_microdollars_to_usd_display(view.limit_microdollars)}",
        f"spent: {_microdollars_to_usd_display(view.spent_microdollars)}",
        f"remaining: {_microdollars_to_usd_display(view.remaining_microdollars)}",
        f"tokens_in: {view.tokens_in}",
        f"tokens_out: {view.tokens_out}",
        f"model_call_count: {view.model_call_count}",
        "last_calls:",
    ]
    for call in view.last_calls:
        lines.append(
            f"  - {call.occurred_at.isoformat()} {call.resource_type} "
            f"model={call.model_id or 'unknown'} request={call.request_id or '-'}"
        )
    lines.append(f"class_distribution: {class_counts}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _cmd_coverage(rest: list[str]) -> int:
    from zest.application.coverage.debt_view import CoverageDebtView
    from zest.data.postgres.engine import create_sync_engine
    from zest.data.postgres.unit_of_work import PostgresUnitOfWork

    coverage_parser = argparse.ArgumentParser(prog="zest coverage")
    coverage_parser.add_argument("--research-run-id", required=True)
    coverage_args = coverage_parser.parse_args(rest)

    db_url = os.environ.get("ZEST_DATABASE_URL")
    if not db_url:
        sys.stderr.write("ZEST_DATABASE_URL is not set\n")
        return 1

    engine = create_sync_engine(db_url)
    uow_factory = PostgresUnitOfWork(engine)
    view = CoverageDebtView(uow_factory)
    try:
        summary = view.execute(coverage_args.research_run_id)
    except Exception as exc:
        sys.stderr.write(f"coverage query failed: {exc}\n")
        return 1

    lines = [
        f"research_run_id: {summary.research_run_id}",
        f"strategy_version: {summary.strategy_version}",
        f"matrix_hash: {summary.matrix_hash}",
        f"total_debt: {summary.total_debt}",
        "cell_counts:",
    ]
    for state, count in sorted(summary.cell_counts.items()):
        lines.append(f"  {state}: {count}")
    lines.append("family_debt:")
    for family_id, count in sorted(summary.family_debt.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"  {family_id}: {count}")
    lines.append("top_nodes:")
    for item in summary.top_nodes:
        lines.append(f"  {item['node_canonical_key']}: {item['debt']}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zest")
    parser.add_argument("command", choices=("status", "export-source", "census", "budget", "coverage"))
    args, rest = parser.parse_known_args(argv)
    if args.command == "status":
        sys.stdout.write(render_operator_status(build_status_snapshot()) + "\n")
        return 0
    if args.command == "export-source":
        from zest.source_export import export_source_archive

        export_parser = argparse.ArgumentParser(prog="zest export-source")
        export_parser.add_argument("--output", required=True)
        export_parser.add_argument("--include-untracked-source", action="store_true")
        export_args = export_parser.parse_args(rest)
        archive, manifest = export_source_archive(
            export_args.output,
            include_untracked_source=export_args.include_untracked_source,
        )
        sys.stdout.write(f"{archive}\n{manifest}\n")
        return 0
    if args.command == "census":
        return _cmd_census(rest)
    if args.command == "budget":
        return _cmd_budget(rest)
    if args.command == "coverage":
        return _cmd_coverage(rest)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
