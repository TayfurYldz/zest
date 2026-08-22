"""research-osd process entrypoint. Composition root only."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from research_os.application.orchestration_lease import LeaseConfig
from research_os.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from research_os.application.research_osd import ResearchOsdRuntime
from research_os.data.postgres.engine import (
    DATABASE_URL_ENV,
    check_schema_head,
    create_sync_engine,
)
from research_os.data.postgres.unit_of_work import PostgresUnitOfWork
from research_os.interface.operator_api import (
    OPERATOR_API_DEFAULT_HOST,
    OPERATOR_API_DEFAULT_PORT,
    OperatorApiServer,
)
from research_os.platform.health import ComponentHealth, HealthCheck
from research_os.platform.worker_health import probe_local_python_worker


def _lease_config_from_env(env: dict[str, str]) -> LeaseConfig:
    heartbeat = float(env.get("RESEARCH_OSD_HEARTBEAT_INTERVAL_SECONDS", "30"))
    ttl = float(env.get("RESEARCH_OSD_LEASE_TTL_SECONDS", "90"))
    return LeaseConfig(heartbeat_interval_seconds=heartbeat, lease_ttl_seconds=ttl)


def _alembic_ini() -> str:
    return str(Path(__file__).resolve().parents[3] / "alembic.ini")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="research-osd")
    parser.add_argument("--host", default=OPERATOR_API_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=OPERATOR_API_DEFAULT_PORT)
    args = parser.parse_args(argv)
    url = os.environ.get(DATABASE_URL_ENV)
    if not url:
        print(f"{DATABASE_URL_ENV} is required", file=sys.stderr)
        return 2
    engine = create_sync_engine(url)
    factory = PostgresUnitOfWork(engine)
    from research_os.integrations.models.cli_session import (
        CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
        CodexCliSessionAdapter,
        load_codex_model_configurations,
        probe_codex_cli,
    )
    from research_os.platform.persistent_browser_worker import PersistentBrowserWorkerAdapter
    from research_os.research.model_runtime import api_runtime_identity
    from research_os.research.routing import CandidateLocality, RuntimeCandidate

    worker = PersistentBrowserWorkerAdapter()
    configurations = load_codex_model_configurations(os.environ)
    configuration = configurations[0] if configurations else None
    availability = probe_codex_cli(configuration=configuration) if configuration else None
    model: CodexCliSessionAdapter | _UnavailableModel
    if configuration is not None and availability is not None and availability.readiness and availability.readiness.auth_ready:
        model = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable=configuration.executable,
            version=availability.version,
            model=configuration.model,
            configuration_id=configuration.configuration_id,
        )
        model_probe = lambda: ModelReadinessInput(
            candidate=RuntimeCandidate(
                identity=api_runtime_identity(
                    adapter_id="codex.cli", runtime_id=configuration.model
                ),
                available=True,
                authenticated=True,
                structured_output_compatible=True,
                locality=CandidateLocality.LOCAL,
            ),
            health=HealthCheck("model", ComponentHealth.HEALTHY, "codex auth ready"),
        )
    else:
        model = _UnavailableModel()
        model_probe = lambda: ModelReadinessInput(
            candidate=None,
            health=HealthCheck("model", ComponentHealth.UNAVAILABLE, "codex not ready"),
        )

    def probe_schema() -> SchemaHealthInput:
        ok, detail = check_schema_head(engine, alembic_ini_path=_alembic_ini())
        return SchemaHealthInput(at_expected_head=ok, detail=detail)

    def probe_worker() -> WorkerReadinessInput:
        health = probe_local_python_worker()
        return WorkerReadinessInput(
            health=health,
            available_capabilities=frozenset({"diagnostic.echo"}),
        )

    runtime = ResearchOsdRuntime(
        factory,
        worker,
        model,
        lease_config=_lease_config_from_env(dict(os.environ)),
        probe_schema=probe_schema,
        probe_worker=probe_worker,
        probe_model=model_probe,
        host_identity=os.uname().nodename or "research-osd",
        process_id=str(os.getpid()),
    )
    runtime.start_process()
    server = OperatorApiServer(runtime, host=args.host, port=args.port)

    def _handle_stop(_signum, _frame) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    print(
        f"research-osd listening on http://{args.host}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        runtime.drain()
        worker.shutdown()
        engine.dispose()
    return 0


class _UnavailableModel:
    def complete(self, request):
        raise RuntimeError("model runtime is unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
