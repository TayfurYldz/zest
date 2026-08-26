"""Protocol/parser specialist planning without active payload construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping

from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError

PROTOCOL_PARSER_PLAN_VERSION = "protocol.parser.v1"
MIN_PROTOCOL_PLAN_STEPS = 8
MAX_PROTOCOL_PLAN_STEPS = 48

PROTOCOL_DIMENSION_VALUES: Mapping[str, tuple[str, ...]] = {
    "frontend_protocol": ("http1", "http2", "h2c_upgrade"),
    "backend_protocol": ("http1", "http2", "unknown_backend"),
    "normalization_boundary": (
        "content_length_transfer_encoding",
        "connection_reuse",
        "header_case_fold",
        "absolute_uri",
    ),
    "cache_key_dimension": ("host", "scheme", "path", "query", "header"),
    "cache_behavior": ("store", "bypass", "vary", "stale_revalidate"),
    "proxy_layer": ("cdn", "reverse_proxy", "edge_cache", "origin_proxy"),
}


@dataclass(frozen=True)
class ProtocolParserPlanStep:
    family_id: str
    dimension_values: Mapping[str, str]
    control: str
    step_id: str

    def __post_init__(self) -> None:
        if not self.family_id.strip():
            raise ResearchInputError("family_id is required")
        if not self.step_id.strip():
            raise ResearchInputError("step_id is required")
        if not isinstance(self.dimension_values, Mapping) or not self.dimension_values:
            raise ResearchInputError("dimension_values must be a non-empty mapping")
        object.__setattr__(self, "dimension_values", dict(self.dimension_values))


@dataclass(frozen=True)
class ProtocolParserPlan:
    family_id: str
    family_name: str
    lane: str
    plan_version: str
    required_surface_signals: tuple[str, ...]
    dimensions: tuple[str, ...]
    controls: tuple[str, ...]
    steps: tuple[ProtocolParserPlanStep, ...]
    plan_hash: str


def build_protocol_parser_plan(
    family: HunterFamilyView,
    *,
    max_steps: int = MAX_PROTOCOL_PLAN_STEPS,
) -> ProtocolParserPlan:
    """Build a deterministic parser/proxy plan. Does not emit payloads or dispatch."""

    if not isinstance(family, HunterFamilyView):
        raise ResearchInputError("family must be a HunterFamilyView")
    if max_steps < MIN_PROTOCOL_PLAN_STEPS:
        raise ResearchInputError("max_steps must allow at least 8 protocol plan steps")
    requirements = family.evidence_requirements
    lane = _required_text(requirements.get("protocol_lane"), "protocol_lane")
    required_surface_signals = _required_tuple(requirements, "required_surface_signals")
    dimensions = _required_tuple(requirements, "required_protocol_dimensions")
    controls = _required_tuple(requirements, "required_controls")

    values = []
    for dimension in dimensions:
        dimension_values = PROTOCOL_DIMENSION_VALUES.get(dimension)
        if dimension_values is None:
            raise ResearchInputError(f"unknown protocol dimension {dimension}")
        values.append(dimension_values)

    steps: list[ProtocolParserPlanStep] = []
    for index, combo in enumerate(product(*values)):
        if len(steps) >= max_steps:
            break
        control = controls[index % len(controls)]
        dimension_values = dict(zip(dimensions, combo, strict=True))
        steps.append(
            ProtocolParserPlanStep(
                family_id=family.family_id,
                dimension_values=dimension_values,
                control=control,
                step_id=f"{family.family_id}:protocol-step:{index:03d}",
            )
        )
    if len(steps) < MIN_PROTOCOL_PLAN_STEPS:
        raise ResearchInputError("protocol parser plan does not meet the 8-step minimum")

    payload = {
        "family_id": family.family_id,
        "family_name": family.name,
        "lane": lane,
        "plan_version": PROTOCOL_PARSER_PLAN_VERSION,
        "required_surface_signals": required_surface_signals,
        "dimensions": dimensions,
        "controls": controls,
        "steps": [
            {
                "step_id": step.step_id,
                "dimension_values": dict(step.dimension_values),
                "control": step.control,
            }
            for step in steps
        ],
    }
    return ProtocolParserPlan(
        family_id=family.family_id,
        family_name=family.name,
        lane=lane,
        plan_version=PROTOCOL_PARSER_PLAN_VERSION,
        required_surface_signals=required_surface_signals,
        dimensions=dimensions,
        controls=controls,
        steps=tuple(steps),
        plan_hash=_sha256_json(payload),
    )


def _required_tuple(requirements: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = requirements.get(key)
    if not isinstance(value, (list, tuple)) or not value:
        raise ResearchInputError(f"{key} must be a non-empty list")
    return tuple(str(item) for item in value)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchInputError(f"{field_name} must be a non-empty string")
    return value.strip()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
