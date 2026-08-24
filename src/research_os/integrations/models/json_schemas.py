"""Transport JSON Schema for ModelPort structured output. Not Research validation."""

from __future__ import annotations

from typing import Any

from research_os.research.model_port import ModelCallRequest
from research_os.research.output_contracts import (
    DIAGNOSTIC_CONTRACT,
    FALSIFIER_CONTRACT,
    GENERATOR_CONTRACT,
    contract_for_role,
)

GENERATOR_OUTPUT_SCHEMA = GENERATOR_CONTRACT.json_schema()
FALSIFIER_OUTPUT_SCHEMA = FALSIFIER_CONTRACT.json_schema()
DIAGNOSTIC_OUTPUT_SCHEMA = DIAGNOSTIC_CONTRACT.json_schema()


def schema_for_role(role_value: str) -> dict[str, Any]:
    return contract_for_role(role_value).json_schema()


def is_diagnostic_readiness_request(request: ModelCallRequest) -> bool:
    return (
        request.context_fingerprint == "codex-diagnostic"
        and request.payload.get("diagnostic") is True
    )


def schema_for_request(request: ModelCallRequest) -> dict[str, Any]:
    if is_diagnostic_readiness_request(request):
        return DIAGNOSTIC_OUTPUT_SCHEMA
    return schema_for_role(request.role.value)
