"""zestd process entrypoint. Composition root only."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from zest.application.orchestration_lease import LeaseConfig
from zest.application.observer import ObserverService, ObserverSettings
from zest.application.osd_settings import load_osd_settings, resolve_alembic_ini
from zest.application.preflight import (
    ModelReadinessInput,
    SchemaHealthInput,
    WorkerReadinessInput,
)
from zest.application.zestd import ZestdRuntime
from zest.data.postgres.engine import (
    check_schema_head,
    create_sync_engine,
)
from zest.data.postgres.unit_of_work import PostgresUnitOfWork
from zest.interface.operator_api import OperatorApiServer
from zest.platform.health import ComponentHealth, HealthCheck
from zest.platform.browser_resource_control import BrowserResourceLimits, browser_resource_controller
from zest.platform.worker_health import probe_local_python_worker
from zest.integrations.observer import NvidiaCompatibleObserverProvider
from zest.tools.registry import load_capability_registry


def _alembic_ini() -> str:
    return str(resolve_alembic_ini(os.environ, source_file=Path(__file__)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zestd")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        settings = load_osd_settings(dict(os.environ))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    log_kwargs: dict[str, object] = {"level": logging.INFO, "format": "%(message)s"}
    if settings.log_path:
        log_kwargs["filename"] = settings.log_path
    logging.basicConfig(**log_kwargs)
    host = args.host or settings.bind_host
    port = settings.api_port if args.port is None else args.port
    engine = create_sync_engine(settings.database_url)
    factory = PostgresUnitOfWork(engine)
    from zest.integrations.models.cli_session import (
        CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
        CodexCliSessionAdapter,
        load_codex_model_configurations,
        probe_codex_cli,
    )
    from zest.platform.persistent_browser_worker import PersistentBrowserWorkerAdapter
    from zest.research.model_runtime import api_runtime_identity
    from zest.research.routing import CandidateLocality, RuntimeCandidate

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
        try:
            ok, detail = check_schema_head(engine, alembic_ini_path=_alembic_ini())
        except (OSError, ValueError) as exc:
            return SchemaHealthInput(at_expected_head=False, detail=exc.__class__.__name__)
        return SchemaHealthInput(at_expected_head=ok, detail=detail)

    def probe_worker() -> WorkerReadinessInput:
        health = probe_local_python_worker()
        browser_ready, browser_detail = worker.probe_startup_readiness()

        browser_health = HealthCheck(
            "browser-runtime",
            ComponentHealth.HEALTHY
            if browser_ready
            else ComponentHealth.UNAVAILABLE,
            browser_detail,
        )

        if health.health is ComponentHealth.HEALTHY and not browser_ready:
            health = HealthCheck(
                "worker",
                ComponentHealth.UNAVAILABLE,
                f"persistent browser worker unavailable: {browser_detail}",
            )

        capabilities = frozenset(
            definition.capability_id
            for definition in load_capability_registry().worker_definitions()
        )

        return WorkerReadinessInput(
            health=health,
            available_capabilities=capabilities,
            browser_containment=browser_health,
        )

    observer_settings = ObserverSettings.from_env(os.environ)
    observer_provider = None
    if (
        observer_settings.enabled
        and observer_settings.provider == "nvidia-compatible"
        and observer_settings.model
        and observer_settings.nvidia_api_key
    ):
        observer_provider = NvidiaCompatibleObserverProvider(
            api_key=observer_settings.nvidia_api_key,
            model=observer_settings.model,
            base_url=observer_settings.base_url,
            timeout_seconds=observer_settings.timeout_seconds,
        )
    runtime = ZestdRuntime(
        factory,
        worker,
        model,
        lease_config=LeaseConfig(
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
            lease_ttl_seconds=settings.lease_ttl_seconds,
        ),
        probe_schema=probe_schema,
        probe_worker=probe_worker,
        probe_model=model_probe,
        host_identity=os.uname().nodename or "zestd",
        process_id=str(os.getpid()),
        engine_version=settings.release_version,
        environment_name=settings.environment_name,
        observer_service=ObserverService(observer_provider, settings=observer_settings),
    )
    runtime.start_process()
    server = OperatorApiServer(runtime, host=host, port=port)
    stop = threading.Event()

    def _handle_stop(_signum, _frame) -> None:
        # Signal-safe for this process: only set an event. Do not call
        # HTTPServer.shutdown() here — that deadlocks if this handler
        # runs on the serve_forever thread (CPython socketserver contract).
        stop.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    server.start()
    bound_host, bound_port = server.address
    print(
        f"zestd listening on http://{bound_host}:{bound_port}",
        flush=True,
    )
    try:
        stop.wait()
    except KeyboardInterrupt:
        stop.set()
    finally:
        server.shutdown()
        runtime.drain()
        worker.shutdown()
        engine.dispose()
    return 0


class _UnavailableModel:
    def complete(self, request):
        raise RuntimeError("model runtime is unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
