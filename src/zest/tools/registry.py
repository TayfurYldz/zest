"""Tools capability registry. Policy source only. No execution. No Core types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from zest.tools.fingerprint import fingerprint_capability_document

CAPABILITY_RESOURCE_PACKAGE = "zest.resources"
CAPABILITY_RESOURCE_RELATIVE = ("contracts", "v1", "capabilities")
SUPPORTED_EXECUTOR_CLASSES = frozenset({"WORKER"})
SUPPORTED_REQUIREMENTS = frozenset({"loopback", "scope_derived"})
WORKER_EXECUTOR_CLASS = "WORKER"


class CapabilityRegistryError(ValueError):
    """Invalid capability catalog. Construction must hard-fail."""


@dataclass(frozen=True)
class CapabilityActionDefinition:
    action_id: str
    argument_schema: Mapping[str, Any]
    result_schema: Mapping[str, Any]
    minimum_side_effect_level: int
    maximum_side_effect_level: int
    target_types: tuple[str, ...]
    network_policy: Mapping[str, Any] | None
    requirements: tuple[str, ...]
    supports_reproduction: bool
    supports_negative_control: bool
    normalizer_reference: str | None


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    version: str
    implementation_reference: str
    executor_class: str
    actions: Mapping[str, CapabilityActionDefinition]
    definition_fingerprint: str

    def action(self, action_id: str) -> CapabilityActionDefinition | None:
        return self.actions.get(action_id)


@dataclass(frozen=True)
class ArgumentValidationIssue:
    reason_code: str
    message: str


class CapabilityRegistry:
    def __init__(self, definitions: Mapping[str, CapabilityDefinition]) -> None:
        self._definitions = dict(definitions)

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        return self._definitions.get(capability_id)

    def __contains__(self, capability_id: object) -> bool:
        return capability_id in self._definitions

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def definitions(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def worker_definitions(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(
            item
            for item in self.definitions()
            if item.executor_class == WORKER_EXECUTOR_CLASS
        )

    def lookup(
        self, capability_id: str, action_id: str
    ) -> tuple[CapabilityDefinition, CapabilityActionDefinition] | None:
        definition = self.get(capability_id)
        if definition is None:
            return None
        action = definition.action(action_id)
        if action is None:
            return None
        return definition, action


def load_capability_documents() -> tuple[dict[str, Any], ...]:
    root = files(CAPABILITY_RESOURCE_PACKAGE).joinpath(*CAPABILITY_RESOURCE_RELATIVE)
    documents: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name.endswith(".json"):
            loaded = json.loads(child.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise CapabilityRegistryError(
                    f"capability document {child.name} must be a JSON object"
                )
            executor_class = loaded.get("executor_class")
            if executor_class != WORKER_EXECUTOR_CLASS:
                raise CapabilityRegistryError(
                    f"{child.name} is not a Worker capability definition"
                )
            documents.append(loaded)
    return tuple(documents)


def registry_from_documents(
    documents: Sequence[Mapping[str, Any]],
) -> CapabilityRegistry:
    definitions: dict[str, CapabilityDefinition] = {}
    for document in documents:
        definition = _definition_from_document(document)
        if definition.capability_id in definitions:
            raise CapabilityRegistryError(
                f"duplicate capability_id {definition.capability_id!r}"
            )
        definitions[definition.capability_id] = definition
    return CapabilityRegistry(definitions)


@lru_cache(maxsize=1)
def load_capability_registry() -> CapabilityRegistry:
    return registry_from_documents(load_capability_documents())


def validate_action_arguments(
    schema: Mapping[str, Any], arguments: Mapping[str, Any]
) -> ArgumentValidationIssue | None:
    if not isinstance(arguments, Mapping):
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", "arguments must be an object")
    try:
        Draft202012Validator(schema).validate(dict(arguments))
    except ValidationError as exc:
        return _issue_from_jsonschema(exc)
    return None


def _issue_from_jsonschema(exc: ValidationError) -> ArgumentValidationIssue:
    validator = exc.validator
    if validator == "required":
        return ArgumentValidationIssue("MISSING_REQUIRED_ARGUMENT", exc.message)
    if validator == "additionalProperties":
        return ArgumentValidationIssue("UNEXPECTED_ARGUMENT", exc.message)
    if validator in {"type", "enum", "const", "minLength", "maxLength", "minimum", "maximum"}:
        return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", exc.message)
    return ArgumentValidationIssue("INVALID_ARGUMENT_TYPE", exc.message)


def _definition_from_document(document: Mapping[str, Any]) -> CapabilityDefinition:
    capability_id = _require_text(document.get("capability_id"), "capability_id")
    version = _require_text(document.get("version"), "version")
    implementation_reference = _require_text(
        document.get("implementation_reference"), "implementation_reference"
    )
    executor_class = _require_text(document.get("executor_class"), "executor_class")
    if executor_class not in SUPPORTED_EXECUTOR_CLASSES:
        raise CapabilityRegistryError(
            f"unsupported executor_class {executor_class!r} for {capability_id}"
        )
    actions_in = document.get("actions")
    if not isinstance(actions_in, Mapping) or not actions_in:
        raise CapabilityRegistryError(f"{capability_id} must declare actions")
    actions: dict[str, CapabilityActionDefinition] = {}
    for action_id, raw_action in actions_in.items():
        if not isinstance(action_id, str) or not action_id.strip():
            raise CapabilityRegistryError("action id must be a non-empty string")
        if action_id in actions:
            raise CapabilityRegistryError(
                f"duplicate action id {action_id!r} in {capability_id}"
            )
        if not isinstance(raw_action, Mapping):
            raise CapabilityRegistryError(f"action {action_id!r} must be an object")
        declared_id = raw_action.get("action_id", action_id)
        if declared_id != action_id:
            raise CapabilityRegistryError(
                f"action id mismatch in {capability_id}: {declared_id!r} != {action_id!r}"
            )
        actions[action_id] = _action_from_document(capability_id, action_id, raw_action)
    fingerprint = fingerprint_capability_document(document)
    return CapabilityDefinition(
        capability_id=capability_id,
        version=version,
        implementation_reference=implementation_reference,
        executor_class=executor_class,
        actions=actions,
        definition_fingerprint=fingerprint,
    )


def _action_from_document(
    capability_id: str, action_id: str, raw: Mapping[str, Any]
) -> CapabilityActionDefinition:
    argument_schema = raw.get("argument_schema")
    result_schema = raw.get("result_schema")
    if not isinstance(argument_schema, Mapping):
        raise CapabilityRegistryError(f"{capability_id}/{action_id} argument_schema is required")
    if argument_schema.get("additionalProperties") is not False:
        raise CapabilityRegistryError(
            f"{capability_id}/{action_id} argument_schema must set additionalProperties false"
        )
    if not isinstance(result_schema, Mapping):
        raise CapabilityRegistryError(f"{capability_id}/{action_id} result_schema is required")
    minimum = _require_side_effect(raw.get("minimum_side_effect_level"), "minimum_side_effect_level")
    maximum = _require_side_effect(raw.get("maximum_side_effect_level"), "maximum_side_effect_level")
    if minimum > maximum:
        raise CapabilityRegistryError(
            f"{capability_id}/{action_id} minimum_side_effect_level exceeds maximum"
        )
    target_types = _require_string_tuple(raw.get("target_types"), "target_types")
    if not target_types:
        raise CapabilityRegistryError(f"{capability_id}/{action_id} target_types must not be empty")
    network_policy = raw.get("network_policy")
    if network_policy is not None and not isinstance(network_policy, Mapping):
        raise CapabilityRegistryError(f"{capability_id}/{action_id} network_policy must be object or null")
    requirements = _require_string_tuple(raw.get("requirements") or (), "requirements")
    for requirement in requirements:
        if requirement not in SUPPORTED_REQUIREMENTS:
            raise CapabilityRegistryError(
                f"{capability_id}/{action_id} has unsupported requirement {requirement!r}"
            )
    supports_reproduction = raw.get("supports_reproduction")
    supports_negative_control = raw.get("supports_negative_control")
    if not isinstance(supports_reproduction, bool) or not isinstance(
        supports_negative_control, bool
    ):
        raise CapabilityRegistryError(
            f"{capability_id}/{action_id} reproduction flags must be bool"
        )
    normalizer_reference = raw.get("normalizer_reference")
    if normalizer_reference is not None:
        normalizer_reference = _require_text(normalizer_reference, "normalizer_reference")
    return CapabilityActionDefinition(
        action_id=action_id,
        argument_schema=dict(argument_schema),
        result_schema=dict(result_schema),
        minimum_side_effect_level=minimum,
        maximum_side_effect_level=maximum,
        target_types=target_types,
        network_policy=None if network_policy is None else dict(network_policy),
        requirements=requirements,
        supports_reproduction=supports_reproduction,
        supports_negative_control=supports_negative_control,
        normalizer_reference=normalizer_reference,
    )


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityRegistryError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_side_effect(value: object, field_name: str) -> int:
    if value not in (0, 1, 2, 3):
        raise CapabilityRegistryError(f"{field_name} must be 0, 1, 2, or 3")
    return int(value)


def _require_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise CapabilityRegistryError(f"{field_name} must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CapabilityRegistryError(f"{field_name} must contain non-empty strings")
        items.append(item.strip())
    return tuple(items)
