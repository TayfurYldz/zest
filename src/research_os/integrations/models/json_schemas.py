"""Transport JSON Schema for ModelPort structured output. Not Research validation."""

from __future__ import annotations

from typing import Any, Mapping

from research_os.research.model_port import ModelCallRequest, StructuredOutputTransportError
from research_os.research.output_contracts import (
    DIAGNOSTIC_CONTRACT,
    FALSIFIER_CONTRACT,
    GENERATOR_CONTRACT,
    OutputContract,
    contract_for_role,
)

SUPPORTED_STRICT_SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "enum"}
)

GENERATOR_APPLICATION_SCHEMA = GENERATOR_CONTRACT.json_schema()
FALSIFIER_APPLICATION_SCHEMA = FALSIFIER_CONTRACT.json_schema()
DIAGNOSTIC_APPLICATION_SCHEMA = DIAGNOSTIC_CONTRACT.json_schema()

GENERATOR_OUTPUT_SCHEMA = GENERATOR_CONTRACT.strict_transport_schema()
FALSIFIER_OUTPUT_SCHEMA = FALSIFIER_CONTRACT.strict_transport_schema()
DIAGNOSTIC_OUTPUT_SCHEMA = DIAGNOSTIC_CONTRACT.strict_transport_schema()


def schema_for_role(role_value: str) -> dict[str, Any]:
    schema = contract_for_role(role_value).strict_transport_schema()
    validate_strict_transport_schema(contract_for_role(role_value), schema)
    return schema


def is_diagnostic_readiness_request(request: ModelCallRequest) -> bool:
    return (
        request.context_fingerprint == "codex-diagnostic"
        and request.payload.get("diagnostic") is True
    )


def schema_for_request(request: ModelCallRequest) -> dict[str, Any]:
    if is_diagnostic_readiness_request(request):
        validate_strict_transport_schema(DIAGNOSTIC_CONTRACT, DIAGNOSTIC_OUTPUT_SCHEMA)
        return DIAGNOSTIC_OUTPUT_SCHEMA
    return schema_for_role(request.role.value)


def contract_for_request(request: ModelCallRequest) -> OutputContract:
    if is_diagnostic_readiness_request(request):
        return DIAGNOSTIC_CONTRACT
    return contract_for_role(request.role.value)


def validate_strict_transport_schema(contract: OutputContract, schema: Mapping[str, Any]) -> None:
    """Fail locally before provider invocation if strict transport rules drift."""

    _reject_unsupported_keywords(schema)
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping):
        raise StructuredOutputTransportError("strict transport schema properties are invalid")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise StructuredOutputTransportError("strict transport schema required list is invalid")
    if set(properties) != set(required):
        raise StructuredOutputTransportError("strict transport schema must require every property")
    if schema.get("additionalProperties") is not False:
        raise StructuredOutputTransportError("strict transport schema requires additionalProperties=false")
    field_by_name = {field.name: field for field in contract.fields}
    for name, value in properties.items():
        if name not in field_by_name:
            raise StructuredOutputTransportError("strict transport schema has unknown property")
        if not isinstance(value, Mapping):
            raise StructuredOutputTransportError("strict transport schema property is invalid")
        nullable = _schema_allows_null(value)
        if field_by_name[name].required and nullable:
            raise StructuredOutputTransportError("required canonical field cannot be nullable")
        if not field_by_name[name].required and not nullable:
            raise StructuredOutputTransportError("optional canonical field must be nullable")


def decode_transport_output(contract: OutputContract, raw: Mapping[str, object]) -> dict[str, object]:
    """Deterministic transport decoding, not repair: optional null means omitted."""

    allowed = contract.allowed_keys
    unknown = set(raw) - allowed
    if unknown:
        raise StructuredOutputTransportError("transport output contained unknown fields")
    decoded: dict[str, object] = {}
    field_by_name = {field.name: field for field in contract.fields}
    for name, field in field_by_name.items():
        if name not in raw:
            if field.required:
                raise StructuredOutputTransportError("transport output omitted required field")
            continue
        value = raw[name]
        if value is None:
            if field.required:
                raise StructuredOutputTransportError("transport output null for required field")
            continue
        _validate_value_against_schema(value, field.schema)
        decoded[name] = value
    return decoded


def decode_transport_output_for_request(
    request: ModelCallRequest, raw: Mapping[str, object]
) -> dict[str, object]:
    return decode_transport_output(contract_for_request(request), raw)


def _reject_unsupported_keywords(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "properties":
                if not isinstance(item, Mapping):
                    raise StructuredOutputTransportError(
                        "strict transport schema properties are invalid"
                    )
                for field_schema in item.values():
                    _reject_unsupported_keywords(field_schema)
                continue
            if key not in SUPPORTED_STRICT_SCHEMA_KEYS:
                raise StructuredOutputTransportError("strict transport schema has unsupported keyword")
            _reject_unsupported_keywords(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unsupported_keywords(item)


def _schema_allows_null(schema: Mapping[str, Any]) -> bool:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return "null" in schema_type
    return schema_type == "null"


def _validate_value_against_schema(value: object, schema: Mapping[str, Any]) -> None:
    schema_type = schema.get("type")
    types = schema_type if isinstance(schema_type, list) else [schema_type]
    if "string" in types and isinstance(value, str):
        _validate_enum(value, schema)
        return
    if "boolean" in types and isinstance(value, bool):
        _validate_enum(value, schema)
        return
    if "array" in types and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise StructuredOutputTransportError("transport array schema is invalid")
        for item in value:
            _validate_value_against_schema(item, item_schema)
        return
    raise StructuredOutputTransportError("transport output field type mismatch")


def _validate_enum(value: object, schema: Mapping[str, Any]) -> None:
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise StructuredOutputTransportError("transport output enum value is invalid")
