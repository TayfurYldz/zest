"""Compiler-owned ProtocolParserPlanStep → http.raw_exchange mapping.

Approved ProtocolPlan is not an execution token. Each step binds independently.
The model does not supply request bytes; framing_profile is a closed enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from zest.research.types import ResearchInputError

PROTOCOL_STEP_EVALUATION_STRATEGY = "protocol.parser.step.v1"
PROTOCOL_STEP_EXPECTED_OBSERVATION = (
    "protocol step produced a bounded raw-exchange observation under its control"
)
PROTOCOL_STEP_DISCONFIRMING_OBSERVATION = (
    "protocol step produced no raw-exchange observation or a redirect/new-origin stop"
)
HTTP_RAW_EXCHANGE_CAPABILITY = "http.raw_exchange"
HTTP_RAW_EXCHANGE_ACTION = "probe"

SMUGGLING_LANE = "http_request_smuggling_desync"
CACHE_LANE = "http_cache_poisoning_deception"

SMUGGLING_REQUIRED_DIMENSIONS = ("frontend_protocol", "backend_protocol", "normalization_boundary")
CACHE_REQUIRED_DIMENSIONS = ("cache_key_dimension", "cache_behavior", "proxy_layer")

FRAMING_PROFILES = frozenset(
    {
        "http1_canonical",
        "http1_header_case_fold",
        "http1_absolute_uri",
        "http1_cl_te",
        "http1_te_cl",
        "http1_connection_reuse",
        "http2_preface",
        "h2c_upgrade",
        "http1_cache_host",
        "http1_cache_scheme",
        "http1_cache_path",
        "http1_cache_query",
        "http1_cache_header",
    }
)


class ProtocolStepContractError(ResearchInputError):
    """Step cannot be bound to http.raw_exchange. Not a Core DENY."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class ProtocolStepBinding:
    arguments: Mapping[str, Any]
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    step_id: str
    control: str
    framing_profile: str


def bind_protocol_step(
    *,
    family_name: str,
    step_id: str,
    dimension_values: Mapping[str, Any],
    control: str,
    authorized_origin: str,
    path: str,
    protocol_lane: str,
) -> ProtocolStepBinding:
    if not isinstance(step_id, str) or not step_id.strip():
        raise ProtocolStepContractError("STEP_ID_REQUIRED", "step_id is required")
    if not isinstance(control, str) or not control.strip():
        raise ProtocolStepContractError("CONTROL_REQUIRED", "control is required")
    if not isinstance(authorized_origin, str) or not authorized_origin.strip():
        raise ProtocolStepContractError("ORIGIN_REQUIRED", "authorized_origin is required")
    if not isinstance(path, str) or not path.strip():
        raise ProtocolStepContractError("PATH_REQUIRED", "path is required")
    if not isinstance(dimension_values, Mapping) or not dimension_values:
        raise ProtocolStepContractError("DIMENSION_VALUES_REQUIRED", "dimension_values are required")
    lane = protocol_lane.strip() if isinstance(protocol_lane, str) else ""
    if lane == SMUGGLING_LANE:
        required = SMUGGLING_REQUIRED_DIMENSIONS
        profile = _smuggling_profile(dimension_values, control)
    elif lane == CACHE_LANE:
        required = CACHE_REQUIRED_DIMENSIONS
        profile = _cache_profile(dimension_values)
    else:
        raise ProtocolStepContractError("UNKNOWN_PROTOCOL_LANE", "protocol_lane is not a specialist lane")
    missing = [name for name in required if not _text(dimension_values.get(name))]
    if missing:
        raise ProtocolStepContractError(
            "PROTOCOL_STEP_DIMENSIONS_INCOMPLETE",
            f"missing dimensions: {','.join(missing)}",
        )
    if profile not in FRAMING_PROFILES:
        raise ProtocolStepContractError("UNKNOWN_FRAMING_PROFILE", "framing_profile is not in the closed catalog")
    arguments = {
        "authorized_origin": authorized_origin.strip().rstrip("/"),
        "path": path.strip(),
        "framing_profile": profile,
        "control": control.strip()[:64],
        "lane": lane,
        "max_response_bytes": 4096,
        "timeout_ms": 2000,
    }
    return ProtocolStepBinding(
        arguments=arguments,
        expected_observation=PROTOCOL_STEP_EXPECTED_OBSERVATION,
        disconfirming_observation=PROTOCOL_STEP_DISCONFIRMING_OBSERVATION,
        evaluation_strategy=PROTOCOL_STEP_EVALUATION_STRATEGY,
        step_id=step_id.strip(),
        control=control.strip(),
        framing_profile=profile,
    )


def _smuggling_profile(dimension_values: Mapping[str, Any], control: str) -> str:
    frontend = _text(dimension_values.get("frontend_protocol"))
    boundary = _text(dimension_values.get("normalization_boundary"))
    if frontend == "http2":
        return "http2_preface"
    if frontend == "h2c_upgrade":
        return "h2c_upgrade"
    if frontend != "http1":
        raise ProtocolStepContractError("UNKNOWN_FRONTEND_PROTOCOL", "frontend_protocol is not in the catalog")
    if boundary == "content_length_transfer_encoding":
        return "http1_te_cl" if "deceptive" in control else "http1_cl_te"
    if boundary == "connection_reuse":
        return "http1_connection_reuse"
    if boundary == "header_case_fold":
        return "http1_header_case_fold"
    if boundary == "absolute_uri":
        return "http1_absolute_uri"
    raise ProtocolStepContractError("UNKNOWN_NORMALIZATION_BOUNDARY", "normalization_boundary is not in the catalog")


def _cache_profile(dimension_values: Mapping[str, Any]) -> str:
    key = _text(dimension_values.get("cache_key_dimension"))
    mapping = {
        "host": "http1_cache_host",
        "scheme": "http1_cache_scheme",
        "path": "http1_cache_path",
        "query": "http1_cache_query",
        "header": "http1_cache_header",
    }
    if key not in mapping:
        raise ProtocolStepContractError("UNKNOWN_CACHE_KEY_DIMENSION", "cache_key_dimension is not in the catalog")
    return mapping[key]


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
