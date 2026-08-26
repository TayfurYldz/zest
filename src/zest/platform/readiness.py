"""Structured runtime readiness. One available=True boolean is not sufficient.

Health (HEALTHY/DEGRADED/...) is distinct from capability/readiness stages.
Scripted baselines never count as GATE 04B ModelRuntime configurations.
Strix is not a Zest ModelRuntime unless an explicit adapter exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from zest.safe_data import reject_secret_keys


class ReadinessStage(Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    INSTALLED = "INSTALLED"
    VERSION_KNOWN = "VERSION_KNOWN"
    AUTH_READY = "AUTH_READY"
    DEPENDENCIES_READY = "DEPENDENCIES_READY"
    DIAGNOSTIC_READY = "DIAGNOSTIC_READY"
    MODELPORT_COMPATIBLE = "MODELPORT_COMPATIBLE"
    BENCHMARK_COMPATIBLE = "BENCHMARK_COMPATIBLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RuntimeReadiness:
    installed: bool
    version_known: bool
    auth_ready: bool
    dependencies_ready: bool
    diagnostic_ready: bool
    modelport_compatible: bool
    benchmark_compatible: bool
    stage: ReadinessStage
    detail: str
    version: str | None = None
    executable: str | None = None

    def __post_init__(self) -> None:
        reject_secret_keys({"detail": self.detail}, "runtime_readiness")
        if self.benchmark_compatible and not self.modelport_compatible:
            raise ValueError("BENCHMARK_COMPATIBLE requires MODELPORT_COMPATIBLE")
        if self.benchmark_compatible and not self.auth_ready:
            raise ValueError("BENCHMARK_COMPATIBLE requires AUTH_READY")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version_known": self.version_known,
            "auth_ready": self.auth_ready,
            "dependencies_ready": self.dependencies_ready,
            "diagnostic_ready": self.diagnostic_ready,
            "modelport_compatible": self.modelport_compatible,
            "benchmark_compatible": self.benchmark_compatible,
            "stage": self.stage.value,
            "detail": self.detail,
            "version": self.version,
            "executable": self.executable,
            "contains_secrets": False,
        }


def readiness_from_flags(
    *,
    installed: bool,
    version_known: bool = False,
    auth_ready: bool = False,
    dependencies_ready: bool = False,
    diagnostic_ready: bool = False,
    modelport_compatible: bool = False,
    benchmark_compatible: bool = False,
    detail: str,
    version: str | None = None,
    executable: str | None = None,
) -> RuntimeReadiness:
    if not installed:
        stage = ReadinessStage.NOT_INSTALLED
    elif benchmark_compatible:
        stage = ReadinessStage.BENCHMARK_COMPATIBLE
    elif modelport_compatible:
        stage = ReadinessStage.MODELPORT_COMPATIBLE
    elif diagnostic_ready:
        stage = ReadinessStage.DIAGNOSTIC_READY
    elif dependencies_ready:
        stage = ReadinessStage.DEPENDENCIES_READY
    elif auth_ready:
        stage = ReadinessStage.AUTH_READY
    elif version_known:
        stage = ReadinessStage.VERSION_KNOWN
    else:
        stage = ReadinessStage.INSTALLED
    return RuntimeReadiness(
        installed=installed,
        version_known=version_known,
        auth_ready=auth_ready,
        dependencies_ready=dependencies_ready,
        diagnostic_ready=diagnostic_ready,
        modelport_compatible=modelport_compatible,
        benchmark_compatible=benchmark_compatible,
        stage=stage,
        detail=detail,
        version=version,
        executable=executable,
    )


def mapping_without_secrets(payload: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    cleaned = reject_secret_keys(dict(payload), field_name)
    if not isinstance(cleaned, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return cleaned
