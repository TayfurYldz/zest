"""Deterministic Worker dispatch through a static implementation registry."""

from __future__ import annotations

from typing import Any, Mapping

from .implementation import IMPLEMENTATION_EXECUTORS
from .packaged_registry import (
    WORKER_EXECUTOR_CLASS,
    load_packaged_capabilities,
    validate_arguments,
)

DIAGNOSTIC_ECHO_CAPABILITY = "diagnostic.echo"
DIAGNOSTIC_ECHO_ACTION = "echo"


def execute(request: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Return (status, raw_result, diagnostics)."""
    catalog = load_packaged_capabilities()
    capability_id = request.get("worker_capability")
    action_id = request.get("action")
    if not isinstance(capability_id, str) or capability_id not in catalog:
        return _failed("UNKNOWN_CAPABILITY", {"capability": capability_id})
    definition = catalog[capability_id]
    if not isinstance(action_id, str) or action_id not in definition.actions:
        return _failed("UNKNOWN_ACTION", {"action": action_id})
    requested_version = request.get("capability_version")
    if requested_version != definition.version:
        return _failed(
            "UNSUPPORTED_CAPABILITY_VERSION",
            {"capability_version": requested_version},
        )
    requested_fingerprint = request.get("capability_definition_fingerprint")
    if requested_fingerprint != definition.definition_fingerprint:
        return _failed(
            "DEFINITION_FINGERPRINT_MISMATCH",
            {"capability_definition_fingerprint": requested_fingerprint},
        )
    action = definition.actions[action_id]
    schema_issue = validate_arguments(action.argument_schema, request.get("arguments"))
    if schema_issue is not None:
        return _failed("SCHEMA_MISMATCH", {"error": "arguments do not match action schema"})
    if definition.executor_class != WORKER_EXECUTOR_CLASS:
        return _failed(
            "IMPLEMENTATION_NOT_AVAILABLE",
            {"implementation_reference": definition.implementation_reference},
        )
    executor = IMPLEMENTATION_EXECUTORS.get(definition.implementation_reference)
    if executor is None:
        return _failed(
            "IMPLEMENTATION_NOT_AVAILABLE",
            {"implementation_reference": definition.implementation_reference},
        )
    return executor(request)


def _failed(
    reason_code: str, diagnostics: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = dict(diagnostics)
    payload["reason_code"] = reason_code
    return "EXECUTION_FAILED", {}, payload
