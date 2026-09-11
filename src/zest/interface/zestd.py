"""zestd process entrypoint. Composition root only."""

from __future__ import annotations

from dataclasses import replace

import argparse
import logging
import os
import signal
import sys
import threading
import time
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


MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS = 60.0
MODEL_CAPACITY_RECONCILE_LOOP_SECONDS = 5.0


class _ObservedModelReadinessState:
    """Thread-safe last-observed operational ModelRuntime health.

    Startup qualification is immutable. This state is observational only and
    is updated exclusively by actual ModelPort calls. Reading it causes no
    provider request and grants no authority.
    """

    def __init__(
        self,
        initial: ModelReadinessInput,
        *,
        capacity_probe=None,
        wall_clock=None,
    ) -> None:
        self._lock = threading.Lock()
        self._value = initial
        self._capacity_probe = capacity_probe
        self._wall_clock = (
            wall_clock
            if wall_clock is not None
            else time.time
        )
        self._next_capacity_probe_at = 0.0

    def snapshot(self) -> ModelReadinessInput:
        with self._lock:
            return self._value

    def publish_readiness(
        self,
        readiness: ModelReadinessInput,
    ) -> None:
        """Publish externally re-qualified runtime truth atomically."""
        with self._lock:
            self._value = readiness
            self._next_capacity_probe_at = 0.0

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
            self._next_capacity_probe_at = 0.0

    def reconcile_usage_capacity(self) -> bool:
        """Reconcile stale usage-limit health from provider metadata.

        No model turn is started. False means either no reconciliation
        was due or the provider did not prove capacity available.
        """
        if self._capacity_probe is None:
            return False

        now = float(
            self._wall_clock()
        )

        with self._lock:
            current = self._value

            if not (
                current.health.health
                is ComponentHealth.RATE_LIMITED
                and current.health.detail
                == "last_runtime_outcome=MODEL_USAGE_LIMITED"
            ):
                return False

            if (
                now
                < self._next_capacity_probe_at
            ):
                return False

            # Reserve the next probe slot before the network read.
            # This prevents concurrent callers from multiplying reads.
            self._next_capacity_probe_at = (
                now
                + MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS
            )

        snapshot = self._capacity_probe()

        with self._lock:
            current = self._value

            # A real ModelPort success/failure may have superseded this
            # metadata read while it was in flight.
            if not (
                current.health.health
                is ComponentHealth.RATE_LIMITED
                and current.health.detail
                == "last_runtime_outcome=MODEL_USAGE_LIMITED"
            ):
                return False

            if snapshot.available is True:
                candidate = current.candidate

                if candidate is not None:
                    candidate = replace(
                        candidate,
                        available=True,
                    )

                self._value = ModelReadinessInput(
                    candidate=candidate,
                    health=HealthCheck(
                        "model",
                        ComponentHealth.HEALTHY,
                        "provider_capacity_snapshot=AVAILABLE",
                    ),
                )
                self._next_capacity_probe_at = 0.0
                return True

            next_probe_at = (
                now
                + MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS
            )

            reset_at = getattr(
                snapshot,
                "reset_at",
                None,
            )

            if (
                isinstance(reset_at, int)
                and not isinstance(
                    reset_at,
                    bool,
                )
                and reset_at > now
            ):
                next_probe_at = min(
                    next_probe_at,
                    float(reset_at),
                )

            self._next_capacity_probe_at = (
                next_probe_at
            )

            return False

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

            if isinstance(
                exc,
                ProviderUsageLimitError,
            ):
                self._next_capacity_probe_at = 0.0


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
    def capacity_domain_id(self) -> str | None:
        value = getattr(
            self._inner,
            "capacity_domain_id",
            None,
        )

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            return None

        return value.strip()

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


