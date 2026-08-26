"""Load packaged Worker capability definitions. No dynamic import. No eval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import fingerprint_capability_document

WORKER_EXECUTOR_CLASS = "WORKER"


@dataclass(frozen=True)
class PackagedAction:
    action_id: str
    argument_schema: Mapping[str, Any]
    minimum_side_effect_level: int
    maximum_side_effect_level: int


@dataclass(frozen=True)
class PackagedCapability:
    capability_id: str
    version: str
    implementation_reference: str
    executor_class: str
    actions: Mapping[str, PackagedAction]
    definition_fingerprint: str
    document: Mapping[str, Any]


def packaged_capability_dir() -> Path:
    return Path(__file__).resolve().parent / "resources" / "capabilities"


def load_packaged_capabilities() -> dict[str, PackagedCapability]:
    root = packaged_capability_dir()
    loaded: dict[str, PackagedCapability] = {}
    if not root.is_dir():
        raise RuntimeError("packaged capability definitions are missing")
    for path in sorted(root.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise RuntimeError(f"{path.name} must be a JSON object")
        capability_id = document.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id:
            raise RuntimeError(f"{path.name} is missing capability_id")
        if capability_id in loaded:
            raise RuntimeError(f"duplicate packaged capability_id {capability_id}")
        actions_in = document.get("actions")
        if not isinstance(actions_in, dict) or not actions_in:
            raise RuntimeError(f"{capability_id} must declare actions")
        actions: dict[str, PackagedAction] = {}
        for action_id, raw in actions_in.items():
            if action_id in actions:
                raise RuntimeError(f"duplicate action {action_id} in {capability_id}")
            if not isinstance(raw, dict):
                raise RuntimeError(f"action {action_id} must be an object")
            schema = raw.get("argument_schema")
            if not isinstance(schema, dict):
                raise RuntimeError(f"{capability_id}/{action_id} argument_schema missing")
            actions[str(action_id)] = PackagedAction(
                action_id=str(action_id),
                argument_schema=schema,
                minimum_side_effect_level=int(raw["minimum_side_effect_level"]),
                maximum_side_effect_level=int(raw["maximum_side_effect_level"]),
            )
        loaded[capability_id] = PackagedCapability(
            capability_id=capability_id,
            version=str(document.get("version") or ""),
            implementation_reference=str(document.get("implementation_reference") or ""),
            executor_class=str(document.get("executor_class") or ""),
            actions=actions,
            definition_fingerprint=fingerprint_capability_document(document),
            document=document,
        )
    return loaded


def validate_arguments(schema: Mapping[str, Any], arguments: object) -> str | None:
    """Bounded JSON Schema subset. additionalProperties:false is enforced."""

    if not isinstance(arguments, dict):
        return "SCHEMA_MISMATCH"
    if schema.get("type") == "object":
        required = schema.get("required") or []
        if not isinstance(required, list):
            return "SCHEMA_MISMATCH"
        for name in required:
            if name not in arguments:
                return "SCHEMA_MISMATCH"
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            return "SCHEMA_MISMATCH"
        if schema.get("additionalProperties") is False:
            extra = set(arguments) - set(properties)
            if extra:
                return "SCHEMA_MISMATCH"
        for key, value in arguments.items():
            if key not in properties:
                continue
            issue = _validate_value(properties[key], value)
            if issue is not None:
                return issue
    return None


def _validate_value(schema: Mapping[str, Any], value: object) -> str | None:
    expected = schema.get("type")
    if expected == "string" and not isinstance(value, str):
        return "SCHEMA_MISMATCH"
    if expected == "integer" and type(value) is not int:
        return "SCHEMA_MISMATCH"
    if expected == "boolean" and type(value) is not bool:
        return "SCHEMA_MISMATCH"
    if expected == "object":
        return validate_arguments(schema, value)
    if expected == "array":
        if not isinstance(value, list):
            return "SCHEMA_MISMATCH"
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for item in value:
                issue = _validate_value(item_schema, item)
                if issue is not None:
                    return issue
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        return "SCHEMA_MISMATCH"
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and isinstance(value, str) and len(value) < min_length:
        return "SCHEMA_MISMATCH"
    return None
