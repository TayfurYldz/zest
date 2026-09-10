"""zestd process entrypoint. Composition root only."""

from __future__ import annotations

from dataclasses import replace

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
from zest.application.runtime_outcomes import (
    runtime_outcome_from_exception,
)
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
from zest.research.model_port import (
    ModelPortError,
    ProviderUsageLimitError,
)
from zest.research.model_runtime import RuntimeOutcome
from zest.integrations.observer import NvidiaCompatibleObserverProvider
from zest.tools.registry import load_capability_registry


def _alembic_ini() -> str:
    return str(resolve_alembic_ini(os.environ, source_file=Path(__file__)))


class _ObservedModelReadinessState:
    """Thread-safe last-observed operational ModelRuntime health.

    Startup qualification is immutable. This state is observational only and
    is updated exclusively by actual ModelPort calls. Reading it causes no
    provider request and grants no authority.
    """

    def __init__(
        self,
        initial: ModelReadinessInput,
    ) -> None:
        self._lock = threading.Lock()
        self._value = initial

    def snapshot(self) -> ModelReadinessInput:
        with self._lock:
            return self._value

    def record_success(self) -> None:
        with self._lock:
            current = self._value
            candidate = current.candidate

            if candidate is not None:
                candidate = replace(
                    candidate,
                    available=True,
                    authenticated=True,
                    structured_output_compatible=True,
                )

            self._value = ModelReadinessInput(
                candidate=candidate,
                health=HealthCheck(
                    "model",
                    ComponentHealth.HEALTHY,
                    "last_runtime_outcome=COMPLETED",
                ),
            )

    def record_failure(
        self,
        exc: ModelPortError,
    ) -> None:
        outcome = runtime_outcome_from_exception(
            exc
        )

        # Cancellation is a control-plane/runtime lifecycle event, not proof
        # that the provider itself became unhealthy.
        if outcome is RuntimeOutcome.CANCELLED:
            return

        health_by_outcome = {
            RuntimeOutcome.AUTH_FAILED:
                ComponentHealth.AUTH_REQUIRED,
            RuntimeOutcome.RATE_LIMITED:
                ComponentHealth.RATE_LIMITED,
            RuntimeOutcome.CONTENT_POLICY_BLOCKED:
                ComponentHealth.BLOCKED_POLICY,
        }

        health = health_by_outcome.get(
            outcome,
            ComponentHealth.UNAVAILABLE,
        )

        if isinstance(
            exc,
            ProviderUsageLimitError,
        ):
            detail = (
                "last_runtime_outcome="
                "MODEL_USAGE_LIMITED"
            )
        elif outcome is RuntimeOutcome.RATE_LIMITED:
            detail = (
                "last_runtime_outcome="
                "MODEL_RATE_LIMITED"
            )
        else:
            detail = (
                "last_runtime_outcome="
                f"{outcome.value}"
            )

        with self._lock:
            current = self._value
            candidate = current.candidate

            if candidate is not None:
                candidate = replace(
                    candidate,
                    available=False,
                    authenticated=(
                        False
                        if outcome
                        is RuntimeOutcome.AUTH_FAILED
                        else candidate.authenticated
                    ),
                    structured_output_compatible=(
                        False
                        if outcome in {
                            RuntimeOutcome.STRUCTURED_OUTPUT_INVALID,
                            RuntimeOutcome.PROTOCOL_ERROR,
                        }
                        else candidate.structured_output_compatible
                    ),
                )

            self._value = ModelReadinessInput(
                candidate=candidate,
                health=HealthCheck(
                    "model",
                    health,
                    detail,
                ),
            )


class _ObservedModelPort:
    """Transparent ModelPort wrapper that publishes operational health."""

    def __init__(
        self,
        inner,
        state: _ObservedModelReadinessState,
    ) -> None:
        self._inner = inner
        self._state = state

    @property
    def runtime_identity(self):
        return self._inner.runtime_identity

    @property
    def adapter_identity(self):
        return self._inner.adapter_identity

    def complete(self, request):
        try:
            result = self._inner.complete(
                request
            )
        except ModelPortError as exc:
            self._state.record_failure(
                exc
            )
            raise

        self._state.record_success()
        return result


