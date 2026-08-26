"""Harmless local Worker health probe. Does not write authoritative research state."""

from __future__ import annotations

from zest.platform.health import ComponentHealth, HealthCheck
from zest.platform.local_process_worker import (
    LocalProcessWorkerAdapter,
    LocalProcessWorkerConfig,
)
from zest.platform.worker import InvocationStatus
from zest.tools.registry import load_capability_registry

_ECHO = load_capability_registry().get("diagnostic.echo")
assert _ECHO is not None

DIAGNOSTIC_WORKER_REQUEST = {
    "contract_version": "v1",
    "correlation": {
        "correlation_id": "health-probe-1",
        "research_run_id": "health-probe-run",
        "experiment_id": "health-probe-experiment",
        "request_id": "health-probe-request",
    },
    "worker_capability": "diagnostic.echo",
    "action": "echo",
    "target_reference": "health-probe-target",
    "authorization_decision_reference": "health-probe-authz",
    "execution_budget": {
        "budget_id": "health-probe-budget",
        "max_requests": 1,
        "max_tool_calls": 0,
        "max_runtime_ms": 5_000,
        "max_concurrency": 1,
    },
    "side_effect_level": 0,
    "capability_version": _ECHO.version,
    "capability_definition_fingerprint": _ECHO.definition_fingerprint,
    "secret_references": [],
    "arguments": {"message": "ping"},
}


def probe_local_python_worker(
    adapter: LocalProcessWorkerAdapter | None = None,
) -> HealthCheck:
    worker = adapter or LocalProcessWorkerAdapter(LocalProcessWorkerConfig())
    try:
        outcome = worker.invoke(DIAGNOSTIC_WORKER_REQUEST, timeout_ms=5_000)
    except OSError as exc:
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.UNAVAILABLE,
            detail=f"worker probe failed to start ({type(exc).__name__})",
        )
    if outcome.invocation_status is InvocationStatus.START_FAILED:
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.UNAVAILABLE,
            detail="worker process failed to start",
        )
    if outcome.invocation_status is InvocationStatus.TIMED_OUT:
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.DEGRADED,
            detail="worker diagnostic probe timed out",
        )
    if outcome.invocation_status is not InvocationStatus.COMPLETED:
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.DEGRADED,
            detail=f"worker diagnostic probe {outcome.invocation_status.value}",
        )
    result = outcome.worker_result or {}
    status = result.get("status")
    correlation = result.get("correlation") if isinstance(result.get("correlation"), dict) else {}
    if status != "SUCCEEDED" or correlation.get("request_id") != "health-probe-request":
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.DEGRADED,
            detail="worker diagnostic protocol/correlation check failed",
        )
    if outcome.exit_code not in (0, None):
        return HealthCheck(
            component="local-python",
            health=ComponentHealth.DEGRADED,
            detail="worker diagnostic exited uncleanly",
        )
    return HealthCheck(
        component="local-python",
        health=ComponentHealth.HEALTHY,
        detail="diagnostic.echo probe succeeded",
    )
