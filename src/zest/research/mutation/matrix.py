"""Bounded mutation matrix planning for broad injection families."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping

from zest.research.selection import HunterFamilyView
from zest.research.types import ResearchInputError

MUTATION_MATRIX_VERSION = "mutation.matrix.v1"
MIN_MEANINGFUL_CELLS = 30
MAX_MATRIX_CELLS = 96

DIMENSION_VALUES: Mapping[str, tuple[str, ...]] = {
    "input_vector": ("query", "path", "json_body", "form_body", "header", "cookie_free"),
    "encoding": ("raw", "url", "double_url", "unicode_escape", "html_entity", "stacked"),
    "parser_delta": ("boolean_delta", "latency_delta", "shape_delta", "status_delta"),
    "template_engine_probe": (
        "math_eval",
        "delimiter_probe",
        "context_escape",
        "engine_marker",
        "blind_timing",
        "filter_boundary",
    ),
    "path_vector": ("path_param", "query_param", "json_field", "multipart_name"),
    "normalization": ("dot_segment", "encoded_slash", "double_decode", "mixed_separator"),
    "field_family": ("role", "owner", "tenant", "state", "balance", "admin_flag"),
    "role": ("anonymous", "owner", "peer", "lower_role"),
    "state_change": ("write_attempt", "read_back", "second_session_read"),
    "algorithm": ("none", "hs_rs_confusion", "kid_path", "jwk_swap"),
    "key_source": ("inline_jwk", "remote_jku", "kid_header", "default_secret"),
    "claim": ("sub", "aud", "iss", "role", "tenant"),
    "origin_variant": ("exact", "null", "suffix_confusion", "scheme_confusion"),
    "credentials": ("omit", "include", "same_origin_cookie"),
    "data_sink": ("me_endpoint", "billing_endpoint", "export_endpoint", "admin_probe"),
    "operation_kind": ("query", "mutation", "subscription"),
    "resolver": ("node", "edge", "admin", "bulk"),
    "identity": ("anonymous", "owner", "peer", "admin_candidate"),
    "source": ("location", "hash", "post_message", "storage", "referrer"),
    "sink": ("inner_html", "script_url", "navigation", "template"),
    "execution_token": ("dom_marker", "console", "global_write", "oob_callback"),
    "instruction_channel": ("direct_prompt", "retrieved_doc", "tool_result", "system_reflection"),
    "retrieval_context": ("none", "benign_doc", "hostile_doc", "mixed_doc"),
    "tool_boundary": ("no_tool", "read_tool", "write_tool", "exfil_sink"),
}


@dataclass(frozen=True)
class MutationMatrixCell:
    family_id: str
    dimension_values: Mapping[str, str]
    control: str
    cell_id: str

    def __post_init__(self) -> None:
        if not self.family_id.strip():
            raise ResearchInputError("family_id is required")
        if not self.cell_id.strip():
            raise ResearchInputError("cell_id is required")
        if not isinstance(self.dimension_values, Mapping) or not self.dimension_values:
            raise ResearchInputError("dimension_values must be a non-empty mapping")
        object.__setattr__(self, "dimension_values", dict(self.dimension_values))


@dataclass(frozen=True)
class MutationMatrixPlan:
    family_id: str
    family_name: str
    matrix_version: str
    dimensions: tuple[str, ...]
    controls: tuple[str, ...]
    cells: tuple[MutationMatrixCell, ...]
    matrix_hash: str


def build_mutation_matrix(
    family: HunterFamilyView,
    *,
    max_cells: int = MAX_MATRIX_CELLS,
) -> MutationMatrixPlan:
    """Build a deterministic matrix plan. Does not create payloads or dispatch."""

    if not isinstance(family, HunterFamilyView):
        raise ResearchInputError("family must be a HunterFamilyView")
    if max_cells < MIN_MEANINGFUL_CELLS:
        raise ResearchInputError("max_cells must allow at least 30 meaningful cells")
    requirements = family.evidence_requirements
    dimensions = _required_tuple(requirements, "required_matrix_dimensions")
    controls = _required_tuple(requirements, "required_controls")
    values = []
    for dimension in dimensions:
        dimension_values = DIMENSION_VALUES.get(dimension)
        if dimension_values is None:
            raise ResearchInputError(f"unknown matrix dimension {dimension}")
        values.append(dimension_values)
    cells: list[MutationMatrixCell] = []
    for index, combo in enumerate(product(*values)):
        if len(cells) >= max_cells:
            break
        control = controls[index % len(controls)]
        dimension_values = dict(zip(dimensions, combo, strict=True))
        cells.append(
            MutationMatrixCell(
                family_id=family.family_id,
                dimension_values=dimension_values,
                control=control,
                cell_id=f"{family.family_id}:cell:{index:03d}",
            )
        )
    if len(cells) < MIN_MEANINGFUL_CELLS:
        raise ResearchInputError("family matrix does not meet the 30-cell minimum")
    payload = {
        "family_id": family.family_id,
        "family_name": family.name,
        "matrix_version": MUTATION_MATRIX_VERSION,
        "dimensions": dimensions,
        "controls": controls,
        "cells": [
            {
                "cell_id": cell.cell_id,
                "dimension_values": dict(cell.dimension_values),
                "control": cell.control,
            }
            for cell in cells
        ],
    }
    return MutationMatrixPlan(
        family_id=family.family_id,
        family_name=family.name,
        matrix_version=MUTATION_MATRIX_VERSION,
        dimensions=dimensions,
        controls=controls,
        cells=tuple(cells),
        matrix_hash=_sha256_json(payload),
    )


def _required_tuple(requirements: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = requirements.get(key)
    if not isinstance(value, (list, tuple)) or not value:
        raise ResearchInputError(f"{key} must be a non-empty list")
    return tuple(str(item) for item in value)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
