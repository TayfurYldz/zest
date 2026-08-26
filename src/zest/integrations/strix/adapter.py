"""Strix adapter skeleton. Untrusted Integration. Does not write the SoR."""

from __future__ import annotations

from zest.platform.argv_process import ArgvProcessStatus, resolve_executable, run_argv
from zest.platform.readiness import readiness_from_flags
from zest.platform.strix import (
    ALLOWED_STRIX_CAPABILITIES,
    UNRESTRICTED_CAPABILITY_MARKERS,
    StrixExecutionOutcome,
    StrixExecutionRequest,
    StrixRuntimeStatus,
)


def probe_strix_runtime() -> dict[str, object]:
    path = resolve_executable("strix")
    docker = resolve_executable("docker")
    if path is None:
        readiness = readiness_from_flags(
            installed=False,
            detail="strix executable not found on PATH",
        )
        return {
            "available": False,
            "installed": False,
            "healthy": False,
            "outcome": "UNAVAILABLE",
            "detail": readiness.detail,
            "unavailable_is_not_architecture_failure": True,
            "readiness": readiness.to_mapping(),
        }
    result = run_argv((path, "--version"), timeout_ms=5_000)
    version_known = result.status is ArgvProcessStatus.COMPLETED
    version = (result.stdout or result.stderr or "").strip().splitlines()[0][:200] if version_known else None
    dependencies_ready = docker is not None
    diagnostic_ready = False
    if version_known and dependencies_ready:
        ping = run_argv((path, "--version"), timeout_ms=5_000)
        diagnostic_ready = ping.status is ArgvProcessStatus.COMPLETED
    healthy = version_known and dependencies_ready and diagnostic_ready
    detail = (
        "strix executable and sandbox/docker dependency are ready"
        if healthy
        else (
            "strix executable found but sandbox/docker dependency is unavailable"
            if version_known and not dependencies_ready
            else (result.stdout or result.stderr or result.reason or "strix probe failed")[:200]
        )
    )
    readiness = readiness_from_flags(
        installed=True,
        version_known=version_known,
        auth_ready=False,
        dependencies_ready=dependencies_ready,
        diagnostic_ready=diagnostic_ready,
        modelport_compatible=False,
        benchmark_compatible=False,
        detail=detail,
        version=version,
        executable=path,
    )
    return {
        "available": healthy,
        "installed": True,
        "healthy": healthy,
        "outcome": "COMPLETED" if healthy else "DEGRADED" if version_known else "UNAVAILABLE",
        "executable": path,
        "docker": docker is not None,
        "detail": detail,
        "unavailable_is_not_architecture_failure": True,
        "readiness": readiness.to_mapping(),
        "strix_is_not_model_runtime": True,
    }


class StrixDiagnosticAdapter:
    """Diagnostic-only StrixIntegration. Security scanning workflows are deferred."""

    def __init__(self, *, executable: str | None = None) -> None:
        self._executable = executable
        self.calls: list[StrixExecutionRequest] = []

    def execute(self, request: StrixExecutionRequest) -> StrixExecutionOutcome:
        self.calls.append(request)
        lowered = {item.lower() for item in request.allowed_capabilities}
        if lowered & UNRESTRICTED_CAPABILITY_MARKERS:
            return StrixExecutionOutcome(
                status=StrixRuntimeStatus.DENIED,
                untrusted=True,
                capability=request.capability,
                reason_codes=("UNRESTRICTED_CAPABILITY_REJECTED",),
                payload={"not_observation": True},
            )
        if request.capability not in request.allowed_capabilities:
            return StrixExecutionOutcome(
                status=StrixRuntimeStatus.DENIED,
                untrusted=True,
                capability=request.capability,
                reason_codes=("CAPABILITY_NOT_ALLOWLISTED",),
                payload={"not_observation": True},
            )
        if request.capability not in ALLOWED_STRIX_CAPABILITIES:
            return StrixExecutionOutcome(
                status=StrixRuntimeStatus.DENIED,
                untrusted=True,
                capability=request.capability,
                reason_codes=("NON_DIAGNOSTIC_STRIX_CAPABILITY_DEFERRED",),
                payload={"not_observation": True},
            )
        executable = self._executable or resolve_executable("strix")
        if executable is None:
            return StrixExecutionOutcome(
                status=StrixRuntimeStatus.UNAVAILABLE,
                untrusted=True,
                capability=request.capability,
                reason_codes=("STRIX_RUNTIME_UNAVAILABLE",),
                payload={"not_observation": True, "not_evidence": True},
            )
        result = run_argv((executable, "--version"), timeout_ms=5_000)
        if result.status is not ArgvProcessStatus.COMPLETED:
            return StrixExecutionOutcome(
                status=StrixRuntimeStatus.PROCESS_FAILED,
                untrusted=True,
                capability=request.capability,
                reason_codes=("STRIX_DIAGNOSTIC_PROCESS_FAILED",),
                payload={"not_observation": True, "not_evidence": True},
            )
        return StrixExecutionOutcome(
            status=StrixRuntimeStatus.COMPLETED,
            untrusted=True,
            capability=request.capability,
            reason_codes=("STRIX_DIAGNOSTIC_PING", "NOT_OBSERVATION", "NOT_EVIDENCE"),
            payload={
                "untrusted": True,
                "echo": "pong",
                "version_text": (result.stdout or "").strip()[:200],
                "not_observation": True,
                "not_evidence": True,
                "not_candidate": True,
                "not_finding": True,
            },
        )