def _compose_codex_model(configurations, *, probe_codex=None):
    """Qualify once; observe later runtime health without active health probes."""

    from zest.integrations.models.cli_session import (
        CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
        CodexCliSessionAdapter,
        probe_codex_cli,
    )
    from zest.research.model_runtime import RuntimeOutcome, cli_session_runtime_identity
    from zest.research.routing import CandidateLocality, RuntimeCandidate

    configuration = configurations[0] if configurations else None
    if configuration is None:
        readiness = ModelReadinessInput(
            candidate=None,
            health=HealthCheck(
                "model",
                ComponentHealth.UNAVAILABLE,
                "no Codex model configuration selected",
            ),
        )
        static_probe = (
            lambda readiness=readiness: readiness
        )
        return (
            _UnavailableModel(),
            (),
            static_probe,
            static_probe,
        )

    probe = probe_codex or probe_codex_cli
    availability = probe(configuration=configuration, live_probe=True)
    runtime_readiness = availability.readiness
    qualified = (
        availability.available is True
        and runtime_readiness is not None
        and runtime_readiness.auth_ready is True
        and runtime_readiness.diagnostic_ready is True
        and runtime_readiness.modelport_compatible is True
        and runtime_readiness.benchmark_compatible is True
        and availability.outcome is RuntimeOutcome.COMPLETED
    )

    if qualified:
        model = CodexCliSessionAdapter(
            allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
            executable=configuration.executable,
            version=availability.version,
            model=configuration.model,
            configuration_id=configuration.configuration_id,
        )
        identity = model.runtime_identity
        health = HealthCheck(
            "model",
            ComponentHealth.HEALTHY,
            availability.detail,
        )

        # Secondary configurations are operator-declared failover
        # runtimes. They are not used for startup qualification and
        # therefore do not add request-consuming startup probes.
        # The same strict adapter/schema path still fails closed when
        # a fallback is actually invoked.
        fallback_models = tuple(
            CodexCliSessionAdapter(
                allowed_capabilities=(
                    CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
                ),
                executable=item.executable,
                version=availability.version,
                model=item.model,
                configuration_id=(
                    item.configuration_id
                ),
            )
            for item in configurations[1:]
        )
    else:
        model = _UnavailableModel()
        fallback_models = ()
        identity = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id=configuration.configuration_id,
            runtime_version=availability.version,
            session_reference="local-authenticated-cli-session",
            model_id=configuration.model,
            runtime_configuration=configuration.runtime_configuration(),
        )
        health_by_outcome = {
            RuntimeOutcome.AUTH_FAILED: ComponentHealth.AUTH_REQUIRED,
            RuntimeOutcome.RATE_LIMITED: ComponentHealth.RATE_LIMITED,
            RuntimeOutcome.CONTENT_POLICY_BLOCKED: ComponentHealth.BLOCKED_POLICY,
        }
        health = HealthCheck(
            "model",
            health_by_outcome.get(availability.outcome, ComponentHealth.UNAVAILABLE),
            availability.detail,
        )

    readiness = ModelReadinessInput(
        candidate=RuntimeCandidate(
            identity=identity,
            available=availability.available is True,
            authenticated=(
                runtime_readiness is not None and runtime_readiness.auth_ready is True
            ),
            structured_output_compatible=qualified,
            locality=CandidateLocality.LOCAL,
        ),
        health=health,
    )
    startup_probe = (
        lambda readiness=readiness: readiness
    )

    if not qualified:
        return (
            model,
            fallback_models,
            startup_probe,
            startup_probe,
        )

    observed = _ObservedModelReadinessState(
        readiness
    )

    model = _ObservedModelPort(
        model,
        observed,
    )

    fallback_models = tuple(
        _ObservedModelPort(
            item,
            observed,
        )
        for item in fallback_models
    )

    return (
        model,
        fallback_models,
        startup_probe,
        observed.snapshot,
    )


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
        load_codex_model_configurations,
    )
    from zest.platform.persistent_browser_worker import PersistentBrowserWorkerAdapter

    worker = PersistentBrowserWorkerAdapter()
    configurations = load_codex_model_configurations(os.environ)
    (
        model,
        fallback_models,
        model_probe,
        model_health_probe,
    ) = _compose_codex_model(
        configurations
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
        fallback_models=fallback_models,
        lease_config=LeaseConfig(
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
            lease_ttl_seconds=settings.lease_ttl_seconds,
        ),
        probe_schema=probe_schema,
        probe_worker=probe_worker,
        probe_model=model_probe,
        probe_model_health=model_health_probe,
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
