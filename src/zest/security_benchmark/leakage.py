"""Detect ground-truth leakage into pipeline inputs. Not a security scanner."""

from __future__ import annotations

import json
from typing import Any, Mapping

from zest.security_benchmark.scenarios import SecurityGroundTruthScenario
from zest.security_benchmark.types import FORBIDDEN_PIPELINE_KEYS, FORBIDDEN_PIPELINE_LABELS


def pipeline_blob(values: Mapping[str, Any] | list[Any] | tuple[Any, ...] | str | None) -> str:
    return json.dumps(values, sort_keys=True, default=str, ensure_ascii=True)


def leakage_hits(
    scenario: SecurityGroundTruthScenario,
    *blobs: object,
) -> tuple[str, ...]:
    hits: list[str] = []
    canary = scenario.hidden_evaluation.leakage_canary
    for blob in blobs:
        text = blob if isinstance(blob, str) else pipeline_blob(blob)  # type: ignore[arg-type]
        lowered = text.lower()
        if canary and canary in text:
            hits.append("leakage_canary")
        if "hidden_evaluation" in lowered:
            hits.append("hidden_evaluation")
        for key in FORBIDDEN_PIPELINE_KEYS:
            if f'"{key}"' in text or f"'{key}'" in text:
                hits.append(key)
        for label in FORBIDDEN_PIPELINE_LABELS:
            if label in text:
                hits.append(label)
    return tuple(dict.fromkeys(hits))
