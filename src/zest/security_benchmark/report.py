"""Immutable security-benchmark report artifacts. Do not overwrite prior reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from zest.security_benchmark.types import BENCHMARK_VERSION


class _ScorecardMapping(Protocol):
    benchmark_version: str

    def to_mapping(self) -> dict[str, Any]: ...


class SecurityBenchmarkReportError(ValueError):
    pass


def write_immutable_report(
    directory: Path,
    scorecard: _ScorecardMapping,
    *,
    postgresql_backed: bool,
    source_commit: str = "unknown",
    created_at: datetime | None = None,
    extra: Mapping[str, Any] | None = None,
    report_prefix: str = "gate15",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp_source = created_at or datetime.now(timezone.utc)
    stamp = stamp_source.strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{stamp}_{report_prefix}_{scorecard.benchmark_version.replace('.', '_')}.json"
    if path.exists():
        raise SecurityBenchmarkReportError(f"refusing to overwrite security benchmark report: {path}")
    payload = {
        "benchmark_version": scorecard.benchmark_version or BENCHMARK_VERSION,
        "created_at": stamp_source.isoformat(),
        "postgresql_backed": postgresql_backed,
        "source_commit": source_commit,
        "contains_secrets": False,
        "scorecard": scorecard.to_mapping(),
    }
    if extra:
        payload.update(dict(extra))
    serialized = json.dumps(payload, indent=2, ensure_ascii=True)
    lowered = serialized.lower()
    for marker in ("password=", "password:", "postgresql+psycopg://", "sk-", "api_key"):
        if marker in lowered:
            raise SecurityBenchmarkReportError("report must not contain secret material")
    path.write_text(serialized + "\n", encoding="utf-8")
    return path
