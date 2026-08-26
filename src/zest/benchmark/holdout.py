"""Sealed holdout loader. Sealed contents do not belong in the development repo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from zest.benchmark.errors import BenchmarkError
from zest.benchmark.identity import DEFAULT_SUITE_ID
from zest.benchmark.scenarios import ScenarioSplit, load_scenarios
from zest.benchmark.suite import SuiteManifest, build_suite_manifest

HOLDOUT_PATH_ENV = "ZEST_BENCHMARK_HOLDOUT_PATH"


@dataclass(frozen=True)
class HoldoutLoad:
    available: bool
    reason: str
    scenarios: tuple = ()
    manifest: SuiteManifest | None = None
    path: str | None = None

    def to_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "available": self.available,
            "reason": self.reason,
            "path": self.path,
            "sealed_contents_omitted": True,
        }
        if self.manifest is not None:
            payload["manifest"] = self.manifest.to_mapping()
        return payload


def resolve_holdout_path(explicit: str | None = None) -> Path | None:
    raw = explicit if explicit is not None else os.environ.get(HOLDOUT_PATH_ENV)
    if raw is None or not str(raw).strip():
        return None
    return Path(raw)


def load_sealed_holdout(
    path: Path | None,
    *,
    suite_id: str = "zest.sealed-holdout.v1",
) -> HoldoutLoad:
    if path is None:
        return HoldoutLoad(
            available=False,
            reason="sealed holdout path not configured",
        )
    if not path.is_dir():
        return HoldoutLoad(
            available=False,
            reason=f"sealed holdout unavailable: {path}",
            path=str(path),
        )
    scenarios = load_scenarios(path, include_sealed=True)
    unexpected = [item.identity for item in scenarios if item.split is not ScenarioSplit.SEALED_HOLDOUT]
    if unexpected:
        raise BenchmarkError(
            "sealed holdout directory must contain only sealed_holdout scenarios: "
            f"{unexpected}"
        )
    if not scenarios:
        return HoldoutLoad(
            available=False,
            reason="sealed holdout directory has no scenarios",
            path=str(path),
        )
    manifest = build_suite_manifest(scenarios, suite_id=suite_id, sealed=True)
    return HoldoutLoad(
        available=True,
        reason="loaded",
        scenarios=scenarios,
        manifest=manifest,
        path=str(path),
    )


def development_suite_id() -> str:
    return DEFAULT_SUITE_ID
