from __future__ import annotations

import copy
import json
import unittest

import pathsetup  # noqa: F401

from zest.tools.fingerprint import canonical_json_bytes, fingerprint_capability_document
from zest.tools.registry import (
    CapabilityRegistryError,
    load_capability_registry,
    registry_from_documents,
    validate_action_arguments,
)


def _echo_document() -> dict:
    registry = load_capability_registry()
    echo = registry.get("diagnostic.echo")
    assert echo is not None
    return dict(echo.document) if hasattr(echo, "document") else {
        "capability_id": echo.capability_id,
        "version": echo.version,
        "implementation_reference": echo.implementation_reference,
        "executor_class": echo.executor_class,
        "actions": {
            action.action_id: {
                "action_id": action.action_id,
                "argument_schema": dict(action.argument_schema),
                "result_schema": dict(action.result_schema),
                "minimum_side_effect_level": action.minimum_side_effect_level,
                "maximum_side_effect_level": action.maximum_side_effect_level,
                "target_types": list(action.target_types),
                "network_policy": action.network_policy,
                "requirements": list(action.requirements),
                "supports_reproduction": action.supports_reproduction,
                "supports_negative_control": action.supports_negative_control,
                "normalizer_reference": action.normalizer_reference,
            }
            for action in echo.actions.values()
        },
    }


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        load_capability_registry.cache_clear()

    def test_loads_existing_actions_with_fixed_side_effects(self) -> None:
        registry = load_capability_registry()
        echo = registry.get("diagnostic.echo")
        auth = registry.get("http.authorization.differential")
        state = registry.get("http.state_transition")
        transaction = registry.get("http.transaction")
        assert echo is not None and auth is not None and state is not None and transaction is not None
        self.assertEqual(echo.action("echo").minimum_side_effect_level, 0)
        self.assertEqual(echo.action("echo").maximum_side_effect_level, 0)
        self.assertEqual(auth.action("probe").minimum_side_effect_level, 0)
        self.assertEqual(state.action("probe").minimum_side_effect_level, 1)
        self.assertEqual(state.action("probe").maximum_side_effect_level, 1)
        self.assertEqual(transaction.action("read").minimum_side_effect_level, 0)
        self.assertEqual(transaction.action("read").maximum_side_effect_level, 0)
        self.assertEqual(transaction.action("mutate").minimum_side_effect_level, 1)
        self.assertEqual(transaction.action("mutate").maximum_side_effect_level, 1)
        self.assertEqual(echo.executor_class, "WORKER")
        self.assertNotIn("strix.diagnostic.ping", {item.capability_id for item in registry.worker_definitions()})

    def test_duplicate_capability_id_hard_fails(self) -> None:
        document = _echo_document()
        with self.assertRaises(CapabilityRegistryError):
            registry_from_documents([document, copy.deepcopy(document)])

    def test_duplicate_action_id_hard_fails(self) -> None:
        document = _echo_document()
        document["actions"]["other"] = copy.deepcopy(document["actions"]["echo"])
        document["actions"]["other"]["action_id"] = "echo"
        with self.assertRaises(CapabilityRegistryError):
            registry_from_documents([document])

    def test_fingerprint_ignores_formatting_only_changes(self) -> None:
        document = _echo_document()
        pretty = json.loads(json.dumps(document, indent=8))
        self.assertEqual(
            fingerprint_capability_document(document),
            fingerprint_capability_document(pretty),
        )

    def test_fingerprint_changes_when_policy_changes(self) -> None:
        document = _echo_document()
        original = fingerprint_capability_document(document)
        document["version"] = "2"
        self.assertNotEqual(original, fingerprint_capability_document(document))

    def test_fingerprint_does_not_hash_fingerprint_field(self) -> None:
        document = _echo_document()
        first = fingerprint_capability_document(document)
        document["definition_fingerprint"] = "should-not-matter"
        self.assertEqual(first, fingerprint_capability_document(document))

    def test_canonical_json_is_sorted_and_compact(self) -> None:
        payload = {"b": 1, "a": 2}
        self.assertEqual(canonical_json_bytes(payload), b'{"a":2,"b":1}')

    def test_additional_properties_false_is_enforced(self) -> None:
        registry = load_capability_registry()
        echo = registry.get("diagnostic.echo")
        assert echo is not None
        schema = echo.action("echo").argument_schema
        issue = validate_action_arguments(schema, {"message": "ping", "extra": True})
        self.assertIsNotNone(issue)
        self.assertEqual(issue.reason_code, "UNEXPECTED_ARGUMENT")

    def test_registry_rejects_integration_executor_class(self) -> None:
        document = _echo_document()
        document["capability_id"] = "strix.diagnostic.ping"
        document["implementation_reference"] = "strix.diagnostic.ping"
        document["executor_class"] = "INTEGRATION"
        with self.assertRaises(CapabilityRegistryError):
            registry_from_documents([document])

    def test_strix_and_codex_are_not_worker_registry_entries(self) -> None:
        registry = load_capability_registry()
        self.assertIsNone(registry.get("strix.diagnostic.ping"))
        self.assertIsNone(registry.get("codex.diagnostic.structured_output"))
        self.assertEqual(
            set(registry.ids()),
            {
                "browser.page",
                "diagnostic.echo",
                "http.authentication",
                "http.authorization.differential",
                "http.raw_exchange",
                "http.state_transition",
                "http.transaction",
            },
        )


if __name__ == "__main__":
    unittest.main()
