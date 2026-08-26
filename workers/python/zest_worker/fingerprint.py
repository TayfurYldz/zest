"""Deterministic capability-definition fingerprints. Not authorization."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"
CANONICAL_JSON_SEPARATORS = (",", ":")
DEFINITION_POLICY_FIELDS = (
    "capability_id",
    "version",
    "implementation_reference",
    "executor_class",
    "actions",
)
ACTION_POLICY_FIELDS = (
    "action_id",
    "argument_schema",
    "result_schema",
    "minimum_side_effect_level",
    "maximum_side_effect_level",
    "target_types",
    "network_policy",
    "requirements",
    "supports_reproduction",
    "supports_negative_control",
    "normalizer_reference",
)


def canonical_json_bytes(payload: Any) -> bytes:
    """Stable UTF-8 JSON used as the SHA-256 fingerprint input.

    Formatting-only changes of a source document must not change the hash
    because only allowlisted policy fields are serialized with sort_keys.
    """

    return json.dumps(
        payload,
        sort_keys=True,
        separators=CANONICAL_JSON_SEPARATORS,
        ensure_ascii=False,
    ).encode("utf-8")


def allowlisted_policy_payload(document: Mapping[str, Any]) -> dict[str, Any]:
    """Policy fields only. Never includes definition_fingerprint."""

    actions_in = document.get("actions")
    if not isinstance(actions_in, Mapping):
        raise ValueError("capability definition actions must be a mapping")
    actions: dict[str, Any] = {}
    for action_id in sorted(str(key) for key in actions_in):
        raw_action = actions_in[action_id]
        if not isinstance(raw_action, Mapping):
            raise ValueError(f"action {action_id!r} must be a mapping")
        action_payload = {field: raw_action.get(field) for field in ACTION_POLICY_FIELDS}
        if isinstance(action_payload.get("target_types"), Sequence) and not isinstance(
            action_payload.get("target_types"), (str, bytes)
        ):
            action_payload["target_types"] = list(action_payload["target_types"])
        if isinstance(action_payload.get("requirements"), Sequence) and not isinstance(
            action_payload.get("requirements"), (str, bytes)
        ):
            action_payload["requirements"] = list(action_payload["requirements"])
        actions[action_id] = action_payload
    return {
        "capability_id": document.get("capability_id"),
        "version": document.get("version"),
        "implementation_reference": document.get("implementation_reference"),
        "executor_class": document.get("executor_class"),
        "actions": actions,
    }


def fingerprint_capability_document(document: Mapping[str, Any]) -> str:
    payload = allowlisted_policy_payload(document)
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return digest
