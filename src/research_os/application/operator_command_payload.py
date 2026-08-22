"""Reject client attempts to override persisted research authority."""

from __future__ import annotations

from typing import Any, Mapping

from research_os.application.operator_errors import OperatorError, OperatorErrorCode

FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "target",
        "target_reference",
        "scope",
        "authorization_source",
        "authorization_source_id",
        "budget",
        "budget_id",
        "max_requests",
        "max_tool_calls",
        "max_runtime_ms",
        "max_concurrency",
        "max_cycles",
        "max_experiments",
        "max_model_calls",
        "max_worker_invocations",
        "max_elapsed_ms",
        "side_effect_ceiling",
        "research_question",
        "worker_envelope",
        "issued_budget",
        "program_id",
        "policy",
    }
)


def reject_authority_overrides(payload: Mapping[str, Any] | None) -> None:
    if payload is None:
        return
    if not isinstance(payload, Mapping):
        raise OperatorError(OperatorErrorCode.INVALID_INPUT, "request body must be an object")
    present = sorted(key for key in payload if str(key).lower() in FORBIDDEN_AUTHORITY_KEYS)
    if present:
        raise OperatorError(
            OperatorErrorCode.INVALID_INPUT,
            "client must not supply authoritative fields: " + ", ".join(present),
        )