def _compose_codex_model(
    configurations,
    *,
    probe_codex=None,
    probe_codex_capacity=None,
):
    """Qualify once; observe later runtime health without active health probes."""

    from zest.integrations.models.cli_session import (
        CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
        CodexCliSessionAdapter,
        probe_codex_account_capacity,
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
            (lambda: False),
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

    recoverable_startup_rate_limit = (
        not qualified
        and availability.outcome
        is RuntimeOutcome.RATE_LIMITED
        and runtime_readiness is not None
        and runtime_readiness.auth_ready is True
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
        if recoverable_startup_rate_limit:
            model = CodexCliSessionAdapter(
                allowed_capabilities=(
                    CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,
                ),
                executable=configuration.executable,
                version=availability.version,
                model=configuration.model,
                configuration_id=(
                    configuration.configuration_id
                ),
            )

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

            identity = model.runtime_identity
        else:
            model = _UnavailableModel()
            fallback_models = ()
            identity = cli_session_runtime_identity(
                adapter_id="codex.cli.session",
                runtime_id=(
                    configuration.configuration_id
                ),
                runtime_version=availability.version,
                session_reference=(
                    "local-authenticated-cli-session"
                ),
                model_id=configuration.model,
                runtime_configuration=(
                    configuration.runtime_configuration()
                ),
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

    if (
        not qualified
        and not recoverable_startup_rate_limit
    ):
        return (
            model,
            fallback_models,
            startup_probe,
            startup_probe,
            (lambda: False),
        )

    capacity_probe = (
        probe_codex_capacity
        if probe_codex_capacity is not None
        else probe_codex_account_capacity
    )

    if recoverable_startup_rate_limit:
        qualification_state = (
            _ObservedModelReadinessState(
                readiness
            )
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

        startup_recovery_lock = (
            threading.Lock()
        )

        startup_next_probe_at = [0.0]

        def _publish_startup_requalification(
            refreshed,
        ) -> bool:
            refreshed_runtime = (
                refreshed.readiness
            )

            requalified = (
                refreshed.available is True
                and refreshed_runtime
                is not None
                and refreshed_runtime.auth_ready
                is True
                and refreshed_runtime.diagnostic_ready
                is True
                and refreshed_runtime.modelport_compatible
                is True
                and refreshed_runtime.benchmark_compatible
                is True
                and refreshed.outcome
                is RuntimeOutcome.COMPLETED
            )

            health_by_outcome = {
                RuntimeOutcome.AUTH_FAILED:
                    ComponentHealth.AUTH_REQUIRED,
                RuntimeOutcome.RATE_LIMITED:
                    ComponentHealth.RATE_LIMITED,
                RuntimeOutcome.CONTENT_POLICY_BLOCKED:
                    ComponentHealth.BLOCKED_POLICY,
            }

            refreshed_health = HealthCheck(
                "model",
                (
                    ComponentHealth.HEALTHY
                    if requalified
                    else health_by_outcome.get(
                        refreshed.outcome,
                        ComponentHealth.UNAVAILABLE,
                    )
                ),
                (
                    "startup_requalification=COMPLETED"
                    if requalified
                    else refreshed.detail
                ),
            )

            refreshed_candidate = (
                RuntimeCandidate(
                    identity=identity,
                    available=(
                        refreshed.available is True
                    ),
                    authenticated=(
                        refreshed_runtime
                        is not None
                        and refreshed_runtime.auth_ready
                        is True
                    ),
                    structured_output_compatible=(
                        requalified
                    ),
                    locality=CandidateLocality.LOCAL,
                )
            )

            refreshed_readiness = (
                ModelReadinessInput(
                    candidate=refreshed_candidate,
                    health=refreshed_health,
                )
            )

            qualification_state.publish_readiness(
                refreshed_readiness
            )

            observed.publish_readiness(
                refreshed_readiness
            )

            return requalified

        def reconcile_startup_rate_limit(
            *,
            require_live: bool = False,
        ) -> bool:
            if require_live:
                try:
                    refreshed = probe(
                        configuration=configuration,
                        live_probe=True,
                    )
                except Exception:
                    return False

                return _publish_startup_requalification(
                    refreshed
                )

            now = time.time()

            with startup_recovery_lock:
                current_qualification = (
                    qualification_state.snapshot()
                )

                if (
                    current_qualification.health.health
                    is not ComponentHealth.RATE_LIMITED
                ):
                    return False

                if (
                    now
                    < startup_next_probe_at[0]
                ):
                    return False

                startup_next_probe_at[0] = (
                    now
                    + MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS
                )

            try:
                capacity = capacity_probe(
                    executable_name=(
                        configuration.executable
                    )
                )
            except Exception:
                return False

            if capacity.available is not True:
                reset_at = getattr(
                    capacity,
                    "reset_at",
                    None,
                )

                next_probe_at = (
                    now
                    + MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS
                )

                if (
                    isinstance(
                        reset_at,
                        int,
                    )
                    and not isinstance(
                        reset_at,
                        bool,
                    )
                    and reset_at > now
                ):
                    next_probe_at = min(
                        next_probe_at,
                        float(reset_at),
                    )

                with startup_recovery_lock:
                    startup_next_probe_at[0] = (
                        next_probe_at
                    )

                return False

            # Provider metadata is only a capacity hint.
            # A request-consuming live diagnostic is still required
            # before startup qualification can become READY.
            try:
                refreshed = probe(
                    configuration=configuration,
                    live_probe=True,
                )
            except Exception:
                return False

            requalified = (
                _publish_startup_requalification(
                    refreshed
                )
            )

            with startup_recovery_lock:
                if requalified:
                    startup_next_probe_at[0] = 0.0
                elif (
                    refreshed.outcome
                    is RuntimeOutcome.RATE_LIMITED
                ):
                    startup_next_probe_at[0] = (
                        now
                        + MODEL_CAPACITY_RECONCILE_MAX_INTERVAL_SECONDS
                    )

            return requalified

        return (
            model,
            fallback_models,
            qualification_state.snapshot,
            observed.snapshot,
            reconcile_startup_rate_limit,
        )

    observed = _ObservedModelReadinessState(
        readiness,
        capacity_probe=(
            lambda: capacity_probe(
                executable_name=(
                    configuration.executable
                )
            )
        ),
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

    def reconcile_model_health(
        *,
        require_live: bool = False,
    ) -> bool:
        if not require_live:
            return (
                observed.reconcile_usage_capacity()
            )

        try:
            refreshed = probe(
                configuration=configuration,
                live_probe=True,
            )
        except Exception:
            return False

        runtime = refreshed.readiness

        requalified = (
            refreshed.available is True
            and runtime is not None
            and runtime.auth_ready is True
            and runtime.diagnostic_ready is True
            and runtime.modelport_compatible is True
            and runtime.benchmark_compatible is True
            and refreshed.outcome
            is RuntimeOutcome.COMPLETED
        )

        health_by_outcome = {
            RuntimeOutcome.AUTH_FAILED:
                ComponentHealth.AUTH_REQUIRED,
            RuntimeOutcome.RATE_LIMITED:
                ComponentHealth.RATE_LIMITED,
            RuntimeOutcome.CONTENT_POLICY_BLOCKED:
                ComponentHealth.BLOCKED_POLICY,
        }

        current = observed.snapshot()
        candidate = current.candidate

        if candidate is not None:
            candidate = replace(
                candidate,
                available=(
                    refreshed.available is True
                ),
                authenticated=(
                    runtime is not None
                    and runtime.auth_ready is True
                ),
                structured_output_compatible=(
                    requalified
                ),
            )

        observed.publish_readiness(
            ModelReadinessInput(
                candidate=candidate,
                health=HealthCheck(
                    "model",
                    (
                        ComponentHealth.HEALTHY
                        if requalified
                        else health_by_outcome.get(
                            refreshed.outcome,
                            ComponentHealth.UNAVAILABLE,
                        )
                    ),
                    (
                        "capacity_requalification=COMPLETED"
                        if requalified
                        else refreshed.detail
                    ),
                ),
            )
        )

        return requalified

    return (
        model,
        fallback_models,
        startup_probe,
        observed.snapshot,
        reconcile_model_health,
    )


def _model_capacity_reconcile_loop(
    stop_event,
    reconcile,
    recover,
    *,
    reconcile_success_proves_live: bool = False,
    interval_seconds: float = (
        MODEL_CAPACITY_RECONCILE_LOOP_SECONDS
    ),
) -> None:
    while not stop_event.wait(
        interval_seconds
    ):
        reconciled = False

        try:
            reconciled = (
                reconcile() is True
            )
        except Exception as exc:
            logging.getLogger(
                "zest.zestd"
            ).warning(
                "model.capacity_reconcile_failed "
                "type=%s",
                exc.__class__.__name__,
            )

        try:
            recover(
                live_already_confirmed=(
                    reconciled
                    and reconcile_success_proves_live
                )
            )
        except Exception as exc:
            logging.getLogger(
                "zest.zestd"
            ).warning(
                "model.capacity_run_recovery_failed "
                "type=%s",
                exc.__class__.__name__,
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
        reconcile_model_health,
    ) = _compose_codex_model(
        configurations
    )

    # Only the startup-under-rate-limit reconciler performs a
    # request-consuming live diagnostic in its default path.
    # Normal runtime reconciliation is metadata-only.
    reconcile_success_proves_live = (
        model_probe().health.health
        is ComponentHealth.RATE_LIMITED
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
        probe_model_recovery=(
            lambda: reconcile_model_health(
                require_live=True
            )
        ),
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

    model_reconcile_thread = threading.Thread(
        target=_model_capacity_reconcile_loop,
        args=(
            stop,
            reconcile_model_health,
            runtime.recover_model_usage_limited_runs,
        ),
        kwargs={
            "reconcile_success_proves_live": (
                reconcile_success_proves_live
            ),
        },
        name="zest-model-capacity-reconciler",
        daemon=True,
    )

    try:
        model_reconcile_thread.start()
        stop.wait()
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()

        if model_reconcile_thread.is_alive():
            model_reconcile_thread.join(
                timeout=20
            )

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
