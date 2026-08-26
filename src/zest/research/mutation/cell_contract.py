"""Compiler-owned MutationMatrixCell → http.transaction mapping.

A cell is Cartesian (dimension_values, control). The model may select a cell;
it does not choose Worker arguments. Incoming query/body/headers are ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

from zest.research.http_transaction import HttpRequestTemplate
from zest.research.types import ResearchInputError

MUTATION_MATRIX_EVALUATION_STRATEGY = "mutation.matrix.v1"
MUTATION_MATRIX_EXPECTED_OBSERVATION = (
    "mutation matrix cell produced a distinguishable HTTP observation under its control"
)
MUTATION_MATRIX_DISCONFIRMING_OBSERVATION = (
    "mutation matrix cell produced no HTTP observation or only the control denial"
)
MAX_QUERY_VALUE_LENGTH = 128
PROBE_TOKEN = "rosq"

FAMILY_REQUIRED_DIMENSIONS: Mapping[str, tuple[str, ...]] = {
    "SQL_INJECTION": ("input_vector", "encoding", "parser_delta"),
    "SERVER_SIDE_TEMPLATE_INJECTION": ("template_engine_probe", "encoding"),
    "FILE_INCLUDE_AND_PATH_TRAVERSAL": ("path_vector", "encoding", "normalization"),
    "MASS_ASSIGNMENT": ("field_family", "role", "state_change"),
    "JWT_CRYPTO_AND_CLAIM_CONFUSION": ("algorithm", "key_source", "claim"),
    "CORS_CREDENTIAL_EXFILTRATION_CHAIN": ("origin_variant", "credentials", "data_sink"),
    "GRAPHQL_AUTHORIZATION_AND_INJECTION": ("operation_kind", "resolver", "identity"),
    "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION": ("source", "sink", "execution_token"),
    "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE": (
        "instruction_channel",
        "retrieval_context",
        "tool_boundary",
    ),
}


class MutationCellContractError(ResearchInputError):
    """Cell cannot be bound to a typed http.transaction plan. Not a Core DENY."""

    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class MutationCellHttpBinding:
    """Deterministic Worker arguments for one matrix cell. Not authorization."""

    template: HttpRequestTemplate
    action: str
    expected_observation: str
    disconfirming_observation: str
    evaluation_strategy: str
    family_name: str
    cell_id: str
    control: str


def bind_mutation_matrix_cell(
    *,
    family_name: str,
    cell_id: str,
    dimension_values: Mapping[str, Any],
    control: str,
    authorized_origin: str,
    path: str,
) -> MutationCellHttpBinding:
    """Map one selected cell onto http.transaction. Does not dispatch."""

    if family_name not in FAMILY_REQUIRED_DIMENSIONS:
        raise MutationCellContractError("UNKNOWN_MUTATION_FAMILY", "family is not a mutation matrix family")
    if not isinstance(cell_id, str) or not cell_id.strip():
        raise MutationCellContractError("CELL_ID_REQUIRED", "cell_id is required")
    if not isinstance(control, str) or not control.strip():
        raise MutationCellContractError("CONTROL_REQUIRED", "control is required")
    if not isinstance(authorized_origin, str) or not authorized_origin.strip():
        raise MutationCellContractError("ORIGIN_REQUIRED", "authorized_origin is required")
    if not isinstance(path, str) or not path.strip():
        raise MutationCellContractError("PATH_REQUIRED", "path is required")
    if not isinstance(dimension_values, Mapping) or not dimension_values:
        raise MutationCellContractError("DIMENSION_VALUES_REQUIRED", "dimension_values are required")
    required = FAMILY_REQUIRED_DIMENSIONS[family_name]
    missing = [name for name in required if not _text(dimension_values.get(name))]
    if missing:
        raise MutationCellContractError(
            "MUTATION_MATRIX_CELL_DIMENSIONS_INCOMPLETE",
            f"missing dimensions: {','.join(missing)}",
        )
    origin = authorized_origin.strip().rstrip("/")
    base_path = path.strip() or "/"
    control_name = control.strip()
    dims = {key: str(dimension_values[key]) for key in required}
    query: dict[str, str] = {
        "ros_fam": _short_family(family_name),
        "ros_ctl": control_name[:32],
        "ros_cell": cell_id.split(":")[-1][:16],
    }
    headers: dict[str, str] | None = None
    body: str | None = None
    content_type: str | None = None
    method = "GET"
    request_path = base_path

    if family_name == "SQL_INJECTION":
        encoded = _encode(PROBE_TOKEN, dims["encoding"])
        _require_query_fit(encoded)
        query["ros_enc"] = dims["encoding"]
        query["ros_dlt"] = dims["parser_delta"]
        method, request_path, query, headers, body, content_type = _apply_input_vector(
            dims["input_vector"],
            encoded,
            base_path,
            query,
        )
    elif family_name == "SERVER_SIDE_TEMPLATE_INJECTION":
        encoded = _encode(f"t{dims['template_engine_probe'][:8]}", dims["encoding"])
        _require_query_fit(encoded)
        query["ros_enc"] = dims["encoding"]
        query["ros_prb"] = dims["template_engine_probe"]
        method, request_path, query, headers, body, content_type = _apply_input_vector(
            "query" if dims["template_engine_probe"] != "filter_boundary" else "json_body",
            encoded,
            base_path,
            query,
        )
    elif family_name == "FILE_INCLUDE_AND_PATH_TRAVERSAL":
        encoded = _encode(PROBE_TOKEN, dims["encoding"])
        _require_query_fit(encoded)
        query["ros_enc"] = dims["encoding"]
        query["ros_nrm"] = dims["normalization"]
        query["ros_vec"] = dims["path_vector"]
        query["ros_p"] = encoded
        if dims["path_vector"] == "path_param":
            request_path = _join_path(base_path, PROBE_TOKEN)
        elif dims["path_vector"] == "json_field":
            method = "POST"
            body = json.dumps({"ros_p": encoded, "ros_nrm": dims["normalization"]}, separators=(",", ":"))
            content_type = "application/json"
        elif dims["path_vector"] == "multipart_name":
            method = "POST"
            body = f"ros_p={encoded}"
            content_type = "application/x-www-form-urlencoded"
        else:
            query["ros_p"] = encoded
    elif family_name == "MASS_ASSIGNMENT":
        query["ros_fld"] = dims["field_family"]
        query["ros_role"] = dims["role"]
        query["ros_st"] = dims["state_change"]
        if dims["state_change"] == "write_attempt":
            method = "POST"
            body = json.dumps({dims["field_family"]: "1", "ros_role": dims["role"]}, separators=(",", ":"))
            content_type = "application/json"
        else:
            method = "GET"
    elif family_name == "JWT_CRYPTO_AND_CLAIM_CONFUSION":
        query["ros_alg"] = dims["algorithm"]
        query["ros_key"] = dims["key_source"]
        query["ros_clm"] = dims["claim"]
        method = "GET"
    elif family_name == "CORS_CREDENTIAL_EXFILTRATION_CHAIN":
        query["ros_org"] = dims["origin_variant"]
        query["ros_crd"] = dims["credentials"]
        query["ros_snk"] = dims["data_sink"]
        method = "GET"
        headers = {"Accept": "application/json"}
    elif family_name == "GRAPHQL_AUTHORIZATION_AND_INJECTION":
        method = "POST"
        body = json.dumps(
            {
                "ros_op": dims["operation_kind"],
                "ros_res": dims["resolver"],
                "ros_id": dims["identity"],
            },
            separators=(",", ":"),
        )
        content_type = "application/json"
        query["ros_op"] = dims["operation_kind"]
    elif family_name == "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION":
        query["ros_src"] = dims["source"]
        query["ros_snk"] = dims["sink"]
        query["ros_tok"] = dims["execution_token"]
        method = "GET"
    elif family_name == "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE":
        method = "POST"
        body = json.dumps(
            {
                "ros_ch": dims["instruction_channel"],
                "ros_ctx": dims["retrieval_context"],
                "ros_tb": dims["tool_boundary"],
            },
            separators=(",", ":"),
        )
        content_type = "application/json"
        query["ros_ch"] = dims["instruction_channel"]
    else:
        raise MutationCellContractError("UNKNOWN_MUTATION_FAMILY", "family is not a mutation matrix family")

    action = "mutate" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
    template = HttpRequestTemplate(
        authorized_origin=origin,
        method=method,
        path=request_path,
        query=query,
        headers=headers,
        body=body,
        content_type=content_type,
        max_response_bytes=4096,
        timeout_ms=2000,
    )
    return MutationCellHttpBinding(
        template=template,
        action=action,
        expected_observation=MUTATION_MATRIX_EXPECTED_OBSERVATION,
        disconfirming_observation=MUTATION_MATRIX_DISCONFIRMING_OBSERVATION,
        evaluation_strategy=MUTATION_MATRIX_EVALUATION_STRATEGY,
        family_name=family_name,
        cell_id=cell_id.strip(),
        control=control_name,
    )


def _apply_input_vector(
    vector: str,
    encoded: str,
    base_path: str,
    query: dict[str, str],
) -> tuple[str, str, dict[str, str], dict[str, str] | None, str | None, str | None]:
    if vector == "query":
        query["ros_p"] = encoded
        return "GET", base_path, query, None, None, None
    if vector == "path":
        query["ros_p"] = encoded
        return "GET", _join_path(base_path, PROBE_TOKEN), query, None, None, None
    if vector == "header":
        query["ros_p"] = encoded
        return "GET", base_path, query, {"X-Request-Id": encoded[:128]}, None, None
    if vector == "cookie_free":
        query["ros_p"] = encoded
        query["ros_ck"] = "omit"
        return "GET", base_path, query, None, None, None
    if vector == "json_body":
        query["ros_p"] = encoded
        body = json.dumps({"ros_p": encoded}, separators=(",", ":"))
        return "POST", base_path, query, None, body, "application/json"
    if vector == "form_body":
        query["ros_p"] = encoded
        return "POST", base_path, query, None, f"ros_p={encoded}", "application/x-www-form-urlencoded"
    raise MutationCellContractError("UNKNOWN_INPUT_VECTOR", f"unknown input_vector {vector}")


def _encode(token: str, encoding: str) -> str:
    if encoding == "raw":
        return token
    if encoding == "url":
        return quote(token, safe="")
    if encoding == "double_url":
        return quote(quote(token, safe=""), safe="")
    if encoding == "unicode_escape":
        return token.encode("unicode_escape").decode("ascii")
    if encoding == "html_entity":
        return "".join(f"&#{ord(char)};" for char in token)
    if encoding == "stacked":
        return f"{token};{token}"
    raise MutationCellContractError("UNKNOWN_ENCODING", f"unknown encoding {encoding}")


def _require_query_fit(value: str) -> None:
    if len(value) > MAX_QUERY_VALUE_LENGTH:
        raise MutationCellContractError("PROBE_EXCEEDS_BOUND", "encoded probe exceeds query bound")
    if any(marker in value for marker in ("\r", "\n", "\x00")):
        raise MutationCellContractError("PROBE_INVALID", "encoded probe contains a forbidden marker")


def _join_path(base_path: str, segment: str) -> str:
    trimmed = base_path.rstrip("/") or ""
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    if any(marker in segment for marker in ("/", "\\", "..", "%")):
        raise MutationCellContractError("PATH_SEGMENT_INVALID", "path probe segment is ambiguous")
    return f"{trimmed}/{segment}"


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _short_family(family_name: str) -> str:
    return {
        "SQL_INJECTION": "sqli",
        "SERVER_SIDE_TEMPLATE_INJECTION": "ssti",
        "FILE_INCLUDE_AND_PATH_TRAVERSAL": "lfi",
        "MASS_ASSIGNMENT": "mass",
        "JWT_CRYPTO_AND_CLAIM_CONFUSION": "jwt",
        "CORS_CREDENTIAL_EXFILTRATION_CHAIN": "cors",
        "GRAPHQL_AUTHORIZATION_AND_INJECTION": "gql",
        "DOM_TAINT_AND_CLIENT_SIDE_EXECUTION": "dom",
        "AI_LLM_PROMPT_INJECTION_AND_TOOL_ABUSE": "llm",
    }[family_name]
